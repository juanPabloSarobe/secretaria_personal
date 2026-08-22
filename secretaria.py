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
import random
import sys
import threading
import time
from datetime import datetime, timedelta

import bot, clasificador, correo, memoria, reglas

EN_SECO = os.environ.get("SECRETARIA_EN_SECO", "true").lower() != "false"
CADA = 180                    # cada cuánto mira la casilla, en segundos

# Cuántas veces se reintenta archivar un correo antes de rendirse y
# avisarle a JP. Cinco intentos, uno por vuelta del ciclo, son unos 15
# minutos: alcanza de sobra para que se resuelva sola una caída corta de
# red o un servidor IMAP que se reinicia -- las fallas que de verdad se
# arreglan solas duran segundos, no un cuarto de hora. Y tiene que ser
# MUCHO menos que la ventana de traer_nuevos (un día): pasada esa
# ventana el correo ya no vuelve a aparecer entre los entrantes y el
# reintento se apagaría solo, en silencio, que es exactamente la falla
# que no queremos. Con cinco, el correo siempre llega a un estado final
# -archivado o avisado- mucho antes de salirse de la ventana.
INTENTOS_MAXIMOS = 5
POLL_HILOS = 1                 # cada cuánto arrancar() se fija que sigan vivos
HORA_INICIO, HORA_FIN = 8, 19

MOMENTOS = [("manana", 8, 30), ("tarde", 17, 0), ("ruido", 18, 0)]

# Medido: 80 correos en un resumen dan 4.573 caracteres, 100 dan 5.684.
# Telegram corta el texto de un mensaje en 4.096 -Bot API, no negociable-
# y lo rechaza entero: no es un mensaje recortado, es CERO mensaje.
LIMITE_TELEGRAM = 4096

# Overhead reservado -título del bloque del equipo y el aviso de cuántos
# quedaron sin listar, ver armar_resumen()- para decidir si lo accionable
# solo ya entra en un mensaje sin tener que armar el resumen completo
# para medirlo. No depende del volumen de equipo: el título y el aviso
# tienen un tamaño acotado, aunque haya miles de correos del equipo.
MARGEN_COLAPSO_EQUIPO = 300

# Tope duro de mensajes de Telegram por tanda de resumen. Ronda 2: con
# 1000 correos y 70 accionables, partir todo en lotes parejos de 15
# mandaba 67 mensajes de una -la misma andanada que este número existe
# para evitar, justo en el momento -la vuelta después de una caída
# larga- en que es más probable que pase. Con 3 entran cómodos dos
# mensajes de detalle de lo accionable -que en la práctica son decenas
# de correos cada uno, ver _empacar_accionable- más un cierre con los
# conteos de lo que no entró. JP prefiere tres mensajes y saber cuánto
# hay, a que le lleguen cuarenta y no lea ninguno.
TOPE_MENSAJES = 3


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
    —o varios días—, devuelve todo lo que se perdió, en orden. Sábado y
    domingo no generan cortes -los MOMENTOS son de días hábiles, igual que
    en_horario()-, así que un fin de semana caído no cuenta como perdido.
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
            if dia.weekday() < 5:  # los MOMENTOS son de días hábiles,
                # igual que en_horario(): un sábado o domingo no
                # corresponde ningún corte, aunque caiga dentro del
                # rango. Sin esto, una caída de viernes a la tarde a
                # lunes a la mañana -un fin de semana largo, nada raro-
                # dispara los tres cortes de sábado y los tres de
                # domingo de una sola vez al volver.
                for nombre, h, m in MOMENTOS:
                    corte = datetime(dia.year, dia.month, dia.day, h, m)
                    if ultimo < corte <= ahora:
                        salida.append(nombre)
            dia += un_dia
        return salida


class SecretariaFrenada(Exception):
    """Rever no puede escribir en la casilla ahora mismo: el freno en
    seco está puesto, o JP pausó la secretaria.

    A diferencia de _archivar() -parte del ciclo automático, que
    simplemente no hace nada este ciclo porque el próximo reintenta-,
    rever_ruido() es una acción puntual que JP disparó a mano, ahora: no
    hacer nada y quedarse callado sería la misma falla silenciosa de
    siempre, sólo que esta vez JP cree que tocó Rever y no pasó nada. Por
    eso esto se levanta en vez de devolver algo o no hacer nada: quien
    maneja el botón (tarea 11) puede así decirle a JP "no puedo, está
    frenado" en vez de contestar como si hubiera funcionado.
    """


def _categoria_de_resumen(c):
    """Con qué categoría entra un correo al resumen: la que dijo JP si
    corrigió (categoria_jp), la que dijo el sistema si no.

    memoria.corregir() preserva `categoria` a propósito -es la que
    compara revisar_reglas.py para medir si el sistema aprende, y
    pisarla le arruinaría esa comparación- así que ese campo no sirve
    para decidir cómo mostrar el correo HOY. Sin esto, un correo que
    Rever reclasificó como NATALIA seguía contando como ruido en el
    próximo resumen: armar_resumen miraba `categoria` (RUIDO, lo que
    dijo el sistema la primera vez) donde hacía falta mirar
    `categoria_jp` (NATALIA, lo que corrigió JP). Ese conteo mal hecho es
    exactamente el trabajo que Rever existe para evitarle a JP.
    """
    return c.get("categoria_jp") or c["categoria"]


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
        # Qué message_id puede cerrar cada tanda de resumen con "Leí
        # todo": sin esto el botón no sabe cuáles marcar, y como los
        # botones de Telegram no vencen, dos resúmenes del mismo día no
        # pueden compartir token (ver armar_resumen).
        self.abiertos = {}

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
        pendientes = self.reloj.momentos_pendientes(ahora, self.ultimo_reloj)
        if pendientes:
            # Si se perdieron varios -una caída de días, o un fin de
            # semana largo que el filtro del reloj no alcanzó a
            # absorber del todo- mandarlos todos de a uno sería una
            # andanada apenas se reconecta. JP quiere saber qué pasó,
            # no reconstruir una cronología resumen por resumen: uno
            # solo alcanza, y como mandar_resumen arma su contenido a
            # partir de lo que sigue sin resumir en la base (no de qué
            # momento se lo llamó), ese único envío ya junta todo lo
            # pendiente, no sólo lo del último corte.
            #
            # El saludo evita "ruido" si hay otro pendiente: ese saludo
            # es "Lo que archivé hoy", y si el contenido trae de vuelta
            # correos de JP acumulados de varios días -típico después de
            # una caída larga- ese texto no describe lo que hay adentro.
            # Sólo se usa "ruido" cuando es el único momento pendiente.
            momento = next((m for m in reversed(pendientes) if m != "ruido"),
                           pendientes[-1])
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

    def puede_escribir(self):
        """Si la secretaria puede escribir en la casilla ahora mismo.

        Junta los dos frenos en un solo lugar: EN_SECO (variable de
        entorno, fija para todo el proceso) y self.pausada (que JP puede
        prender desde Telegram). Lo usan mover_a (_archivar) y
        devolver_a_bandeja (rever_ruido, tarea 10); falta conectar
        marcar_leido, que en algún momento de la tarea 11 va a necesitar
        el mismo chequeo en vez de repetir EN_SECO suelto en cada lugar
        —que es justo como estaba antes.
        """
        return not EN_SECO and not self.pausada

    def _archivar(self, c):
        """Intenta mover un correo a Ruido y deja la base consistente con
        lo que de verdad pasó en la casilla.

        Nunca marca "archivado" sin que mover_a haya confirmado el
        borrado del original -eso dejaría a la base diciendo algo que la
        casilla desmiente. Y una falla -de red, o correo.OperacionAMedias
        cuando el COPY se confirmó pero el borrado quedó a medias- no
        puede hacer que el correo se pierda de vista para siempre: queda
        en "pendiente_de_archivar", que revisar_casilla reintenta en el
        próximo ciclo sin volver a consultar al modelo, porque ya está
        clasificado. Antes, una excepción acá se escapaba hasta
        ciclo_de_correo, que la atajaba y seguía -pero memoria.anotar()
        ya había corrido, así que el chequeo de "¿ya lo vi?" lo saltaba
        para siempre en la vuelta siguiente: quedaba en la bandeja de JP,
        sin archivar y sin que nadie lo reintentara.
        """
        if not self.puede_escribir():
            return
        try:
            if correo.mover_a(c, "INBOX.Ruido"):
                memoria.cambiar(self.cx, c["message_id"], "archivado")
            else:
                # No estaba en INBOX -alguien ya lo movió o lo borró a
                # mano-: no hay nada que reintentar. Vuelve a
                # "clasificado" si venía de un reintento, para no quedar
                # dando vueltas en pendiente_de_archivar para siempre.
                memoria.cambiar(self.cx, c["message_id"], "clasificado")
        except correo.OperacionAMedias as e:
            # Esta excepción dice algo que ninguna otra dice: la copia YA
            # está en Ruido y lo único que faltó fue borrar el original.
            # Reintentar el mover_a entero desde acá vuelve a copiar y
            # deja un duplicado nuevo en Ruido por cada vuelta del ciclo
            # -con una falla sostenida del lado del borrado eso acumula
            # varias copias por hora-. Por eso queda en una situación
            # distinta, que reintenta sólo el borrado.
            self._anotar_falla(c, e, "pendiente_de_borrar")
        except Exception as e:
            # Todo lo demás -red caída, timeout, login rechazado, y
            # correo.CopiaRechazada cuando el servidor no dejó copiar-
            # comparte una cosa: en el destino no quedó ninguna copia, así
            # que reintentar la mudanza entera es seguro y es lo que
            # corresponde. CopiaRechazada entra acá a propósito y no en la
            # rama de arriba: mandarla a "pendiente_de_borrar" haría que
            # el reintento borre de INBOX un correo que nunca se copió a
            # ningún lado.
            self._anotar_falla(c, e, "pendiente_de_archivar")

    def _terminar_de_archivar(self, c):
        """Reintenta SÓLO el borrado del original, sin volver a copiar.

        Es el reintento del caso a medias: la copia ya está en Ruido.

        Lo que devuelve borrar_el_original() se mira, y hasta la ronda 7
        no se miraba: se marcaba "archivado" pasara lo que pasara. Si el
        borrado no encontraba el original -por ejemplo porque un SEARCH
        rechazado se leía como "ya no está"- el correo quedaba duplicado,
        en INBOX y en Ruido, con la base diciendo que estaba archivado:
        sin aviso, sin reintento y sin nadie que lo mirara. Ahora hay dos
        finales y los dos son ciertos: True, se borró; False, el original
        no estaba Y la copia sí -eso lo confirma borrar_el_original antes
        de devolverlo-. Cualquier otra cosa sale por excepción.
        """
        if not self.puede_escribir():
            return
        try:
            if correo.borrar_el_original(c, "INBOX.Ruido"):
                memoria.cambiar(self.cx, c["message_id"], "archivado")
            else:
                # El original ya no estaba y la copia está en Ruido: la
                # mudanza terminó igual, sólo que el EXPUNGE que la
                # terminó fue el de un intento anterior cuya respuesta se
                # perdió.
                memoria.cambiar(self.cx, c["message_id"], "archivado")
        except Exception as e:
            self._anotar_falla(c, e, "pendiente_de_borrar")

    def _anotar_falla(self, c, e, pendiente):
        """Cuenta el intento fallido y decide si se sigue reintentando.

        Sin tope, un archivado que falla siempre se reintenta cada CADA
        segundos para siempre y lo único que queda es una línea en un log
        que nadie mira: la falla silenciosa que el diseño dice que es la
        que más preocupa, porque no recibir avisos se parece demasiado a
        un día tranquilo. Al agotarse los intentos se deja de tocar la
        casilla y JP se entera por Telegram.

        El correo queda en su situación pendiente -la que dice QUÉ quedó
        hecho- hasta que el aviso salga de verdad. Recién ahí pasa a
        "no_se_pudo_archivar", que es un estado terminal: nadie lo vuelve
        a mirar. Ese orden es el arreglo: antes se marcaba terminal
        primero y se avisaba después, así que si Telegram estaba caído en
        ese momento el aviso se perdía para siempre y el correo quedaba
        sin archivar sin que nadie lo supiera. Medido: 30 vueltas del
        ciclo con Telegram caído, un solo intento de envío.
        """
        _registrar("archivar", e)
        memoria.anotar_falla(self.cx, c["message_id"],
                             f"{type(e).__name__}: {e}")
        intentos = memoria.sumar_intento(self.cx, c["message_id"])
        memoria.cambiar(self.cx, c["message_id"], pendiente)
        if intentos >= INTENTOS_MAXIMOS:
            self._avisar_la_falla(dict(c, situacion=pendiente,
                                       falla=f"{type(e).__name__}: {e}"))

    def _avisar_la_falla(self, fila):
        """Le cuenta a JP que un correo se quedó sin intentos.

        Sólo si el aviso salió de verdad el correo pasa a terminal. Si no
        salió, se queda donde está y la vuelta siguiente lo reintenta:
        reintentar un aviso que no llegó no duplica nada -a diferencia de
        reintentar un COPY- así que acá no hace falta tope. Lo que no
        puede pasar es que el mensaje que existe para que nada quede en
        silencio se pierda en silencio.
        """
        if self.avisar_que_no_se_pudo_archivar(
                fila, fila.get("falla") or "(no quedó registrado)",
                fila.get("situacion") == "pendiente_de_borrar"):
            memoria.cambiar(self.cx, fila["message_id"],
                            "no_se_pudo_archivar")

    def _atender_pendientes(self):
        """Retoma lo que quedó a medio hacer, leyéndolo de la BASE.

        El estado es la fuente de verdad de lo que falta hacer. Antes esto
        no existía: un correo en pendiente_de_archivar se reintentaba sólo
        porque volvía a aparecer en la ventana de un día de traer_nuevos,
        o sea por casualidad. Si el proceso se reiniciaba cruzando ese
        borde -y launchd lo reinicia por diseño- el correo quedaba
        abandonado para siempre: no llegaba al tope, no generaba aviso, no
        lo miraba nadie. Medido: 10 vueltas del ciclo con un pendiente
        fuera de la ventana, cero intentos de escritura.

        Primero los pendientes y después los entrantes, para que un correo
        que falla recién ahora no se reintente dos veces en la misma
        vuelta y queme dos intentos de una.

        Con el freno puesto no se hace nada de esto, tampoco el aviso.
        Los reintentos ya lo respetaban -_archivar y _terminar_de_archivar
        chequean puede_escribir()- pero el aviso se colaba: en seco o en
        pausa la secretaria igual le escribía a JP "no pude archivar un
        correo", que en seco es directamente falso (no lo intentó) y en
        pausa es actuar cuando se le pidió que no actúe. Nada se pierde:
        los pendientes siguen en la base y se retoman cuando el freno se
        saca.
        """
        if not self.puede_escribir():
            return
        for situacion, seguir in (("pendiente_de_borrar",
                                   self._terminar_de_archivar),
                                  ("pendiente_de_archivar", self._archivar)):
            for fila in memoria.pendientes(self.cx, situacion):
                if fila["intentos"] >= INTENTOS_MAXIMOS:
                    # Se agotaron los intentos: no se toca más la casilla,
                    # lo único que falta es que el aviso salga.
                    self._avisar_la_falla(fila)
                else:
                    seguir(fila)

    def revisar_casilla(self):
        """Trae lo nuevo, lo clasifica, archiva el ruido y avisa lo de JP.

        La regla que manda: si ningún motor respondió, o si respondió pero
        no pudo decidir (DUDA), el correo se le muestra a JP con los
        botones de siempre. El silencio no es una categoría, así que nunca
        se archiva algo que no se pudo clasificar con confianza.
        """
        from datetime import date, timedelta
        self._atender_pendientes()
        entrantes = correo.traer_nuevos(date.today() - timedelta(days=1))
        sistema = clasificador.prompt_sistema()
        direcciones, dominios = reglas.remitentes_ruido()
        frases = reglas.codigos_convenidos()
        nuevos = 0
        for c in entrantes:
            identidad = correo.identidad(c)
            situacion_previa = memoria.situacion(self.cx, identidad)

            if situacion_previa is not None:
                # Ya procesado: si el proceso se cayó y volvió, no hay que
                # avisarle a JP dos veces por el mismo correo. Los que
                # quedaron a medio archivar tampoco se retoman desde acá:
                # de eso se ocupa _atender_pendientes() leyendo la base,
                # que no depende de que el correo siga cayendo dentro de
                # la ventana de búsqueda.
                continue

            c["message_id"] = identidad
            nuevos += 1

            # Un código convenido gana sobre todo, igual que en
            # simulacro.py: JP acordó esa frase con su interlocutor para
            # marcar que el correo es suyo, así que ninguna heurística
            # -ni el ruido conocido, ni protegido(), ni el clasificador-
            # puede taparlo. Es la alternativa que JP eligió en vez de
            # tener que avisarle al bot cada vez que espera algo
            # importante: la señal deliberada de una persona gana sobre
            # cualquier estadística. Sin consultar al modelo: la frase
            # está o no está.
            codigo = reglas.tiene_codigo(c, frases)
            if codigo:
                pred = {"categoria": "TUYO", "confianza": "alta",
                        "unanime": True, "pasadas": 0,
                        "motivo": f"código convenido: «{codigo}»"}
                memoria.anotar(self.cx, c, pred["categoria"], pred["motivo"])
                if not c.get("ya_leido"):
                    self.avisar_en_el_momento(c, pred)
                continue

            # Atajo sin LLM, igual al de simulacro.py: si JP ya marcó este
            # remitente como ruido dos veces o más, y nunca de otra forma,
            # no hace falta gastar una llamada al único motor configurado
            # -con cuota limitada- para que le diga lo mismo. protegido()
            # manda sobre el atajo: sobre lo que JP ya se pronunció por
            # escrito, el sistema no decide solo (una vez el filtro
            # archivó una promoción de SiPago pese a que reglas.md decía
            # explícitamente que no era ruido). Y 1 de cada
            # MUESTREO_CONTROL igual se pregunta, para no dejar de medir
            # si el criterio se degrada.
            motivo_auto = (None if reglas.protegido(c)
                           else reglas.es_ruido_conocido(c, direcciones, dominios))
            if motivo_auto and random.randrange(reglas.MUESTREO_CONTROL):
                memoria.anotar(self.cx, c, "RUIDO", f"ruido conocido — {motivo_auto}")
                self._archivar(c)
                continue

            try:
                pred = clasificador.clasificar(sistema, c)
            except Exception as e:
                # Ningún motor respondió. Se le muestra a JP igual: el
                # silencio no es una categoría.
                memoria.anotar(self.cx, c, "ERROR", f"{type(e).__name__}")
                self.avisar_para_que_decida(c)
                continue
            memoria.anotar(self.cx, c, pred["categoria"], pred.get("motivo", ""))
            if pred["categoria"] == "DUDA":
                # Resultado NORMAL de clasificar() cuando las pasadas no
                # coinciden (11 de 46 casos medidos) -- no una excepción.
                # Es exactamente el caso para el que existe DUDA: se le
                # muestra a JP, nunca se archiva ni se pierde en silencio.
                self.avisar_para_que_decida(c)
            elif (pred["categoria"] == "RUIDO" and pred.get("unanime")
                    and not reglas.protegido(c)):
                self._archivar(c)
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

    def avisar_para_que_decida(self, c):
        """Ningún motor respondió, o respondió pero no logró decidir
        (DUDA). En los dos casos se le muestra igual, con los botones de
        siempre: si el sistema no sabe, decide JP. Nunca se archiva."""
        import secrets
        tanda = secrets.token_hex(2)
        texto = (f"<b>⚠️ No pude decidir</b>\n"
                 f"<b>De:</b> {html.escape(c['de'][:90])}\n"
                 f"<b>Asunto:</b> {html.escape(c['asunto'][:120])}\n\n"
                 f"<pre>{html.escape((c['cuerpo'] or '')[:600])}</pre>\n\n"
                 f"¿Qué correspondía?")
        self.enviar(texto, bot.teclado(1, tanda))

    def avisar_que_no_se_pudo_archivar(self, c, falla, duplicado=False):
        """Le avisa a JP que un correo se quedó sin intentos.

        Devuelve si el mensaje salió de verdad. Quien llama lo usa para
        decidir si el correo puede pasar a terminal: un aviso que no llegó
        no cierra nada.

        Va sin botones y sin chequear en_horario(): no es una decisión
        que JP tenga que tomar en el momento ni un correo más para
        triar, es una falla del sistema, y son rarísimas -si empiezan a
        ser frecuentes, enterarse rápido es justamente lo que hace
        falta. Dice de quién es el correo y de qué se trata para que JP
        pueda encontrarlo en la bandeja sin buscar en ningún log, y qué
        falló para que se entienda si es cosa del servidor o de la
        casilla.

        `duplicado` cambia la última línea, y no es un detalle: cuando la
        copia ya está hecha y lo que falló fue el borrado, el correo está
        en la bandeja Y en Ruido. Decirle "quedó en tu bandeja, sin
        archivar" -como decía siempre- lo manda a archivarlo de nuevo y a
        quedarse con dos copias.
        """
        donde = ("Quedó duplicado: sigue en tu bandeja y ya hay una copia"
                 " en INBOX.Ruido." if duplicado else
                 "Quedó en tu bandeja, sin archivar.")
        texto = (f"<b>🚨 No pude archivar un correo</b>\n"
                 f"<b>De:</b> {html.escape(str(c.get('de', ''))[:90])}\n"
                 f"<b>Asunto:</b> {html.escape(str(c.get('asunto', ''))[:120])}\n"
                 f"<b>Falló:</b> {html.escape(str(falla)[:250])}\n\n"
                 f"Lo intenté {INTENTOS_MAXIMOS} veces y dejo de "
                 f"intentar. {donde}")
        return bool(self.enviar(texto))

    def enviar(self, texto, teclado=None):
        """Manda a Telegram. Una falla acá no puede matar el proceso.

        Devuelve lo que contestó Telegram, o None si no salió. Ese None
        importa: hay un aviso -el de "no pude archivar"- que no se puede
        dar por entregado sin mirarlo, porque es justamente el que existe
        para que nada quede en silencio.
        """
        try:
            respuesta = (bot.tg("sendMessage",
                                chat_id=os.environ["TELEGRAM_CHAT_ID"],
                                text=texto, parse_mode="HTML",
                                reply_markup=teclado) if teclado else
                         bot.tg("sendMessage",
                                chat_id=os.environ["TELEGRAM_CHAT_ID"],
                                text=texto, parse_mode="HTML"))
        except Exception as e:
            print(f"[telegram] no pude enviar: {type(e).__name__}: {e}", flush=True)
            return None
        # Telegram puede contestar 200 con {"ok": false} -- un chat_id que
        # no existe, el bot bloqueado. Eso no es un envío.
        if isinstance(respuesta, dict) and not respuesta.get("ok", True):
            print(f"[telegram] rechazado: {str(respuesta)[:200]}", flush=True)
            return None
        return respuesta

    SALUDO = {"manana": "Buen día.", "tarde": "Cierre del día.",
              "ruido": "Lo que archivé hoy."}

    def _linea_accionable(self, c):
        """Una línea de "Tuyo" o "Para derivar", según la categoría.

        Factorizada de armar_resumen para poder reusarla en el camino de
        mucho volumen (_empacar_accionable) sin duplicar el formato: que
        las dos rutas dibujen el mismo correo distinto sería un bug
        esperando pasar."""
        categoria = _categoria_de_resumen(c)
        if categoria == "TUYO":
            return (f"  · {html.escape(c['de'][:34])} — "
                   f"{html.escape(c['asunto'][:48])}")
        return (f"  · {html.escape(c['asunto'][:44])} → "
               f"{categoria.title()}")

    def _lineas_mios_derivar(self, mios, derivar):
        """El bloque de lo accionable -título con conteo más una línea
        por correo-. Nunca se recorta: es poco y es lo que importa."""
        lineas = []
        if mios:
            lineas.append(f"\n📌 <b>Tuyo ({len(mios)})</b>")
            lineas += [self._linea_accionable(c) for c in mios]
        if derivar:
            lineas.append(f"\n➡️ <b>Para derivar ({len(derivar)})</b>")
            lineas += [self._linea_accionable(c) for c in derivar]
        return lineas

    def armar_resumen(self, correos, momento):
        """El texto y los botones de un resumen.

        Lo de JP y lo derivable van arriba y sin botón de cierre: quedan
        abiertos hasta que JP haga algo con ellos -eso lo maneja el
        manejo de botones de la tarea 11, no esta función-. Lo del
        equipo se cierra de un toque, con "Leí todo".

        Medido: 80 correos ya dan 4.573 caracteres, contra el límite de
        Telegram de 4.096 -y Telegram no recorta, rechaza el mensaje
        entero-. Lo de JP y lo para derivar nunca se recortan -es poco y
        es lo que importa-, pero el bloque del equipo es el grueso y
        crece con el volumen sin que a JP le haga falta ver cada línea
        -son correos que nadie tiene que revisar uno por uno-. Por eso,
        si el texto completo no entra, el equipo se colapsa a las
        primeras líneas que entren más un conteo explícito de cuántas se
        omitieron: nunca un recorte callado, que es la falla silenciosa
        que este archivo ya aprendió a temer. El conteo del encabezado
        ("El equipo lo maneja (N)") sigue siendo el total real aunque no
        se listen todas, así que nada quedó afuera del resumen en sí:
        sólo de la lista de nombres.

        Esta función asume que lo accionable solo ya entra en un
        mensaje -es responsabilidad de quien llama (mandar_resumen)
        decidir eso ANTES de llamarla, sin invocarla "a ver si entra":
        cada llamada acá arma una tanda nueva en self.abiertos, y una
        tanda armada y descartada es un botón fantasma que no
        corresponde a ningún mensaje real (ronda 2, hallazgo menor).
        """
        import secrets
        tanda = secrets.token_hex(2)
        mios = [c for c in correos if _categoria_de_resumen(c) == "TUYO"]
        derivar = [c for c in correos
                  if _categoria_de_resumen(c) in ("ENZO", "NATALIA")]
        equipo = [c for c in correos if _categoria_de_resumen(c) == "DELEGADO"]
        ruido = [c for c in correos if _categoria_de_resumen(c) == "RUIDO"]

        if not correos:
            # "No entró nada nuevo" es información, no la ausencia de
            # ella: si la secretaria se rompe y deja de avisar, el
            # silencio se parece demasiado a un día tranquilo. Por eso
            # el resumen sale siempre, aunque no haya nada que contar.
            return (f"{self.SALUDO[momento]} No entró nada nuevo.",
                    {"inline_keyboard": []})

        cabecera = ([f"{self.SALUDO[momento]} Entraron {len(correos)} correos."]
                   + self._lineas_mios_derivar(mios, derivar))
        pie = [f"\n🗑 Archivado como ruido: {len(ruido)}"] if ruido else []

        titulo_equipo = (f"\n✅ <b>El equipo lo maneja ({len(equipo)})</b>"
                         if equipo else None)
        lineas_equipo = [f"  {n}. {html.escape(c['de'][:26])} — "
                         f"{html.escape(c['asunto'][:40])}"
                         for n, c in enumerate(equipo, 1)]

        bloque_equipo = ([titulo_equipo] + lineas_equipo) if equipo else []
        texto = "\n".join(cabecera + bloque_equipo + pie)

        if equipo and len(texto) > LIMITE_TELEGRAM:
            # No entra completo: se colapsa el equipo, no lo accionable.
            # Presupuesto = lo que sobra después de lo que nunca se
            # recorta, con margen para el título y el aviso de corte.
            base = "\n".join(cabecera + pie)
            presupuesto = LIMITE_TELEGRAM - len(base) - len(titulo_equipo) - 80
            incluidas, largo = [], 0
            for linea in lineas_equipo:
                if largo + len(linea) + 1 > presupuesto:
                    break
                incluidas.append(linea)
                largo += len(linea) + 1
            faltan = len(equipo) - len(incluidas)
            aviso = ([f"  … y {faltan} más, sin listar por espacio "
                      f"(ya están en el conteo de arriba)"] if faltan else [])
            texto = "\n".join(cabecera + [titulo_equipo] + incluidas
                              + aviso + pie)

        filas = []
        if equipo:
            filas.append([{"text": "✓ Leí todo",
                           "callback_data": f"t|{tanda}-0|equipo"},
                          {"text": "⚠ Uno es mío",
                           "callback_data": f"m|{tanda}-0|equipo"}])
        # Asocia esta tanda con TODOS los correos del equipo -aunque
        # algunos no se hayan listado por espacio-. Sin esto, "Leí todo"
        # no sabría a cuáles marcar como leídos, y como cada tanda tiene
        # su propio token, un resumen viejo nunca puede cerrar los
        # correos de uno nuevo.
        self.abiertos[tanda] = [c["message_id"] for c in equipo]
        return texto, {"inline_keyboard": filas}

    def _cabria_en_un_mensaje(self, correos, momento):
        """¿Entra lo accionable solo -lo que armar_resumen nunca
        recorta- en un mensaje, sumado al margen fijo que se reserva
        para el colapso del equipo?

        Se calcula sin llamar a armar_resumen(): esa función arma una
        tanda nueva en self.abiertos por cada llamada, así que usarla
        "para medir" y después descartar el resultado deja un token
        fantasma con un botón que no corresponde a ningún mensaje real
        -el hallazgo menor de la ronda 2-. Esta cuenta es una
        aproximación conservadora (MARGEN_COLAPSO_EQUIPO de sobra para
        el título y el aviso de corte, que no dependen del volumen de
        equipo), no un armado exacto: si por algún borde se queda corta,
        el peor caso es un mensaje que Telegram rechaza y que se
        reintenta entero la próxima vuelta -no una pérdida.
        """
        mios = [c for c in correos if _categoria_de_resumen(c) == "TUYO"]
        derivar = [c for c in correos
                  if _categoria_de_resumen(c) in ("ENZO", "NATALIA")]
        saludo = f"{self.SALUDO[momento]} Entraron {len(correos)} correos."
        largo = len(saludo) + sum(
            len(l) + 1 for l in self._lineas_mios_derivar(mios, derivar))
        return largo + MARGEN_COLAPSO_EQUIPO <= LIMITE_TELEGRAM

    def _empacar_accionable(self, accionable, cupo):
        """Parte lo accionable en como mucho `cupo` mensajes que entren
        en el límite de Telegram, preservando el orden -lo de JP antes
        que lo para derivar, como en el resto del resumen-.

        Devuelve (lotes, sobran): `sobran` es lo que no entró ni
        agotando `cupo` mensajes -se queda "clasificado" en la base para
        que el próximo resumen lo vuelva a mostrar entero, nunca se da
        por contado sin que JP lo haya visto de verdad."""
        lotes = []
        i, n = 0, len(accionable)
        while i < n and len(lotes) < cupo:
            largo = 120  # margen para el saludo y los títulos de sección
            j = i
            while j < n:
                extra = len(self._linea_accionable(accionable[j])) + 1
                if largo + extra > LIMITE_TELEGRAM:
                    break
                largo += extra
                j += 1
            j = max(j, i + 1)  # un solo ítem que ya excede: se manda igual
            lotes.append(accionable[i:j])
            i = j
        return lotes, accionable[i:]

    def _texto_cierre_grande(self, sobran, equipo, ruido):
        """El mensaje final del camino de mucho volumen: nunca enumera
        equipo ni ruido -eso sería volver a mezclar el grueso con lo
        accionable, el problema que este camino existe para evitar-,
        sólo cuenta. Lo accionable que no entró en el detalle también se
        cuenta acá, pero no se marca "en_resumen": queda pendiente para
        volver completo, con nombre y asunto, en el próximo resumen."""
        lineas = ["Eso es lo que entra acá. Además:"]
        if sobran:
            lineas.append(f"  · {len(sobran)} tuyos o para derivar más "
                          f"-completos en el próximo resumen-.")
        if equipo:
            lineas.append(f"  ✅ El equipo maneja {len(equipo)} más.")
        if ruido:
            lineas.append(f"  🗑 Se archivaron {len(ruido)} como ruido.")
        lineas.append("\nPara verlos ahora, entrá directo a la casilla.")
        return "\n".join(lineas)

    def _mandar_resumen_grande(self, momento, mios, derivar, equipo, ruido):
        """El camino de mucho volumen: ni lo accionable solo entra en un
        mensaje. Se manda en varios, respetando la jerarquía -lo
        accionable primero y completo, el equipo y el ruido nunca se
        enumeran acá, sólo se cuentan- y con TOPE_MENSAJES como cota
        dura: la vuelta después de una caída larga es exactamente el
        momento en que más atraso hay, y es cuando menos sentido tiene
        mandar una andanada.

        Ningún mensaje de acá lleva botones -no enumeran equipo, así que
        no hay nada que "Leí todo" pueda cerrar- y por lo tanto no arma
        tandas en self.abiertos: nada fantasma que limpiar.
        """
        accionable = mios + derivar
        cupo_detalle = TOPE_MENSAJES - 1  # uno se reserva para el cierre
        lotes, sobran = self._empacar_accionable(accionable, cupo_detalle)

        for n, lote in enumerate(lotes, 1):
            mios_l = [c for c in lote if c["categoria"] == "TUYO"]
            derivar_l = [c for c in lote if c["categoria"] in ("ENZO", "NATALIA")]
            cabecera = [f"{self.SALUDO[momento]} Volviste con "
                       f"{len(accionable)} correos tuyos o para derivar "
                       f"esperando (parte {n}/{len(lotes) + 1})."]
            texto = "\n".join(cabecera + self._lineas_mios_derivar(mios_l, derivar_l))
            if not self.enviar(texto):
                # Este lote no salió: ni éste ni el cierre se mandan, y
                # nada de acá se marca -se reintenta entero la próxima
                # vez, en vez de dar por visto algo que Telegram nunca
                # entregó.
                return
            memoria.cambiar_lote(self.cx, [c["message_id"] for c in lote],
                                 "en_resumen")

        texto_cierre = self._texto_cierre_grande(sobran, equipo, ruido)
        if self.enviar(texto_cierre):
            # El equipo y el ruido se marcan igual que en el camino
            # normal (ronda 1): el conteo del cierre es exacto aunque no
            # se hayan listado, y no necesitan revisión uno por uno. Lo
            # accionable sobrante NO se marca -queda "clasificado" para
            # volver completo la próxima vez.
            memoria.cambiar_lote(self.cx, [c["message_id"] for c in equipo + ruido],
                                 "en_resumen")

    def armar_resumen_de_ruido(self, correos):
        """El texto y los botones del resumen de ruido de las 18:00.

        Es la red que atrapa el error más caro del sistema: algo de JP
        que se fue al tacho. Por eso es una lista NUMERADA -a diferencia
        del pie de armar_resumen(), que sólo cuenta cuántos se
        archivaron- y por eso lleva sólo dos botones: "Confirmar" cierra
        la tanda de un toque si a JP no le llama la atención nada;
        "Rever" es el arranque de la conversación en la que JP dice qué
        número no era ruido y por qué. Esa conversación -leer el número
        contra self.abiertos[tanda] y juntar la explicación, texto o
        audio- la maneja la tarea 11; lo que hace con lo que JP contesta
        es rever_ruido(), acá al lado.

        Sin correos no hay tanda ni botones: no hay nada para confirmar
        ni para rever, y una tanda sin un resumen real detrás es un
        botón fantasma (mismo hallazgo que armar_resumen, ronda 2).
        """
        if not correos:
            return (f"{self.SALUDO['ruido']} No archivé nada como ruido.",
                    {"inline_keyboard": []})

        import secrets
        tanda = secrets.token_hex(2)
        cabecera = [f"{self.SALUDO['ruido']} Son {len(correos)}."]
        lineas = [f"  {n}. {html.escape(c['de'][:34])} — "
                 f"{html.escape(c['asunto'][:44])}"
                 for n, c in enumerate(correos, 1)]
        texto = "\n".join(cabecera + lineas)

        if len(texto) > LIMITE_TELEGRAM:
            # Mismo criterio que el colapso del equipo en armar_resumen:
            # se corta con un aviso explícito de cuántos quedaron
            # afuera, nunca en silencio. A diferencia de ahí, lo que se
            # omite acá JP no lo puede Rever desde este mensaje -haría
            # falta ir a la casilla-, así que el aviso lo dice.
            base = "\n".join(cabecera)
            presupuesto = LIMITE_TELEGRAM - len(base) - 120
            incluidas, largo = [], 0
            for linea in lineas:
                if largo + len(linea) + 1 > presupuesto:
                    break
                incluidas.append(linea)
                largo += len(linea) + 1
            faltan = len(lineas) - len(incluidas)
            aviso = ([f"  … y {faltan} más, sin listar por espacio -para"
                      f" Rever alguno de ésos hace falta ir a la casilla-"]
                     if faltan else [])
            texto = "\n".join(cabecera + incluidas + aviso)

        # Misma tanda que usa armar_resumen: el token identifica esta
        # entrega, y self.abiertos dice a qué correos se refiere -acá
        # los mismos que se archivaron, aunque no todos se hayan
        # listado por espacio, igual que el equipo en armar_resumen.
        self.abiertos[tanda] = [c["message_id"] for c in correos]
        teclado = {"inline_keyboard": [[
            {"text": "✅ Confirmar", "callback_data": f"c|{tanda}-0|ruido"},
            {"text": "🔁 Rever", "callback_data": f"r|{tanda}-0|ruido"}]]}
        return texto, teclado

    def _mandar_resumen_de_ruido(self):
        """El envío del resumen de ruido: sólo lo archivado hoy, con
        armar_resumen_de_ruido() -nunca el combinado de manana/tarde-.
        Antes de esta tarea "ruido" mandaba el mismo resumen general de
        siempre, sólo con otro saludo; separado en su propio método por
        la misma razón que _mandar_resumen_grande: mandar_resumen()
        decide QUÉ camino corresponde, no arma el contenido.
        """
        correos = memoria.del_dia(self.cx, "archivado", "")
        texto, teclado = self.armar_resumen_de_ruido(correos)
        resultado = self.enviar(texto, teclado)
        if resultado and correos:
            # Mismo cuidado que en el camino general: si Telegram no
            # confirma el envío, no se marca nada -se reintenta entero
            # la próxima vez en vez de dar por vistos correos que JP
            # nunca vio.
            memoria.cambiar_lote(self.cx, [c["message_id"] for c in correos],
                                 "en_resumen")

    def mandar_resumen(self, momento):
        """Junta lo pendiente de resumir y lo manda por Telegram.

        Entran los correos que siguen en "clasificado" (lo de JP y lo
        para derivar que no interrumpió porque llegó fuera de horario, y
        todo lo del equipo, que nunca interrumpe) y en "archivado" (el
        ruido que ya se movió a INBOX.Ruido). Lo que ya se avisó al
        toque (avisar_en_el_momento) no vuelve a aparecer acá: ya lo
        vio.

        El momento "ruido" es distinto de los otros dos y se resuelve
        aparte (_mandar_resumen_de_ruido): es la lista numerada de lo
        archivado hoy con los botones Confirmar/Rever, no el resumen
        combinado de JP + derivar + equipo + ruido. Mezclarlos sería
        precisamente lo que el hallazgo de la ronda 1 (ver
        NoMandaUnaAndanadaSiSePerdieronVarios en test_secretaria.py) ya
        identificó como un problema: "Lo que archivé hoy" sobre un
        resumen que en realidad trae de vuelta correos de JP.

        Sin corte por fecha (desde="") a propósito: si algo quedara sin
        resumir por algún borde no previsto, tiene que aparecer en el
        próximo resumen que salga, no perderse en silencio hasta que
        alguien lo note a mano.

        Normalmente un solo mensaje alcanza -armar_resumen ya colapsa el
        bloque del equipo así que no crece sin límite con el volumen-.
        La decisión de si alcanza se toma ANTES de armar ese mensaje
        (_cabria_en_un_mensaje), no probando y descartando: ver el
        docstring de armar_resumen sobre por qué. Sólo si ni lo
        accionable solo entra se pasa al camino de mucho volumen
        (_mandar_resumen_grande), que trocea respetando la jerarquía en
        vez de partir la lista entera en lotes parejos -la ronda 1
        mandaba 67 mensajes con 1000 correos y 70 accionables porque
        troceaba todo junto, sin distinguir lo accionable del grueso.
        """
        if momento == "ruido":
            return self._mandar_resumen_de_ruido()

        correos = (memoria.del_dia(self.cx, "clasificado", "") +
                   memoria.del_dia(self.cx, "archivado", ""))

        if not correos or self._cabria_en_un_mensaje(correos, momento):
            texto, teclado = self.armar_resumen(correos, momento)
            resultado = self.enviar(texto, teclado)
            if resultado and correos:
                # Una sola transacción para todo el lote: con un commit
                # por correo, morir a mitad de camino deja marcados sólo
                # algunos aunque el mensaje entero ya salió y JP ya lo
                # vio entero -la próxima vuelta repetiría el resto como
                # si fuera nuevo. Atómico, se marcan todos o ninguno.
                memoria.cambiar_lote(self.cx, [c["message_id"] for c in correos],
                                     "en_resumen")
            return

        mios = [c for c in correos if _categoria_de_resumen(c) == "TUYO"]
        derivar = [c for c in correos
                  if _categoria_de_resumen(c) in ("ENZO", "NATALIA")]
        equipo = [c for c in correos if _categoria_de_resumen(c) == "DELEGADO"]
        ruido = [c for c in correos if _categoria_de_resumen(c) == "RUIDO"]
        self._mandar_resumen_grande(momento, mios, derivar, equipo, ruido)

    # Categorías con las que corresponde hacer algo -TUYO/ENZO/NATALIA
    # interrumpen o esperan al resumen, DELEGADO lo maneja el equipo-,
    # a diferencia de RUIDO (otra vez, después de que JP dijo que no),
    # DUDA y ERROR, que necesitan que JP decida y por eso rever_ruido()
    # las muestra en el momento en vez de dejarlas esperando un resumen
    # que no sabría cómo contarlas.
    CATEGORIAS_ACCIONABLES = ("TUYO", "ENZO", "NATALIA", "DELEGADO")

    def rever_ruido(self, message_id, explicacion):
        """El circuito de corrección del resumen de ruido: JP dice que
        un correo archivado no era ruido, y de acá en más no lo es.

        Tres pasos, EN ESTE ORDEN -el orden es lo que garantiza la regla
        de oro: nunca se queda en Ruido después de que JP dijo que no lo
        era-:

          1. Vuelve a la bandeja, sin leer (correo.devolver_a_bandeja).
             Va PRIMERO y sin condicionar nada de lo que sigue: si el
             paso 2 revienta, esto ya pasó y no hay reversa. Respeta
             puede_escribir() como cualquier escritura en la casilla; si
             el freno está puesto se levanta SecretariaFrenada -a
             diferencia del ciclo automático, que simplemente espera al
             próximo intento, esto lo disparó JP ahora, y quedarse mudo
             sería la misma falla silenciosa de siempre.

          2. Se reclasifica con la explicación de JP como contexto
             extra -la misma clasificador.clasificar() de siempre, así
             que el reintento con espera ante un 429 ya viene incluido,
             sin armar una llamada aparte-. Si el motor no contesta, la
             categoría nueva queda "ERROR": el silencio no es una
             categoría, tampoco acá.

          3. Se guarda la corrección (memoria.corregir): categoria_jp y
             la explicación de JP TAL CUAL la mandó -texto o, si vino
             por audio, ya transcripta por quien llama (bot.transcribir,
             tarea 11)-. corregir() no toca `categoria`: es lo que dijo
             el sistema la primera vez, y hace falta conservarlo para
             medir si mejora. Si la categoría nueva es accionable
             -TUYO, ENZO, NATALIA, DELEGADO- el correo vuelve a
             "clasificado" para que el PRÓXIMO resumen lo muestre donde
             corresponde -por eso armar_resumen mira categoria_jp antes
             que categoria, ver _categoria_de_resumen: si no, quedaba
             contado como ruido para siempre-. Si no -RUIDO otra vez,
             DUDA, o ERROR- se le muestra a JP en el momento
             (avisar_para_que_decida): que el sistema no haya aceptado
             la corrección de JP es justo lo que no puede pasar en
             silencio.

        Si el paso 1 revienta de verdad -CopiaRechazada, EscrituraRechazada
        u OperacionAMedias, ninguna es el False benigno de "ya no
        estaba"- la excepción sube tal cual y acá no se guarda nada: ni
        la reclasificación ni la corrección corren, porque no hay nada
        confirmado todavía y guardar la corrección igual mentiría "ya no
        es ruido" de un correo que puede seguir en Ruido.

        Devuelve la categoría nueva.
        """
        fila = memoria.obtener(self.cx, message_id)
        if fila is None:
            raise ValueError(f"no conozco el correo {message_id!r}")
        if not self.puede_escribir():
            raise SecretariaFrenada(
                f"{message_id}: no puedo devolverlo a la bandeja con el"
                " freno puesto -en seco o en pausa-")

        correo.devolver_a_bandeja(fila)

        # El correo real, con lo que JP acaba de decir como contexto
        # extra delante del cuerpo -clasificador.clasificar() no tiene
        # un parámetro aparte para esto, y agregar uno lo obligaría a
        # armar el prompt distinto según quien llame-. El resto de los
        # campos (de/para/cc/asunto/adjuntos) quedan igual: son los que
        # ya se guardaron cuando se archivó.
        contexto = dict(fila, cuerpo=(
            f"JP acaba de decir, sobre ESTE correo: «{explicacion}»\n\n"
            f"{fila.get('cuerpo') or ''}"))
        try:
            pred = clasificador.clasificar(clasificador.prompt_sistema(),
                                           contexto)
            nueva = pred["categoria"]
        except Exception:
            nueva = "ERROR"

        memoria.corregir(self.cx, message_id, nueva, explicacion)
        if nueva in self.CATEGORIAS_ACCIONABLES:
            memoria.cambiar(self.cx, message_id, "clasificado")
        else:
            memoria.cambiar(self.cx, message_id, "mostrado_sin_clasificar")
            self.avisar_para_que_decida(fila)
        return nueva

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
