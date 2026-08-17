#!/usr/bin/env python3
"""
Simulacro de triage por Telegram.

Lee los últimos N correos SIN marcarlos como leídos, los clasifica, y te
pregunta por Telegram qué correspondía. Compara tu respuesta con la del
clasificador y guarda todo.

NO mueve correos. NO envía correos. NO modifica la casilla de ninguna forma.
Lo único que sale hacia afuera son mensajes de Telegram para vos.

Uso:  python3 simulacro.py [cantidad] [--hoy] [--motor groq|nvidia|ollama]
"""
import email.utils, glob, html, json, os, random, sys, time
from collections import Counter
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

from correo import (TIPOS_ADJUNTO, MESES_IMAP, abrir_buzon, adjuntos,  # noqa: F401
                    adjuntos_legibles, completar_adjuntos, identidad,
                    texto_plano, traer_correos)

def parsear_argumentos(argv):
    """(cantidad, motor, desde, lista) a partir de la línea de comandos.

    --hoy trae los correos del día en lugar de los últimos N.
    --lista <archivo> revisa una lista corta de explorar.py en vez de la
    bandeja: los correos ya vienen con el texto adentro, así que ni se abre
    el IMAP.
    """
    args = list(argv)
    motor = None
    if "--motor" in args:
        i = args.index("--motor")
        motor = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    lista = None
    if "--lista" in args:
        i = args.index("--lista")
        lista = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    desde = None
    if "--hoy" in args:
        args.remove("--hoy")
        desde = datetime.now().date()
    return (int(args[0]) if args else 10), motor, desde, lista


# Solo al ejecutarse como script: importado desde otra herramienta, los
# argumentos de la línea de comandos son de ESA herramienta, no de esta.
CANTIDAD, MOTOR, DESDE, LISTA = (parsear_argumentos(sys.argv[1:])
                                if __name__ == "__main__"
                                else (10, None, None, None))
# Identifica a ESTA corrida. Los botones de Telegram siguen vivos para
# siempre: una tanda que se cortó deja mensajes con botones "3/12" que, si se
# tocan, la tanda siguiente acepta como respuesta a SU correo 3 —que es otro
# correo—. El número de pregunta no alcanza para distinguirlos porque toda
# tanda empieza en 1. Con el token, los botones viejos se rechazan solos.
TANDA = f"{os.getpid() % 10000:04d}"
UA = {"User-Agent": "secretaria-personal/0.1"}  # sin esto, Cloudflare devuelve 403/1010

CATEGORIAS = {
    "RUIDO":    "🗑 Ruido",
    "DELEGADO": "✅ Ya está en copia",
    "ENZO":     "➡️ Derivar a Enzo",
    "NATALIA":  "➡️ Derivar a Natalia",
    "TUYO":     "🔴 Es mío",
    "DUDA":     "❓ No sé",
}


# ------------------------------------------------------------------ Telegram
def tg(metodo, _intentos=5, **params):
    """Llamada a Telegram, tolerante a tropiezos de red.

    Esperar a que JP conteste significa mantener conexiones abiertas durante
    horas: una tanda quedó 84 minutos esperando el primer botón. En ese lapso
    un corte momentáneo es inevitable, y sin reintento mata la sesión entera
    junto con todo lo que se hubiera avanzado.

    Un long-poll que vence es lo normal, no un error: se vuelve a pedir.
    """
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{metodo}"
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
         for k, v in params.items()}).encode()

    for intento in range(_intentos):
        req = urllib.request.Request(url, data=data, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=70) as r:
                return json.load(r)

        except urllib.error.HTTPError as e:
            detalle = e.read().decode(errors="replace")[:200]
            if e.code < 500 and e.code != 429:
                # un 4xx no se arregla repitiéndolo; el cuerpo dice qué pasó
                raise RuntimeError(f"Telegram {metodo} -> {e.code}: {detalle}") from None
            if intento == _intentos - 1:
                raise RuntimeError(f"Telegram {metodo} -> {e.code}: {detalle}") from None
            print(f"      (Telegram {e.code}, reintento {intento + 1})", flush=True)
            time.sleep(min(2 ** intento, 15))

        except (TimeoutError, urllib.error.URLError, OSError,
                json.JSONDecodeError) as e:
            if intento == _intentos - 1:
                raise RuntimeError(
                    f"Telegram {metodo}: red caída tras {_intentos} intentos "
                    f"({type(e).__name__}: {e})") from None
            # el long-poll que vence es rutina; no vale la pena llenar el log
            if metodo != "getUpdates":
                print(f"      (red: {type(e).__name__}, reintento {intento + 1})",
                      flush=True)
            time.sleep(min(2 ** intento, 15))


def tg_suave(metodo, **params):
    """Llamada cosmética: si falla, se sigue igual.

    answerCallbackQuery solo apaga el relojito del botón, y Telegram lo rechaza
    con 400 si la consulta ya expiró — cosa que pasa cuando JP se toma su tiempo
    para contestar. Que no se pueda apagar un reloj no puede costar una sesión
    entera de trabajo manual.
    """
    try:
        return tg(metodo, **params)
    except Exception as e:
        print(f"      (aviso: {e})", flush=True)
        return None


def teclado(idx):
    orden = ["RUIDO", "DELEGADO", "ENZO", "NATALIA", "TUYO", "DUDA"]
    filas, fila = [], []
    for cat in orden:
        fila.append({"text": CATEGORIAS[cat], "callback_data": f"c|{TANDA}-{idx}|{cat}"})
        if len(fila) == 2:
            filas.append(fila); fila = []
    if fila:
        filas.append(fila)
    filas.append([{"text": "⭐ Cliente importante", "callback_data": f"i|{TANDA}-{idx}|0"}])
    return {"inline_keyboard": filas}


def teclado_direcciones(idx, direcciones):
    """Lista de direcciones del correo, para marcar cuál es el cliente importante."""
    filas = [[{"text": f"⭐ {etiqueta}"[:60], "callback_data": f"d|{TANDA}-{idx}|{n}"}]
             for n, (etiqueta, _) in enumerate(direcciones)]
    filas.append([{"text": "← volver", "callback_data": f"v|{TANDA}-{idx}|0"}])
    return {"inline_keyboard": filas}


def esperar_respuesta(idx, offset, msg_id, texto, direcciones):
    """Long-polling hasta que JP elija una categoría.

    En el medio puede entrar y salir del submenú de clientes importantes
    tantas veces como quiera; solo un botón de categoría termina el ciclo.
    """
    marcados = []
    while True:
        d = tg("getUpdates", offset=offset, timeout=60, allowed_updates=["callback_query"])
        for u in d.get("result", []):
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if not cq:
                continue
            partes = cq["data"].split("|")
            if len(partes) != 3 or partes[1] != f"{TANDA}-{idx}":
                tg_suave("answerCallbackQuery", callback_query_id=cq["id"],
                   text="Ese botón es de otro correo, ya pasó.")
                continue
            accion, valor = partes[0], partes[2]

            if accion == "c":                                   # categoría: termina
                tg_suave("answerCallbackQuery", callback_query_id=cq["id"])
                return valor, offset, marcados

            if accion == "i":                                   # abrir submenú
                tg_suave("answerCallbackQuery", callback_query_id=cq["id"])
                if not direcciones:
                    tg_suave("answerCallbackQuery", callback_query_id=cq["id"],
                       text="Este correo no tiene direcciones externas.")
                    continue
                tg_suave("editMessageText", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                   message_id=msg_id, parse_mode="HTML",
                   text=texto.replace("¿Qué correspondía?",
                                      "¿Cuál de estos es el cliente importante?"),
                   reply_markup=teclado_direcciones(idx, direcciones))

            elif accion == "d":                                 # elegir dirección
                etiqueta, direccion = direcciones[int(valor)]
                nuevo = marcar_importante(etiqueta, direccion)
                tg_suave("answerCallbackQuery", callback_query_id=cq["id"],
                   text=("⭐ Guardado: " + direccion) if nuevo else "Ya estaba en la lista")
                marcados.append(direccion)
                tg_suave("editMessageText", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                   message_id=msg_id, parse_mode="HTML",
                   text=texto.replace("¿Qué correspondía?",
                                      f"⭐ {html.escape(direccion)} marcado como importante.\n\n"
                                      "¿Qué correspondía?"),
                   reply_markup=teclado(idx))

            elif accion == "v":                                 # volver sin marcar
                tg_suave("answerCallbackQuery", callback_query_id=cq["id"])
                tg_suave("editMessageText", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                   message_id=msg_id, parse_mode="HTML", text=texto,
                   reply_markup=teclado(idx))


# ------------------------------------------------------------------ Explicaciones
def transcribir(file_id):
    """Baja un audio de Telegram y lo transcribe con Whisper en Groq.

    Cada paso se anuncia y tiene su propio tope de tiempo: una vez esto se
    colgó en silencio durante minutos y desde afuera no había forma de saber
    si estaba bajando, transcribiendo o muerto.
    """
    print("      [audio] pidiendo la ubicación del archivo…", flush=True)
    d = tg("getFile", file_id=file_id)
    ruta = d["result"]["file_path"]

    print("      [audio] descargando…", flush=True)
    url = f"https://api.telegram.org/file/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{ruta}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        audio = r.read()
    print(f"      [audio] {len(audio)} bytes, transcribiendo…", flush=True)

    # multipart armado a mano: urllib no trae ayuda para esto
    borde = "----secretaria" + str(len(audio))
    partes = []
    for campo, valor in (("model", "whisper-large-v3-turbo"),
                         ("response_format", "json"), ("language", "es")):
        partes.append(f"--{borde}\r\nContent-Disposition: form-data; name=\"{campo}\"\r\n"
                      f"\r\n{valor}\r\n".encode())
    partes.append(f"--{borde}\r\nContent-Disposition: form-data; name=\"file\"; "
                  f"filename=\"nota.ogg\"\r\nContent-Type: audio/ogg\r\n\r\n".encode())
    partes.append(audio)
    partes.append(f"\r\n--{borde}--\r\n".encode())

    req = urllib.request.Request(
        os.environ["GROQ_BASE_URL"] + "/audio/transcriptions", data=b"".join(partes),
        headers={**UA, "Authorization": "Bearer " + os.environ["GROQ_API_KEY"],
                 "Content-Type": f"multipart/form-data; boundary={borde}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        texto = json.load(r).get("text", "").strip()
    print(f"      [audio] listo: {texto[:60]!r}", flush=True)
    return texto


def pedir_explicacion(idx, offset, esperado, dicho):
    """Cuando diferimos, pregunta el porqué. Acepta texto o audio.

    El botón dice QUÉ correspondía; solo el porqué permite escribir una regla
    que generalice a los casos que todavía no aparecieron.
    """
    tg("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"], parse_mode="HTML", text=(
        f"🤔 Acá diferimos: yo dije <b>{dicho}</b> y vos <b>{esperado}</b>.\n\n"
        "¿Por qué? Contame con tus palabras — <b>texto o audio</b>, lo que te quede cómodo.\n\n"
        "<i>Con esto escribo la regla. Sin esto solo sé que me equivoqué, "
        "no cómo no volver a equivocarme.</i>"),
        reply_markup={"inline_keyboard": [[
            {"text": "⏭ Saltear", "callback_data": f"x|{TANDA}-{idx}|0"}]]})

    # Tope duro: sin esto, un fallo de red mientras se procesa un audio deja la
    # tanda esperando para siempre y a JP mirando el teléfono sin saberlo.
    limite = time.time() + ESPERA_MAXIMA_EXPLICACION

    while True:
        if time.time() > limite:
            tg_suave("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                     text="Sigo sin recibir tu explicación, así que continúo. "
                          "Tu decisión quedó registrada igual.")
            print("      (sin explicación tras el tiempo de espera; sigo)", flush=True)
            return None, offset

        d = tg("getUpdates", offset=offset, timeout=60,
               allowed_updates=["message", "callback_query"])
        for u in d.get("result", []):
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if cq:
                tg_suave("answerCallbackQuery", callback_query_id=cq["id"])
                if cq["data"].startswith(f"x|{TANDA}-{idx}|"):
                    return None, offset
                continue
            m = u.get("message") or u.get("edited_message") or {}
            if m.get("text"):
                return m["text"].strip(), offset
            if m.get("voice") or m.get("audio"):
                nota = m.get("voice") or m.get("audio")
                # acusar recibo ANTES de trabajar: si la transcripción tarda o
                # falla, JP igual sabe que su audio llegó
                tg_suave("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                         text="🎙 Recibí tu audio, lo estoy transcribiendo…")
                try:
                    texto = transcribir(nota["file_id"])
                except Exception as e:
                    print(f"      [audio] falló: {type(e).__name__}: {e}", flush=True)
                    tg_suave("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                             text=f"No pude transcribir el audio ({type(e).__name__}). "
                                  "¿Me lo escribís?")
                    continue
                tg_suave("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                         parse_mode="HTML",
                         text=f"🎙 Te entendí: <i>{html.escape(texto)}</i>")
                return texto, offset


# ------------------------------------------------------------------ Códigos convenidos
def _sin_acentos(s):
    tabla = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return s.translate(tabla).lower()


def codigos_convenidos():
    """Frases que JP le pide a sus contactos para marcar que él pidió el correo."""
    try:
        texto = open("codigos.md", encoding="utf-8").read()
    except FileNotFoundError:
        return []
    frases = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith("- ") and len(linea) > 6:
            frases.append(_sin_acentos(linea[2:].strip()))
    return frases


def tiene_codigo(correo, frases):
    """¿El correo trae alguna frase convenida? Comprobación literal.

    A propósito NO pasa por el modelo. Un correo que continúa una charla de
    JP se lee igual que publicidad no solicitada, y ese es justamente el caso
    que no puede quedar librado a criterio: si la frase está, es de JP y
    punto. Una comparación de texto no tiene pasadas ni desacuerdos.
    """
    donde = _sin_acentos(f"{correo.get('asunto','')} {correo.get('cuerpo','')}")
    for f in frases:
        if f in donde:
            return f
    return None


# ------------------------------------------------------------------ Ruido conocido
# Dominios de correo personal: jamás se pueden dar por ruido en bloque, porque
# el mismo dominio trae publicidad y clientes. En los datos, gmail.com ya
# aparece mezclado entre RUIDO y NATALIA.
DOMINIOS_GENERICOS = {"gmail.com", "hotmail.com", "yahoo.com", "yahoo.com.ar",
                      "outlook.com", "live.com", "icloud.com", "aol.com"}

# Cuántas veces JP tiene que haber marcado lo mismo antes de automatizarlo.
MARCAS_PARA_AUTOMATIZAR = 2

# De cada cuántos correos auto-clasificados se muestra uno igual, para no perder
# de vista si el criterio se degrada. Sin esto, automatizar es dejar de medir.
MUESTREO_CONTROL = 4


def remitentes_ruido():
    """Remitentes y dominios que JP marcó siempre como RUIDO.

    Solo cuenta lo que él decidió a mano, y solo si NUNCA los clasificó de otra
    forma. Un remitente mezclado queda afuera: en los datos, orbcomm.com llegó
    con RUIDO unas veces y TUYO otras, y automatizarlo habría escondido correos
    suyos.
    """
    direcciones, dominios = {}, {}
    for ruta in glob.glob("datos/simulacro-*.json"):
        try:
            d = json.load(open(ruta, encoding="utf-8"))
        except Exception:
            continue
        for c in d.get("casos", []):
            _, dire = email.utils.parseaddr(c.get("de", ""))
            dire = dire.lower().strip()
            if not dire or "@" not in dire:
                continue
            direcciones.setdefault(dire, []).append(c.get("correcto"))
            dom = dire.split("@")[1]
            if dom not in DOMINIOS_GENERICOS:
                dominios.setdefault(dom, []).append(c.get("correcto"))

    def uniformes(mapa):
        return {k for k, v in mapa.items()
                if len(v) >= MARCAS_PARA_AUTOMATIZAR and set(v) == {"RUIDO"}}

    return uniformes(direcciones), uniformes(dominios)


def es_ruido_conocido(correo, direcciones, dominios):
    """¿JP ya dijo, repetidamente, que este remitente es ruido?"""
    _, dire = email.utils.parseaddr(correo.get("de", ""))
    dire = dire.lower().strip()
    if not dire or "@" not in dire:
        return None
    if dire in direcciones:
        return f"remitente {dire}"
    dom = dire.split("@")[1]
    if dom in dominios:
        return f"dominio {dom}"
    return None


def protegido(correo):
    """Remitentes que nunca se archivan solos, por más que parezcan ruido.

    Cubre dos archivos, y el segundo se agregó por un error real: el filtro
    archivó una promoción de SiPago pese a que reglas.md dice explícitamente
    que nada de SiPago es ruido. JP no llegó a verlo nunca.

    El principio es simple: **sobre lo que JP ya se pronunció, el sistema no
    decide solo.** Si su nombre o su dominio figura en el roster o en las
    reglas, el correo se le muestra. Puede que la regla esté de más, o que
    corresponda afinarla — pero eso lo decide él viendo el caso, no un
    clasificador saltándose lo que él escribió.
    """
    _, dire = email.utils.parseaddr(correo.get("de", ""))
    dire = dire.lower().strip()
    if not dire or "@" not in dire:
        return False
    dominio = dire.split("@")[1]
    for archivo in ("roster.md", "reglas.md"):
        try:
            texto = open(archivo, encoding="utf-8").read().lower()
        except FileNotFoundError:
            continue
        if dire in texto or dominio in texto:
            return True
    return False


# ------------------------------------------------------------------ Roster
DOMINIO_PROPIO = "fullcontrolgps.com.ar"


def direcciones_externas(correo):
    """Direcciones del correo que no son del propio equipo, sin repetir."""
    crudas = email.utils.getaddresses(
        [correo["de"], correo["para"], correo.get("cc", "")])
    vistas, salida = set(), []
    for nombre, dire in crudas:
        dire = dire.strip().lower()
        if not dire or "@" not in dire or DOMINIO_PROPIO in dire or dire in vistas:
            continue
        vistas.add(dire)
        salida.append(((nombre.strip() or dire), dire))
    return salida[:8]


def marcar_importante(etiqueta, direccion):
    """Agrega la dirección a roster.md. Devuelve False si ya estaba."""
    texto = open("roster.md", encoding="utf-8").read()
    if direccion in texto:
        return False
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    linea = f"- {direccion}"
    if etiqueta and etiqueta.lower() != direccion:
        linea += f"  <!-- {etiqueta} -->"
    with open("roster.md", "a", encoding="utf-8") as f:
        f.write(f"{linea}  <!-- marcado {hoy} desde Telegram -->\n")
    return True


# ------------------------------------------------------------------ Correo
def ids_respondidos():
    """Correos que JP ya clasificó en tandas anteriores."""
    vistos = set()
    for ruta in glob.glob("datos/simulacro-*.json"):
        try:
            d = json.load(open(ruta, encoding="utf-8"))
        except Exception:
            continue
        for c in d.get("casos", []):
            vistos.add(identidad(c))
    return vistos


# ------------------------------------------------------------------ Clasificador
def prompt_sistema():
    roster = open("roster.md", encoding="utf-8").read()
    reglas = open("reglas.md", encoding="utf-8").read()
    try:
        codigos = open("codigos.md", encoding="utf-8").read()
    except FileNotFoundError:
        codigos = ""
    return f"""Sos la secretaria de Juan Pablo Sarobe (JP), director de Full Control GPS.
Clasificás su correo entrante.

{roster}

{reglas}

{codigos}

PROCEDIMIENTO. Contestá estas preguntas EN ESTE ORDEN y frená en la primera que
dé resultado. El orden no es una sugerencia: una pregunta posterior nunca revierte
lo que decidió una anterior.

PASO 1 — ¿Quién ESCRIBE el correo?
  Si el remitente es del dominio fullcontrolgps.com.ar (Enzo, Natalia, o cualquier
  casilla propia), entonces el equipo YA está actuando sobre este asunto. La
  respuesta es DELEGADO y terminás acá.
  Esto vale aunque el texto sea una despedida, un agradecimiento o algo cariñoso:
  esa cortesía ya la escribió el equipo, no hay nada que JP tenga que agregar.
  ÚNICA EXCEPCIÓN: que le pidan ayuda a JP de forma explícita. Entonces es TUYO.

PASO 2 — ¿Este correo es AJENO a la operación de Full Control GPS?
  RUIDO es lo que no tiene nada que ver con el trabajo con clientes,
  proveedores o el equipo: publicidad, ventas frías, boletines, invitaciones a
  eventos, capacitaciones, avisos de mantenimiento programado de proveedores.
  Terminás acá.

  CUIDADO, dos cosas que NO son ruido:
  - Un correo dentro de un hilo de trabajo con un cliente o con el equipo
    NUNCA es ruido, aunque no quede nada pendiente por hacer. Avisar que algo
    ya se completó es parte de la operación, no publicidad.
  - Un pago rechazado o una factura con problema tampoco: exigen acción.

  Que el texto use vocabulario técnico o mencione dinero no lo hace ruido; lo
  que decide es si el asunto pertenece a la operación o le es ajeno.

PASO 3 — ¿El correo es para JP, o es una escalación?
  Antes de responder que sí, preguntate: ¿qué tendría que HACER JP con esto?
  Si la única respuesta es "leerlo para estar al tanto", entonces NO es suyo:
  es DELEGADO. Estar enterado no es tener una acción pendiente. Un mensaje que
  dos personas se cruzan entre ellas, con JP en copia, es información.
  Conflicto, disconformidad, pedido de reunión de alguien CON relación previa,
  desarrollo nuevo, despedida o agradecimiento dirigido a JP, o un asunto de la
  lista de personales. Si es así: TUYO, sin importar el tema.

PASO 4 — ¿De qué se trata, y quién ya está en el hilo?
  Determiná el responsable por el tema (Enzo o Natalia). Después fijate si su
  casilla aparece en De, en Para o en CC.

  Compará por DIRECCIÓN DE CORREO, no por el nombre que se muestra. Un
  destinatario puede figurar como "Tecnicos2 <tecnicos@fullcontrolgps.com.ar>":
  lo que cuenta es tecnicos@fullcontrolgps.com.ar, sin importar cómo lo
  apodaron. Lo mismo con administracion@fullcontrolgps.com.ar.

    - Aparece en alguno de los tres  -> DELEGADO
    - No aparece en ninguno          -> ENZO o NATALIA, según corresponda

PASO 5 — Si no alcanza la información para decidir: DUDA.

Categorías: RUIDO, DELEGADO, ENZO, NATALIA, TUYO, DUDA.

Respondé SOLO un objeto JSON, sin texto alrededor:
{{"categoria":"<una de las seis>","motivo":"<máximo 12 palabras>","confianza":"alta|media|baja"}}"""


# Más allá de esto no es un límite pasajero sino cuota agotada: no tiene sentido
# dormir, conviene cambiar de motor. Groq llegó a mandar Retry-After de 1462s.
# Nemotron razona antes de contestar y no manda nada hasta terminar: con 60s
# se cortaba la conexión a mitad de pensamiento y el correo quedaba sin
# clasificar. Es el motor único mientras ollama esté fuera, así que conviene
# tenerle paciencia.
ESPERA_RESPUESTA = 180
ESPERA_MAXIMA = 90

# Tope para que JP explique un caso. Pasado esto se sigue sin explicación:
# el trabajo ya hecho vale más que una explicación que quizá nunca llegue.
ESPERA_MAXIMA_EXPLICACION = 900

# Catálogo de motores. El .env guarda las credenciales de TODOS a la vez;
# elegir cuál se usa es una decisión del código, no un archivo que haya que
# reescribir cada vez.  nombre -> (var de URL, var de clave, modelo)
MOTORES = {
    "groq":   ("GROQ_BASE_URL",   "GROQ_API_KEY",   "llama-3.3-70b-versatile"),
    "nvidia": ("NVIDIA_BASE_URL", "NVIDIA_API_KEY", "nvidia/nemotron-3-super-120b-a12b"),
    # "ollama": ("OLLAMA_BASE_URL", "OLLAMA_API_KEY", "qwen2.5:14b"),  # ver nota abajo
}

# Orden de uso: el primero es el principal y los siguientes son respaldo
# automático cuando el anterior agota su cuota. Se puede anteponer uno desde
# la línea de comandos con --motor <nombre>.
#
# 2026-08-12: ollama queda FUERA de la cadena por pedido de JP. La Mac no
# puede tener dos modelos cargados a la vez y él lo necesita para otro
# proyecto. Volver a agregarlo solo cuando él lo habilite. nvidia va primero
# porque groq agota su cuota diaria con el entrenamiento; cuando se repone,
# conviene volver a ponerlo adelante — es más de cincuenta veces más rápido.
PREFERENCIA = ["nvidia", "groq"]


def motores(preferido=None):
    """Motores disponibles, en orden de uso.

    Descarta los que no tengan credenciales cargadas, así el catálogo puede
    listar más proveedores de los que estén configurados en esta máquina.
    """
    orden = list(PREFERENCIA)
    if preferido:
        if preferido not in MOTORES:
            raise SystemExit(f"motor desconocido: {preferido}. "
                             f"Opciones: {', '.join(MOTORES)}")
        orden.remove(preferido)
        orden.insert(0, preferido)

    lista = []
    for nombre in orden:
        var_url, var_key, modelo = MOTORES[nombre]
        url = os.environ.get(var_url)
        if url:
            lista.append((nombre, url, os.environ.get(var_key, ""), modelo))
    if not lista:
        raise SystemExit("no hay ningún motor configurado en el .env")
    return lista


def _pedir(base, key, cuerpo, intentos=4):
    """Una petición a un motor, tolerando límites pasajeros.

    Reintenta los 429 y los 5xx. Respeta Retry-After solo si la espera es
    razonable: si el proveedor pide más que ESPERA_MAXIMA, la cuota está
    agotada y quien llama debería probar otro motor en vez de dormirse.
    """
    req = urllib.request.Request(
        base + "/chat/completions", data=cuerpo,
        headers={**UA, "Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    for intento in range(intentos):
        try:
            with urllib.request.urlopen(req, timeout=ESPERA_RESPUESTA) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise
            pedida = float(e.headers.get("Retry-After") or 0)
            if pedida > ESPERA_MAXIMA:
                raise RuntimeError(
                    f"cuota agotada: el proveedor pide esperar {pedida:.0f}s") from e
            if intento == intentos - 1:
                raise
            espera = pedida or min(2 ** intento, 30)
            print(f"      (HTTP {e.code} — esperando {espera:.0f}s)", flush=True)
            time.sleep(espera + 0.5)
        except OSError as e:
            # Corte de red o de paciencia: la conexión se cayó, o el modelo
            # tardó más de lo que esperamos. No es cuota agotada —cambiar de
            # motor por esto sería tirar el bueno— así que se reintenta acá.
            if intento == intentos - 1:
                raise
            espera = min(2 ** intento, 30)
            print(f"      (red: {e} — reintento en {espera:.0f}s)", flush=True)
            time.sleep(espera)


def clasificar(sistema, correo, preferido=None, pasadas=3):
    """Clasifica con doble pasada y desempate.

    Dos pasadas que coinciden alcanzan. Si difieren, una tercera decide por
    mayoría. Si las tres difieren, la categoría es DUDA y se pregunta.

    El motivo es que el campo `confianza` que devuelve el modelo no sirve como
    señal de duda: dijo "alta" mientras se equivocaba en los tres correos de
    SiPago. La discrepancia entre pasadas sí sirve — marca los casos donde dos
    reglas del archivo compiten. Medido sobre 46 casos: 11 oscilan, y en todos
    la mayoría acierta. Una sola pasada se los juega a cara o cruz.
    """
    votos = []
    for n in range(pasadas):
        votos.append(clasificar_una_vez(sistema, correo, preferido))
        # dos iguales seguidas bastan: no se paga una tercera al pedo
        if n == 1 and votos[0]["categoria"] == votos[1]["categoria"]:
            return {**votos[0], "pasadas": 2, "unanime": True}

    conteo = Counter(v["categoria"] for v in votos)
    categoria, apoyos = conteo.most_common(1)[0]
    emitidas = [v["categoria"] for v in votos]

    if apoyos == 1:                     # ninguna se repitió: ambigüedad real
        return {"categoria": "DUDA",
                "motivo": "sin acuerdo entre pasadas: " + " / ".join(emitidas),
                "confianza": "baja", "pasadas": len(votos), "unanime": False,
                "emitidas": emitidas}

    ganador = next(v for v in votos if v["categoria"] == categoria)
    return {**ganador, "pasadas": len(votos), "unanime": False,
            "emitidas": emitidas}


def _adjuntos_para_modelo(correo):
    """Los adjuntos como texto para el prompt, avisando si el cuerpo está vacío.

    El aviso importa: un remito escaneado llega con el cuerpo en blanco, y sin
    esa aclaración el modelo lee un correo sin contenido y lo trata como ruido.
    """
    lista = correo.get("adjuntos") or []
    if not lista:
        return ""
    linea = "Adjuntos: " + "; ".join(
        f"{a['nombre']} ({TIPOS_ADJUNTO.get(a['tipo'], a['tipo'])}, {a['kb']} KB)"
        for a in lista)
    if len((correo.get("cuerpo") or "").strip()) < 200:
        linea += ("\n(El cuerpo está casi vacío: el contenido real del correo "
                  "está en el adjunto, que no podés leer. Clasificá por el "
                  "asunto, el remitente y el nombre del archivo.)")
    return linea + "\n"


def clasificar_una_vez(sistema, correo, preferido=None):
    disponibles = motores(preferido)
    for n, (nombre, base, key, modelo) in enumerate(disponibles):
        cuerpo = json.dumps({
            "model": modelo,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content":
                 f"Fecha: {correo.get('fecha') or '(desconocida)'}\n"
                 f"De: {correo['de']}\nPara: {correo['para']}\nCC: {correo['cc'] or '(nadie)'}\n"
                 f"Asunto: {correo['asunto']}\n"
                 f"{_adjuntos_para_modelo(correo)}"
                 f"\n{correo['cuerpo'][:3000]}"},
            ],
            # 3000 y no 500: los modelos de razonamiento gastan tokens pensando
            # antes de escribir, y con un presupuesto corto se cortan justo antes
            # del JSON. Los modelos comunes paran solos mucho antes, así que no
            # cuesta nada dejarles margen.
            "temperature": 0, "max_tokens": 3000,
        }).encode()
        try:
            msg = _pedir(base, key, cuerpo)["choices"][0]["message"]
            # algunos modelos dejan content en null y ponen todo en reasoning_content
            txt = msg.get("content") or msg.get("reasoning_content") or ""
            break
        except Exception as e:
            if n == len(disponibles) - 1:
                raise
            print(f"      ({nombre} falló: {e} — paso a {disponibles[n+1][0]})", flush=True)

    i, j = txt.find("{"), txt.rfind("}")
    try:
        return json.loads(txt[i:j + 1])
    except Exception:
        return {"categoria": "DUDA", "motivo": "respuesta ilegible", "confianza": "baja"}


# ------------------------------------------------------------------ Principal
def recortar(s, n):
    return s if len(s) <= n else s[:n].rstrip() + "…"


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


def valor_didactico(correo):
    """Cuánto se aprende preguntando por este correo. Menor = preguntar antes.

    El tanteo del barrido no decide nada —lo hizo un modelo chico y sin
    contexto— pero sí sirve para ordenar la cola. Donde el modelo se
    contradijo entre pasadas hay ambigüedad real, y la respuesta de JP la
    resuelve. Donde vio rutina, lo más probable es que confirme lo que ya
    sabemos, y eso gasta su tiempo sin enseñar nada.
    """
    a, b = (correo.get("tanteo") or ["", ""])[:2]
    if a != b:
        return 0
    return {"DUDA": 1, "TUYO": 2, "ENZO": 2, "NATALIA": 2}.get(a, 3)


def main():
    assert os.environ.get("MODO_SIMULACRO", "true").lower() == "true", \
        "MODO_SIMULACRO no está en true. Abortando por seguridad."

    chat = os.environ["TELEGRAM_CHAT_ID"]
    sistema = prompt_sistema()

    cadena = motores(MOTOR)
    modelo_en_uso = cadena[0][3]
    print("Motores, en orden de uso: "
          + " → ".join(f"{n} ({m})" for n, _, _, m in cadena))

    # CANTIDAD son correos NUEVOS para revisar, no correos a traer. Como ya hay
    # tandas respondidas, hay que traer de más para llegar a esa cantidad.
    ya = ids_respondidos()
    if LISTA:
        # La lista corta ya trae los correos enteros: no hace falta el IMAP.
        d = json.load(open(LISTA, encoding="utf-8"))
        traidos = sorted(d["candidatos"], key=valor_didactico)
        tope = CANTIDAD
        print(f"Lista corta de {LISTA}: {len(traidos)} candidatos de "
              f"{d['total']} barridos. Van primero los que más enseñan.")
    elif DESDE:
        print(f"Trayendo los correos desde {DESDE} (sin marcarlos como leídos)…")
        traidos = traer_correos(0, DESDE)
        tope = len(traidos)
    else:
        pozo = min(CANTIDAD + len(ya) + 10, 400)
        print(f"Trayendo hasta {pozo} correos (sin marcarlos como leídos), "
              f"para juntar {CANTIDAD} sin revisar…")
        traidos = traer_correos(pozo)
        tope = CANTIDAD

    correos = completar_adjuntos(
        [c for c in traidos if identidad(c) not in ya][:tope])
    repetidos = len(traidos) - len([c for c in traidos if identidad(c) not in ya])
    print(f"  {len(traidos)} leídos, {repetidos} ya respondidos antes, "
          f"{len(correos)} para revisar.\n")
    if not correos:
        print("  No hay nada nuevo. Probá con un número mayor.")
        return

    tg("sendMessage", chat_id=chat, parse_mode="HTML", text=(
        f"🧪 <b>Simulacro — {len(correos)} correos</b>\n\n"
        "Te voy a mostrar uno por uno. Decime qué correspondía hacer.\n\n"
        "<i>No te muestro mi respuesta hasta que elegís, así que no te condiciono. "
        "Después te digo si coincidimos.</i>\n\n"
        "⭐ Si además el remitente es un <b>cliente importante</b>, tocá ese botón "
        "y elegí cuál de las direcciones es. Queda guardado en el roster y habilita "
        "que te avise fuera de horario.\n\n"
        "🤔 Cuando diferimos te voy a preguntar <b>por qué</b>. Podés contestar "
        "escribiendo o mandando un audio.\n\n"
        "Ahora clasifico cada correo <b>dos veces</b>, y si no me pongo de acuerdo "
        "conmigo mismo, una tercera. Si aun así hay empate, te lo digo en vez de "
        "elegir al azar.\n\n"
        "No muevo ni mando nada: esto es solo lectura."))

    offset, resultados, t_inicio = 0, [], time.time()

    # Se guarda después de CADA respuesta, no al final. Las respuestas de JP son
    # trabajo manual irrecuperable: si el proceso muere a mitad de camino —por
    # cuota agotada, por un corte de red, por lo que sea— lo hecho queda en disco.
    os.makedirs("datos", exist_ok=True)
    sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ruta = f"datos/simulacro-{sello}.json"

    def guardar():
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump({"fecha_utc": sello, "modelo": modelo_en_uso,
                       "aciertos": sum(r["coincide"] for r in resultados),
                       "total": len(resultados), "completo": len(resultados) == len(correos),
                       "casos": resultados}, f, ensure_ascii=False, indent=2)

    dirs_ruido, doms_ruido = remitentes_ruido()
    if dirs_ruido or doms_ruido:
        print(f"Ruido conocido: {len(dirs_ruido)} remitente(s), "
              f"{len(doms_ruido)} dominio(s)\n")
    automaticos = []

    frases = codigos_convenidos()
    if frases:
        print(f"Códigos convenidos activos: {len(frases)}\n")

    for idx, c in enumerate(correos, 1):
        # Un código convenido gana sobre todo: JP pidió ese correo, y ninguna
        # heurística de ruido debería poder taparlo.
        codigo = tiene_codigo(c, frases)

        # Atajo sin LLM: si JP ya marcó este remitente como ruido dos veces o
        # más, y nunca de otra forma, no hace falta preguntárselo de nuevo.
        motivo_auto = (None if (protegido(c) or codigo)
                       else es_ruido_conocido(c, dirs_ruido, doms_ruido))
        if motivo_auto and random.randrange(MUESTREO_CONTROL):   # 1 de cada N igual se pregunta
            automaticos.append((c, f"ruido conocido — {motivo_auto}"))
            resultados.append({**{k: c[k] for k in
                                  ("uid", "de", "para", "cc", "asunto", "fecha", "message_id")},
                               "cuerpo": c["cuerpo"][:4000],
                               "prediccion": {"categoria": "RUIDO",
                                              "motivo": motivo_auto, "confianza": "alta"},
                               "correcto": "RUIDO", "coincide": True,
                               "automatico": motivo_auto,
                               "explicacion_jp": None, "marcados_importantes": [],
                               "seg_clasificacion": 0.0, "seg_decision_jp": 0.0})
            guardar()
            print(f"  {idx}/{len(correos)}  AUTO ruido ({motivo_auto})", flush=True)
            continue

        t0 = time.time()
        if codigo:
            # Sin consultar al modelo: la frase está o no está.
            pred = {"categoria": "TUYO", "confianza": "alta", "unanime": True,
                    "motivo": f"código convenido: «{codigo}»", "pasadas": 0}
            print(f"      [código] «{codigo}» — es tuyo sin discusión", flush=True)
        else:
            try:
                pred = clasificar(sistema, c, MOTOR)
            except Exception as e:
                # Que se caigan TODOS los motores no puede costar la tanda. Lo
                # valioso de cada correo es el criterio de JP, no mi opinión: se
                # lo muestro igual y su respuesta queda registrada como siempre.
                print(f"      (sin clasificar: {e})", flush=True)
                pred = {"categoria": "ERROR", "confianza": "baja",
                        "motivo": f"ningún motor respondió ({type(e).__name__})"}
        t_clas = time.time() - t0

        # Segundo atajo: el clasificador lleva 32 aciertos de 32 detectando
        # ruido, sin un solo error. Cuando las dos pasadas coinciden en RUIDO,
        # preguntárselo a JP no agrega información — y su tiempo es el recurso
        # escaso. Exige unanimidad, respeta a los protegidos, y 1 de cada N se
        # pregunta igual para no dejar de medir si el criterio se degrada.
        if (pred["categoria"] == "RUIDO" and pred.get("unanime")
                and not protegido(c) and random.randrange(MUESTREO_CONTROL)):
            automaticos.append((c, "clasificador unánime"))
            resultados.append({**{k: c[k] for k in
                                  ("uid", "de", "para", "cc", "asunto", "fecha", "message_id")},
                               "cuerpo": c["cuerpo"][:4000],
                               "prediccion": pred, "correcto": "RUIDO",
                               "coincide": True, "automatico": "clasificador unánime",
                               "explicacion_jp": None, "marcados_importantes": [],
                               "seg_clasificacion": round(t_clas, 2),
                               "seg_decision_jp": 0.0})
            guardar()
            print(f"  {idx}/{len(correos)}  AUTO ruido (unánime)", flush=True)
            continue

        cuerpo = recortar(c["cuerpo"].replace("\r", ""), 600)
        texto = (f"<b>{idx}/{len(correos)}</b>   <i>{html.escape(fecha_legible(c['fecha']))}</i>\n"
                 f"<b>De:</b> {html.escape(recortar(c['de'], 90))}\n"
                 f"<b>Para:</b> {html.escape(recortar(c['para'], 90))}\n"
                 f"<b>CC:</b> {html.escape(recortar(c['cc'] or '(nadie)', 90))}\n"
                 f"<b>Asunto:</b> {html.escape(recortar(c['asunto'], 120))}\n"
                 f"{html.escape(adjuntos_legibles(c.get('adjuntos')))}\n\n"
                 f"<pre>{html.escape(cuerpo)}</pre>\n\n¿Qué correspondía?")
        m = tg("sendMessage", chat_id=chat, text=texto, parse_mode="HTML",
               reply_markup=teclado(idx))
        msg_id = m["result"]["message_id"]

        t_espera = time.time()
        eleccion, offset, marcados = esperar_respuesta(
            idx, offset, msg_id, texto, direcciones_externas(c))
        t_espera = time.time() - t_espera

        coincide = eleccion == pred["categoria"]
        if pred.get("emitidas"):
            # hubo desacuerdo entre pasadas: vale la pena que JP lo sepa, es la
            # diferencia entre "me equivoqué" y "este caso es genuinamente ambiguo"
            desacuerdo = (f"\n<i>Me clasifiqué distinto en cada pasada: "
                          f"{' / '.join(pred['emitidas'])}</i>")
        else:
            desacuerdo = ""
        if pred["categoria"] == "ERROR":
            # No me equivoqué: no llegué a opinar. Decir "aprendido" acá sería
            # atribuirme un criterio que no tuve.
            veredicto = "⚠️ <b>No pude clasificarlo</b> — queda tu respuesta"
        else:
            veredicto = (("✅ <b>Coincidimos</b>" if coincide else
                          f"📚 <b>Aprendido</b> — yo dije <b>{pred['categoria']}</b>")
                         + desacuerdo)
        tg_suave("editMessageText", chat_id=chat, message_id=msg_id, parse_mode="HTML",
           text=texto.replace("¿Qué correspondía?",
                              f"Vos: <b>{eleccion}</b>\n{veredicto}\n"
                              f"<i>Mi motivo: {html.escape(pred.get('motivo',''))} "
                              f"(confianza {pred.get('confianza','?')})</i>"))

        explicacion = None
        # si ningún motor respondió no hay nada que explicar: no me equivoqué
        # de criterio, directamente no opiné
        if not coincide and pred["categoria"] != "ERROR":
            explicacion, offset = pedir_explicacion(
                idx, offset, eleccion, pred["categoria"])

        resultados.append({**{k: c[k] for k in
                              ("uid", "de", "para", "cc", "asunto", "fecha", "message_id")},
                           "cuerpo": c["cuerpo"][:4000],
                           "prediccion": pred, "correcto": eleccion,
                           "coincide": coincide,
                           "explicacion_jp": explicacion,
                           "marcados_importantes": marcados,
                           "seg_clasificacion": round(t_clas, 2),
                           "seg_decision_jp": round(t_espera, 1)})
        guardar()
        print(f"  {idx}/{len(correos)}  pred={pred['categoria']:<9} jp={eleccion:<9} "
              f"{'ok' if coincide else 'DIFIERE'}  ({t_clas:.1f}s clas, {t_espera:.0f}s vos)")

    # ---------------------------------------------------------- resumen
    revisados = [r for r in resultados if not r.get("automatico")]
    aciertos = sum(r["coincide"] for r in revisados)
    n = len(revisados)
    if not revisados:                       # tanda entera archivada sola
        print("\n  Nada que revisar: todo se archivó como ruido conocido.")
        return
    t_clas_prom = sum(r["seg_clasificacion"] for r in revisados) / n
    t_jp_prom = sum(r["seg_decision_jp"] for r in revisados) / n
    fallos = [r for r in revisados if not r["coincide"]]

    detalle = "\n\n".join(
        f"• <b>{r['correcto']}</b> (yo dije {r['prediccion']['categoria']}) — "
        f"{html.escape(recortar(r['asunto'], 45))}"
        + (f"\n  <i>{html.escape(recortar(r['explicacion_jp'], 160))}</i>"
           if r.get("explicacion_jp") else "")
        for r in fallos) or "—"

    tg("sendMessage", chat_id=chat, parse_mode="HTML", text=(
        f"🧪 <b>Simulacro terminado</b>\n\n"
        f"Coincidimos en <b>{aciertos} de {n}</b> de los que revisaste\n\n"
        f"⏱ Yo tardé <b>{t_clas_prom:.1f}s</b> por correo\n"
        f"⏱ Vos tardaste <b>{t_jp_prom:.0f}s</b> por correo\n"
        f"⏱ Total: <b>{(time.time()-t_inicio)/60:.1f} min</b>\n\n"
        f"<b>Donde nos diferimos:</b>\n{detalle}\n\n"
        f"<i>Cada diferencia es una regla nueva. Nada se movió ni se envió.</i>"
        + (f"\n\n🤖 <b>{len(automaticos)} archivados sin preguntarte</b> "
           f"(ruido ya confirmado por vos):\n"
           + "\n".join(f"• {html.escape(recortar(x[0]['asunto'], 40))} "
                       f"<i>{x[1]}</i>" for x in automaticos[:8])
           + ("\n…" if len(automaticos) > 8 else "")
           + "\n\n<i>Si alguno no era ruido, decímelo y lo saco de la lista.</i>"
           if automaticos else "")))

    print(f"\n  Coincidencias: {aciertos}/{n}")
    print(f"  Clasificación: {t_clas_prom:.1f}s por correo")
    print(f"  Tu decisión:   {t_jp_prom:.0f}s por correo")
    print(f"  Guardado en:   {ruta}")


if __name__ == "__main__":
    main()
