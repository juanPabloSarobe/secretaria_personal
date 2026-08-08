#!/usr/bin/env python3
"""
Simulacro de triage por Telegram.

Lee los últimos N correos SIN marcarlos como leídos, los clasifica, y te
pregunta por Telegram qué correspondía. Compara tu respuesta con la del
clasificador y guarda todo.

NO mueve correos. NO envía correos. NO modifica la casilla de ninguna forma.
Lo único que sale hacia afuera son mensajes de Telegram para vos.

Uso:  python3 simulacro.py [cantidad]
"""
import email, email.policy, html, imaplib, json, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

CANTIDAD = int(sys.argv[1]) if len(sys.argv) > 1 else 10
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
def tg(metodo, **params):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{metodo}"
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
         for k, v in params.items()}).encode()
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.load(r)


def teclado(idx):
    orden = ["RUIDO", "DELEGADO", "ENZO", "NATALIA", "TUYO", "DUDA"]
    filas, fila = [], []
    for cat in orden:
        fila.append({"text": CATEGORIAS[cat], "callback_data": f"c|{idx}|{cat}"})
        if len(fila) == 2:
            filas.append(fila); fila = []
    if fila:
        filas.append(fila)
    return {"inline_keyboard": filas}


def esperar_respuesta(idx, offset):
    """Long-polling hasta que JP toque un botón de este correo."""
    while True:
        d = tg("getUpdates", offset=offset, timeout=60, allowed_updates=["callback_query"])
        for u in d.get("result", []):
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if not cq:
                continue
            partes = cq["data"].split("|")
            if len(partes) == 3 and partes[0] == "c" and partes[1] == str(idx):
                tg("answerCallbackQuery", callback_query_id=cq["id"])
                return partes[2], offset, cq["message"]["message_id"]
            tg("answerCallbackQuery", callback_query_id=cq["id"],
               text="Ese es de otro correo, ya pasó.")


# ------------------------------------------------------------------ Correo
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


def traer_correos(n):
    M = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.environ["IMAP_PORT"]), timeout=40)
    M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    M.select("INBOX", readonly=True)                   # readonly: no toca banderas
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
            "cuerpo": texto_plano(msg)[:1500],
        })
    M.logout()
    return correos


# ------------------------------------------------------------------ Clasificador
def prompt_sistema():
    roster = open("roster.md", encoding="utf-8").read()
    reglas = open("reglas.md", encoding="utf-8").read()
    return f"""Sos la secretaria de Juan Pablo Sarobe (JP), director de Full Control GPS.
Clasificás su correo entrante.

{roster}

{reglas}

Categorías posibles:
- RUIDO: sin ninguna consecuencia operativa (newsletters, publicidad, avisos informativos).
- DELEGADO: es tema de Enzo o de Natalia, y esa casilla YA figura en Para o CC. No hay que hacer nada.
- ENZO: es tema de Enzo y tecnicos@ NO está en los destinatarios. Hay que derivarlo.
- NATALIA: es tema de Natalia y administracion@ NO está en los destinatarios. Hay que derivarlo.
- TUYO: escalación, conflicto, disconformidad, pedido de reunión con JP, desarrollo nuevo,
  o pedido de ayuda del equipo. Esta categoría PISA a las anteriores.
- DUDA: no alcanza la información para decidir.

Respondé SOLO un objeto JSON, sin texto alrededor:
{{"categoria":"<una de las seis>","motivo":"<máximo 12 palabras>","confianza":"alta|media|baja"}}"""


def clasificar(sistema, correo):
    payload = json.dumps({
        "model": os.environ["LLM_CLASIFICADOR_MODEL"],
        "messages": [
            {"role": "system", "content": sistema},
            {"role": "user", "content":
             f"De: {correo['de']}\nPara: {correo['para']}\nCC: {correo['cc'] or '(nadie)'}\n"
             f"Asunto: {correo['asunto']}\n\n{correo['cuerpo'][:1200]}"},
        ],
        "temperature": 0, "max_tokens": 500,
    }).encode()
    req = urllib.request.Request(
        os.environ["LLM_CLASIFICADOR_BASE_URL"] + "/chat/completions", data=payload,
        headers={**UA, "Authorization": "Bearer " + os.environ["LLM_CLASIFICADOR_API_KEY"],
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        txt = json.load(r)["choices"][0]["message"]["content"]
    i, j = txt.find("{"), txt.rfind("}")
    try:
        return json.loads(txt[i:j + 1])
    except Exception:
        return {"categoria": "DUDA", "motivo": "respuesta ilegible", "confianza": "baja"}


# ------------------------------------------------------------------ Principal
def recortar(s, n):
    return s if len(s) <= n else s[:n].rstrip() + "…"


def main():
    assert os.environ.get("MODO_SIMULACRO", "true").lower() == "true", \
        "MODO_SIMULACRO no está en true. Abortando por seguridad."

    chat = os.environ["TELEGRAM_CHAT_ID"]
    sistema = prompt_sistema()

    print(f"Trayendo los últimos {CANTIDAD} correos (sin marcarlos como leídos)…")
    correos = traer_correos(CANTIDAD)
    print(f"  {len(correos)} correos leídos.\n")

    tg("sendMessage", chat_id=chat, parse_mode="HTML", text=(
        f"🧪 <b>Simulacro — {len(correos)} correos</b>\n\n"
        "Te voy a mostrar uno por uno. Decime qué correspondía hacer.\n\n"
        "<i>No veo mi propia respuesta hasta que elegís, así que no te condiciono. "
        "Después te digo si coincidimos.</i>\n\n"
        "No muevo ni mando nada: esto es solo lectura."))

    offset, resultados, t_inicio = 0, [], time.time()

    for idx, c in enumerate(correos, 1):
        t0 = time.time()
        pred = clasificar(sistema, c)
        t_clas = time.time() - t0

        cuerpo = recortar(c["cuerpo"].replace("\r", ""), 600)
        texto = (f"<b>{idx}/{len(correos)}</b>\n"
                 f"<b>De:</b> {html.escape(recortar(c['de'], 90))}\n"
                 f"<b>Para:</b> {html.escape(recortar(c['para'], 90))}\n"
                 f"<b>CC:</b> {html.escape(recortar(c['cc'] or '(nadie)', 90))}\n"
                 f"<b>Asunto:</b> {html.escape(recortar(c['asunto'], 120))}\n\n"
                 f"<pre>{html.escape(cuerpo)}</pre>\n\n¿Qué correspondía?")
        m = tg("sendMessage", chat_id=chat, text=texto, parse_mode="HTML",
               reply_markup=teclado(idx))
        msg_id = m["result"]["message_id"]

        t_espera = time.time()
        eleccion, offset, _ = esperar_respuesta(idx, offset)
        t_espera = time.time() - t_espera

        coincide = eleccion == pred["categoria"]
        veredicto = ("✅ <b>Coincidimos</b>" if coincide else
                     f"📚 <b>Aprendido</b> — yo dije <b>{pred['categoria']}</b>")
        tg("editMessageText", chat_id=chat, message_id=msg_id, parse_mode="HTML",
           text=texto.replace("¿Qué correspondía?",
                              f"Vos: <b>{eleccion}</b>\n{veredicto}\n"
                              f"<i>Mi motivo: {html.escape(pred.get('motivo',''))} "
                              f"(confianza {pred.get('confianza','?')})</i>"))

        resultados.append({**{k: c[k] for k in
                              ("uid", "de", "para", "cc", "asunto", "fecha", "message_id")},
                           "cuerpo": c["cuerpo"][:1500],
                           "prediccion": pred, "correcto": eleccion,
                           "coincide": coincide,
                           "seg_clasificacion": round(t_clas, 2),
                           "seg_decision_jp": round(t_espera, 1)})
        print(f"  {idx}/{len(correos)}  pred={pred['categoria']:<9} jp={eleccion:<9} "
              f"{'ok' if coincide else 'DIFIERE'}  ({t_clas:.1f}s clas, {t_espera:.0f}s vos)")

    # ---------------------------------------------------------- resumen
    aciertos = sum(r["coincide"] for r in resultados)
    n = len(resultados)
    t_clas_prom = sum(r["seg_clasificacion"] for r in resultados) / n
    t_jp_prom = sum(r["seg_decision_jp"] for r in resultados) / n
    fallos = [r for r in resultados if not r["coincide"]]

    detalle = "\n".join(
        f"• <b>{r['correcto']}</b> (yo dije {r['prediccion']['categoria']}) — "
        f"{html.escape(recortar(r['asunto'], 45))}" for r in fallos) or "—"

    tg("sendMessage", chat_id=chat, parse_mode="HTML", text=(
        f"🧪 <b>Simulacro terminado</b>\n\n"
        f"Coincidimos en <b>{aciertos} de {n}</b>\n\n"
        f"⏱ Yo tardé <b>{t_clas_prom:.1f}s</b> por correo\n"
        f"⏱ Vos tardaste <b>{t_jp_prom:.0f}s</b> por correo\n"
        f"⏱ Total: <b>{(time.time()-t_inicio)/60:.1f} min</b>\n\n"
        f"<b>Donde nos diferimos:</b>\n{detalle}\n\n"
        f"<i>Cada diferencia es una regla nueva. Nada se movió ni se envió.</i>"))

    os.makedirs("datos", exist_ok=True)
    sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ruta = f"datos/simulacro-{sello}.json"
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"fecha_utc": sello, "modelo": os.environ["LLM_CLASIFICADOR_MODEL"],
                   "aciertos": aciertos, "total": n, "casos": resultados},
                  f, ensure_ascii=False, indent=2)

    print(f"\n  Coincidencias: {aciertos}/{n}")
    print(f"  Clasificación: {t_clas_prom:.1f}s por correo")
    print(f"  Tu decisión:   {t_jp_prom:.0f}s por correo")
    print(f"  Guardado en:   {ruta}")


if __name__ == "__main__":
    main()
