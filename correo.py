#!/usr/bin/env python3
"""Todo lo que habla con la casilla, y todo lo que interpreta un mensaje.

Nadie más abre una conexión IMAP. Las dos reglas que no se negocian viven
acá adentro: se lee con BODY.PEEK[] para no marcar como leído lo que no lo
estaba, y se busca por Message-ID y nunca por número de secuencia, porque
el número cambia en cuanto se archiva algo.
"""
import email, email.policy, email.utils, hashlib, html, imaplib, os, re
from datetime import datetime, timezone


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
    """Copia el mensaje a `carpeta` y lo borra de INBOX. False si no está.

    COPY + UID EXPUNGE porque el servidor no tiene MOVE pero sí UIDPLUS.
    Nunca EXPUNGE a secas: eso borraría otros mensajes marcados.

    El `typ` del COPY se chequea a propósito: `imaplib` sólo levanta
    excepción si el servidor contesta BAD. Una respuesta NO -carpeta que
    no existe, cuota superada, un error transitorio- vuelve en silencio,
    y sin este chequeo el código seguía igual con STORE +Deleted y
    EXPUNGE: el primer COPY que el servidor rechazara borraba un correo
    de JP sin haberlo copiado a ningún lado, de forma irreversible.
    """
    M = abrir_buzon(readonly=False)
    try:
        uid = _uid_de(M, message_id)
        if not uid:
            return False
        typ, _ = M.uid("COPY", uid, carpeta)
        if typ != "OK":
            return False
        M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        M.uid("EXPUNGE", uid)
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
        M.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        return True
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
    existiendo en Ruido y una reintento repite la operación desde el
    principio -en el peor caso deja un duplicado sin leer en INBOX y el
    original todavía en Ruido, nunca un correo perdido o mal marcado.

    El COPY se chequea igual que en mover_a: un NO sin excepción no puede
    hacer que sigamos de largo borrando el original de Ruido.
    """
    M = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"],
                          int(os.environ["IMAP_PORT"]), timeout=40)
    M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    try:
        M.select("INBOX.Ruido", readonly=False)
        uid = _uid_de(M, message_id)
        if not uid:
            return False
        M.uid("STORE", uid, "-FLAGS", "(\\Seen)")
        typ, _ = M.uid("COPY", uid, "INBOX")
        if typ != "OK":
            return False
        M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        M.uid("EXPUNGE", uid)
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
