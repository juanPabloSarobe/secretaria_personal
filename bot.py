#!/usr/bin/env python3
"""Todo lo que habla con Telegram.

Incluye el token de tanda: lo que impide que un botón de un resumen viejo
conteste por el correo de hoy. No lo calcula este módulo —cada resumen
genera el suyo—, solo lo recibe como parámetro y lo compara.
"""
import json, os, time
import urllib.error, urllib.parse, urllib.request

UA = {"User-Agent": "secretaria-personal/0.1"}  # sin esto, Cloudflare devuelve 403/1010

CATEGORIAS = {
    "RUIDO":    "🗑 Ruido",
    "DELEGADO": "✅ Ya está en copia",
    "ENZO":     "➡️ Derivar a Enzo",
    "NATALIA":  "➡️ Derivar a Natalia",
    "TUYO":     "🔴 Es mío",
    "DUDA":     "❓ No sé",
}


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


def teclado(idx, tanda):
    orden = ["RUIDO", "DELEGADO", "ENZO", "NATALIA", "TUYO", "DUDA"]
    filas, fila = [], []
    for cat in orden:
        fila.append({"text": CATEGORIAS[cat], "callback_data": f"c|{tanda}-{idx}|{cat}"})
        if len(fila) == 2:
            filas.append(fila); fila = []
    if fila:
        filas.append(fila)
    filas.append([{"text": "⭐ Cliente importante", "callback_data": f"i|{tanda}-{idx}|0"}])
    return {"inline_keyboard": filas}


def teclado_direcciones(idx, direcciones, tanda):
    """Lista de direcciones del correo, para marcar cuál es el cliente importante."""
    filas = [[{"text": f"⭐ {etiqueta}"[:60], "callback_data": f"d|{tanda}-{idx}|{n}"}]
             for n, (etiqueta, _) in enumerate(direcciones)]
    filas.append([{"text": "← volver", "callback_data": f"v|{tanda}-{idx}|0"}])
    return {"inline_keyboard": filas}


def es_de_esta_tanda(data, idx, tanda):
    """¿Este botón pertenece a este envío y a este correo?

    Un botón lleva 'accion|<tanda>-<idx>|valor'. Comparar solo el idx no
    alcanza: todo envío numera desde 1, así que el botón 3 de un resumen
    viejo pasaría por el 3 del de hoy.
    """
    partes = (data or "").split("|")
    return len(partes) == 3 and partes[1] == f"{tanda}-{idx}"


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
