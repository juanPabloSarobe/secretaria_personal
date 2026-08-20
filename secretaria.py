#!/usr/bin/env python3
"""La secretaria: el proceso que corre solo y coordina a los demás módulos.

Dos hilos. El que escucha nunca hace nada pesado —recibe el botón o el
audio, lo anota y contesta— porque clasificar tarda hasta tres minutos con
nvidia y con un solo hilo el bot quedaría mudo todo ese rato.

Arranca EN SECO: clasifica y manda los resúmenes, pero no toca la casilla.
Se le saca el freno con SECRETARIA_EN_SECO=false, y de a un paso: primero
que archive ruido, que es reversible, y otro día que marque leído.
"""
import html
import os
import sys
import threading
import time
from datetime import datetime, timedelta

import bot, clasificador, correo, memoria, reglas

EN_SECO = os.environ.get("SECRETARIA_EN_SECO", "true").lower() != "false"
CADA = 180                    # cada cuánto mira la casilla, en segundos
POLL_HILOS = 1                 # cada cuánto arrancar() se fija que sigan vivos
HORA_INICIO, HORA_FIN = 8, 19

MOMENTOS = [("manana", 8, 30), ("tarde", 17, 0), ("ruido", 18, 0)]


def en_horario(momento):
    """De 8 a 19, días hábiles (lunes a viernes). Fuera de eso, lo que
    entra espera al resumen de las 8:30 siguiente.

    Acá no hay excepción para clientes importantes: esta función sólo mide
    el reloj de pared. Esa excepción —avisar al toque en vez de esperar al
    resumen— vive del lado de cómo se manda el aviso, no del horario en
    sí; la implementa la tarea 8."""
    return momento.weekday() < 5 and HORA_INICIO <= momento.hour < HORA_FIN


class Reloj:
    """Decide qué resúmenes corresponden, sin dormir ni mirar la hora sola.

    Recibe el ahora y el último momento en que se lo consultó, así se puede
    probar con fechas inventadas. Si la secretaria estuvo caída doce horas
    —o varios días—, devuelve todo lo que se perdió, en orden.
    """

    def momentos_pendientes(self, ahora, ultimo):
        # Recorre cada día entre `ultimo` y `ahora`, inclusive: calcular el
        # corte siempre sobre la fecha de `ahora` (como hacía la primera
        # versión) pierde para siempre los resúmenes de los días
        # intermedios si la caída duró más de una jornada. Como MOMENTOS
        # ya está en orden cronológico dentro del día, y los días se
        # recorren de más viejo a más nuevo, la salida queda ordenada sin
        # necesidad de un sort aparte.
        salida = []
        dia = ultimo.date()
        un_dia = timedelta(days=1)
        while dia <= ahora.date():
            for nombre, h, m in MOMENTOS:
                corte = datetime(dia.year, dia.month, dia.day, h, m)
                if ultimo < corte <= ahora:
                    salida.append(nombre)
            dia += un_dia
        return salida


def _registrar(prefijo, e):
    """Loguea una excepción de los ciclos sin poder tirar nada hacia
    afuera.

    Si esto fallara —el propio `print` con un pipe roto o el disco lleno,
    nada exótico en un proceso que corre meses— la excepción se escapaba
    del `except` que la llama y mataba el hilo en silencio: el otro hilo
    seguía vivo, así que ni `arrancar()` se enteraba. Por eso acá adentro
    no puede quedar nada sin atajar, y por eso además `arrancar()` vigila
    si algún hilo se murió en vez de confiar en que esto alcance."""
    try:
        print(f"[{prefijo}] {type(e).__name__}: {e}", flush=True)
    except Exception:
        pass


class Secretaria:
    def __init__(self, cx=None):
        self.cx = cx or memoria.abrir()
        self.reloj = Reloj()
        self.ultimo_reloj = datetime.now()
        self.offset = 0
        self.parada = threading.Event()
        self.pausada = False

    def ciclo_de_correo(self):
        while not self.parada.is_set():
            try:
                self.revisar_casilla()
                ahora = datetime.now()
                self._disparar_resumenes(ahora)
                memoria.latido(self.cx, ahora.isoformat(timespec="seconds"))
            except Exception as e:
                # Nada que pase acá adentro puede matar el proceso: si se
                # muere, JP no se entera, porque no recibir avisos se parece
                # mucho a un día tranquilo.
                _registrar("correo", e)
            self.parada.wait(CADA)

    def _disparar_resumenes(self, ahora):
        """Manda los resúmenes que correspondan entre `self.ultimo_reloj`
        y `ahora`, y actualiza el reloj interno.

        Separado de `ciclo_de_correo` para poder probarlo con un par de
        llamadas manuales seguidas, sin hilos ni tiempos reales de por
        medio. El reloj sólo avanza hacia adelante: si `ahora` viene antes
        que `self.ultimo_reloj` —el reloj del sistema saltó hacia atrás,
        típico de un NTP que corrige después de un suspend largo— no lo
        piso, porque si no la vuelta siguiente compararía contra un
        `ultimo_reloj` adelantado y podría volver a disparar un resumen
        que ya se mandó.
        """
        for momento in self.reloj.momentos_pendientes(
                ahora, self.ultimo_reloj):
            self.mandar_resumen(momento)
        if ahora > self.ultimo_reloj:
            self.ultimo_reloj = ahora

    def ciclo_de_escucha(self):
        while not self.parada.is_set():
            try:
                d = bot.tg("getUpdates", offset=self.offset, timeout=50)
                for u in d.get("result", []):
                    self.offset = u["update_id"] + 1
                    self.atender(u)
            except Exception as e:
                _registrar("escucha", e)
                time.sleep(5)

    def revisar_casilla(self):
        """Trae lo nuevo, lo clasifica, archiva el ruido y avisa lo de JP.

        La regla que manda: si ningún motor respondió, el correo se le
        muestra a JP con los botones de siempre. El silencio no es una
        categoría, así que nunca se archiva algo que no se pudo clasificar.
        """
        from datetime import date, timedelta
        entrantes = correo.traer_nuevos(date.today() - timedelta(days=1))
        sistema = clasificador.prompt_sistema()
        direcciones, dominios = reglas.remitentes_ruido()
        nuevos = 0
        for c in entrantes:
            if memoria.situacion(self.cx, correo.identidad(c)) is not None:
                # Ya procesado: si el proceso se cayó y volvió, no hay que
                # avisarle a JP dos veces por el mismo correo.
                continue
            c["message_id"] = correo.identidad(c)
            nuevos += 1
            try:
                pred = clasificador.clasificar(sistema, c)
            except Exception as e:
                # Ningún motor respondió. Se le muestra a JP igual: el
                # silencio no es una categoría.
                memoria.anotar(self.cx, c, "ERROR", f"{type(e).__name__}")
                self.avisar_sin_clasificar(c)
                continue
            memoria.anotar(self.cx, c, pred["categoria"], pred.get("motivo", ""))
            if (pred["categoria"] == "RUIDO" and pred.get("unanime")
                    and not reglas.protegido(c)):
                if not EN_SECO and correo.mover_a(c["message_id"], "INBOX.Ruido"):
                    memoria.cambiar(self.cx, c["message_id"], "archivado")
            elif pred["categoria"] in ("TUYO", "ENZO", "NATALIA"):
                if not c.get("ya_leido"):
                    self.avisar_en_el_momento(c, pred)
        return nuevos

    def avisar_en_el_momento(self, c, pred):
        """El único aviso que interrumpe a JP. Son unos 4 por día.

        Fuera de horario no interrumpe: el correo queda como clasificado y
        entra en el resumen de las 8:30. La excepción son los clientes
        importantes, que interrumpen siempre — la lista está vacía hoy, así
        que en la práctica todavía no hay excepción.
        """
        import secrets
        importante = reglas.protegido(c) and pred["categoria"] == "TUYO"
        if not en_horario(datetime.now()) and not importante:
            return
        tanda = secrets.token_hex(2)
        titulo = ("📌 Es tuyo" if pred["categoria"] == "TUYO"
                  else f"➡️ Para derivar a {pred['categoria'].title()}")
        texto = (f"<b>{titulo}</b> · <i>{html.escape(correo.fecha_legible(c['fecha']))}</i>\n"
                 f"<b>De:</b> {html.escape(c['de'][:90])}\n"
                 f"<b>CC:</b> {html.escape((c['cc'] or '(nadie)')[:90])}\n"
                 f"<b>Asunto:</b> {html.escape(c['asunto'][:120])}\n"
                 f"{html.escape(correo.adjuntos_legibles(c.get('adjuntos')))}\n\n"
                 f"<pre>{html.escape((c['cuerpo'] or '')[:600])}</pre>")
        self.enviar(texto, {"inline_keyboard": [[
            {"text": "Listo, lo vi", "callback_data": f"v|{tanda}-1|ok"},
            {"text": "No era mío", "callback_data": f"n|{tanda}-1|0"}]]})
        memoria.cambiar(self.cx, c["message_id"], "avisado")

    def avisar_sin_clasificar(self, c):
        """Ningún motor respondió. Se le muestra igual, con los botones de
        siempre: si el sistema no sabe, decide JP. Nunca se archiva."""
        import secrets
        tanda = secrets.token_hex(2)
        texto = (f"<b>⚠️ No pude clasificarlo</b>\n"
                 f"<b>De:</b> {html.escape(c['de'][:90])}\n"
                 f"<b>Asunto:</b> {html.escape(c['asunto'][:120])}\n\n"
                 f"<pre>{html.escape((c['cuerpo'] or '')[:600])}</pre>\n\n"
                 f"¿Qué correspondía?")
        self.enviar(texto, bot.teclado(1, tanda))

    def enviar(self, texto, teclado=None):
        """Manda a Telegram. Una falla acá no puede matar el proceso."""
        try:
            return bot.tg("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                          text=texto, parse_mode="HTML",
                          reply_markup=teclado) if teclado else \
                   bot.tg("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                          text=texto, parse_mode="HTML")
        except Exception as e:
            print(f"[telegram] no pude enviar: {type(e).__name__}: {e}", flush=True)
            return None

    def mandar_resumen(self, momento):
        raise NotImplementedError("tarea 9")

    def atender(self, update):
        raise NotImplementedError("tarea 11")

    def arrancar(self):
        hilos = [threading.Thread(target=self.ciclo_de_correo, daemon=True,
                                   name="correo"),
                 threading.Thread(target=self.ciclo_de_escucha, daemon=True,
                                   name="escucha")]
        for h in hilos:
            h.start()
        # Nada de join() a ciegas: los cuerpos de los ciclos ya no dejan
        # escapar excepciones (ver _registrar), pero si un hilo se muere
        # igual —por lo que sea— el otro sigue vivo y un join() sobre
        # ambos se queda esperando para siempre. El proceso no se cae, y
        # entonces launchd nunca lo reinicia: queda medio muerto y en
        # silencio, que es la falla que más preocupa. Por eso vigilamos, y
        # apenas se cae alguno terminamos ruidosamente con código
        # distinto de cero para que KeepAlive lo levante de nuevo.
        while not self.parada.is_set():
            muertos = [h for h in hilos if not h.is_alive()]
            if muertos:
                nombres = ", ".join(h.name for h in muertos)
                print(f"[arrancar] se murió el hilo {nombres}: fuerzo la"
                      " salida para que launchd reinicie el proceso",
                      flush=True)
                self.parada.set()
                sys.exit(1)
            self.parada.wait(POLL_HILOS)


if __name__ == "__main__":
    Secretaria().arrancar()
