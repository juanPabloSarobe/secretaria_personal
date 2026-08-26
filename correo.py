#!/usr/bin/env python3
"""Todo lo que habla con la casilla, y todo lo que interpreta un mensaje.

Nadie más abre una conexión IMAP. Las reglas que no se negocian viven acá
adentro:

  - se lee con BODY.PEEK[] para no marcar como leído lo que no lo estaba;

  - nunca se busca por número de secuencia: el número se corre en cuanto
    se borra algo de la carpeta y para entonces apunta a otro correo;

  - y nunca se escribe sobre un mensaje sin haber CONFIRMADO que es el
    que creíamos. Ubicarlo y confirmarlo son dos cosas distintas y acá se
    hacen por separado (_ubicar_en_inbox / _buscar_por_identidad para lo
    primero, _es_el_mismo para lo segundo).

Sobre esto último: durante mucho tiempo el único identificador que se usó
contra el servidor fue el Message-ID, y eso dejaba afuera justo a los
correos que más queremos archivar. `identidad()` inventa un `sha:...`
cuando el mensaje no trae Message-ID —le sirve a la base para reconocer un
correo entre corridas— pero ese hash NO existe del lado del servidor: un
`SEARCH HEADER Message-ID "sha:..."` no encuentra nada nunca, y los
remitentes automáticos, que son los que omiten el Message-ID, no se
archivaban jamás. Son dos identificadores para dos cosas distintas y se
habían mezclado en uno.

El identificador que el servidor sí entiende es el UID, que `traer_nuevos`
ya trae. Los UID no se corren como los números de secuencia: son estables
mientras no cambie el UIDVALIDITY de la carpeta, y por eso se guarda
también ese número junto al UID. Si cambió, los UID viejos no valen y hay
que decirlo (UidsVencidos) en vez de tocar el mensaje equivocado.
"""
import email, email.policy, email.utils, hashlib, html, imaplib, os, re
from datetime import datetime, timedelta, timezone


class OperacionAMedias(Exception):
    """El COPY se confirmó, pero el paso que borra el original no.

    El mensaje quedó duplicado -una copia ya en el destino, otra todavía
    en el origen sin borrar- y desde acá no hay forma de saber si
    conviene reintentar el borrado, dejarlo así, o algo distinto: quien
    llama decide. Por eso esto se levanta como excepción en vez de
    devolver un booleano: ni `True` sirve (mentiría: la operación no
    terminó como se pidió) ni `False` (también mentiría: sí se copió
    algo, no es que no haya pasado nada).
    """


class CopiaRechazada(Exception):
    """El servidor contestó NO al COPY: no se copió nada.

    Es distinta de OperacionAMedias, y la diferencia no es cosmética: acá
    NO hay ninguna copia en el destino, así que reintentar la mudanza
    entera es seguro y es lo correcto. Si este caso reusara
    OperacionAMedias, el reintento llamaría a borrar_el_original() -que
    borra de INBOX sin copiar nada, porque confía en que la copia ya
    existe- y el correo se perdería. Por eso son dos excepciones y no
    una: cada una dice qué quedó hecho, y de eso depende qué es seguro
    reintentar.

    Y es distinta del False que devuelve mover_a cuando el mensaje ya no
    está en INBOX: eso es benigno -alguien lo movió, o ya se archivó- y no
    hay nada para reintentar ni para avisar. Un COPY rechazado (cuota
    agotada en la carpeta destino, carpeta renombrada, permisos) es una
    falla de verdad, y leerla como el caso benigno dejaba el correo sin
    archivar, sin reintento y sin aviso: una falla silenciosa más.
    """


class EscrituraRechazada(Exception):
    """El servidor rechazó una escritura en Ruido antes de que hubiera
    nada copiado a INBOX.

    Es el equivalente de CopiaRechazada para el paso que le precede: en
    `devolver_a_bandeja()` primero se saca el `\\Seen` en Ruido y recién
    después se copia a INBOX. Un NO en ese primer STORE no dejó ninguna
    copia en ningún lado -es exactamente la misma garantía que
    CopiaRechazada documenta para el COPY-, pero no es un COPY el que
    falló, así que reusar esa excepción diría algo que no pasó. La
    consecuencia es la misma: reintentar `devolver_a_bandeja()` entera
    es seguro.

    Y es distinta del False que devuelve cuando el mensaje ya no está en
    Ruido: eso es benigno -nada para reintentar-; esto es una falla real
    del servidor -permisos, un timeout parcial- que hay que reintentar y,
    si no se arregla, contarle a JP. Confundirlas deja el correo sin
    volver a la bandeja, sin reintento y sin aviso.
    """


class IdentidadIncierta(Exception):
    """No se pudo confirmar que el mensaje sea el que creíamos.

    No se tocó nada, y ésa es la única respuesta aceptable: escribir sobre
    un mensaje sin confirmar cuál es puede archivar o borrar el correo de
    otro. Como no se escribió nada, reintentar la operación entera más
    adelante es seguro -y si el problema no se arregla, el tope de
    intentos hace que JP se entere, que es exactamente lo que no pasaba
    cuando esto devolvía False en silencio.
    """


class CorreoPerdido(Exception):
    """No está en INBOX y tampoco en el destino: no está en ningún lado.

    Es la única cosa que este sistema no puede permitirse, así que cuando
    aparece no se sigue como si nada: se levanta, se reintenta, y si
    persiste JP se entera. Un correo duplicado es feo y se arregla con un
    toque; uno perdido no se deshace.

    En condiciones normales es imposible -el borrado del original es
    siempre el último paso, después de que la copia está confirmada- y
    por eso mismo, si pasa, algo que creemos no es cierto: alguien movió
    cosas a mano, o el mensaje no era el que pensábamos.
    """


class UidsVencidos(IdentidadIncierta):
    """El UIDVALIDITY de la carpeta cambió: los UID guardados no valen.

    Un UID identifica un mensaje sólo dentro de un UIDVALIDITY dado. Si el
    servidor lo cambia -la carpeta se borró y se volvió a crear, una
    restauración desde backup, una migración- los UID se reparten de nuevo
    y el 1043 de hoy puede ser cualquier otro correo. Por eso no se
    intenta "arreglarlo solo": se levanta esto, que llega hasta JP por el
    camino de aviso que ya existe. Es raro y es grave; que lo mire una
    persona es más barato que adivinar.
    """


def _motivo(respuesta):
    """El texto con el que el servidor rechazó un comando.

    Va derecho al aviso de Telegram: "no se pudo copiar" no le sirve a
    nadie, y "[OVERQUOTA] mailbox full" o "[TRYCREATE] no existe la
    carpeta" se entienden y se arreglan. imaplib devuelve una lista que
    según el comando trae bytes, strings o None, así que se normaliza
    acá adentro y nunca revienta: esto corre dentro del camino de error,
    donde una excepción de más taparía la que importa.
    """
    try:
        return " ".join(p.decode("utf-8", "replace") if isinstance(p, bytes)
                        else str(p)
                        for p in (respuesta or []) if p)[:200]
    except Exception:
        return ""


def abrir_buzon(readonly=True):
    """Una conexión a la casilla, ya autenticada y con INBOX seleccionado.

    readonly=True no es un detalle: con readonly=False un FETCH marca como
    leídos los mensajes que no lo estaban, y eso no se puede deshacer
    cómodamente en la casilla real de JP.
    """
    M = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"],
                          int(os.environ["IMAP_PORT"]), timeout=40)
    M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    seleccionar(M, "INBOX", readonly)
    return M


# Cómo nombrar cada tipo de archivo en el mensaje. Lo que no está acá se
# muestra con el subtipo MIME en mayúsculas, que es feo pero informativo.
TIPOS_ADJUNTO = {
    "application/pdf": "PDF",
    "image/jpeg": "imagen JPG", "image/png": "imagen PNG",
    "image/heic": "imagen HEIC", "image/tiff": "imagen TIFF",
    "application/vnd.ms-excel": "planilla Excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "planilla Excel",
    "application/msword": "documento Word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "documento Word",
    "text/xml": "XML", "application/xml": "XML",
    "application/zip": "ZIP", "text/csv": "CSV",
    "message/rfc822": "correo reenviado",
}


def adjuntos(msg):
    """Qué vino adjunto: nombre, tipo y tamaño. El contenido no se abre.

    Un correo escaneado desde el celular no tiene cuerpo —el de Genius Scan
    trae solo su propia publicidad— y todo lo que importa está en el PDF. Sin
    esta lista llega como un correo vacío y no hay nada que clasificar.
    """
    fuera = []
    for parte in msg.walk():
        if parte.get_content_maintype() == "multipart":
            continue
        nombre = parte.get_filename()
        if not nombre:
            continue
        # Content-ID significa que el cuerpo HTML referencia esa parte con
        # cid:… — o sea que va incrustada, no adjunta. Es lo que distingue el
        # logo de la firma de Outlook (image001.jpg, sin Content-Disposition)
        # del remito escaneado (disposition attachment, sin Content-ID). Por
        # el nombre o el tamaño no se distinguen: hay logos de 25 KB.
        if parte.get("Content-ID") or parte.get_content_disposition() == "inline":
            continue
        try:
            bytes_ = len(parte.get_payload(decode=True) or b"")
        except Exception:
            bytes_ = 0
        fuera.append({"nombre": str(nombre)[:120],
                      "tipo": parte.get_content_type(),
                      "kb": round(bytes_ / 1024)})
    return fuera[:10]


def adjuntos_legibles(lista):
    """Una línea por adjunto, o cadena vacía si no hay ninguno."""
    if not lista:
        return ""
    return "\n".join(
        f"📎 {a['nombre']} — "
        f"{TIPOS_ADJUNTO.get(a['tipo'], a['tipo'].split('/')[-1].upper()[:18])}"
        f", {a['kb']} KB" for a in lista)


#: En qué orden se busca el cuerpo, de mejor a peor. text/calendar va
#: último porque es un formato de máquina, pero tiene el SUMMARY y la
#: descripción de la reunión: sin él, una invitación queda sin una sola
#: palabra que clasificar.
TIPOS_DE_CUERPO = ("text/plain", "text/html", "text/calendar")


def _contenido(parte):
    """El texto de una parte, o "" si no se puede leer."""
    try:
        return parte.get_content() or ""
    except Exception:
        return ""


def texto_plano(msg):
    """Devuelve el cuerpo legible del mensaje.

    Recorre TIPOS_DE_CUERPO en orden y se queda con la primera parte que
    tenga texto de verdad. Lo de "de verdad" no es un detalle: Outlook y
    medio sistema de envío mandan un text/plain de dos caracteres
    ("\r\n") al lado del text/html con el correo entero. La versión
    anterior tomaba ese text/plain y cortaba con `break`; como "\r\n" es
    truthy, el `if not cuerpo` que iba a buscar el HTML nunca corría, y
    se tiraban 26 KB de correo por dos caracteres de relleno.

    Medido sobre los 141 correos del atraso de agosto: 13 quedaron sin
    cuerpo, cuatro de ellos TUYO y uno DUDA -- una cédula de embargo y
    una invitación de ORBCOMM entre ellos. El clasificador los decidió
    con el remitente y el asunto nada más.
    """
    cuerpo = ""
    if msg.is_multipart():
        for tipo in TIPOS_DE_CUERPO:
            for parte in msg.walk():
                if parte.get_content_type() != tipo:
                    continue
                if "attachment" in str(parte.get("Content-Disposition", "")):
                    continue
                texto = _contenido(parte)
                if texto.strip():          # con texto de verdad, no relleno
                    cuerpo = texto
                    break
            if cuerpo:
                break
    else:
        cuerpo = _contenido(msg)
    cuerpo = re.sub(r"<[^>]+>", " ", cuerpo)          # sacar etiquetas HTML
    cuerpo = html.unescape(cuerpo)
    cuerpo = re.sub(r"[ \t]+", " ", cuerpo)
    cuerpo = re.sub(r"\n\s*\n+", "\n", cuerpo)
    return cuerpo.strip()


def identidad(c):
    """Identificador estable de un correo, del lado NUESTRO.

    El Message-ID es lo correcto, pero no todos los remitentes automáticos lo
    mandan. Sin una reserva, esos correos se preguntan una y otra vez en cada
    tanda. El resumen de remitente + asunto + fecha alcanza para reconocerlos.

    Ojo con qué es y qué no es esto: sirve para reconocer un correo entre
    corridas -es la clave de la base- y para CONFIRMAR que un mensaje del
    servidor es el que creíamos, porque se puede recalcular sobre las
    cabeceras que devuelve un FETCH. Lo que no es, es algo que el servidor
    entienda: un `sha:...` no se puede buscar. Para ubicar un mensaje están
    el UID y _buscar_por_identidad().
    """
    mid = (c.get("message_id") or "").strip()
    if mid:
        return mid
    semilla = f"{c.get('de','')}|{c.get('asunto','')}|{c.get('fecha','')}"
    return "sha:" + hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:32]


def _resumen(msg):
    """Los campos de un mensaje tal como los guarda la secretaria.

    Un solo lugar para esto, y no por prolijidad: `identidad()` se
    recalcula sobre lo que devuelve un FETCH de cabeceras para confirmar
    que el mensaje es el que creíamos, y si el recorte o el valor por
    defecto de alguno de estos campos fuera distinto del que se usó al
    guardarlo, el hash daría distinto y ningún correo sin Message-ID se
    podría confirmar nunca. Tienen que ser exactamente los mismos.
    """
    return {"de": str(msg.get("From", ""))[:200],
            "para": str(msg.get("To", ""))[:300],
            "cc": str(msg.get("Cc", ""))[:300],
            "asunto": str(msg.get("Subject", "(sin asunto)"))[:200],
            "fecha": str(msg.get("Date", "")),
            "message_id": str(msg.get("Message-ID", ""))}


MESES_IMAP = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def traer_correos(n, desde=None):
    """Los últimos n correos, o todos los recibidos desde una fecha.

    `desde` es un date. IMAP compara por día, no por hora, así que SINCE con
    la fecha de hoy devuelve exactamente los de hoy.
    """
    M = abrir_buzon()
    if desde:
        criterio = f"{desde.day:02d}-{MESES_IMAP[desde.month - 1]}-{desde.year}"
        typ, data = M.search(None, "SINCE", criterio)
        ids = data[0].split()
    else:
        typ, data = M.search(None, "ALL")
        ids = data[0].split()[-n:]
    correos = []
    for i in reversed(ids):
        # BODY.PEEK en vez de RFC822: no marca el correo como leído
        typ, d = M.fetch(i, "(BODY.PEEK[])")
        msg = email.message_from_bytes(d[0][1], policy=email.policy.default)
        # Ojo: acá `i` es un NÚMERO DE SECUENCIA (viene de M.search, no de
        # M.uid("SEARCH")), no un UID. Se guarda por compatibilidad con
        # los simulacros viejos, pero no sirve para ubicar nada después:
        # los correos que se archivan salen de traer_nuevos().
        correos.append(dict(
            _resumen(msg),
            uid=i.decode(),
            cuerpo=texto_plano(msg)[:4000],
            adjuntos=adjuntos(msg),
        ))
    M.logout()
    return correos


def completar_adjuntos(correos):
    """Rellena los adjuntos de correos guardados antes de que se listaran.

    Los barridos hechos hasta hoy no los tienen. Se buscan por Message-ID y no
    por número de secuencia: el número cambia cuando se archiva o se borra
    algo, y para entonces apunta a otro correo.
    """
    faltan = [c for c in correos if not c.get("adjuntos") and c.get("message_id")
              and "adjuntos" not in c]
    if not faltan:
        return correos
    print(f"Releyendo {len(faltan)} correos para ver qué traían adjunto…", flush=True)
    M = abrir_buzon()
    con, sin = 0, 0
    for c in faltan:
        c["adjuntos"] = []
        try:
            typ, d = M.uid("SEARCH", None, "HEADER", "Message-ID",
                           f'"{c["message_id"]}"')
            uids = d[0].split()
            if not uids:
                sin += 1
                continue
            typ, d = M.uid("FETCH", uids[-1], "(BODY.PEEK[])")
            msg = email.message_from_bytes(d[0][1], policy=email.policy.default)
            c["adjuntos"] = adjuntos(msg)
            con += 1 if c["adjuntos"] else 0
        except Exception as e:
            sin += 1
    M.logout()
    print(f"  {con} con adjuntos, {len(faltan) - con - sin} sin, "
          f"{sin} que ya no están en la bandeja", flush=True)
    return correos


CABECERAS_DE_IDENTIDAD = ("(BODY.PEEK[HEADER.FIELDS"
                          " (MESSAGE-ID FROM SUBJECT DATE)])")

# Cuántos candidatos de una búsqueda se confirman de a uno antes de
# rendirse. Cada uno cuesta un FETCH de cabeceras, así que el número
# acota el trabajo; lo que NO hace es acotarlo en silencio -pasado el
# tope se levanta IdentidadIncierta, que reintenta y termina avisándole a
# JP-. Doscientos es holgado para el criterio que se usa (un remitente,
# una ventana de tres días) y sigue siendo un par de segundos en el peor
# caso.
TOPE_CANDIDATOS = 200


def uidvalidity(M):
    """El UIDVALIDITY de la carpeta seleccionada, o None si no se sabe.

    Se lee de la respuesta del SELECT, que el RFC obliga a mandar, y se
    guarda en la conexión. Lo de guardarlo no es una optimización:
    `imaplib.response()` CONSUME la respuesta -la segunda llamada
    devuelve None-, así que leerlo dos veces dejaba la comparación en "no
    puedo comparar" sin que nada lo dijera. Probado contra la casilla
    real: la primera lectura da 603289753 y la segunda, None.

    Cuando de verdad no se sabe se devuelve None, y quien llama decide:
    eso significa "no puedo comparar", no "está todo bien".
    """
    guardado = getattr(M, "_validez", None)
    if guardado:
        return guardado
    try:
        typ, d = M.response("UIDVALIDITY")
        for parte in (d or []):
            if parte:
                valor = (parte.decode() if isinstance(parte, bytes)
                         else str(parte)).strip()
                M._validez = valor
                return valor
    except Exception:
        pass
    return None


def seleccionar(M, carpeta, readonly):
    """Selecciona una carpeta y se queda con su UIDVALIDITY.

    Todo select pasa por acá: el UIDVALIDITY es por carpeta, así que la
    referencia vieja hay que tirarla en el momento de cambiar, no
    después.
    """
    M._validez = None
    M.select(carpeta, readonly=readonly)
    return uidvalidity(M)


def _como_bytes(uid):
    if not uid:
        return None
    return uid if isinstance(uid, bytes) else str(uid).strip().encode()


def _es_el_mismo(M, uid, esperada):
    """¿El mensaje que hoy tiene ese UID es el que creemos?

    Devuelve True (es ése), False (hay un mensaje pero es OTRO) o None (no
    hay ningún mensaje con ese UID). Los tres casos son distintos y hacen
    falta los tres: "es otro" obliga a no escribir, "no está" es benigno.
    Un cuarto caso -el servidor rechazó el FETCH- no es ninguno de esos y
    sale por IdentidadIncierta: no se pudo averiguar.

    Se compara recalculando `identidad()` sobre las cabeceras que devuelve
    el servidor, no sólo el Message-ID: así también se confirman los
    correos que no traen Message-ID, que son justamente los que no se
    podían ni ubicar.
    """
    typ, d = M.uid("FETCH", uid, CABECERAS_DE_IDENTIDAD)
    if typ != "OK":
        # El servidor rechazó el FETCH. Eso NO es "no hay ningún mensaje
        # con ese UID": es que no se pudo averiguar, y las dos cosas
        # tienen respuestas opuestas -una es benigna y la otra obliga a
        # no escribir y a reintentar-. Confundirlas es la misma falla que
        # se cerró en la ronda 5 para el COPY, mudada al lado de la
        # lectura.
        raise IdentidadIncierta(
            f"{esperada}: el servidor rechazó leer las cabeceras del UID"
            f" {uid!r} -- {_motivo(d)}")
    for parte in (d or []):
        if isinstance(parte, tuple) and len(parte) > 1 and parte[1]:
            msg = email.message_from_bytes(parte[1],
                                           policy=email.policy.default)
            return identidad(_resumen(msg)) == esperada
    return None


def _criterio_de_busqueda(c):
    """Con qué se le pregunta al servidor por un correo sin Message-ID.

    El SEARCH acota (remitente y ventana de fechas) y después cada
    candidato se confirma cabecera por cabecera: el criterio puede traer
    de más, nunca decide solo. Lo que no puede es traer de menos, así que
    la ventana va con un día para cada lado -las fechas del Date y las que
    usa el servidor no siempre están en el mismo huso.
    """
    partes = []
    direccion = email.utils.parseaddr(c.get("de") or "")[1]
    if direccion and direccion.isascii():
        partes += ["FROM", f'"{direccion}"']
    try:
        d = email.utils.parsedate_to_datetime(c.get("fecha") or "")
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        d = d.astimezone(timezone.utc)
        desde, hasta = d - timedelta(days=1), d + timedelta(days=2)
        partes += ["SINCE", f"{desde.day:02d}-{MESES_IMAP[desde.month - 1]}"
                            f"-{desde.year}",
                   "BEFORE", f"{hasta.day:02d}-{MESES_IMAP[hasta.month - 1]}"
                             f"-{hasta.year}"]
    except Exception:
        pass
    return partes


def _buscar_por_identidad(M, c):
    """Ubica el mensaje en la carpeta seleccionada, confirmándolo.

    Es la forma de ubicar un correo cuando el UID no sirve: en otra
    carpeta (el UID de INBOX no significa nada en INBOX.Ruido) o cuando el
    UID guardado ya no apunta a lo que apuntaba.

    El SEARCH sólo ACOTA; quien decide es la confirmación cabecera por
    cabecera, para los dos criterios. Con el Message-ID también, y esto
    último no es prolijidad: `SEARCH HEADER` compara por SUBCADENA (RFC
    3501, "the string ... matches ... a substring of the header text"),
    así que un Message-ID sin ángulos -"abc@def"- lo matchea el
    "<xabc@def>" de otro correo. Devolver ese candidato sin confirmarlo
    hacía que esta_en() dijera que la copia ya estaba en Ruido, que
    mover_a saltara el COPY y que borrara el original: el correo
    desaparecía de las dos carpetas y la base lo anotaba archivado. Era el
    único camino de escritura sin confirmar que quedaba.

    Devuelve None sólo cuando el servidor contestó bien y el mensaje no
    está. Todo lo demás -búsqueda rechazada, o más candidatos de los que
    se pueden confirmar- sale por IdentidadIncierta: no encontrarlo y no
    poder buscarlo se parecen mucho y significan cosas opuestas.
    """
    esperada = identidad(c)
    if not esperada.startswith("sha:"):
        criterio = ["HEADER", "Message-ID", f'"{esperada}"']
    else:
        criterio = _criterio_de_busqueda(c)
        if not criterio:
            # Ni Message-ID, ni remitente utilizable, ni fecha legible: no
            # hay forma de preguntar por este correo sin recorrer la
            # carpeta entera, y adivinar no es una opción cuando el paso
            # siguiente borra algo.
            raise IdentidadIncierta(
                f"{esperada}: sin Message-ID, sin remitente y sin fecha"
                " utilizables, no hay cómo ubicar el mensaje en el servidor")

    typ, d = M.uid("SEARCH", None, *criterio)
    if typ != "OK":
        raise IdentidadIncierta(
            f"{esperada}: el servidor rechazó la búsqueda -- {_motivo(d)}")
    uids = d[0].split() if (d and d[0]) else []
    if not uids:
        # El servidor contestó bien y no hay ninguno. Con Message-ID esto
        # es concluyente incluso siendo por subcadena: si el nuestro
        # estuviera, su cabecera contendría la cadena y habría venido en
        # la lista.
        return None
    if len(uids) > TOPE_CANDIDATOS:
        # Antes se miraban los primeros 60 y el resto se descartaba en
        # silencio: con más de 60 correos del mismo remitente automático
        # en la ventana -que es exactamente el perfil de lo que
        # archivamos- el nuestro quedaba afuera y la respuesta era "no
        # está". Descartar en silencio es lo que venimos cerrando hace
        # siete rondas; si son demasiados para confirmarlos, se dice.
        raise IdentidadIncierta(
            f"{esperada}: la búsqueda devolvió {len(uids)} candidatos, más"
            f" de los {TOPE_CANDIDATOS} que se pueden confirmar de a uno;"
            " no se toca ninguno")
    # De atrás para adelante: el más nuevo primero, que es el que casi
    # siempre buscamos.
    for uid in reversed(uids):
        if _es_el_mismo(M, uid, esperada):
            return uid
    return None


def _ubicar_en_inbox(M, c):
    """El UID del correo en INBOX, confirmado, o None si ya no está.

    Primero el UID guardado, que es el identificador que el servidor
    entiende y el único que existe para los correos sin Message-ID. Antes
    de usarlo se chequea el UIDVALIDITY de la carpeta contra el que se
    guardó junto al UID: si cambió, ese número puede ser hoy cualquier
    otro correo y se levanta UidsVencidos en vez de escribir a ciegas. Y
    aun cuando coincida, se confirma leyendo las cabeceras: comparar
    números no alcanza para saber que el mensaje es el nuestro.

    Si el UID no sirve -no lo hay, o quedó apuntando a otra cosa- se cae a
    buscarlo por identidad, que confirma igual. Ese camino es el que
    permite que un correo siga siendo archivable después de que alguien lo
    mueva a mano y vuelva.
    """
    esperada = identidad(c)
    uid = _como_bytes(c.get("uid"))
    guardado = str(c.get("uidvalidity") or "").strip()
    if uid:
        actual = uidvalidity(M)
        if guardado and actual and guardado != actual:
            raise UidsVencidos(
                f"{esperada}: el UIDVALIDITY de INBOX pasó de {guardado} a"
                f" {actual}; los UID guardados ya no valen y no se toca"
                " ningún mensaje hasta que alguien mire qué pasó")
        if _es_el_mismo(M, uid, esperada):
            return uid
    return _buscar_por_identidad(M, c)


def _sacar_de_inbox(M, uid, quien, carpeta):
    """STORE +Deleted y UID EXPUNGE, con los dos `typ` chequeados.

    Corre siempre DESPUÉS de que la copia en `carpeta` está confirmada,
    así que un NO en cualquiera de los dos deja el mensaje duplicado y no
    se puede reportar ni como éxito ni como "no pasó nada".
    """
    typ, _ = M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
    if typ != "OK":
        raise OperacionAMedias(
            f"{quien}: está copiado en {carpeta} pero no se pudo marcar"
            " para borrar en INBOX -- quedó duplicado")
    typ, _ = M.uid("EXPUNGE", uid)
    if typ != "OK":
        raise OperacionAMedias(
            f"{quien}: está copiado en {carpeta} y marcado para borrar,"
            " pero el EXPUNGE no se confirmó -- sigue duplicado en INBOX")
    return True


def esta_en(M, carpeta, c):
    """¿El correo ya existe en esa carpeta? Deja INBOX seleccionado.

    Se pregunta antes de copiar. Un COPY que sale bien y una conexión que
    se corta antes de que llegue el OK son indistinguibles desde acá: el
    cliente ve un OSError igual que si no hubiera pasado nada, y el
    reintento vuelve a copiar. Medido: una sola caída de red en ese punto
    dejaba dos copias en Ruido, en silencio y sin que el estado final
    dijera nada raro. Preguntar es la única forma de saberlo que no
    depende de adivinar por el tipo de excepción.

    La carpeta destino se abre readonly: acá sólo se mira.
    """
    try:
        seleccionar(M, carpeta, readonly=True)
        return _buscar_por_identidad(M, c) is not None
    finally:
        seleccionar(M, "INBOX", readonly=False)


def mover_a(c, carpeta):
    """Copia el correo a `carpeta` y lo borra de INBOX.

    Recibe el correo entero, no un Message-ID: hace falta el UID (y el
    UIDVALIDITY con el que se lo leyó) para poder ubicar también los
    correos que no traen Message-ID, que son la mayoría del ruido. Ver
    _ubicar_en_inbox.

    Devuelve True si terminó, y False SOLO en el caso benigno: el mensaje
    ya no está en INBOX. Todo lo que sí es una falla sale por una
    excepción -CopiaRechazada si el servidor no dejó copiar,
    OperacionAMedias si copió pero no pudo borrar el original,
    IdentidadIncierta si no se pudo confirmar cuál es el mensaje- porque
    un booleano de dos valores no alcanza para tantos finales distintos, y
    los que se caían por la rendija eran justo los que había que
    reintentar.

    COPY + UID EXPUNGE porque el servidor no tiene MOVE pero sí UIDPLUS.
    Nunca EXPUNGE a secas: eso borraría otros mensajes marcados.

    El `typ` de los tres comandos se chequea a propósito: `imaplib` sólo
    levanta excepción si el servidor contesta BAD. Una respuesta NO
    -carpeta que no existe, cuota superada, un error transitorio- vuelve
    en silencio.
    """
    quien = identidad(c)
    M = abrir_buzon(readonly=False)
    try:
        uid = _ubicar_en_inbox(M, c)
        if not uid:
            # Ya no está en INBOX. Si la copia está en el destino, la
            # mudanza terminó -en un intento anterior cuya respuesta se
            # perdió, o a mano- y decir False haría que la base lo anote
            # como "clasificado", o sea sin archivar, cuando está
            # archivado. Si no está en ningún lado, ahí sí no hay nada
            # que hacer.
            return esta_en(M, carpeta, c)
        if esta_en(M, carpeta, c):
            # Ya hay una copia allá: copiar de nuevo dejaría dos. Lo único
            # que falta es sacar el original de INBOX, que es la misma
            # mitad que reintenta borrar_el_original(). El UID sigue
            # sirviendo: esta_en() deja INBOX seleccionado y el
            # UIDVALIDITY ya se chequeó al ubicarlo.
            return _sacar_de_inbox(M, uid, quien, carpeta)
        typ, respuesta = M.uid("COPY", uid, carpeta)
        if typ != "OK":
            # Nada se copió: no hay nada que deshacer, es seguro
            # reintentar la mudanza entera más adelante. Pero tampoco es
            # un False, que acá significa "el mensaje no estaba" y quien
            # llama lee como "listo, nada que hacer": esto es una falla
            # del servidor -cuota, carpeta, permisos- que hay que
            # reintentar y, si no se arregla, contarle a JP.
            raise CopiaRechazada(
                f"{quien}: el servidor rechazó copiar a {carpeta}"
                f" -- {_motivo(respuesta)}")
        return _sacar_de_inbox(M, uid, quien, carpeta)
    finally:
        M.logout()


def borrar_el_original(c, carpeta="INBOX.Ruido"):
    """Saca de INBOX un correo que YA está copiado en otra carpeta.

    Es la mitad que falta cuando mover_a() levanta OperacionAMedias: el
    COPY se confirmó pero el STORE +Deleted o el UID EXPUNGE no, así que
    el mensaje está en las dos carpetas a la vez. Reintentar mover_a()
    entero en ese estado vuelve a copiar y deja un duplicado NUEVO en el
    destino por cada vuelta del ciclo -con una falla sostenida del lado
    del borrado (cuota agotada en esa carpeta, por ejemplo) eso acumula
    varias copias por hora. Esta función no hace COPY nunca: por eso
    existe separada en vez de un parámetro de mover_a().

    Devuelve True si borró, y False si el original ya no está en INBOX
    -el EXPUNGE anterior sí había salido y la respuesta se perdió, o JP
    lo borró a mano-. Ese False dice "la mudanza terminó", y quien llama
    lo usa para marcar el correo como archivado, así que antes de
    devolverlo se CONFIRMA que la copia esté en `carpeta`: si no está en
    ninguna de las dos, el correo no está en ningún lado y eso se levanta
    como CorreoPerdido en vez de anotarse como resuelto.

    Si el STORE o el EXPUNGE vuelven NO, sigue a medias y levanta
    OperacionAMedias, igual que mover_a: el estado no cambió y el
    reintento es el mismo.
    """
    quien = identidad(c)
    M = abrir_buzon(readonly=False)
    try:
        uid = _ubicar_en_inbox(M, c)
        if not uid:
            if esta_en(M, carpeta, c):
                return False
            raise CorreoPerdido(
                f"{quien}: no está en INBOX y tampoco en {carpeta}")
        return _sacar_de_inbox(M, uid, quien, carpeta)
    finally:
        M.logout()


def marcar_leido(c):
    """Marca un correo como leído sin tocar ninguna otra cosa."""
    M = abrir_buzon(readonly=False)
    try:
        uid = _ubicar_en_inbox(M, c)
        if not uid:
            return False
        typ, _ = M.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        return typ == "OK"
    finally:
        M.logout()


def devolver_a_bandeja(c):
    """Saca un correo de INBOX.Ruido y lo deja en INBOX SIN LEER.

    Sin leer a propósito: si JP dice que no era ruido, tiene que
    encontrarlo como encontraría cualquier correo que no vio.

    El orden importa. El \\Seen se saca ANTES del COPY -no después- porque
    COPY duplica los flags tal como están en ese momento: si se sacara
    después, un corte entre el EXPUNGE de Ruido y ese último STORE dejaba
    la copia en INBOX marcada como leída para siempre, sin ningún rastro
    de que hacía falta corregirla (la versión anterior tenía justo ese
    problema). Y el borrado de Ruido queda como último paso: si el
    proceso se corta en cualquier punto anterior, el mensaje sigue
    existiendo en Ruido y un reintento repite la operación desde el
    principio -en el peor caso deja un duplicado sin leer en INBOX y el
    original todavía en Ruido, nunca un correo perdido o mal marcado.

    Devuelve True si terminó, y False SOLO en el caso benigno: el mensaje
    ya no está en Ruido. Todo lo que sí es una falla sale por una
    excepción -EscrituraRechazada si el servidor rechazó sacar el
    `\\Seen` antes de copiar nada, CopiaRechazada si rechazó el COPY a
    INBOX, OperacionAMedias si copió pero no pudo borrar el original en
    Ruido- porque un booleano de dos valores confundía "no había nada
    que hacer" con "el servidor dijo que no", que necesitan respuestas
    opuestas: la primera no se reintenta, la segunda sí y, si persiste,
    JP se entera. Mismo criterio que `mover_a()`.

    Los CUATRO comandos de escritura se chequean, no sólo el COPY: los
    dos primeros (STORE -Seen, COPY) todavía no cambiaron nada del lado
    de Ruido si fallan, así que un NO ahí no dejó ningún duplicado y es
    seguro reintentar la operación entera desde cero -pero eso no es lo
    mismo que "no había nada que hacer", así que sale por excepción y no
    por False. Los dos últimos (STORE +Deleted, EXPUNGE) corren DESPUÉS
    de que el COPY ya confirmó una copia nueva en INBOX: un NO en
    cualquiera de esos dos deja un duplicado -mismo caso que en
    mover_a- y levanta OperacionAMedias en vez de mentir con un booleano.

    Acá el mensaje se ubica SIEMPRE por identidad y nunca por el UID
    guardado: ese UID es el que tenía en INBOX, y en INBOX.Ruido el mismo
    número es otro correo. Es la razón por la que ubicar en INBOX y ubicar
    en otra carpeta son dos funciones distintas y no un parámetro.
    """
    quien = identidad(c)
    M = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"],
                          int(os.environ["IMAP_PORT"]), timeout=40)
    M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    try:
        seleccionar(M, "INBOX.Ruido", readonly=False)
        uid = _buscar_por_identidad(M, c)
        if not uid:
            return False
        typ, respuesta = M.uid("STORE", uid, "-FLAGS", "(\\Seen)")
        if typ != "OK":
            # Nada se copió todavía: es seguro reintentar desde cero,
            # pero no es "no había nada que hacer" -eso confundía un
            # rechazo real del servidor con el caso benigno.
            raise EscrituraRechazada(
                f"{quien}: el servidor rechazó sacar el \\Seen en Ruido"
                f" -- {_motivo(respuesta)}")
        typ, respuesta = M.uid("COPY", uid, "INBOX")
        if typ != "OK":
            # Nada se copió: mismo caso que CopiaRechazada en mover_a,
            # con la carpeta de destino invertida.
            raise CopiaRechazada(
                f"{quien}: el servidor rechazó copiar a INBOX"
                f" -- {_motivo(respuesta)}")
        typ, _ = M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise OperacionAMedias(
                f"{quien}: se copió a INBOX (ya sin \\Seen) pero no"
                " se pudo marcar para borrar en Ruido -- quedó duplicado")
        typ, _ = M.uid("EXPUNGE", uid)
        if typ != "OK":
            raise OperacionAMedias(
                f"{quien}: se copió a INBOX y se marcó para borrar"
                " en Ruido, pero el EXPUNGE no se confirmó -- puede"
                " haber quedado duplicado")
        return True
    finally:
        M.logout()


def traer_nuevos(desde_fecha):
    """Los correos recibidos desde una fecha, con marca de si vienen leídos.

    `ya_leido` importa: si JP lo abrió del celular antes de que la
    secretaria lo mire, no hay que interrumpirlo con algo que ya vio.
    """
    M = abrir_buzon(readonly=True)
    try:
        validez = uidvalidity(M)
        criterio = (f"{desde_fecha.day:02d}-{MESES_IMAP[desde_fecha.month - 1]}"
                    f"-{desde_fecha.year}")
        typ, d = M.uid("SEARCH", None, "SINCE", criterio)
        salida = []
        for uid in d[0].split():
            typ, dd = M.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
            crudo = dd[0][1]
            banderas = str(dd[0][0])
            msg = email.message_from_bytes(crudo, policy=email.policy.default)
            salida.append(dict(
                _resumen(msg),
                uid=uid.decode(),
                # El UID solo no alcanza: vale mientras el UIDVALIDITY de
                # la carpeta sea éste. Se guardan juntos porque juntos son
                # una referencia y por separado son un número suelto.
                uidvalidity=validez,
                cuerpo=texto_plano(msg)[:4000],
                adjuntos=adjuntos(msg),
                ya_leido="\\Seen" in banderas,
            ))
        return salida
    finally:
        M.logout()


DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic"]


def fecha_legible(cabecera):
    """Fecha del correo en criollo, con la antigüedad al lado.

    Sin esto no se distingue un correo de hoy de uno de la semana pasada, ni
    un reenvío de un pedido nuevo: JP se topó con un correo del jueves anterior
    sin ninguna forma de saberlo.
    """
    try:
        d = email.utils.parsedate_to_datetime(cabecera)
    except Exception:
        return cabecera[:30] if cabecera else "(sin fecha)"
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)

    dias = (datetime.now(timezone.utc) - d).days
    if dias <= 0:
        antiguedad = "hoy"
    elif dias == 1:
        antiguedad = "ayer"
    elif dias < 7:
        antiguedad = f"hace {dias} días"
    else:
        antiguedad = f"hace {dias // 7} semana{'s' if dias >= 14 else ''}"

    return (f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]} "
            f"{d.strftime('%H:%M')} · {antiguedad}")
