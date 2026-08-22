#!/usr/bin/env python3
"""El estado de cada correo, en SQLite.

Dos hilos tocan esto a la vez: el que escucha a JP y el que procesa correo.
Con archivos JSON se corrompe el día que coincidan. SQLite viene con Python,
pero `check_same_thread=False` sólo permite compartir la conexión entre
hilos: no vuelve seguras las llamadas simultáneas sobre esa conexión. Un
`sqlite3.Connection` de Python no es reentrante frente a dos hilos que
llaman a `execute()` al mismo tiempo (probado: sin candado esto tira
`InterfaceError` e `IntegrityError`, y pierde filas en silencio bajo
carga). Por eso todas las funciones que tocan `cx` toman `_CANDADO` antes
de hacerlo. El costo de serializar es irrelevante acá: son 10-20 correos
por día, no por segundo.

Está en disco y no en memoria a propósito: si el proceso se cae, al volver
retoma sin reclasificar lo que ya clasificó ni volver a avisar lo que ya
avisó.
"""
import json
import os
import sqlite3
import threading

# "pendiente_de_borrar" y "pendiente_de_archivar" NO son sinónimos, y la
# diferencia es lo que evita llenar Ruido de duplicados: en el primer caso
# la copia ya está hecha y sólo falta borrar el original de INBOX (hay que
# reintentar sólo el borrado, nunca un COPY nuevo); en el segundo no se
# copió nada todavía y hay que reintentar la mudanza entera.
# "no_se_pudo_archivar" es el final del camino: se agotaron los intentos,
# el correo deja de reintentarse y JP ya recibió el aviso.
#
# "pendiente_de_rever" y "no_se_pudo_rever" -tarea 11, ronda 1- son el
# mismo patrón aplicado a Rever: JP ya contestó el motivo, pero
# rever_ruido() llama a clasificador.clasificar() -hasta tres minutos de
# red- y por eso no puede correr en el hilo que escucha (ver el hallazgo
# CRÍTICO). El motivo se guarda en `explicacion` y la fila queda acá
# hasta que ciclo_de_correo la retoma; si se agotan los intentos, pasa a
# "no_se_pudo_rever" recién después de avisarle a JP -mismo orden que
# "no_se_pudo_archivar", por la misma razón: avisar antes de dar por
# terminal evita que un Telegram caído en ese instante se lleve el aviso
# puesto para siempre.
#
# "pendiente_de_avisar" -despacho final- es la cola de avisos que el
# diseño (§8) pide y no existía: "los avisos quedan en cola y salen
# cuando vuelve". Un aviso al toque que Telegram no entregó dejaba el
# correo en "avisado" igual, y "avisado" no lo lee nadie: el correo de
# JP desaparecía del sistema entero -no volvía a interrumpir, no entraba
# en ningún resumen, y el de las 8:30 decía "No entró nada nuevo"-. Es
# la misma invariante que "no_se_pudo_archivar" ya respetaba, aplicada
# al aviso que más importa: ningún estado terminal sin entrega
# confirmada.
SITUACIONES = {"clasificado", "archivado", "en_resumen", "avisado",
               "cerrado", "corregido", "mostrado_sin_clasificar",
               "pendiente_de_archivar", "pendiente_de_borrar",
               "no_se_pudo_archivar", "pendiente_de_rever",
               "no_se_pudo_rever", "pendiente_de_avisar"}

# RLock y no Lock: alguna función de acá podría terminar llamando a otra
# de acá (p.ej. anotar() llamaba a situacion()), y con un Lock simple eso
# sería un auto-bloqueo.
_CANDADO = threading.RLock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS correos (
    message_id   TEXT PRIMARY KEY,
    uid          TEXT,
    -- El UID de IMAP sólo identifica un mensaje mientras el UIDVALIDITY
    -- de la carpeta siga siendo el mismo. Guardar uno sin el otro es
    -- guardar un número suelto: si la carpeta se recrea, ese UID puede
    -- ser hoy cualquier otro correo. Van juntos por eso.
    uidvalidity  TEXT,
    de           TEXT,
    para         TEXT,
    cc           TEXT,
    asunto       TEXT,
    fecha        TEXT,
    cuerpo       TEXT,
    adjuntos     TEXT,
    categoria    TEXT,
    motivo       TEXT,
    categoria_jp TEXT,
    explicacion  TEXT,
    situacion    TEXT NOT NULL,
    visto        TEXT NOT NULL,
    actualizado  TEXT NOT NULL,
    intentos     INTEGER NOT NULL DEFAULT 0,
    -- Qué falló la última vez que se intentó archivarlo. Va en la base y
    -- no en una variable porque el aviso a JP puede quedar pendiente
    -- -Telegram caído- y tiene que poder mandarse después de un reinicio,
    -- diciendo todavía qué fue lo que pasó.
    falla        TEXT
);
CREATE INDEX IF NOT EXISTS por_situacion ON correos(situacion);
CREATE TABLE IF NOT EXISTS latidos (momento TEXT PRIMARY KEY);
-- Estado chico y singular del proceso que tiene que sobrevivir un
-- reinicio -hoy sólo self.pendiente (tarea 11, ronda 1: si el proceso se
-- reinicia entre que JP contesta un paso de Rever/Uno es mío y el
-- siguiente, sin esto la pregunta se pierde y el bot le contesta "no te
-- entiendo" a algo que sí contestó).
CREATE TABLE IF NOT EXISTS estado (clave TEXT PRIMARY KEY, valor TEXT);
"""


def abrir(ruta="datos/secretaria.db"):
    with _CANDADO:
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        cx = sqlite3.connect(ruta, check_same_thread=False, timeout=30)
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA journal_mode=WAL")   # dos hilos sin pisarse
        cx.executescript(ESQUEMA)
        _migrar(cx)
        cx.commit()
        return cx


# Columnas que el esquema ganó después de que la base ya existía, con la
# definición exacta que hay que agregarle a una tabla vieja.
AGREGADAS = [("intentos", "INTEGER NOT NULL DEFAULT 0"),
             ("uidvalidity", "TEXT"),
             ("falla", "TEXT")]


def _migrar(cx):
    """Agrega a una base que ya existe las columnas que el esquema ganó
    después.

    `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya está: la base
    que corre hace semanas en la Mac mini se creó sin `intentos`, así que
    sin esto el primer reintento de archivado reventaría con
    OperationalError en la Mac mini y no acá con los tests. Es
    idempotente a propósito: se ejecuta en cada `abrir()`.
    """
    columnas = {f["name"] for f in cx.execute("PRAGMA table_info(correos)")}
    for nombre, definicion in AGREGADAS:
        if nombre not in columnas:
            cx.execute(f"ALTER TABLE correos ADD COLUMN {nombre} {definicion}")


def _ahora():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def anotar(cx, correo, categoria, motivo):
    """Anota un correo recién clasificado. False si ya estaba.

    El INSERT es atómico (ON CONFLICT DO NOTHING): la unicidad la garantiza
    la base, no un chequeo previo. Un check-then-act ("¿existe? si no,
    inserto") deja una ventana de carrera entre los dos hilos que tocan
    esta conexión, y es frágil por construcción: cualquier código nuevo que
    inserte sin pasar por acá la reintroduce. Con esto, aunque alguien
    llame a `anotar()` sin el candado, la base sola evita el duplicado.
    """
    inicial = ("mostrado_sin_clasificar" if categoria in ("ERROR", "DUDA")
               else "clasificado")
    with _CANDADO:
        cur = cx.execute(
            "INSERT INTO correos (message_id, uid, uidvalidity, de, para,"
            " cc, asunto, fecha, cuerpo, adjuntos, categoria, motivo,"
            " situacion, visto, actualizado)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(message_id) DO NOTHING",
            (correo["message_id"], correo.get("uid"),
             correo.get("uidvalidity"), correo.get("de"),
             correo.get("para"), correo.get("cc"), correo.get("asunto"),
             correo.get("fecha"), (correo.get("cuerpo") or "")[:8000],
             json.dumps(correo.get("adjuntos") or [], ensure_ascii=False),
             categoria, motivo, inicial, _ahora(), _ahora()))
        cx.commit()
        return cur.rowcount > 0


def situacion(cx, message_id):
    with _CANDADO:
        f = cx.execute("SELECT situacion FROM correos WHERE message_id = ?",
                       (message_id,)).fetchone()
    return f["situacion"] if f else None


def cambiar(cx, message_id, nueva):
    if nueva not in SITUACIONES:
        raise ValueError(f"situación desconocida: {nueva}. "
                         f"Hay: {', '.join(sorted(SITUACIONES))}")
    with _CANDADO:
        cx.execute("UPDATE correos SET situacion = ?, actualizado = ?"
                   " WHERE message_id = ?", (nueva, _ahora(), message_id))
        cx.commit()


def cambiar_lote(cx, message_ids, nueva):
    """Como cambiar(), pero para varios mensajes en una sola transacción.

    Marcar un resumen entero llamando a cambiar() uno por uno hace un
    commit por fila: si el proceso muere a mitad de camino, los que ya
    se marcaron no se repiten pero los que no llegaron a marcarse sí,
    aunque Telegram ya confirmó que el mensaje entero salió y JP ya los
    vio. Acá el UPDATE de todo el lote va en una sola transacción, con
    un solo commit al final: o quedan todos marcados, o -si el proceso
    muere antes del commit- no queda marcado ninguno y el próximo
    resumen los vuelve a incluir. Repetir algo que JP ya vio es
    molesto; darlo por visto sin que lo esté es la falla silenciosa que
    este archivo existe para evitar.
    """
    if not message_ids:
        return
    if nueva not in SITUACIONES:
        raise ValueError(f"situación desconocida: {nueva}. "
                         f"Hay: {', '.join(sorted(SITUACIONES))}")
    with _CANDADO:
        momento = _ahora()
        cx.executemany(
            "UPDATE correos SET situacion = ?, actualizado = ?"
            " WHERE message_id = ?",
            [(nueva, momento, mid) for mid in message_ids])
        cx.commit()


def sumar_intento(cx, message_id):
    """Cuenta un intento fallido de archivado y devuelve cuántos van.

    El contador va en la base y no en un diccionario del proceso porque
    los reintentos tienen que sobrevivir a un reinicio: launchd levanta
    esto de nuevo cada vez que se cae, y con el contador en memoria el
    tope no se alcanzaría nunca -el correo volvería a reintentarse para
    siempre, que es justo lo que se está arreglando.
    """
    with _CANDADO:
        cx.execute("UPDATE correos SET intentos = intentos + 1,"
                   " actualizado = ? WHERE message_id = ?",
                   (_ahora(), message_id))
        cx.commit()
        f = cx.execute("SELECT intentos FROM correos WHERE message_id = ?",
                       (message_id,)).fetchone()
    return f["intentos"] if f else 0


def anotar_falla(cx, message_id, texto):
    """Guarda qué falló al intentar archivar este correo.

    Se guarda en la base y no en memoria del proceso porque el aviso a JP
    puede no salir en el momento -si Telegram está caído queda pendiente-
    y launchd reinicia esto cada vez que se cae: al volver hay que poder
    contar todavía qué fue lo que pasó, no un "algo falló".
    """
    with _CANDADO:
        cx.execute("UPDATE correos SET falla = ?, actualizado = ?"
                   " WHERE message_id = ?", (texto[:500], _ahora(),
                                             message_id))
        cx.commit()


def pendientes(cx, sit):
    with _CANDADO:
        return [dict(f) for f in cx.execute(
            "SELECT * FROM correos WHERE situacion = ? ORDER BY visto",
            (sit,))]


def del_dia(cx, sit, desde):
    with _CANDADO:
        return [dict(f) for f in cx.execute(
            "SELECT * FROM correos WHERE situacion = ? AND visto >= ?"
            " ORDER BY visto", (sit, desde))]


def obtener(cx, message_id):
    """La fila entera de un correo, como dict, o None si no está.

    A diferencia de del_dia()/pendientes(), acá los adjuntos se
    devuelven decodificados -una lista, no el JSON que guarda anotar()-
    porque quien llama a esto (Rever, en secretaria.py) reconstruye el
    dict que le pasa a clasificador.clasificar(), y ese código espera
    poder iterarlos como adjuntos, no como texto.
    """
    with _CANDADO:
        f = cx.execute("SELECT * FROM correos WHERE message_id = ?",
                       (message_id,)).fetchone()
    if not f:
        return None
    fila = dict(f)
    fila["adjuntos"] = json.loads(fila["adjuntos"] or "[]")
    return fila


def corregir(cx, message_id, categoria_nueva, explicacion):
    """Guarda que JP dijo otra cosa, y por qué.

    No pisa `categoria`: lo que dijo el sistema hay que conservarlo, si no
    se pierde la comparación que permite medir si mejora.

    Tampoco toca `situacion`. Antes la dejaba en 'corregido' por su
    cuenta, pero el único llamador (rever_ruido, en secretaria.py) la
    pisaba siempre en el paso siguiente -con 'clasificado' si la
    categoría nueva es accionable, o 'mostrado_sin_clasificar' si no- así
    que ese 'corregido' nunca llegaba a ser observable: era una
    escritura de más, sin ningún lector. Decidir qué situación
    corresponde después de una corrección depende de qué se va a hacer
    con esa corrección, y eso lo sabe quien llama, no esta función.
    """
    with _CANDADO:
        cx.execute("UPDATE correos SET categoria_jp = ?, explicacion = ?,"
                   " actualizado = ? WHERE message_id = ?",
                   (categoria_nueva, explicacion, _ahora(), message_id))
        cx.commit()


def preparar_rever(cx, message_id, explicacion):
    """Deja anotado el motivo que JP acaba de contestar para un Rever, y
    pasa la fila a "pendiente_de_rever" -tarea 11, ronda 1-.

    rever_ruido() hace red (clasificador.clasificar(), hasta tres minutos
    con nvidia) y por eso no puede correr en el hilo que escucha: acá
    sólo se guarda lo que hace falta para que ciclo_de_correo lo retome
    en su próxima vuelta, leyéndolo de la base -no de una variable en
    memoria, para que sobreviva a un reinicio del proceso entre que JP
    contestó y que se ejecuta de verdad (ver memoria.pendientes())."""
    with _CANDADO:
        cx.execute("UPDATE correos SET explicacion = ?, situacion ="
                   " 'pendiente_de_rever', actualizado = ? WHERE"
                   " message_id = ?", (explicacion, _ahora(), message_id))
        cx.commit()


def guardar_pendiente(cx, pendiente):
    """Persiste self.pendiente -tarea 11, ronda 1-: qué le preguntó la
    secretaria a JP y todavía espera contestación.

    Sin esto, un reinicio del proceso entre que JP contesta un paso de
    Rever o de Uno es mío y el siguiente le hace perder la pregunta: el
    próximo mensaje de JP -que sí contesta lo que se le pidió- cae en
    responder_pendiente() sin nada pendiente, y el bot le miente
    diciendo que no entiende preguntas sueltas cuando en realidad el que
    se olvidó fue el proceso. `pendiente=None` borra lo guardado."""
    with _CANDADO:
        if pendiente is None:
            cx.execute("DELETE FROM estado WHERE clave = 'pendiente'")
        else:
            cx.execute(
                "INSERT INTO estado (clave, valor) VALUES ('pendiente', ?)"
                " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                (json.dumps(pendiente, ensure_ascii=False),))
        cx.commit()


def cargar_pendiente(cx):
    """Lo que quedó guardado con guardar_pendiente(), o None. Se lee al
    construir la Secretaria, para retomar un reinicio a mitad de una
    pregunta en vez de perderla."""
    with _CANDADO:
        f = cx.execute("SELECT valor FROM estado WHERE clave = 'pendiente'"
                       ).fetchone()
    return json.loads(f["valor"]) if f else None


def latido(cx, momento):
    with _CANDADO:
        cx.execute("INSERT OR IGNORE INTO latidos (momento) VALUES (?)",
                   (momento,))
        cx.commit()


def contar_desde(cx, desde):
    """Cuántos correos se vieron desde `desde` (fecha ISO), para /estado.

    Con el candado, como cualquier otra lectura de acá: es la misma
    conexión que toca el otro hilo (ver el docstring del módulo), y un
    execute() suelto sin el candado reintroduce justo lo que a la tarea 6
    le costó dos rondas arreglar -filas perdidas e InterfaceError bajo
    carga con dos hilos escribiendo a la vez.
    """
    with _CANDADO:
        f = cx.execute("SELECT COUNT(*) c FROM correos WHERE visto >= ?",
                       (desde,)).fetchone()
    return f["c"]


def ultimo_latido(cx):
    with _CANDADO:
        f = cx.execute("SELECT MAX(momento) AS m FROM latidos").fetchone()
    return f["m"] if f and f["m"] else None
