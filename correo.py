#!/usr/bin/env python3
"""Todo lo que habla con la casilla, y todo lo que interpreta un mensaje.

Nadie más abre una conexión IMAP. Las dos reglas que no se negocian viven
acá adentro: se lee con BODY.PEEK[] para no marcar como leído lo que no lo
estaba, y se busca por Message-ID y nunca por número de secuencia, porque
el número cambia en cuanto se archiva algo.
"""
import email, email.policy, email.utils, hashlib, html, imaplib, os, re
from datetime import datetime, timezone


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
    M.select("INBOX", readonly=readonly)
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


def texto_plano(msg):
    """Devuelve el cuerpo legible del mensaje."""
    cuerpo = ""
    if msg.is_multipart():
        for parte in msg.walk():
            if parte.get_content_type() == "text/plain" and \
               "attachment" not in str(parte.get("Content-Disposition", "")):
                try:
                    cuerpo = parte.get_content(); break
                except Exception:
                    pass
        if not cuerpo:
            for parte in msg.walk():
                if parte.get_content_type() == "text/html":
                    try:
                        cuerpo = parte.get_content(); break
                    except Exception:
                        pass
    else:
        try:
            cuerpo = msg.get_content()
        except Exception:
            cuerpo = ""
    cuerpo = re.sub(r"<[^>]+>", " ", cuerpo)          # sacar etiquetas HTML
    cuerpo = html.unescape(cuerpo)
    cuerpo = re.sub(r"[ \t]+", " ", cuerpo)
    cuerpo = re.sub(r"\n\s*\n+", "\n", cuerpo)
    return cuerpo.strip()


def identidad(c):
    """Identificador estable de un correo.

    El Message-ID es lo correcto, pero no todos los remitentes automáticos lo
    mandan. Sin una reserva, esos correos se preguntan una y otra vez en cada
    tanda. El resumen de remitente + asunto + fecha alcanza para reconocerlos.
    """
    mid = (c.get("message_id") or "").strip()
    if mid:
        return mid
    semilla = f"{c.get('de','')}|{c.get('asunto','')}|{c.get('fecha','')}"
    return "sha:" + hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:32]


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
        correos.append({
            "uid": i.decode(),
            "de": str(msg.get("From", ""))[:200],
            "para": str(msg.get("To", ""))[:300],
            "cc": str(msg.get("Cc", ""))[:300],
            "asunto": str(msg.get("Subject", "(sin asunto)"))[:200],
            "fecha": str(msg.get("Date", "")),
            "message_id": str(msg.get("Message-ID", "")),
            "cuerpo": texto_plano(msg)[:4000],
            "adjuntos": adjuntos(msg),
        })
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


def _uid_de(M, message_id):
    """El UID actual de un mensaje, buscado por Message-ID.

    Nunca por número de secuencia: el número cambia en cuanto se archiva o
    se borra algo, y para entonces apunta a otro correo.
    """
    typ, d = M.uid("SEARCH", None, "HEADER", "Message-ID", f'"{message_id}"')
    uids = d[0].split()
    return uids[-1] if uids else None


def mover_a(message_id, carpeta):
    """Copia el mensaje a `carpeta` y lo borra de INBOX.

    Devuelve True si terminó, y False SOLO en el caso benigno: el mensaje
    ya no está en INBOX. Todo lo que sí es una falla sale por una
    excepción -CopiaRechazada si el servidor no dejó copiar,
    OperacionAMedias si copió pero no pudo borrar el original- porque un
    booleano de dos valores no alcanza para tres finales distintos, y el
    que se caía por la rendija era justo el que había que reintentar.

    COPY + UID EXPUNGE porque el servidor no tiene MOVE pero sí UIDPLUS.
    Nunca EXPUNGE a secas: eso borraría otros mensajes marcados.

    El `typ` de LOS TRES comandos se chequea a propósito: `imaplib` sólo
    levanta excepción si el servidor contesta BAD. Una respuesta NO
    -carpeta que no existe, cuota superada, un error transitorio- vuelve
    en silencio. Sin chequear el COPY, un NO ahí borraba un correo sin
    haberlo copiado a ningún lado (eso ya se arregló). Un NO en el STORE
    o el EXPUNGE, DESPUÉS de un COPY que sí salió bien, es otro problema:
    acá ya existe una copia nueva en `carpeta`, así que ni devolver True
    (mentiría: no terminó como se pidió) ni False (también mentiría: sí
    se copió algo) describe lo que pasó.
    """
    M = abrir_buzon(readonly=False)
    try:
        uid = _uid_de(M, message_id)
        if not uid:
            return False
        typ, respuesta = M.uid("COPY", uid, carpeta)
        if typ != "OK":
            # Nada se copió: no hay nada que deshacer, es seguro
            # reintentar la mudanza entera más adelante. Pero tampoco es
            # un False, que acá significa "el mensaje no estaba" y quien
            # llama lee como "listo, nada que hacer": esto es una falla
            # del servidor -cuota, carpeta, permisos- que hay que
            # reintentar y, si no se arregla, contarle a JP.
            raise CopiaRechazada(
                f"{message_id}: el servidor rechazó copiar a {carpeta}"
                f" -- {_motivo(respuesta)}")
        typ, _ = M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise OperacionAMedias(
                f"{message_id}: se copió a {carpeta} pero no se pudo"
                " marcar para borrar en INBOX -- quedó duplicado")
        typ, _ = M.uid("EXPUNGE", uid)
        if typ != "OK":
            raise OperacionAMedias(
                f"{message_id}: se copió a {carpeta} y se marcó para"
                " borrar, pero el EXPUNGE no se confirmó -- puede haber"
                " quedado duplicado en INBOX")
        return True
    finally:
        M.logout()


def borrar_el_original(message_id):
    """Saca de INBOX un mensaje que YA está copiado en otra carpeta.

    Es la mitad que falta cuando mover_a() levanta OperacionAMedias: el
    COPY se confirmó pero el STORE +Deleted o el UID EXPUNGE no, así que
    el mensaje está en las dos carpetas a la vez. Reintentar mover_a()
    entero en ese estado vuelve a copiar y deja un duplicado NUEVO en el
    destino por cada vuelta del ciclo -con una falla sostenida del lado
    del borrado (cuota agotada en esa carpeta, por ejemplo) eso acumula
    varias copias por hora. Esta función no hace COPY nunca: por eso
    existe separada en vez de un parámetro de mover_a().

    Devuelve True si borró, y False si el mensaje ya no está en INBOX
    -el EXPUNGE anterior sí había salido y la respuesta se perdió, o JP
    lo borró a mano-: en los dos casos el estado final es el que se
    quería y no hay nada más que hacer. Si el STORE o el EXPUNGE
    vuelven NO, sigue a medias y levanta OperacionAMedias, igual que
    mover_a: el estado no cambió y el reintento es el mismo.
    """
    M = abrir_buzon(readonly=False)
    try:
        uid = _uid_de(M, message_id)
        if not uid:
            return False
        typ, _ = M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise OperacionAMedias(
                f"{message_id}: sigue copiado en el destino y sin poder"
                " marcarse para borrar en INBOX")
        typ, _ = M.uid("EXPUNGE", uid)
        if typ != "OK":
            raise OperacionAMedias(
                f"{message_id}: marcado para borrar en INBOX, pero el"
                " EXPUNGE no se confirmó -- sigue duplicado")
        return True
    finally:
        M.logout()


def marcar_leido(message_id):
    """Marca un mensaje como leído sin tocar ninguna otra cosa."""
    M = abrir_buzon(readonly=False)
    try:
        uid = _uid_de(M, message_id)
        if not uid:
            return False
        typ, _ = M.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        return typ == "OK"
    finally:
        M.logout()


def devolver_a_bandeja(message_id):
    """Saca un mensaje de INBOX.Ruido y lo deja en INBOX SIN LEER.

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

    Los CUATRO comandos de escritura se chequean, no sólo el COPY: los
    dos primeros (STORE -Seen, COPY) todavía no cambiaron nada del lado
    de Ruido si fallan, así que un NO ahí es simplemente False, seguro
    de reintentar desde cero. Los dos últimos (STORE +Deleted, EXPUNGE)
    corren DESPUÉS de que el COPY ya confirmó una copia nueva en INBOX:
    un NO en cualquiera de esos dos deja un duplicado -mismo caso que en
    mover_a- y levanta OperacionAMedias en vez de mentir con un booleano.
    """
    M = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"],
                          int(os.environ["IMAP_PORT"]), timeout=40)
    M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    try:
        M.select("INBOX.Ruido", readonly=False)
        uid = _uid_de(M, message_id)
        if not uid:
            return False
        typ, _ = M.uid("STORE", uid, "-FLAGS", "(\\Seen)")
        if typ != "OK":
            # Todavía no se copió nada: seguro reintentar desde cero.
            return False
        typ, _ = M.uid("COPY", uid, "INBOX")
        if typ != "OK":
            return False
        typ, _ = M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise OperacionAMedias(
                f"{message_id}: se copió a INBOX (ya sin \\Seen) pero no"
                " se pudo marcar para borrar en Ruido -- quedó duplicado")
        typ, _ = M.uid("EXPUNGE", uid)
        if typ != "OK":
            raise OperacionAMedias(
                f"{message_id}: se copió a INBOX y se marcó para borrar"
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
        criterio = (f"{desde_fecha.day:02d}-{MESES_IMAP[desde_fecha.month - 1]}"
                    f"-{desde_fecha.year}")
        typ, d = M.uid("SEARCH", None, "SINCE", criterio)
        salida = []
        for uid in d[0].split():
            typ, dd = M.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
            crudo = dd[0][1]
            banderas = str(dd[0][0])
            msg = email.message_from_bytes(crudo, policy=email.policy.default)
            salida.append({
                "uid": uid.decode(),
                "de": str(msg.get("From", ""))[:200],
                "para": str(msg.get("To", ""))[:300],
                "cc": str(msg.get("Cc", ""))[:300],
                "asunto": str(msg.get("Subject", "(sin asunto)"))[:200],
                "fecha": str(msg.get("Date", "")),
                "message_id": str(msg.get("Message-ID", "")),
                "cuerpo": texto_plano(msg)[:4000],
                "adjuntos": adjuntos(msg),
                "ya_leido": "\\Seen" in banderas,
            })
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
