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

# Cuánto silencio cuenta como "estuvo caída". El ciclo late cada CADA
# segundos, así que un hueco de cinco vueltas ya no es un tropiezo de
# red: es el proceso muerto -o la Mac dormida- el rato suficiente como
# para que JP tenga que enterarse. El diseño §8 lo pide con todas las
# letras: "si la secretaria estuvo caída, el resumen lo dice y desde
# cuándo". Es el antídoto que el propio diseño propone contra la falla
# que más preocupa, la silenciosa, porque no recibir avisos se parece
# demasiado a un día tranquilo.
HUECO_CAIDA = 5 * CADA
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
        # De dónde retoma el reloj: del último latido que quedó en la
        # base, NO de "ahora". launchd tiene KeepAlive y arrancar() sale
        # con código 1 a propósito cuando un hilo se muere, así que
        # reiniciar es el camino normal y no la excepción. Con
        # datetime.now() acá, Reloj.momentos_pendientes -25 líneas
        # dedicadas a recuperar los resúmenes de una caída larga- no
        # corría NUNCA a través de un reinicio: la caída se borraba sola
        # al arrancar. La tabla `latidos` se escribe en cada vuelta del
        # ciclo desde la tarea 6 y hasta ahora sólo la leía /estado.
        self.ultimo_reloj = self._retomar_el_reloj()
        # Desde cuándo y hasta cuándo estuvo caída, si lo estuvo. Los
        # pone _disparar_resumenes y los consume el primer resumen que
        # sale de verdad (ver _encabezado_de_caida). Se guarda también
        # el "hasta" y no se usa datetime.now() al armar el texto para
        # que el "estuve caída N horas" salga de los dos extremos que se
        # midieron de verdad, y no de cuándo casualmente se armó el
        # mensaje.
        self.caida_desde = None
        self.caida_hasta = None
        self.offset = 0
        self.parada = threading.Event()
        self.pausada = False
        # Qué message_id puede cerrar cada tanda de resumen con "Leí
        # todo": sin esto el botón no sabe cuáles marcar, y como los
        # botones de Telegram no vencen, dos resúmenes del mismo día no
        # pueden compartir token (ver armar_resumen). Tarea 11: cada vez
        # que una tanda queda del todo resuelta -"Leí todo", "Confirmar",
        # o el último ítem corregido con "Uno es mío"/Rever- se saca de
        # acá (ver _sacar_de_abiertos). Sin esto crecía sin límite
        # mientras viviera el proceso, quedó anotado desde la tarea 9.
        self.abiertos = {}
        # Qué se le preguntó a JP y todavía espera contestación: un botón
        # (Uno es mío, Rever) no puede quedarse bloqueado esperando la
        # respuesta -el hilo que escucha se queda mudo para todo lo demás
        # mientras tanto-, así que la pregunta pendiente vive acá, no en
        # la pila de ninguna función, y se retoma en la próxima llamada a
        # atender(), sea en un segundo o -tanda real medida- once horas
        # después (ver responder_pendiente). Sólo una pregunta a la vez:
        # JP es una sola persona contestando en orden, no hace falta cola.
        # Ronda 1 de la tarea 11: se lee de la base al arrancar -y se
        # reescribe ahí con cada cambio, ver _set_pendiente-, porque
        # "vive en el objeto" no alcanza si el proceso se reinicia justo
        # entre que JP contestó un paso y el siguiente: sin esto, esa
        # respuesta cae acá sin nada pendiente y el bot le miente
        # diciendo que no entiende preguntas sueltas.
        self.pendiente = memoria.cargar_pendiente(self.cx)

    def _retomar_el_reloj(self):
        """Desde cuándo mirar para atrás al arrancar: el último latido.

        Sin latido -base nueva- arranca en `ahora`, que es lo que hacía
        antes: sin historia no hay nada que recuperar. Si el latido
        quedó en el futuro -el reloj del sistema se corrigió para atrás
        mientras el proceso estaba muerto- se usa `ahora` igual, por el
        mismo motivo que _disparar_resumenes no deja retroceder el
        reloj: un `ultimo` adelantado se arrastraría a todas las vueltas
        siguientes.
        """
        ahora = datetime.now()
        crudo = memoria.ultimo_latido(self.cx)
        if not crudo:
            return ahora
        try:
            ultimo = datetime.fromisoformat(crudo)
        except (TypeError, ValueError):
            # Un latido ilegible no puede impedir que arranque: lo peor
            # que pasa volviendo a `ahora` es perder la recuperación de
            # esa caída, que es exactamente lo que pasaba siempre antes.
            return ahora
        if ultimo.tzinfo is not None:
            ultimo = ultimo.replace(tzinfo=None)
        return min(ultimo, ahora)

    def _encabezado_de_caida(self):
        """La línea que encabeza el primer resumen después de una caída,
        o "" si no estuvo caída.

        Vale igual para el hueco de un reinicio -__init__ retoma el
        reloj del último latido- y para uno con el proceso vivo pero
        dormido (la Mac suspendida): desde acá los dos se ven igual, un
        salto grande entre dos vueltas del ciclo.
        """
        if not self.caida_desde:
            return ""
        hasta = self.caida_hasta or datetime.now()
        segundos = max((hasta - self.caida_desde).total_seconds(), 0)
        if segundos >= 48 * 3600:
            largo = f"{segundos / 86400:.1f} días"
        elif segundos >= 3600:
            largo = f"{segundos / 3600:.1f} horas"
        else:
            largo = f"{segundos / 60:.0f} minutos"
        return (f"⚠️ <b>Estuve caída</b> desde el "
                f"{self.caida_desde.strftime('%d/%m a las %H:%M')} "
                f"-{largo}-. Puede que algo haya entrado sin que lo "
                f"viera en el momento.")

    def ciclo_de_correo(self):
        while not self.parada.is_set():
            try:
                # Cuándo EMPEZÓ esta vuelta, antes de tocar la casilla.
                # Es lo que _disparar_resumenes necesita para distinguir
                # "estuve caída" de "esta vuelta tardó": lo que sigue
                # puede llevarse quince minutos y eso no es un hueco.
                arranque = datetime.now()
                self.revisar_casilla()
                ahora = datetime.now()
                self._disparar_resumenes(ahora, arranque)
                memoria.latido(self.cx, ahora.isoformat(timespec="seconds"))
            except (Exception, SystemExit) as e:
                # Nada que pase acá adentro puede matar el proceso: si se
                # muere, JP no se entera, porque no recibir avisos se parece
                # mucho a un día tranquilo.
                #
                # SystemExit y no sólo Exception: clasificador.motores()
                # levanta SystemExit cuando no hay ningún motor
                # configurado en el .env, y SystemExit NO hereda de
                # Exception. Con "except Exception" a secas se escapaba
                # de acá, mataba el hilo del correo, arrancar() salía con
                # 1, launchd reiniciaba, y el motor seguía sin estar: un
                # bucle de reinicio sin un solo aviso. Ya se sabía -
                # /estado usa (Exception, SystemExit) por esto mismo-
                # pero se había parchado un call site de dos.
                _registrar("correo", e)
            self.parada.wait(CADA)

    def _disparar_resumenes(self, ahora, arranque=None):
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

        `arranque` es cuándo EMPEZÓ esta vuelta del ciclo, y es contra
        eso -no contra `ahora`- que se mide el hueco. La diferencia
        importa: `ciclo_de_correo` llama acá DESPUÉS de revisar_casilla,
        así que `ahora - ultimo_reloj` es wait(CADA) MÁS lo que tardó el
        trabajo. Con HUECO_CAIDA = 900s alcanzaba con que una vuelta de
        clasificación tardara más de 12 minutos para que el proceso
        -vivo y trabajando- se avisara a sí mismo como caído; medido,
        13 minutos de trabajo daban "⚠️ Estuve caída desde el 17/08 a
        las 08:20 -16 minutos-" en el resumen de un lunes normal. Y 13
        minutos no es raro: ESPERA_RESPUESTA son 180s por pedido, dos
        pasadas por correo, más el backoff de los 429.

        Medido contra `arranque`, el hueco es lo que el proceso NO
        estuvo trabajando -la espera entre dos vueltas, unos 180s, o el
        tiempo entero que estuvo muerto o dormido-, que es lo que
        "estuve caída" quiere decir.

        Lo que se pierde a cambio: una Mac que se suspende en el medio
        de revisar_casilla queda adentro de "el trabajo tardó" y no se
        anuncia como caída. Los resúmenes de ese hueco salen igual
        -momentos_pendientes sigue mirando de `ultimo_reloj` a `ahora`-;
        lo único que falta es el encabezado. Se elige ese lado a
        propósito: un "Estuve caída" falso cada lunes ocupado es
        exactamente lo que hace que JP deje de leerlo el día que es
        verdad.

        `ahora` sigue siendo el que manda para los resúmenes y para el
        reloj: lo que corresponde mandar es todo lo que se cruzó hasta
        este instante, no hasta que empezó la vuelta.
        """
        if arranque is None:
            arranque = ahora
        if (arranque - self.ultimo_reloj).total_seconds() > HUECO_CAIDA:
            self.caida_desde, self.caida_hasta = self.ultimo_reloj, arranque
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
            # El saludo evita "ruido" para ese resumen general: ese
            # saludo es "Lo que archivé hoy", y si el contenido trae de
            # vuelta correos de JP acumulados de varios días -típico
            # después de una caída larga- ese texto no describe lo que
            # hay adentro.
            #
            # Pero el de ruido NO se colapsa contra el general: son dos
            # cosas distintas y el general no lo reemplaza. El general
            # cuenta el ruido ("Archivado como ruido: N"); el de las
            # 18:00 es la única lista NUMERADA, la única con botón de
            # Rever, y por lo tanto la única forma que tiene JP de sacar
            # de Ruido algo suyo que se fue al tacho. Colapsarlos -como
            # se hacía- borraba esa red entera cada vez que el proceso
            # estuvo caído cruzando las 18:00. Son dos mensajes en el
            # peor caso, no la andanada de nueve que este código evita.
            general = next((m for m in reversed(pendientes) if m != "ruido"),
                           None)
            if general:
                self.mandar_resumen(general)
            if "ruido" in pendientes:
                self.mandar_resumen("ruido")
        if ahora > self.ultimo_reloj:
            self.ultimo_reloj = ahora

    def ciclo_de_escucha(self):
        while not self.parada.is_set():
            try:
                d = bot.tg("getUpdates", offset=self.offset, timeout=50)
                for u in d.get("result", []):
                    self.offset = u["update_id"] + 1
                    self.atender(u)
            except (Exception, SystemExit) as e:
                # SystemExit por lo mismo que ciclo_de_correo: acá abajo
                # se llega a clasificador (por /motor) y no hereda de
                # Exception.
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

    def _nueva_tanda(self):
        """Un token de tanda que NO pise a ninguno de los abiertos.

        Los botones de Telegram no vencen nunca: un resumen de hace
        semanas sigue teniendo botones vivos. Con token_hex(2) son 65.536
        valores, y `self.abiertos[tanda] = ...` asignaba sin chequear
        nada, así que una colisión hace que un botón viejo opere sobre
        los correos de otra tanda: medido, JP toca "Listo, lo vi" en el
        aviso del lunes y se cierra el correo de hoy que nunca miró. En
        una tanda de resumen es peor, porque _cerrar_equipo marca leído
        en la casilla real.

        Son dos arreglos y hacen falta los dos: token_hex(4) baja la
        probabilidad (4.294.967.296 valores), y el chequeo contra
        self.abiertos la lleva a cero. Sin el chequeo, "el token es
        largo" es una apuesta; con el chequeo, es una garantía -y es lo
        que hace que la mutación "token fijo" se caiga, en vez de
        sobrevivir la suite entera.
        """
        import secrets
        tanda = ""
        for _ in range(64):
            tanda = secrets.token_hex(4)
            if tanda not in self.abiertos:
                return tanda
        # 64 colisiones seguidas con 2^32 valores no pasan: si pasan, el
        # generador no está generando. Antes que devolver una tanda
        # repetida -que es exactamente el daño que esto existe para
        # evitar- se desempata con un sufijo. bot.es_de_esta_tanda()
        # compara la tanda entera contra "{tanda}-{idx}", así que un
        # sufijo no rompe el formato del callback_data.
        n = 0
        while f"{tanda}x{n:x}" in self.abiertos:
            n += 1
        return f"{tanda}x{n:x}"

    def _atender_avisos_pendientes(self):
        """La cola de avisos que el diseño §8 pide -"los avisos quedan
        en cola y salen cuando vuelve"- y que no existía.

        Un aviso al toque que Telegram no entregó marcaba el correo
        "avisado" igual, y "avisado" no lo lee nadie: el correo de JP
        desaparecía del sistema entero. Medido con Telegram caído sobre
        un correo TUYO: quedaba "avisado" y el resumen de las 8:30 decía
        "No entró nada nuevo". Ahora el que no sale queda en
        "pendiente_de_avisar" y se reintenta acá en cada vuelta.

        Sin tope de intentos, por la misma razón que _avisar_la_falla:
        reintentar un aviso que no llegó no duplica nada -a diferencia
        de reintentar un COPY- así que no hace falta cortar. Y si
        Telegram no vuelve en todo el día el correo tampoco se pierde:
        mandar_resumen() también mira esta situación, así que el resumen
        es el segundo camino por el que ese correo llega igual.

        La fila se relee con memoria.obtener() y no se usa la de
        pendientes(): obtener() decodifica `adjuntos` a lista, que es lo
        que espera correo.adjuntos_legibles(); el JSON crudo lo haría
        iterar caracteres.
        """
        for pendiente in memoria.pendientes(self.cx, "pendiente_de_avisar"):
            c = memoria.obtener(self.cx, pendiente["message_id"]) or pendiente
            categoria = _categoria_de_resumen(c)
            if categoria in ("TUYO", "ENZO", "NATALIA"):
                self.avisar_en_el_momento(c, {"categoria": categoria})
            else:
                # RUIDO, DUDA o ERROR: lo que hay que volver a mandar es
                # el "no pude decidir" con el teclado de categorías, que
                # es el aviso que no salió.
                self.avisar_para_que_decida(c)

    def _atender_revers_pendientes(self):
        """Retoma los Rever cuyo motivo ya contestó JP pero que todavía
        no se ejecutaron de verdad -tarea 11, ronda 1, hallazgo CRÍTICO.

        _responder_motivo_rever (en el hilo que escucha) sólo anota el
        motivo con memoria.preparar_rever() y le contesta a JP en el
        acto; ejecutar rever_ruido() de verdad -que llama a
        clasificador.clasificar(), hasta tres minutos de red con
        nvidia- es trabajo de ESTE hilo, el mismo que ya hace
        clasificar() para el correo entrante. Se lee de la base, no de
        una variable en memoria, así que un reinicio del proceso entre
        que JP contestó el motivo y que esto corre no pierde nada -el
        motivo ya está anotado antes de contestarle "te confirmo en un
        rato".

        Mismo tope de intentos que _atender_pendientes, y por la misma
        razón: sin tope, un Rever que falla siempre -IMAP caído,
        clasificar() reventando antes de tiempo- se reintentaría cada
        CADA segundos para siempre sin que nadie se entere.
        """
        if not self.puede_escribir():
            return
        for fila in memoria.pendientes(self.cx, "pendiente_de_rever"):
            if fila["intentos"] >= INTENTOS_MAXIMOS:
                self._avisar_rever_no_se_pudo(fila)
                continue
            explicacion = fila.get("explicacion") or ""
            try:
                self.rever_ruido(fila["message_id"], explicacion)
            except SecretariaFrenada:
                # Se frenó justo ahora, entre el chequeo de arriba y
                # esta llamada -o rever_ruido() volvió a chequearlo y
                # ahora dio distinto-: no es una falla real, se
                # reintenta la próxima vuelta sin gastar un intento.
                continue
            except (Exception, SystemExit) as e:
                # SystemExit incluido: rever_ruido() termina en
                # clasificador.clasificar(), que puede levantarlo si no
                # hay motor configurado, y no hereda de Exception.
                _registrar("rever", e)
                memoria.anotar_falla(self.cx, fila["message_id"],
                                     f"{type(e).__name__}: {e}")
                intentos = memoria.sumar_intento(self.cx, fila["message_id"])
                if intentos >= INTENTOS_MAXIMOS:
                    self._avisar_rever_no_se_pudo(
                        dict(fila, falla=f"{type(e).__name__}: {e}"))

    def _avisar_rever_no_se_pudo(self, fila):
        """Le avisa a JP que un Rever se quedó sin intentos.

        Mismo criterio que _avisar_la_falla: sólo pasa a un estado
        terminal ("no_se_pudo_rever") si el aviso salió de verdad -si
        Telegram está caído justo en ese momento, se queda como está y
        se reintenta avisar la próxima vuelta, sin volver a tocar la
        red del clasificador."""
        texto = (f"<b>🚨 No pude terminar un Rever</b>\n"
                 f"<b>De:</b> {html.escape(str(fila.get('de', ''))[:90])}\n"
                 f"<b>Asunto:</b> "
                 f"{html.escape(str(fila.get('asunto', ''))[:120])}\n"
                 f"<b>Motivo que dijiste:</b> "
                 f"{html.escape(str(fila.get('explicacion') or '')[:200])}\n"
                 f"<b>Falló:</b> "
                 f"{html.escape(str(fila.get('falla') or '(no quedó registrado)')[:250])}\n\n"
                 f"Lo intenté {INTENTOS_MAXIMOS} veces y dejo de intentar. "
                 f"El correo puede seguir en Ruido -hace falta que lo "
                 f"mires a mano.")
        if self.enviar(texto):
            memoria.cambiar(self.cx, fila["message_id"], "no_se_pudo_rever")

    def revisar_casilla(self):
        """Trae lo nuevo, lo clasifica, archiva el ruido y avisa lo de JP.

        La regla que manda: si ningún motor respondió, o si respondió pero
        no pudo decidir (DUDA), el correo se le muestra a JP con los
        botones de siempre. El silencio no es una categoría, así que nunca
        se archiva algo que no se pudo clasificar con confianza.

        Con self.pausada en True esto NO corta de entrada -a propósito,
        aunque /pausa's docstring en comando() diga "no toco la casilla":
        esa frase es sobre ESCRIBIR (archivar, marcar leído), y acá abajo
        eso ya lo garantiza puede_escribir() en cada punto que escribe
        (_archivar, _terminar_de_archivar, _atender_pendientes). Cortar
        acá arriba, antes de traer_nuevos, apagaría también la
        clasificación y los avisos en el momento -haría falsa la otra
        mitad de la misma frase, "te sigo avisando lo que entra" (diseño
        §7.3/§8, y el propio texto de /pausa)-. Tocar la casilla y
        seguir mirándola son cosas distintas; /pausa frena sólo la
        primera.
        """
        from datetime import date, timedelta
        self._atender_pendientes()
        self._atender_revers_pendientes()
        # Antes que nada nuevo: lo que ya se sabe que hay que avisarle a
        # JP y todavía no salió. Va primero por el mismo motivo que los
        # pendientes de archivar -lo viejo no puede quedar atrás de lo
        # nuevo- y porque si Telegram sigue caído, el resto de la vuelta
        # tampoco va a poder avisar nada.
        self._atender_avisos_pendientes()
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
            except (Exception, SystemExit) as e:
                # Ningún motor respondió. Se le muestra a JP igual: el
                # silencio no es una categoría.
                #
                # SystemExit entra acá a propósito: es lo que levanta
                # clasificador.motores() cuando el .env no tiene ningún
                # motor, y no hereda de Exception. Escapándose de acá
                # mataba el hilo del correo y dejaba a launchd
                # reiniciando en bucle, sin un solo aviso -o sea, "no
                # hay motor configurado" se comportaba peor que
                # cualquier falla de red.
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

        Ronda 1 de la tarea 11: se registra la tanda en self.abiertos
        -antes no lo hacía, así que tocar cualquiera de los tres botones
        de acá contestaba "ya pasó" sobre un correo que había llegado
        hace segundos, justo los que más le importan a JP (lo suyo y lo
        para derivar)-, y se agrega el tercer botón que el diseño (§7.2)
        siempre tuvo y el código no: "⭐ Cliente importante".

        Despacho final, CRÍTICO 2: el retorno de enviar() se MIRA antes
        de dar el aviso por entregado. enviar() devuelve None cuando
        Telegram no contestó y no levanta nada, y memoria.cambiar(...,
        "avisado") corría igual: como nadie lee "avisado", el correo de
        JP desaparecía del sistema entero -no volvía a interrumpir, no
        entraba en ningún resumen, y el de las 8:30 decía "No entró nada
        nuevo"-. Es exactamente la invariante que _avisar_la_falla ya
        respetaba 240 líneas más abajo, que no se había aplicado al
        aviso que más importa: ningún estado terminal sin entrega
        confirmada. Si no salió queda en cola
        (_atender_avisos_pendientes), que es lo que el diseño §8 pide.

        Devuelve si el aviso salió de verdad.
        """
        importante = reglas.protegido(c) and pred["categoria"] == "TUYO"
        if not en_horario(datetime.now()) and not importante:
            # Ni se intentó: el correo queda como está -"clasificado"
            # si es nuevo- y entra en el resumen de las 8:30, que es lo
            # que corresponde fuera de horario.
            return False
        tanda = self._nueva_tanda()
        titulo = ("📌 Es tuyo" if pred["categoria"] == "TUYO"
                  else f"➡️ Para derivar a {pred['categoria'].title()}")
        texto = (f"<b>{titulo}</b> · <i>{html.escape(correo.fecha_legible(c['fecha']))}</i>\n"
                 f"<b>De:</b> {html.escape(c['de'][:90])}\n"
                 f"<b>CC:</b> {html.escape((c['cc'] or '(nadie)')[:90])}\n"
                 f"<b>Asunto:</b> {html.escape(c['asunto'][:120])}\n"
                 f"{html.escape(correo.adjuntos_legibles(c.get('adjuntos')))}\n\n"
                 f"<pre>{html.escape((c['cuerpo'] or '')[:600])}</pre>")
        if not self.enviar(texto, {"inline_keyboard": [
                [{"text": "Listo, lo vi", "callback_data": f"v|{tanda}-1|ok"},
                 {"text": "No era mío", "callback_data": f"n|{tanda}-1|0"}],
                [{"text": "⭐ Cliente importante",
                  "callback_data": f"i|{tanda}-1|0"}]]}):
            # No salió: a la cola. Y la tanda no se registra -sus
            # botones no existen en ningún lado, registrarla sería un
            # token fantasma más.
            memoria.cambiar(self.cx, c["message_id"], "pendiente_de_avisar")
            return False
        memoria.cambiar(self.cx, c["message_id"], "avisado")
        self.abiertos[tanda] = [c["message_id"]]
        return True

    def avisar_para_que_decida(self, c):
        """Ningún motor respondió, o respondió pero no logró decidir
        (DUDA). En los dos casos se le muestra igual, con los botones de
        siempre: si el sistema no sabe, decide JP. Nunca se archiva.

        Ronda 1 de la tarea 11: mismo arreglo que avisar_en_el_momento,
        registra la tanda para que sus botones -las seis categorías y
        "⭐ Cliente importante", ambos ya en bot.teclado()- funcionen de
        verdad en vez de contestar "ya pasó".

        Despacho final: mismo arreglo del CRÍTICO 2 que
        avisar_en_el_momento, por la misma razón y peor todavía acá.
        Este correo queda en "mostrado_sin_clasificar", y a ESA situación
        no la mira ningún resumen: si el mensaje no salía, el correo que
        el sistema no supo clasificar -justo el que necesita que decida
        JP- no volvía a aparecer nunca. Ahora, si no salió, queda en
        cola.

        Y la contracara, que faltaba: cuando SÍ sale, el correo pasa a
        "mostrado_sin_clasificar", que es la situación que este docstring
        dice que le corresponde. Sin esto, un aviso que había fallado una
        vez se quedaba en "pendiente_de_avisar" PARA SIEMPRE:
        avisar_en_el_momento marca "avisado" al salir bien, éste no
        marcaba nada, y _atender_avisos_pendientes lo reintenta en cada
        vuelta del ciclo sin tope -y sin tope con razón, ver su
        docstring-. Medido: 5 vueltas con Telegram ya restablecido daban
        5 mensajes idénticos de "⚠️ No pude decidir", uno por vuelta, más
        una tanda nueva en self.abiertos cada vez. El ciclo de correo no
        tiene reja de horario, así que son unos 20 por hora hasta el
        próximo corte: un DUDA que falla a las 17:05 son ~300 mensajes
        durante la noche. Y DUDA no es un caso raro: son 11 de 46.

        No es la misma situación que "avisado" a propósito. "avisado" es
        terminal -JP ya lo vio y no vuelve a aparecer por ningún lado-;
        acá el sistema todavía no sabe qué es el correo, así que
        "mostrado_sin_clasificar" es lo correcto: ya se le mostró, y
        sigue esperando que JP diga qué era. Es también la situación con
        la que memoria.anotar() lo dejó al entrar (DUDA y ERROR nacen
        ahí), así que esto lo devuelve a donde estaba.

        Devuelve si el aviso salió de verdad.
        """
        tanda = self._nueva_tanda()
        texto = (f"<b>⚠️ No pude decidir</b>\n"
                 f"<b>De:</b> {html.escape(c['de'][:90])}\n"
                 f"<b>Asunto:</b> {html.escape(c['asunto'][:120])}\n\n"
                 f"<pre>{html.escape((c['cuerpo'] or '')[:600])}</pre>\n\n"
                 f"¿Qué correspondía?")
        if not self.enviar(texto, bot.teclado(1, tanda)):
            memoria.cambiar(self.cx, c["message_id"], "pendiente_de_avisar")
            return False
        memoria.cambiar(self.cx, c["message_id"], "mostrado_sin_clasificar")
        self.abiertos[tanda] = [c["message_id"]]
        return True

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

        CorreoPerdido es el tercer caso y hasta el despacho final decía
        justo lo contrario de la verdad: llega por la misma vía que el
        duplicado (borrar_el_original lo levanta y _anotar_falla lo manda
        a "pendiente_de_borrar"), así que el aviso decía "Quedó
        duplicado: sigue en tu bandeja y ya hay una copia en
        INBOX.Ruido" a renglón seguido de un "Falló: CorreoPerdido: no
        está en INBOX y tampoco en INBOX.Ruido". Mandaba a JP a buscar el
        correo en los dos lugares donde el sistema acababa de confirmar
        que no estaba. Se reconoce por el texto de la falla y no por el
        tipo de la excepción a propósito: el aviso puede salir después de
        un reinicio, leyendo `falla` de la base, donde lo único que
        sobrevive es el texto.
        """
        if str(falla).startswith("CorreoPerdido"):
            donde = ("No lo encuentro ni en tu bandeja ni en INBOX.Ruido. "
                     "Puede que esté en otra carpeta o que se haya movido "
                     "a mano: hace falta que lo busques vos.")
        elif duplicado:
            donde = ("Quedó duplicado: sigue en tu bandeja y ya hay una "
                     "copia en INBOX.Ruido.")
        else:
            donde = "Quedó en tu bandeja, sin archivar."
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

    def armar_resumen(self, correos, momento, encabezado=""):
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

        `encabezado` es la línea de "estuve caída desde ..." cuando
        corresponde (ver _encabezado_de_caida). Entra por parámetro y no
        se pega afuera para que cuente en el presupuesto del colapso del
        equipo: pegada afuera podría empujar el mensaje por encima del
        límite de Telegram, que lo rechaza entero.
        """
        tanda = self._nueva_tanda()
        prefijo = [encabezado] if encabezado else []
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
            return ("\n".join(prefijo +
                              [f"{self.SALUDO[momento]} No entró nada nuevo."]),
                    {"inline_keyboard": []})

        cabecera = (prefijo
                   + [f"{self.SALUDO[momento]} Entraron {len(correos)} correos."]
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

    def _cabria_en_un_mensaje(self, correos, momento, encabezado=""):
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
        if encabezado:
            largo += len(encabezado) + 1
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

    def _mandar_resumen_grande(self, momento, mios, derivar, equipo, ruido,
                               consumibles=None, encabezado=""):
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

        `consumibles` son los message_id que ESTE resumen puede dar por
        contados. No es lo mismo que "los que se mostraron": lo
        archivado se muestra (se cuenta en el cierre) pero es del
        resumen de las 18:00, el único que lo lista numerado y con botón
        de Rever. Ver mandar_resumen.
        """
        consumibles = set(consumibles or ())
        accionable = mios + derivar
        cupo_detalle = TOPE_MENSAJES - 1  # uno se reserva para el cierre
        lotes, sobran = self._empacar_accionable(accionable, cupo_detalle)

        for n, lote in enumerate(lotes, 1):
            # `lote` sale de `mios`/`derivar` (que mandar_resumen ya armó
            # con _categoria_de_resumen), pero filtrarlo de nuevo por la
            # categoría cruda del sistema volvía a perder los corregidos
            # por Rever: un correo con categoria="RUIDO" y
            # categoria_jp="NATALIA" no es "TUYO" ni está en ("ENZO",
            # "NATALIA") por su categoria cruda, así que no entraba en
            # ninguna de las dos líneas -desaparecía del mensaje- y
            # cambiar_lote() lo marcaba "en_resumen" igual, como si JP lo
            # hubiera visto. Mismo criterio que en todos los demás
            # filtros de este archivo: _categoria_de_resumen.
            mios_l = [c for c in lote if _categoria_de_resumen(c) == "TUYO"]
            derivar_l = [c for c in lote
                        if _categoria_de_resumen(c) in ("ENZO", "NATALIA")]
            cabecera = ([encabezado] if encabezado and n == 1 else []) + [
                f"{self.SALUDO[momento]} Volviste con "
                f"{len(accionable)} correos tuyos o para derivar "
                f"esperando (parte {n}/{len(lotes) + 1})."]
            texto = "\n".join(cabecera + self._lineas_mios_derivar(mios_l, derivar_l))
            if not self.enviar(texto):
                # Este lote no salió: ni éste ni el cierre se mandan, y
                # nada de acá se marca -se reintenta entero la próxima
                # vez, en vez de dar por visto algo que Telegram nunca
                # entregó.
                return
            self.caida_desde = self.caida_hasta = None
            memoria.cambiar_lote(
                self.cx, [c["message_id"] for c in lote
                          if c["message_id"] in consumibles], "en_resumen")

        texto_cierre = self._texto_cierre_grande(sobran, equipo, ruido)
        if self.enviar(texto_cierre):
            # El equipo y el ruido se marcan igual que en el camino
            # normal (ronda 1): el conteo del cierre es exacto aunque no
            # se hayan listado, y no necesitan revisión uno por uno. Lo
            # accionable sobrante NO se marca -queda "clasificado" para
            # volver completo la próxima vez. Lo archivado tampoco: no
            # está en `consumibles`, es del resumen de las 18:00.
            self.caida_desde = self.caida_hasta = None
            memoria.cambiar_lote(
                self.cx, [c["message_id"] for c in equipo + ruido
                          if c["message_id"] in consumibles], "en_resumen")

    def armar_resumen_de_ruido(self, correos, encabezado=""):
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
        prefijo = [encabezado] if encabezado else []
        if not correos:
            return ("\n".join(prefijo + [f"{self.SALUDO['ruido']} No archivé"
                                         f" nada como ruido."]),
                    {"inline_keyboard": []})

        tanda = self._nueva_tanda()
        cabecera = prefijo + [f"{self.SALUDO['ruido']} Son {len(correos)}."]
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

        Este método es el DUEÑO de la situación "archivado": es el único
        que la consume. Los resúmenes de las 8:30 y las 17:00 la miran
        para contarla en el pie ("Archivado como ruido: N") pero no la
        tocan. Antes sí la tocaban -marcaban "en_resumen" todo lo que
        juntaban, clasificado y archivado por igual- y por eso a las
        18:00 acá no quedaba nada: medido, un día con 3 ruidos archivados
        daba "No archivé nada como ruido" y el teclado vacío. Con eso,
        todo lo archivado entre las 18:00 de ayer y las 17:00 de hoy
        nunca aparecía en una lista numerada y nunca se podía Rever: la
        red que atrapa el error más caro del sistema -algo de JP que se
        fue al tacho- no existía.
        """
        correos = memoria.del_dia(self.cx, "archivado", "")
        texto, teclado = self.armar_resumen_de_ruido(
            correos, self._encabezado_de_caida())
        resultado = self.enviar(texto, teclado)
        if resultado:
            self.caida_desde = self.caida_hasta = None
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
        todo lo del equipo, que nunca interrumpe), los que quedaron en
        "pendiente_de_avisar" (el aviso al toque no salió) y, sólo para
        contarlos, los "archivado" (el ruido que ya se movió a
        INBOX.Ruido). Lo que ya se avisó al toque
        (avisar_en_el_momento) no vuelve a aparecer acá: ya lo vio.

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

        # Lo que este resumen MUESTRA y lo que este resumen CONSUME no
        # son lo mismo, y confundirlos era el CRÍTICO 1.
        #
        # Se consume lo "clasificado" -lo de JP y lo para derivar que no
        # interrumpió, y todo lo del equipo- y lo que quedó en cola de
        # aviso: si el aviso al toque no salió, el resumen es el otro
        # camino por el que ese correo llega igual (ver
        # _atender_avisos_pendientes).
        #
        # Lo "archivado" se muestra -el conteo del pie- pero NO se
        # consume: su dueño es _mandar_resumen_de_ruido, el único que lo
        # convierte en la lista numerada con botón de Rever. Marcarlo
        # "en_resumen" acá dejaba vacío el mensaje de las 18:00 casi
        # todos los días, y con él la única forma que tiene JP de sacar
        # de Ruido algo suyo. Un correo archivado deja de ser reversible
        # sólo cuando JP lo confirma, nunca por un efecto secundario de
        # otro resumen.
        clasificados = sorted(
            memoria.del_dia(self.cx, "clasificado", "") +
            memoria.del_dia(self.cx, "pendiente_de_avisar", ""),
            key=lambda c: c.get("visto") or "")
        archivados = memoria.del_dia(self.cx, "archivado", "")
        correos = clasificados + archivados
        consumibles = {c["message_id"] for c in clasificados}
        encabezado = self._encabezado_de_caida()

        if not correos or self._cabria_en_un_mensaje(correos, momento,
                                                     encabezado):
            texto, teclado = self.armar_resumen(correos, momento, encabezado)
            resultado = self.enviar(texto, teclado)
            if resultado:
                # Salió: el aviso de la caída ya se dio, no se repite en
                # el próximo resumen.
                self.caida_desde = self.caida_hasta = None
            if resultado and consumibles:
                # Una sola transacción para todo el lote: con un commit
                # por correo, morir a mitad de camino deja marcados sólo
                # algunos aunque el mensaje entero ya salió y JP ya lo
                # vio entero -la próxima vuelta repetiría el resto como
                # si fuera nuevo. Atómico, se marcan todos o ninguno.
                memoria.cambiar_lote(self.cx, sorted(consumibles),
                                     "en_resumen")
            return

        mios = [c for c in correos if _categoria_de_resumen(c) == "TUYO"]
        derivar = [c for c in correos
                  if _categoria_de_resumen(c) in ("ENZO", "NATALIA")]
        equipo = [c for c in correos if _categoria_de_resumen(c) == "DELEGADO"]
        ruido = [c for c in correos if _categoria_de_resumen(c) == "RUIDO"]
        self._mandar_resumen_grande(momento, mios, derivar, equipo, ruido,
                                    consumibles, encabezado)

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
        except (Exception, SystemExit):
            # SystemExit incluido -no hereda de Exception y es lo que
            # levanta motores() sin motor configurado-: acá también el
            # silencio no es una categoría, queda "ERROR" y se le
            # muestra a JP.
            nueva = "ERROR"

        memoria.corregir(self.cx, message_id, nueva, explicacion)
        if nueva in self.CATEGORIAS_ACCIONABLES:
            memoria.cambiar(self.cx, message_id, "clasificado")
        else:
            memoria.cambiar(self.cx, message_id, "mostrado_sin_clasificar")
            self.avisar_para_que_decida(fila)
        return nueva

    def atender(self, update):
        """Todo lo que llega de Telegram entra por acá: un botón, un audio
        o texto.

        Esto corre en ciclo_de_escucha, que no puede quedarse esperando
        nada -mientras espera, Telegram queda mudo para todo lo demás,
        que es justo por lo que clasificar (hasta tres minutos con
        nvidia) vive en el otro hilo-. Por eso "esperar una respuesta" no
        es un bucle acá adentro: es guardar QUÉ se preguntó en
        self.pendiente -vive en el objeto, no en la pila de esta
        función- y volver cuando llegue el próximo update, sea en el
        segundo siguiente o -tanda real medida, ver bot.tg- once horas
        después. Ver responder_pendiente().
        """
        cq = update.get("callback_query")
        if cq:
            return self.atender_boton(cq)
        m = update.get("message") or {}
        if m.get("voice"):
            try:
                texto = bot.transcribir(m["voice"]["file_id"])
            except Exception as e:
                self.enviar(f"No pude escuchar el audio ({type(e).__name__}). "
                            f"¿Me lo escribís?")
                return
            texto = texto.strip()
            if not texto:
                # Transcribió pero no hay nada entendible -silencio, ruido
                # de fondo-. Quedarse callado de acá en más sería la misma
                # falla que este archivo entero existe para evitar: JP
                # mandó un audio, cree que llegó, y no pasa nada.
                self.enviar("No entendí nada en el audio. ¿Me lo escribís?")
                return
        elif "text" in m:
            # Puede venir vacío o sólo espacios -no es lo normal desde la
            # UI de Telegram, pero es justo la forma que toma un "motivo"
            # en blanco si algún día se prueba a mano-: se deja pasar
            # igual, para que responder_pendiente() decida qué hacer con
            # eso en vez de que quede cortado acá sin que nadie se entere.
            texto = m["text"].strip()
        else:
            return  # sticker, foto, etc.: ni texto ni audio, nada que atender
        if texto.startswith("/"):
            return self.comando(texto)
        return self.responder_pendiente(texto)

    def comando(self, texto):
        from datetime import date
        partes = texto.split()
        cual = partes[0].lower()

        if cual == "/pausa":
            self.pausada = True
            return self.enviar("⏸ Frenada. No toco más la casilla — no "
                               "archivo ni marco leído. Te sigo avisando lo "
                               "que entra. <code>/sigo</code> para soltarla.")
        if cual == "/sigo":
            self.pausada = False
            return self.enviar("▶️ Listo, sigo trabajando.")
        if cual == "/motor":
            if len(partes) < 2:
                return self.enviar("Decime cuál. Por ejemplo: "
                                   "<code>/motor groq</code>")
            try:
                orden = clasificador.elegir_motor(partes[1])
            except ValueError as e:
                return self.enviar(f"⚠️ {html.escape(str(e))}")
            return self.enviar(f"Listo, ahora uso <b>{orden[0]}</b>. "
                               f"Si se queda sin cuota sigo con "
                               f"{', '.join(orden[1:]) or '(nada)'}.")
        if cual == "/estado":
            hoy = date.today().isoformat()
            n = memoria.contar_desde(self.cx, hoy)
            latido = memoria.ultimo_latido(self.cx) or "nunca"
            try:
                motor = clasificador.motores()[0][0]
            except (Exception, SystemExit):
                # Sin ningún motor con credenciales en el .env,
                # clasificador.motores() no tira ValueError: tira
                # SystemExit, que NO hereda de Exception. Un "except
                # Exception" a secas lo dejaba pasar de largo y tiraba
                # abajo el hilo que escucha por algo tan poco excepcional
                # como no tener un motor configurado todavía.
                motor = "ninguno configurado"
            return self.enviar(
                f"<b>Estado</b>\n"
                f"Correos de hoy: {n}\n"
                f"Último latido: {latido}\n"
                f"Motor: {motor}\n"
                f"En seco: {'sí' if EN_SECO else 'no'}\n"
                f"Pausada: {'sí' if self.pausada else 'no'}")
        return self.enviar("No conozco ese comando. Tengo "
                           "<code>/pausa</code>, <code>/sigo</code>, "
                           "<code>/motor</code> y <code>/estado</code>.")

    def atender_boton(self, cq):
        """Todo lo que llega como botón entra por acá.

        Los botones de Telegram no vencen nunca: un resumen de hace
        semanas sigue teniendo botones vivos. Por eso la tanda no se saca
        parseando el dato a mano -eso sólo confirma que TIENE forma de
        tanda, no que sea una tanda que sigue abierta- sino recorriendo
        self.abiertos y preguntándole a bot.es_de_esta_tanda() cuál, si
        alguna, matchea de verdad: tanda, idx Y forma del dato, los tres
        a la vez. El idx no es siempre 0 -"Leí todo"/"Confirmar"/"Rever"
        usan 0 porque son de tanda entera, pero avisar_en_el_momento y
        avisar_para_que_decida usan 1 porque son de un solo correo (ver
        el hallazgo Importante 1, ronda 1)- así que se prueban los dos
        valores conocidos contra cada tanda abierta.

        OJO: el idx que se prueba NUNCA sale del propio dato entrante
        -eso validaría el dato contra sí mismo, y "t|{tanda}-3|equipo"
        pasaría igual que "t|{tanda}-0|equipo" con sólo copiar el 3 dos
        veces (bug real de la ronda 1, atrapado por
        test_un_boton_con_el_idx_de_otro_correo_tambien_se_rechaza)-.
        Los candidatos (0, 1) son los únicos que el resto del código
        genera, así que sólo uno de los dos -si alguno- puede ser el de
        verdad para una tanda dada. Si ninguna combinación matchea, es
        un botón viejo -de una tanda ya cerrada, o de un resumen tan
        viejo que ya ni arrancó el proceso actual- y se le dice que ya
        pasó, en vez de aceptarlo como si fuera de hoy.
        """
        datos = cq.get("data") or ""
        match = next(((t, i) for t in self.abiertos for i in (0, 1)
                     if bot.es_de_esta_tanda(datos, i, t)), None)
        tanda, idx = match if match else (None, None)
        if tanda is None:
            bot.tg_suave("answerCallbackQuery", callback_query_id=cq["id"],
                         text="Ese botón es de otro correo, ya pasó.")
            return
        bot.tg_suave("answerCallbackQuery", callback_query_id=cq["id"])
        # El match de arriba ya confirmó -adentro de es_de_esta_tanda-
        # que datos tiene exactamente tres partes separadas por "|".
        accion, _, valor = datos.split("|")

        if accion == "t":                       # leí todo
            return self._cerrar_equipo(tanda)
        if accion == "m":                        # uno es mío
            return self._abrir_uno_es_mio(tanda)
        if accion == "c" and valor == "ruido":   # confirmar el resumen de ruido
            return self._confirmar_ruido(tanda)
        if accion == "r" and valor == "ruido":   # rever el resumen de ruido
            return self._abrir_rever(tanda)
        if accion == "v" and valor == "ok":      # listo, lo vi (aviso al toque)
            return self._listo_lo_vi(tanda)
        if accion == "n":                        # no era mío (aviso al toque)
            return self._abrir_no_era_mio(tanda)
        if accion == "i":                        # cliente importante: abre el submenú
            return self._abrir_cliente_importante(tanda, idx)
        if accion == "d":                        # cliente importante: eligió una dirección
            return self._elegir_direccion(tanda, idx, valor)
        if accion == "v" and valor == "0":       # volver del submenú de direcciones
            return self._volver_a_categorias(tanda, idx)
        if accion == "c":                        # eligió una categoría
            return self._elegir_categoria(tanda, idx, valor)
        # No debería quedar ninguna acción sin cubrir a esta altura, pero
        # mejor decirlo que quedarse mudo si algún día aparece una nueva.
        self.enviar("Todavía no sé qué hacer con ese botón.")

    def _numerar(self, mids):
        """La lista numerada de una tanda abierta, para mostrarla de
        vuelta.

        self.abiertos sólo guarda message_id -ver el comentario en
        __init__-, así que de/asunto se reconstruyen desde la base. El
        orden es el mismo con el que se numeró en armar_resumen /
        armar_resumen_de_ruido (mids conserva el orden original incluso
        si el mensaje se recortó por espacio), así que el número N
        siempre selecciona el mismo correo que vio JP, esté o no
        listado en el mensaje que se mandó.
        """
        lineas = []
        for n, mid in enumerate(mids, 1):
            fila = memoria.obtener(self.cx, mid)
            if fila:
                lineas.append(f"  {n}. {html.escape((fila['de'] or '')[:34])}"
                              f" — {html.escape((fila['asunto'] or '')[:44])}")
            else:
                lineas.append(f"  {n}. (no lo encuentro en la base)")
        return "\n".join(lineas)

    def _set_pendiente(self, valor):
        """Único punto de escritura de self.pendiente.

        Además de fijarlo en el objeto, lo persiste
        (memoria.guardar_pendiente) para que un reinicio del proceso a
        mitad de un Rever o de un Uno es mío no le haga perder a JP la
        pregunta que se le hizo -ronda 1, hallazgo Importante 3: sin
        esto, si el proceso se reinicia después de que JP eligió el
        número y antes de que mande el motivo, cuando lo manda cae acá
        sin nada pendiente y el bot le contesta como si no hubiera
        entendido español, cuando en realidad contestó lo que se le
        pidió y el que se olvidó fue el proceso.
        """
        self.pendiente = valor
        memoria.guardar_pendiente(self.cx, valor)

    def _sacar_de_abiertos(self, tanda, mid):
        """Saca un solo correo de una tanda abierta, sin cerrarla entera.

        El resto de la tanda puede seguir esperando -"Leí todo" para lo
        que falta del equipo, u otro Rever para otro número de la misma
        lista de ruido-. Si era el último, borra la tanda: es la
        limpieza que quedó pendiente desde la tarea 9 (self.abiertos
        creciendo sin límite mientras vive el proceso).
        """
        restantes = [m for m in self.abiertos.get(tanda, []) if m != mid]
        if restantes:
            self.abiertos[tanda] = restantes
        else:
            self.abiertos.pop(tanda, None)

    def _cerrar_equipo(self, tanda):
        """"Leí todo": marca leídos y cierra los correos del equipo que
        siguen en esta tanda.

        Toda escritura pasa por puede_escribir() -en seco o pausada, no
        se toca la casilla-, y la situación pasa a "cerrado" igual: JP ya
        dijo que los vio, que es lo que este estado registra, no si el
        \\Seen efectivamente se pudo escribir en el servidor. Un fallo de
        marcar_leido no puede tirar abajo el resto del lote -a
        diferencia de archivar, leído no tiene reintento propio, así que
        no hay nada que perder reintentando desde cero la próxima vez- y
        tampoco puede quedar en silencio: si algo falló, se avisa cuántos.

        Ronda 1, hallazgo Importante 2: cada mid sale de self.abiertos
        recién DESPUÉS de que memoria.cambiar() confirma el "cerrado" -
        antes se sacaba la tanda entera de entrada, así que si cambiar()
        reventaba a mitad de camino la excepción subía con la tanda ya
        vacía: quedaba sin marcar y, si JP volvía a tocar el botón,
        "Leí todo" contestaba "ya pasó" sobre algo que en realidad nunca
        terminó de cerrarse.
        """
        mids = list(self.abiertos.get(tanda) or [])
        fallaron = 0
        for mid in mids:
            if self.puede_escribir():
                fila = memoria.obtener(self.cx, mid)
                try:
                    if fila:
                        correo.marcar_leido(fila)
                except Exception as e:
                    fallaron += 1
                    _registrar("leido", e)
            try:
                memoria.cambiar(self.cx, mid, "cerrado")
            except Exception as e:
                _registrar("cerrar", e)
                continue  # no se saca: un reintento del botón lo retoma
            self._sacar_de_abiertos(tanda, mid)
        if fallaron:
            self.enviar(f"Listo. Ojo: {fallaron} no se pudieron marcar "
                       f"como leídos en la casilla -van a seguir "
                       f"apareciendo sin leer ahí, aunque ya no te los "
                       f"muestre más.")

    def _abrir_uno_es_mio(self, tanda):
        """"Uno es mío": muestra de vuelta la lista numerada del equipo y
        se queda esperando qué número señala JP."""
        mids = self.abiertos.get(tanda) or []
        if not mids:
            return
        self._set_pendiente({"tipo": "uno_es_mio", "tanda": tanda})
        self.enviar(f"{self._numerar(mids)}\n\n¿Cuál es tuyo? Decime el "
                   f"número.")

    def _confirmar_ruido(self, tanda):
        """"Confirmar" del resumen de ruido: nada que corregir, se cierra
        la tanda entera.

        Ronda 1, mismo criterio que _cerrar_equipo: la tanda se saca
        DESPUÉS de que memoria.cambiar_lote() confirma, no antes. Si el
        UPDATE revienta, se queda abierta para que un segundo toque de
        Confirmar la reintente, en vez de decir "ya pasó" sobre algo
        que nunca se cerró.
        """
        mids = self.abiertos.get(tanda) or []
        try:
            memoria.cambiar_lote(self.cx, mids, "cerrado")
        except Exception as e:
            _registrar("confirmar", e)
            self.enviar("No pude cerrar esta tanda del todo. Probá "
                       "tocar Confirmar de nuevo en un rato.")
            return
        self.abiertos.pop(tanda, None)

    def _abrir_rever(self, tanda):
        """"Rever": muestra de vuelta la lista numerada de lo archivado
        hoy y se queda esperando qué número no era ruido -el motivo se
        pide después, una vez que JP elige (ver _responder_numero)."""
        mids = self.abiertos.get(tanda) or []
        if not mids:
            return
        self._set_pendiente({"tipo": "rever_elegir", "tanda": tanda})
        self.enviar(f"{self._numerar(mids)}\n\n¿Cuál no era ruido? Decime "
                   f"el número.")

    def _listo_lo_vi(self, tanda):
        """"Listo, lo vi": no hay nada que escribir en la casilla -el
        correo ya estaba en INBOX y ahí se queda-, sólo cerrar la
        tanda. Mismo cuidado de orden que _confirmar_ruido: se saca
        recién si memoria.cambiar_lote() confirmó.
        """
        mids = self.abiertos.get(tanda) or []
        try:
            memoria.cambiar_lote(self.cx, mids, "cerrado")
        except Exception as e:
            _registrar("listo", e)
            self.enviar("No pude anotarlo. Probá tocar 'Listo, lo vi' "
                       "de nuevo.")
            return
        self.abiertos.pop(tanda, None)

    def _abrir_no_era_mio(self, tanda):
        """"No era mío": la clasificación (TUYO, o el derivado a Enzo o
        Natalia) estaba mal.

        A diferencia de Rever, acá JP mismo elige la categoría correcta
        con botones -no hace falta reclasificar con el modelo, así que
        no hay nada que sacarle al hilo que escucha-, así que reusa el
        mismo teclado de categorías de siempre (bot.teclado) sobre la
        MISMA tanda -no hace falta un token nuevo, sigue siendo el
        mismo correo- y lo resuelve _elegir_categoria().
        """
        mids = self.abiertos.get(tanda) or []
        if not mids:
            return
        self.enviar("¿Qué era en realidad?", bot.teclado(1, tanda))

    def _abrir_cliente_importante(self, tanda, idx):
        """"⭐ Cliente importante": el submenú con las direcciones del
        correo, para marcar cuál (reglas.direcciones_externas /
        bot.teclado_direcciones, ya existían para esto desde la tarea 2
        pero nada los conectaba con la secretaria real)."""
        mids = self.abiertos.get(tanda) or []
        if not mids:
            return
        fila = memoria.obtener(self.cx, mids[0])
        direcciones = reglas.direcciones_externas(fila) if fila else []
        if not direcciones:
            self.enviar("Este correo no tiene direcciones externas para "
                       "marcar.")
            return
        self.enviar("¿Cuál de estos es el cliente importante?",
                   bot.teclado_direcciones(idx, direcciones, tanda))

    def _elegir_direccion(self, tanda, idx, valor):
        """JP eligió una dirección del submenú de "Cliente importante":
        se guarda en el roster y se vuelve al teclado de categorías -
        marcar la dirección no contesta por sí solo qué categoría le
        corresponde a ESTE correo."""
        mids = self.abiertos.get(tanda) or []
        if not mids:
            return
        fila = memoria.obtener(self.cx, mids[0])
        direcciones = reglas.direcciones_externas(fila) if fila else []
        try:
            etiqueta, direccion = direcciones[int(valor)]
        except (ValueError, IndexError):
            return
        try:
            nuevo = reglas.marcar_importante(etiqueta, direccion)
        except Exception as e:
            _registrar("importante", e)
            self.enviar("No pude guardarlo en el roster. Probá de "
                       "nuevo.")
            return
        self.enviar(("⭐ Guardado: " if nuevo else "Ya estaba en la "
                    "lista: ") + html.escape(direccion))
        self.enviar("¿Qué era en realidad?", bot.teclado(idx, tanda))

    def _volver_a_categorias(self, tanda, idx):
        """"← volver" del submenú de direcciones: vuelve al teclado de
        categorías sin marcar nada."""
        mids = self.abiertos.get(tanda) or []
        if not mids:
            return
        self.enviar("¿Qué era en realidad?", bot.teclado(idx, tanda))

    def _elegir_categoria(self, tanda, idx, categoria):
        """JP corrigió con el teclado de categorías -desde "No era mío",
        o directamente desde avisar_para_que_decida cuando nadie pudo
        decidir-.

        Pura escritura de base más, si corresponde, un archivado -
        ninguna de las dos cosas llama al modelo, así que es seguro
        correrlo acá mismo, a diferencia de Rever (ver el hallazgo
        CRÍTICO de la ronda 1)-. El archivado reusa self._archivar(),
        el mismo camino con reintentos que ya usa el ciclo automático,
        en vez de un mover_a() suelto sin esa red de contención.
        """
        mids = self.abiertos.get(tanda) or []
        if not mids:
            return
        mid = mids[0]
        fila = memoria.obtener(self.cx, mid)
        if not fila:
            self.abiertos.pop(tanda, None)
            return
        try:
            memoria.corregir(self.cx, mid, categoria,
                             f"(elegido con el botón "
                             f"'{bot.CATEGORIAS.get(categoria, categoria)}')")
        except Exception as e:
            _registrar("categoria", e)
            self.enviar("No pude anotarlo. Probá de nuevo.")
            return
        if categoria == "RUIDO":
            self._archivar(fila)
        elif categoria in self.CATEGORIAS_ACCIONABLES:
            memoria.cambiar(self.cx, mid, "clasificado")
        else:
            memoria.cambiar(self.cx, mid, "mostrado_sin_clasificar")
        self.abiertos.pop(tanda, None)
        self.enviar(f"Anotado como <b>"
                   f"{html.escape(bot.CATEGORIAS.get(categoria, categoria))}"
                   f"</b>.")

    def responder_pendiente(self, texto):
        """Interpreta lo que JP acaba de escribir (o decir, ya
        transcripto) contra self.pendiente: la pregunta abierta más
        reciente, si hay una.

        Sin nada pendiente, un texto suelto no es un comando ni la
        respuesta a nada -entender preguntas libres es la Etapa 4, que
        todavía no existe-. Contestar "no entiendo" acá sería pobre;
        mentir sobre lo que el sistema sabe hacer hoy sería peor. Se le
        dice la verdad: todavía sólo entiende comandos y botones.
        """
        p = self.pendiente
        if not p:
            return self.enviar(
                "Todavía no sé responder preguntas sueltas -eso es la "
                "Etapa 4, no existe todavía-. Hoy entiendo comandos "
                "(<code>/pausa</code>, <code>/sigo</code>, "
                "<code>/motor</code>, <code>/estado</code>) y los botones "
                "de los resúmenes.")
        if p["tipo"] in ("uno_es_mio", "rever_elegir"):
            return self._responder_numero(texto, p)
        if p["tipo"] == "rever_motivo":
            return self._responder_motivo_rever(texto, p)

    def _responder_numero(self, texto, p):
        """El paso común de "Uno es mío" y "Rever": JP eligió un número de
        la lista que se le mostró.

        Si la tanda se cerró mientras tanto -alguien tocó "Leí todo" u
        otro Rever/Uno es mío ya se comió el último ítem-, no hay nada
        contra qué validar el número: se le avisa en vez de fallar en
        silencio o, peor, aceptar cualquier cosa. Un número fuera de
        rango no borra la pregunta pendiente -JP puede reintentar sin
        tener que tocar el botón de nuevo-.
        """
        tanda = p["tanda"]
        mids = self.abiertos.get(tanda)
        if not mids:
            self._set_pendiente(None)
            return self.enviar("Esa tanda ya se cerró, no puedo tomar esa "
                               "respuesta.")
        crudo = texto.strip()
        if not crudo.isdigit() or not (1 <= int(crudo) <= len(mids)):
            return self.enviar(f"Decime un número del 1 al {len(mids)}.")
        mid = mids[int(crudo) - 1]
        if p["tipo"] == "uno_es_mio":
            self._set_pendiente(None)
            return self._marcar_como_mio(tanda, mid)
        # rever_elegir: falta el motivo, que puede llegar por texto o por
        # audio -bot.transcribir() ya lo convirtió a texto antes de
        # llegar hasta acá, ver atender()-.
        self._set_pendiente({"tipo": "rever_motivo", "tanda": tanda,
                             "message_id": mid})
        return self.enviar("¿Por qué no era ruido? Contame con tus "
                           "palabras -texto o audio-.")

    def _marcar_como_mio(self, tanda, mid):
        """JP corrigió: uno del bloque del equipo es en realidad suyo.

        A diferencia de rever_ruido(), acá no hace falta tocar la
        casilla -DELEGADO nunca se archiva, el correo sigue en INBOX
        donde siempre estuvo- ni volver a preguntarle al modelo: JP ya
        dio la categoría, TUYO, con el propio botón. Se guarda igual
        como corrección (memoria.corregir conserva `categoria`, lo que
        dijo el sistema, para poder medir si aprende) y vuelve a
        "clasificado" para que el PRÓXIMO resumen lo muestre donde
        corresponde -mismo criterio que rever_ruido con las categorías
        accionables, ver _categoria_de_resumen.

        Ronda 1: si la escritura revienta, se avisa y NO se saca de
        abiertos -mismo criterio que _cerrar_equipo/_confirmar_ruido-,
        para que JP pueda reintentar con un "Uno es mío" nuevo en vez
        de quedarse sin respuesta.
        """
        try:
            memoria.corregir(self.cx, mid, "TUYO",
                             "(marcado con el botón 'Uno es mío', sin "
                             "motivo adicional)")
            memoria.cambiar(self.cx, mid, "clasificado")
        except Exception as e:
            _registrar("uno_es_mio", e)
            self.enviar(f"No pude anotarlo ({type(e).__name__}). Probá "
                       f"'Uno es mío' de nuevo en un rato.")
            return
        self._sacar_de_abiertos(tanda, mid)
        self.enviar("Anotado: lo saco del equipo, va a aparecer como tuyo "
                   "en el próximo resumen.")

    def _responder_motivo_rever(self, texto, p):
        """El paso donde JP contesta el motivo de un Rever.

        Ronda 1, hallazgo CRÍTICO: esto YA NO ejecuta rever_ruido() acá
        adentro. Antes lo hacía, síncrono, y rever_ruido() termina en
        clasificador.clasificar() -dos o tres pasadas de red, hasta tres
        minutos con nvidia-, corriendo en ciclo_de_escucha: mientras
        tanto el bot quedaba ciego a cualquier otra cosa -ningún botón,
        ningún /pausa, ninguna otra respuesta-, que es exactamente lo
        que este archivo entero existe para evitar. self.pendiente
        resuelve "no esperar la respuesta de JP", pero eso era sólo la
        mitad del problema: faltaba "no bloquearse procesándola".

        Ahora sólo se anota el motivo (memoria.preparar_rever, que deja
        la fila en "pendiente_de_rever") y se le contesta a JP en el
        acto que se va a hacer -avisando que no es instantáneo, para no
        generar la expectativa de que sí-. ciclo_de_correo, en el otro
        hilo, es quien de verdad llama a rever_ruido()
        (_atender_revers_pendientes), en su próxima vuelta.

        Un motivo vacío -audio que transcribió a nada, mensaje en
        blanco- no alcanza para nada: no hay qué guardar, y se le pide
        que reintente en vez de dejarlo anotado como si hubiera dicho
        algo.
        """
        self._set_pendiente(None)
        mid, tanda = p["message_id"], p["tanda"]
        explicacion = texto.strip()
        if not explicacion:
            return self.enviar("No entendí el motivo. Tocá Rever de "
                               "nuevo si querés reintentar.")
        memoria.preparar_rever(self.cx, mid, explicacion)
        self._sacar_de_abiertos(tanda, mid)
        self.enviar("Listo, lo saco de Ruido y te confirmo en un rato "
                   "-tengo que volver a consultar el modelo, así que no "
                   "es al toque-.")

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
