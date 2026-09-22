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


def _linea(n, c, espiar=0):
    """Una entrada de la lista numerada.

    Lleva cuatro cosas y las cuatro hicieron falta: quién, sobre qué,
    CUÁNDO -con la antigüedad al lado, que sin eso un correo de hoy y
    uno de la semana pasada se leen igual-, qué le puso el clasificador
    y por qué, y un pedazo del cuerpo para poder entenderlo sin ir a
    abrir la casilla.

    `espiar` es cuántos caracteres de cuerpo entran; lo calcula
    armar_tanda repartiendo lo que sobra del mensaje entre los correos
    del lote, así el recorte cae siempre acá y nunca sobre la lista
    -un correo que no se lista es un número que JP no puede corregir-.
    """
    partes = [f"  {n}. {html.escape((c.get('de') or '')[:32])} — "
              f"{html.escape((c.get('asunto') or '')[:42])}",
              f"     <i>{html.escape(correo.fecha_legible(c.get('fecha') or ''))}</i>",
              f"     → <b>{html.escape(c.get('categoria') or '?')}</b> · "
              f"<i>{html.escape((c.get('motivo') or '')[:64])}</i>"]
    cuerpo = " ".join((c.get("cuerpo") or "").split())
    if espiar > 0 and cuerpo:
        recorte = cuerpo[:espiar]
        if len(cuerpo) > espiar:
            recorte += "…"
        partes.append(f"     «{html.escape(recorte)}»")
    return "\n".join(partes)


def armar_tanda(lote, n, total, tanda):
    """El texto y los botones de una tanda. Devuelve (texto, teclado).

    Los dos botones llevan el token de la tanda porque toda tanda numera
    desde 1: sin el token, "Está bien" de una tanda vieja cerraría la
    nueva. Es el mismo cuidado que `bot.es_de_esta_tanda` ya tenía.
    """
    cabecera = (f"📋 <b>Tanda {n} de {total}</b> — {len(lote)} correos.\n"
                f"Si alguno está mal clasificado, tocá «Corregir».")

    # Cuánto cuerpo se puede espiar de cada uno: lo que sobre del
    # mensaje, repartido en partes iguales. Se calcula con las líneas
    # SIN cuerpo, que son las que no se pueden recortar, así el ajuste
    # cae siempre sobre lo espiado y la lista queda entera.
    sin_cuerpo = [_linea(i, c) for i, c in enumerate(lote, 1)]
    fijo = len(cabecera) + sum(len(x) + 1 for x in sin_cuerpo)
    espiar = max(0, min(220, (LIMITE_TELEGRAM - fijo - 60) // max(1, len(lote)) - 12))

    lineas = [_linea(i, c, espiar) for i, c in enumerate(lote, 1)]
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


#: Lo que se anota mientras JP todavía no dijo POR QUÉ corrigió. No es
#: decoración: es lo que permite volver a preguntárselo al final en vez
#: de perder el motivo, que es la parte que se puede escribir en
#: reglas.md. La etiqueta sola no genera ninguna regla.
SIN_MOTIVO = "(todavía sin motivo)"


def corregir(cx, message_id, categoria_nueva, explicacion=""):
    """JP dijo otra cosa. No pisa `categoria`: la comparación entre lo
    que dijo el sistema y lo que dijo JP es todo el producto de esta
    corrida."""
    memoria.corregir(cx, message_id, categoria_nueva,
                     explicacion or SIN_MOTIVO)


def anotar_motivo(cx, message_id, texto):
    """Le pone el porqué a una corrección ya hecha, sin tocar la
    categoría que JP eligió."""
    fila = memoria.obtener(cx, message_id)
    if fila:
        memoria.corregir(cx, message_id, fila["categoria_jp"], texto)


def correcciones_sin_motivo(cx):
    """Las correcciones que todavía no tienen el porqué.

    Sólo correcciones: confirmar no necesita explicación -- la
    explicación es que el sistema acertó.
    """
    filas = cx.execute(
        "SELECT message_id, de, asunto, categoria, categoria_jp FROM correos"
        " WHERE categoria_jp IS NOT NULL AND categoria_jp != categoria"
        "   AND (explicacion IS NULL OR explicacion = ?)"
        " ORDER BY actualizado, message_id", (SIN_MOTIVO,)).fetchall()
    return [dict(f) for f in filas]


# --------------------------------------------------- fase 2 bis: uno a uno

#: Las categorías donde una equivocación le cuesta algo a JP. El ruido y
#: lo que el equipo ya maneja no necesitan que las mire de a una: son el
#: grueso y son lo que menos enseña.
ACCIONABLES = ("TUYO", "DUDA", "ENZO", "NATALIA", "ERROR")


def accionables(cx):
    """Los correos que merecen una mirada de a uno.

    Cuenta lo que dijo CUALQUIERA de los dos: si el sistema dijo TUYO y
    JP dijo RUIDO, se equivocó para el lado caro y hay que entender por
    qué; si el sistema dijo DELEGADO y JP dijo TUYO, también. Filtrar
    sólo por la categoría del sistema perdería justo los casos que más
    enseñan.
    """
    marcas = ",".join("?" * len(ACCIONABLES))
    filas = cx.execute(
        f"SELECT * FROM correos WHERE categoria IN ({marcas})"
        f"   OR categoria_jp IN ({marcas})"
        f" ORDER BY actualizado, message_id",
        ACCIONABLES + ACCIONABLES).fetchall()
    return [dict(f) for f in filas]


def armar_uno(fila, n, total, sesion=""):
    """El correo ENTERO, con todo lo que hace falta para juzgarlo.

    Nace de lo que JP marcó en la corrida real del 2026-08-26: la línea
    de la tanda no tenía fecha ni hora -- y en un hilo de ida y vuelta
    sin eso no se sabe cuál mensaje es cuál -- y el cuerpo se recortaba
    a 600 caracteres, así que para entender de qué se trataba había que
    ir a la casilla. Le pasó con un correo de aspecto judicial que
    resultó ser trucho: no quería marcarlo como error del sistema sin
    estar seguro de que lo fuera.

    Acá el correo se muestra ANTES de pedir nada, y «Está bien así» es
    un botón más: mirar no cuesta una decisión.
    """
    cabecera = [
        f"📬 <b>{n} de {total}</b>",
        f"<b>Fecha:</b> {html.escape(fila.get('fecha') or '—')}",
        f"<b>De:</b> {html.escape((fila.get('de') or '')[:110])}",
        f"<b>Para:</b> {html.escape((fila.get('para') or '—')[:110])}",
    ]
    if fila.get("cc"):
        cabecera.append(f"<b>CC:</b> {html.escape(fila['cc'][:110])}")
    cabecera.append(f"<b>Asunto:</b> {html.escape((fila.get('asunto') or '')[:160])}")
    adjuntos = fila.get("adjuntos")
    if isinstance(adjuntos, str):
        adjuntos = json.loads(adjuntos or "[]")
    if adjuntos:
        # correo.adjuntos() devuelve diccionarios (nombre, tipo, kb), no
        # textos: hay una función que los formatea y es la que usa el
        # resto del sistema. Juntarlos a mano con ', '.join() reventaba
        # con TypeError en el primer correo que trajera un adjunto.
        cabecera.append("<b>Adjuntos:</b>\n"
                        + html.escape(correo.adjuntos_legibles(adjuntos)[:300]))

    veredicto = [f"\nYo dije <b>{html.escape(fila.get('categoria') or '?')}</b> "
                 f"porque <i>{html.escape((fila.get('motivo') or '—')[:200])}</i>"]
    if fila.get("categoria_jp"):
        veredicto.append(f"En las tandas vos dijiste "
                         f"<b>{html.escape(fila['categoria_jp'])}</b>.")
    veredicto.append("¿Está bien? El correo, abajo:")

    fijo = "\n".join(cabecera + veredicto)
    # Lo que sobra después de lo que nunca se recorta va al cuerpo, que
    # es la parte que JP pidió ver entera. El margen cubre las etiquetas
    # <pre> y el aviso de corte.
    presupuesto = LIMITE_TELEGRAM - len(fijo) - 120
    cuerpo = (fila.get("cuerpo") or "").strip()
    escapado = html.escape(cuerpo)
    if len(escapado) > presupuesto:
        escapado = escapado[:presupuesto] + "\n…(sigue en la casilla)"
    # El veredicto va ARRIBA, entre la cabecera y el cuerpo. En el
    # teléfono, con un correo de 4.000 caracteres, tenerlo al final
    # obliga a scrollear el mail entero para saber qué se pregunta.
    texto = "\n".join(cabecera + veredicto + [f"\n<pre>{escapado}</pre>"])

    filas_teclado = []
    fila_actual = []
    for cat in ("RUIDO", "DELEGADO", "ENZO", "NATALIA", "TUYO", "DUDA"):
        fila_actual.append({"text": bot.CATEGORIAS[cat],
                            "callback_data": f"c|{sesion}-{n}|{cat}"})
        if len(fila_actual) == 2:
            filas_teclado.append(fila_actual)
            fila_actual = []
    if fila_actual:
        filas_teclado.append(fila_actual)
    filas_teclado.append([{"text": "✅ Está bien así",
                           "callback_data": f"b|{sesion}-{n}|ok"}])
    return texto, {"inline_keyboard": filas_teclado}

class UnoAUno:
    """El correo completo, uno por mensaje, sobre los que valen.

    Es la contracara de Revision, no su reemplazo: las tandas sirven
    para el grueso -- 57 de ruido y 24 del equipo se confirman de a doce
    sin perder nada --, y esto sirve para los que necesitan criterio.
    JP lo pidió a mitad de la corrida real con el argumento que manda:
    «lo más rico es que la secretaria se retroalimente de la respuesta,
    no la etiqueta».

    Por eso acá el porqué se pregunta SIEMPRE que JP toca una categoría,
    incluso si es la misma que puso el sistema: elegirla a mano es decir
    «está bien POR ESTO», y ese "esto" es lo que se escribe en
    reglas.md. «Está bien así» es el atajo para cuando no hay nada que
    agregar.
    """

    def __init__(self, cx, filas, enviar=None):
        self.cx = cx
        self.filas = list(filas)
        self.i = 0
        self.esperando = None
        self.motivo_de = None
        self.termino = False
        self.sesion = f"{os.getpid() % 1000:03d}{next(_CONTADOR) % 100:02d}"
        self._enviar = enviar or _por_telegram

    def arrancar(self):
        self._mandar_actual()

    def _mandar_actual(self):
        if self.i >= len(self.filas):
            self.termino = True
            self._enviar("🏁 Listo, terminamos con los que importaban.")
            return
        self.esperando = None
        self.motivo_de = None
        texto, teclado = armar_uno(self.filas[self.i], self.i + 1,
                                   len(self.filas), self.sesion)
        self._enviar(texto, teclado)

    def atender(self, update):
        if "callback_query" in update:
            return self._boton(update["callback_query"])
        texto = (update.get("message") or {}).get("text")
        if texto is not None and self.esperando == "motivo":
            return self._guardar_motivo(texto)

    def _boton(self, cq):
        partes = (cq.get("data") or "").split("|")
        if len(partes) != 3:
            return
        accion, referencia, valor = partes
        # El botón tiene que ser de ESTA sesión y de ESTE correo. Lo
        # segundo importa tanto como lo primero: JP scrollea, y el
        # mensaje de arriba sigue teniendo sus botones intactos.
        if referencia != f"{self.sesion}-{self.i + 1}":
            return
        if accion == "b":
            fila = self.filas[self.i]
            confirmar(self.cx, [fila["message_id"]])
            self.i += 1
            self._mandar_actual()
        elif accion == "c":
            fila = self.filas[self.i]
            corregir(self.cx, fila["message_id"], valor)
            self.esperando = "motivo"
            self.motivo_de = fila["message_id"]
            self._enviar(f"Queda como <b>{html.escape(valor)}</b>.\n"
                         f"<b>¿Por qué?</b> Audio o texto -- de esto salen "
                         f"las reglas. «-» si no vale la pena.")

    def _guardar_motivo(self, texto):
        mid, self.motivo_de = self.motivo_de, None
        dicho = (texto or "").strip()
        if mid and dicho and dicho not in ("-", "--", "."):
            anotar_motivo(self.cx, mid, dicho)
        self.i += 1
        self._mandar_actual()

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
        "SELECT message_id, de, asunto, fecha, cuerpo, categoria, motivo"
        " FROM correos WHERE categoria_jp IS NULL"
        " ORDER BY actualizado, message_id"
    ).fetchall()
    return [dict(f) for f in filas]

# ---------------------------------------------------------------- fase 2

#: Cuántas Revisiones se armaron en este proceso. Junto con el PID le da
#: a cada corrida un token propio (ver Revision._sesion): el contador
#: distingue dos revisiones del mismo proceso, el PID distingue dos
#: procesos. Sin las dos mitades, la tanda 1 de toda corrida se llamaría
#: igual y sus botones serían intercambiables.
_CONTADOR = itertools.count(1)


def _por_telegram(texto, teclado=None):
    """El envío de verdad. Lo comparten las dos conversaciones -- las
    tandas y el uno a uno mandan igual."""
    params = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": texto,
              "parse_mode": "HTML"}
    if teclado:
        params["reply_markup"] = json.dumps(teclado)
    return bot.tg("sendMessage", **params)


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
        #: A qué correo le está faltando el porqué, si es que a alguno.
        self.motivo_de = None
        self.termino = False
        self._sesion = f"{os.getpid() % 1000:03d}{next(_CONTADOR) % 100:02d}"
        self._enviar = enviar or _por_telegram

    # -- el hilo de la conversación

    def arrancar(self):
        self._mandar_actual()

    def _mandar_actual(self):
        if self.i >= len(self.lotes):
            self._repasar_motivos()
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
        self.motivo_de = None
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
        if self.esperando == "motivo":
            return self._guardar_motivo(texto)
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

    def _pedir_motivo(self, mid, encabezado=""):
        self.esperando = "motivo"
        self.motivo_de = mid
        self._enviar(f"{encabezado}\n<b>¿Por qué?</b> Contestame con un "
                     f"audio o escribiéndolo -- de eso salen las reglas "
                     f"nuevas. Si no vale la pena, mandá «-».")

    def _guardar_motivo(self, texto):
        mid, self.motivo_de = self.motivo_de, None
        dicho = (texto or "").strip()
        if mid and dicho and dicho not in ("-", "--", "."):
            anotar_motivo(self.cx, mid, dicho)
        if self.i >= len(self.lotes):
            # Estamos en el repaso del final: sigue con el próximo que
            # no tenga motivo, o termina.
            return self._repasar_motivos()
        self.esperando = "numero"
        self._enviar("Anotado. Mandame otro número si hay más para "
                     "corregir, o tocá «Está bien» arriba para cerrar "
                     "la tanda.")

    def _repasar_motivos(self):
        """Después de la última tanda: las correcciones que quedaron sin
        porqué se preguntan una por una.

        Cubre dos casos: las que JP salteó en el momento, y las que hizo
        antes de que la corrida supiera preguntar -- las seis de la
        corrida real del 2026-08-26, que se corrigieron cuando este
        circuito todavía no existía.
        """
        pendientes = correcciones_sin_motivo(self.cx)
        if not pendientes:
            self.termino = True
            self._enviar("🏁 Eso es todo. Ya está toda la corrida revisada.")
            return
        f = pendientes[0]
        quedan = (f" (quedan {len(pendientes)})" if len(pendientes) > 1 else "")
        self._pedir_motivo(
            f["message_id"],
            f"📝 Volvamos sobre una corrección{quedan}:\n"
            f"<b>De:</b> {html.escape((f['de'] or '')[:80])}\n"
            f"<b>Asunto:</b> {html.escape((f['asunto'] or '')[:110])}\n"
            f"Yo dije <b>{html.escape(f['categoria'] or '?')}</b> y vos "
            f"dijiste <b>{html.escape(f['categoria_jp'] or '?')}</b>.")

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
        mid = self.abiertos[n - 1]
        corregir(self.cx, mid, categoria)
        # Acá va la pregunta que importa. La etiqueta sola no se puede
        # escribir en reglas.md: "esto era TUYO" no es una regla. Lo que
        # sí lo es, es el porqué -- "cuando alguien del equipo contesta
        # derivándote a vos, es TUYO aunque haya contestado". Se
        # pregunta en el momento, con el correo fresco, no al final.
        self._pedir_motivo(mid, f"✔️ El {n} queda como "
                                f"<b>{html.escape(categoria)}</b>.")


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
    # Todas las claves se definen ACÁ, siempre. Una opción que agregue
    # una clave que las demás invocaciones no tienen revienta en main()
    # con un KeyError -- y revienta recién al arrancar el programa de
    # verdad, con JP esperando la tanda del otro lado. Ya pasó con
    # --uno-a-uno el 2026-08-28: dejó rota la corrida completa y
    # --solo-clasificar sin que nada lo avisara.
    opciones = {"desde": date(2026, 8, 11), "base": None, "salida": None,
                "tamano": TAMANO_TANDA, "clasificar": True, "revisar": True,
                "uno_a_uno": False}
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
        elif a == "--uno-a-uno":
            # Los accionables, el correo entero, uno por mensaje. Para el
            # grueso (ruido y equipo) están las tandas: son 81 y no
            # necesitan que JP los mire de a uno.
            opciones["clasificar"] = False
            opciones["uno_a_uno"] = True
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

    if o["uno_a_uno"]:
        filas = accionables(cx)
        print(f"\n{len(filas)} accionables, uno por mensaje, con el correo "
              f"entero. Esperando a JP (Ctrl-C para dejarlo para después).")
        conversacion = UnoAUno(cx, filas)
        conversacion.arrancar()
        try:
            escuchar(conversacion)
        except KeyboardInterrupt:
            print("\nCortado a mano. Lo dictaminado quedó guardado.")

    elif o["revisar"]:
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
