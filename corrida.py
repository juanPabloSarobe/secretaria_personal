#!/usr/bin/env python3
"""Corrida en frío sobre el atraso: clasificar mucho, molestar poco.

Hermano de `simulacro.py`, con una diferencia que es la razón de existir
de este archivo: el simulacro le pregunta a JP por CADA correo, uno por
mensaje de Telegram. Sobre el goteo de un día eso está bien; sobre un
atraso de 141 correos son 141 mensajes pidiendo respuesta, y eso ya
pasó una vez -- JP estaba entrenando y no podía contestar nada.

Acá la clasificación es callada: primero corre entera contra la misma
cadena que usa producción, y recién cuando terminó le muestra a JP el
resultado en tandas de doce, numeradas, con dos botones. Doce mensajes
en vez de ciento cuarenta y uno.

**No toca la casilla.** Ni archiva, ni marca leído, ni manda correo. El
freno no es una variable que alguien pueda dejar prendida: las funciones
que escriben no se importan en este archivo, y hay un test que lo
verifica (tests/test_corrida.py, NoTocaLaCasilla).
"""
import datetime
import html
import itertools
import json
import os
import random
import sys
from collections import Counter
from datetime import date

import bot
import clasificador
import correo
import memoria
import reglas

#: Telegram rechaza el mensaje entero pasado este límite -- no lo
#: recorta él. Mismo valor y mismo motivo que en secretaria.py.
LIMITE_TELEGRAM = 4096

#: Doce por tanda: 141 correos entran en 12 mensajes. Un número más
#: chico multiplica los mensajes (que es lo que vinimos a evitar) y uno
#: más grande hace la lista difícil de leer en un teléfono.
TAMANO_TANDA = 12

#: El orden en que JP revisa. No es cronológico a propósito: si
#: abandona la revisión a la mitad -- lo más probable, son 141 --, lo
#: que alcanzó a mirar tiene que ser lo que más valía. Lo suyo primero;
#: enseguida DUDA y ERROR, que son justo donde el clasificador no llegó
#: solo y por eso son los casos que más enseñan; el ruido al final.
ORDEN_REVISION = ["TUYO", "DUDA", "ERROR", "ENZO", "NATALIA",
                  "DELEGADO", "RUIDO"]


def ordenar_para_revisar(correos):
    """Los correos en el orden de ORDEN_REVISION, estable adentro de
    cada categoría.

    Una categoría que no esté en la lista va al final, pero VA: que el
    modelo devuelva algo inesperado no puede hacer desaparecer el caso
    del informe -- es exactamente el caso que más querríamos ver.
    """
    def clave(c):
        cat = c.get("categoria")
        return (ORDEN_REVISION.index(cat) if cat in ORDEN_REVISION
                else len(ORDEN_REVISION))
    return sorted(correos, key=clave)


def partir_en_tandas(correos, tamano=TAMANO_TANDA):
    """Los correos en lotes de a `tamano`, en orden y sin repetir."""
    return [correos[i:i + tamano] for i in range(0, len(correos), tamano)]


def _linea(n, c):
    """Una entrada de la lista numerada: quién, sobre qué, y qué le puso
    el clasificador con su motivo.

    El motivo no es adorno: sin él JP ve una etiqueta sin la razón que
    la explica, y no puede decidir si corregirla.
    """
    return (f"  {n}. {html.escape((c.get('de') or '')[:34])} — "
            f"{html.escape((c.get('asunto') or '')[:44])}\n"
            f"     → <b>{html.escape(c.get('categoria') or '?')}</b> · "
            f"<i>{html.escape((c.get('motivo') or '')[:70])}</i>")


def armar_tanda(lote, n, total, tanda):
    """El texto y los botones de una tanda. Devuelve (texto, teclado).

    Los dos botones llevan el token de la tanda porque toda tanda numera
    desde 1: sin el token, "Está bien" de una tanda vieja cerraría la
    nueva. Es el mismo cuidado que `bot.es_de_esta_tanda` ya tenía.
    """
    cabecera = (f"📋 <b>Tanda {n} de {total}</b> — {len(lote)} correos.\n"
                f"Si alguno está mal clasificado, tocá «Corregir».")
    lineas = [_linea(i, c) for i, c in enumerate(lote, 1)]
    texto = "\n".join([cabecera] + lineas)

    if len(texto) > LIMITE_TELEGRAM:
        # Se recorta con aviso, nunca en silencio: mismo criterio que
        # armar_resumen_de_ruido. Los que no se listan siguen contando
        # -- la tanda los conoce igual, así que el número N sigue
        # seleccionando el mismo correo (ver `abiertos` en Revision).
        presupuesto = LIMITE_TELEGRAM - len(cabecera) - 120
        incluidas, largo = [], 0
        for linea in lineas:
            if largo + len(linea) + 1 > presupuesto:
                break
            incluidas.append(linea)
            largo += len(linea) + 1
        faltan = len(lineas) - len(incluidas)
        aviso = ([f"  … y {faltan} más, sin listar por espacio "
                  f"(se pueden corregir igual, por su número)"]
                 if faltan else [])
        texto = "\n".join([cabecera] + incluidas + aviso)

    teclado = {"inline_keyboard": [[
        {"text": "✅ Está bien", "callback_data": f"b|{tanda}-0|ok"},
        {"text": "✏️ Corregir", "callback_data": f"g|{tanda}-0|ok"}]]}
    return texto, teclado


# ---------------------------------------------------------------- fase 1

def clasificar_todo(cx, entrantes, sistema, al_avanzar=None):
    """Clasifica el lote entero, callado, y lo deja anotado en `cx`.

    Callado es el punto de este archivo: acá no sale ni un mensaje de
    Telegram. Lo que JP ve son las tandas de la fase 2, ya con todo
    clasificado.

    La cadena es la MISMA que la de producción -código convenido, atajo
    de ruido conocido con su muestreo, y recién ahí el modelo-, y no por
    prolijidad: es la regla del proyecto (CLAUDE.md). Si acá midiéramos
    una cadena distinta de la que corre, el número que sale no diría
    nada sobre el sistema real.

    Lo ya anotado se saltea, así que una corrida cortada a la mitad -por
    una caída de NVIDIA, o de la Mac- se retoma sin volver a pagar las
    llamadas que ya salieron.

    `al_avanzar(n, total, correo, categoria)` es para el avance por
    pantalla; no se usa para nada más.
    """
    frases = reglas.codigos_convenidos()
    direcciones, dominios = reglas.remitentes_ruido()
    total = len(entrantes)
    for n, c in enumerate(entrantes, 1):
        if memoria.situacion(cx, c["message_id"]) is not None:
            continue

        codigo = reglas.tiene_codigo(c, frases)
        if codigo:
            categoria, motivo = "TUYO", f"código convenido: «{codigo}»"
        else:
            motivo_auto = (None if (reglas.protegido(c) or codigo)
                           else reglas.es_ruido_conocido(c, direcciones, dominios))
            if motivo_auto and random.randrange(reglas.MUESTREO_CONTROL):
                categoria, motivo = "RUIDO", f"ruido conocido — {motivo_auto}"
            else:
                try:
                    pred = clasificador.clasificar(sistema, c)
                    categoria = pred["categoria"]
                    motivo = pred.get("motivo", "")
                except (Exception, SystemExit) as e:
                    # Un correo que revienta no puede costar los 70 que
                    # ya se clasificaron: se anota como ERROR -que es una
                    # respuesta, no un silencio- y la corrida sigue.
                    categoria, motivo = "ERROR", f"{type(e).__name__}: {e}"

        memoria.anotar(cx, c, categoria, motivo)
        if al_avanzar:
            al_avanzar(n, total, c, categoria)


# ------------------------------------------------- lo que JP dictamina

def confirmar(cx, message_ids):
    """JP tocó «Está bien»: lo que dijo el sistema queda como correcto.

    Se escribe en `categoria_jp` igual que una corrección, porque para
    medir da lo mismo cómo se pronunció -- lo que importa es que se
    pronunció. Un correo sin `categoria_jp` es uno que JP no miró, y
    ésos no entran en la medición (ver armar_json).

    **Lo ya corregido no se toca.** «Está bien» quiere decir "el RESTO
    está bien", nunca "olvidate de lo que te acabo de decir". Sin este
    salto, corregir un correo y después cerrar la tanda -- que es la
    secuencia normal, no un caso raro -- pisaba la corrección con la
    categoría del sistema. Encontrado por JP en la primera prueba real
    (2026-08-26): corrigió uno a ENZO, cerró la tanda, y el informe
    salió diciendo "acertó 3 de 3 (100%)". Perder la corrección es
    malo; devolver un porcentaje perfecto que la esconde es peor, porque
    es el número con el que se decide si el clasificador anda.
    """
    for mid in message_ids:
        fila = memoria.obtener(cx, mid)
        if fila and fila["categoria_jp"] is None:
            memoria.corregir(cx, mid, fila["categoria"], "confirmado por JP")


def corregir(cx, message_id, categoria_nueva, explicacion=""):
    """JP dijo otra cosa. No pisa `categoria`: la comparación entre lo
    que dijo el sistema y lo que dijo JP es todo el producto de esta
    corrida."""
    memoria.corregir(cx, message_id, categoria_nueva,
                     explicacion or "corregido por JP en la corrida")


# ---------------------------------------------------------------- fase 3

def casos_medibles(cx):
    """Las filas sobre las que JP se pronunció, en el orden en que se
    anotaron."""
    filas = cx.execute(
        "SELECT * FROM correos WHERE categoria_jp IS NOT NULL"
        " ORDER BY actualizado, message_id").fetchall()
    return [dict(f) for f in filas]


def armar_json(cx, modelo, fecha_utc=None):
    """El resultado en el mismo formato que los simulacro-*.json, para
    que `revisar_reglas.py` lo mida igual que los 133 casos guardados.

    Sólo entra lo que JP revisó. Contar como acierto un correo que nadie
    miró -dando por bueno lo que dijo el sistema porque lo dijo el
    sistema- sería medir contra sí mismo: el número daría lindo y no
    significaría nada.
    """
    filas = casos_medibles(cx)
    casos = []
    for f in filas:
        casos.append({
            "message_id": f["message_id"],
            "uid": f["uid"],
            "de": f["de"], "para": f["para"], "cc": f["cc"],
            "asunto": f["asunto"], "fecha": f["fecha"],
            "cuerpo": f["cuerpo"],
            "adjuntos": json.loads(f["adjuntos"] or "[]"),
            "correcto": f["categoria_jp"],
            "prediccion": {"categoria": f["categoria"],
                           "motivo": f["motivo"]},
            "explicacion": f["explicacion"],
        })
    aciertos = sum(1 for c in casos
                   if c["prediccion"]["categoria"] == c["correcto"])
    total_en_base = cx.execute("SELECT COUNT(*) n FROM correos").fetchone()["n"]
    return {
        "fecha_utc": fecha_utc or datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "modelo": modelo,
        "aciertos": aciertos,
        "total": len(casos),
        # False mientras JP no haya revisado todo lo clasificado: un
        # parcial es útil, pero tiene que decir que es parcial.
        "completo": len(casos) == total_en_base,
        "casos": casos,
    }



def pendientes_de_revisar(cx):
    """Lo clasificado sobre lo que JP todavía no se pronunció.

    Es lo que hace que la fase 2 se retome: JP mira tres tandas, se va a
    entrenar, y cuando vuelve arranca donde dejó en vez de desde el
    principio. Sin esto, una revisión a medias no sirve de nada -- que
    es exactamente la situación en la que JP va a estar la mayoría de
    las veces, porque son 141.
    """
    filas = cx.execute(
        "SELECT message_id, de, asunto, categoria, motivo FROM correos"
        " WHERE categoria_jp IS NULL ORDER BY actualizado, message_id"
    ).fetchall()
    return [dict(f) for f in filas]

# ---------------------------------------------------------------- fase 2

#: Cuántas Revisiones se armaron en este proceso. Junto con el PID le da
#: a cada corrida un token propio (ver Revision._sesion): el contador
#: distingue dos revisiones del mismo proceso, el PID distingue dos
#: procesos. Sin las dos mitades, la tanda 1 de toda corrida se llamaría
#: igual y sus botones serían intercambiables.
_CONTADOR = itertools.count(1)


class Revision:
    """La conversación por tandas: doce correos por mensaje, dos botones.

    Manda UNA tanda y espera. La siguiente sale recién cuando JP cerró
    la anterior. Eso no es una comodidad de implementación: mandarlas
    todas juntas sería la misma andanada de antes con menos mensajes, y
    lo que hizo falta arreglar es que JP pueda dejar la revisión a la
    mitad, irse a entrenar, y volver sin nada acumulado en la pantalla.

    `enviar(texto, teclado) -> bool` entra por parámetro para poder
    probar la conversación entera sin Telegram; por default va de
    verdad.
    """

    def __init__(self, cx, lotes, enviar=None):
        self.cx = cx
        self.lotes = list(lotes)
        self.i = 0
        self.tanda = None
        #: Los message_id de la tanda actual, EN ORDEN y TODOS -- aunque
        #: el mensaje se haya recortado por espacio. Es lo que hace que
        #: el número N seleccione siempre el correo que JP vio numerado
        #: como N. Mismo criterio que `self.abiertos` en secretaria.py.
        self.abiertos = []
        self.esperando = None
        self.termino = False
        self._sesion = f"{os.getpid() % 1000:03d}{next(_CONTADOR) % 100:02d}"
        self._enviar = enviar or self._por_telegram

    # -- envío

    def _por_telegram(self, texto, teclado=None):
        params = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": texto,
                  "parse_mode": "HTML"}
        if teclado:
            params["reply_markup"] = json.dumps(teclado)
        return bot.tg("sendMessage", **params)

    # -- el hilo de la conversación

    def arrancar(self):
        self._mandar_actual()

    def _mandar_actual(self):
        if self.i >= len(self.lotes):
            self.termino = True
            self._enviar("🏁 Eso es todo. Ya está toda la corrida revisada.")
            return
        lote = self.lotes[self.i]
        # Un token distinto por tanda Y por corrida. Lo segundo se
        # aprendió midiendo: con el token sacado sólo del índice
        # (f"{i+1:04d}"), la tanda 1 de toda corrida se llamaba "0001",
        # y JP cerró la tanda de una corrida tocando el botón del
        # mensaje de la anterior. Doce correos dados por revisados sin
        # que los viera, y contados como aciertos en el informe.
        self.tanda = f"{self._sesion}{self.i + 1:02d}"
        self.abiertos = [c["message_id"] for c in lote]
        self.esperando = None
        texto, teclado = armar_tanda(lote, self.i + 1, len(self.lotes),
                                     self.tanda)
        self._enviar(texto, teclado)

    def atender(self, update):
        """El único punto de entrada: un update de Telegram."""
        if "callback_query" in update:
            return self._boton(update["callback_query"])
        texto = (update.get("message") or {}).get("text")
        if texto is not None:
            return self._contesto(texto)

    def _boton(self, cq):
        partes = (cq.get("data") or "").split("|")
        if len(partes) != 3:
            return
        accion, referencia, valor = partes
        tanda, _, idx = referencia.partition("-")
        if tanda != self.tanda:
            # Botón de una tanda que ya se cerró: no cierra nada ni
            # corrige nada. Callado a propósito -- contestarle a un
            # mensaje viejo confunde más de lo que aclara.
            return
        if accion == "b":
            self._cerrar()
        elif accion == "g":
            self._pedir_numero()
        elif accion == "c":
            self._elegir_categoria(idx, valor)

    def _cerrar(self):
        confirmar(self.cx, self.abiertos)
        self.i += 1
        self._mandar_actual()

    def _pedir_numero(self):
        self.esperando = "numero"
        self._enviar("✏️ ¿Qué <b>número</b> está mal? Mandámelo y te "
                     "muestro ese correo.")

    def _contesto(self, texto):
        if self.esperando != "numero":
            return
        try:
            n = int(texto.strip())
        except ValueError:
            self._enviar("Necesito el <b>número</b> del correo, sólo el "
                         "número. O tocá «Está bien» arriba si ya está.")
            return
        if not 1 <= n <= len(self.abiertos):
            self._enviar(f"No tengo un {n} en esta tanda: van del 1 al "
                         f"{len(self.abiertos)}.")
            return
        self._mostrar(n)

    def _mostrar(self, n):
        fila = memoria.obtener(self.cx, self.abiertos[n - 1])
        if not fila:
            self._enviar(f"El {n} no lo encuentro en la base.")
            return
        texto = (f"<b>{n}.</b> <i>{html.escape(fila['fecha'] or '')}</i>\n"
                 f"<b>De:</b> {html.escape((fila['de'] or '')[:90])}\n"
                 f"<b>Asunto:</b> {html.escape((fila['asunto'] or '')[:120])}\n"
                 f"Le puse <b>{html.escape(fila['categoria'] or '?')}</b> "
                 f"porque {html.escape(fila['motivo'] or '—')}\n\n"
                 f"<pre>{html.escape((fila['cuerpo'] or '')[:600])}</pre>\n"
                 f"¿Qué era en realidad?")
        self._enviar(texto, bot.teclado(n, self.tanda))

    def _elegir_categoria(self, idx, categoria):
        try:
            n = int(idx)
        except ValueError:
            return
        if not 1 <= n <= len(self.abiertos):
            return
        corregir(self.cx, self.abiertos[n - 1], categoria)
        # No se cierra la tanda: puede haber más de uno mal, y obligar a
        # tocar «Corregir» de nuevo por cada uno es un mensaje de más
        # cada vez. Se queda esperando otro número.
        self.esperando = "numero"
        self._enviar(f"✔️ El {n} queda como <b>{html.escape(categoria)}</b>."
                     f" Mandame otro número si hay más, o tocá «Está bien» "
                     f"arriba para cerrar la tanda.")


def informe(d):
    """El informe en criollo de lo que dio la corrida.

    No alcanza con "acertó 118 de 141": de un porcentaje no sale ninguna
    regla. Lo que sirve es en QUÉ se confunde -- confundir RUIDO con
    TUYO es el error caro, confundir ENZO con NATALIA es barato -- y con
    QUIÉN, porque un remitente que aparece tres veces mal es una regla
    de reglas.md esperando a que la escriban.
    """
    casos = d.get("casos") or []
    if not casos:
        return "Sin casos revisados: no hay nada que medir todavía."

    aciertos, total = d["aciertos"], d["total"]
    porcentaje = 100 * aciertos / total
    lineas = [f"Acertó {aciertos} de {total} ({porcentaje:.0f}%).", ""]

    errados = [c for c in casos
               if c["prediccion"]["categoria"] != c["correcto"]]
    if not errados:
        lineas.append("No se equivocó en ninguno de los revisados.")
        return "\n".join(lineas)

    confusiones = Counter((c["prediccion"]["categoria"], c["correcto"])
                          for c in errados)
    lineas.append(f"Se equivocó en {len(errados)}:")
    for (dijo, era), n in confusiones.most_common():
        lineas.append(f"  {n:>3}×  dijo {dijo} → era {era}")

    remitentes = Counter(c["de"] for c in errados)
    repetidos = [(r, n) for r, n in remitentes.most_common() if n > 1]
    if repetidos:
        lineas += ["", "Remitentes que concentran errores "
                       "(candidatos a regla nueva en reglas.md):"]
        for remitente, n in repetidos:
            lineas.append(f"  {n:>3}×  {remitente}")
    return "\n".join(lineas)


# ---------------------------------------------------------------- el programa

def escuchar(revision, offset=None):
    """Long-polling hasta que la revisión termina.

    Traduce los audios a texto antes de pasárselos a la Revisión: JP
    contesta desde el celular y muchas veces por audio, y para la
    Revisión un "3" dictado y un "3" escrito son lo mismo. Que sean lo
    mismo es justamente lo que la mantiene probable sin Telegram.
    """
    if offset is None:
        # Descartar lo viejo: updates de sesiones anteriores no tienen
        # nada que ver con las tandas de ahora, y contestarían botones
        # que ya no existen.
        d = bot.tg("getUpdates", offset=-1, timeout=0)
        pendientes = d.get("result", [])
        offset = (pendientes[-1]["update_id"] + 1) if pendientes else None

    while not revision.termino:
        d = bot.tg("getUpdates", offset=offset, timeout=60,
                   allowed_updates=["callback_query", "message"])
        for u in d.get("result", []):
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if cq:
                # Siempre, y antes de nada: si no, Telegram deja el
                # botón girando en el teléfono de JP.
                bot.tg_suave("answerCallbackQuery",
                             callback_query_id=cq["id"])
            voz = (u.get("message") or {}).get("voice")
            if voz:
                dicho = bot.transcribir(voz["file_id"])
                if dicho:
                    u = {"message": {"text": dicho}}
            revision.atender(u)


def _argumentos(argv):
    opciones = {"desde": date(2026, 8, 11), "base": None, "salida": None,
                "tamano": TAMANO_TANDA, "clasificar": True, "revisar": True}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--desde":
            i += 1
            opciones["desde"] = datetime.datetime.strptime(
                argv[i], "%Y-%m-%d").date()
        elif a == "--base":
            i += 1
            opciones["base"] = argv[i]
        elif a == "--salida":
            i += 1
            opciones["salida"] = argv[i]
        elif a == "--tamano":
            i += 1
            opciones["tamano"] = int(argv[i])
        elif a == "--solo-clasificar":
            opciones["revisar"] = False
        elif a == "--solo-revisar":
            opciones["clasificar"] = False
        else:
            raise SystemExit(f"No conozco la opción {a!r}.\n\n{__doc__}")
        i += 1
    marca = opciones["desde"].strftime("%Y%m%d")
    opciones["base"] = opciones["base"] or f"datos/corrida-{marca}.db"
    opciones["salida"] = opciones["salida"] or f"datos/corrida-{marca}.json"
    return opciones


def main(argv=None):
    o = _argumentos(sys.argv[1:] if argv is None else argv)
    cx = memoria.abrir(o["base"])
    modelo = clasificador._config()["catalogo"].get(
        clasificador._config()["preferencia"][0], {}).get("modelo", "?")

    if o["clasificar"]:
        print(f"Trayendo lo que entró desde {o['desde']} …")
        entrantes = correo.traer_nuevos(o["desde"])
        print(f"  {len(entrantes)} correos. Clasificando (esto tarda: "
              f"son dos o tres pasadas de red por correo).\n")

        def avance(n, total, c, categoria):
            print(f"  [{n:>3}/{total}] {categoria:<9} "
                  f"{(c.get('de') or '')[:38]:<38} "
                  f"{(c.get('asunto') or '')[:44]}")

        clasificar_todo(cx, entrantes, clasificador.prompt_sistema(), avance)
        print("\nClasificación terminada.")

    if o["revisar"]:
        pendientes = ordenar_para_revisar(pendientes_de_revisar(cx))
        lotes = partir_en_tandas(pendientes, o["tamano"])
        print(f"\nQuedan {len(pendientes)} sin revisar → {len(lotes)} tandas "
              f"por Telegram. Esperando a JP (Ctrl-C para dejarlo para "
              f"después; lo revisado queda guardado).")
        revision = Revision(cx, lotes)
        revision.arrancar()
        try:
            escuchar(revision)
        except KeyboardInterrupt:
            print("\nCortado a mano. Lo que JP ya dictaminó quedó en "
                  f"{o['base']}; se retoma con --solo-revisar.")

    d = armar_json(cx, modelo)
    with open(o["salida"], "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"\nResultado en {o['salida']} "
          f"({'completo' if d['completo'] else 'parcial'}).\n")
    print(informe(d))
    print(f"\nPara medir una regla nueva contra esto:"
          f"\n  python3 revisar_reglas.py {o['salida']}")


if __name__ == "__main__":
    main()
