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

SITUACIONES = {"clasificado", "archivado", "en_resumen", "avisado",
               "cerrado", "corregido", "mostrado_sin_clasificar"}

# RLock y no Lock: alguna función de acá podría terminar llamando a otra
# de acá (p.ej. anotar() llamaba a situacion()), y con un Lock simple eso
# sería un auto-bloqueo.
_CANDADO = threading.RLock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS correos (
    message_id   TEXT PRIMARY KEY,
    uid          TEXT,
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
    actualizado  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS por_situacion ON correos(situacion);
CREATE TABLE IF NOT EXISTS latidos (momento TEXT PRIMARY KEY);
"""


def abrir(ruta="datos/secretaria.db"):
    with _CANDADO:
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        cx = sqlite3.connect(ruta, check_same_thread=False, timeout=30)
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA journal_mode=WAL")   # dos hilos sin pisarse
        cx.executescript(ESQUEMA)
        cx.commit()
        return cx


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
            "INSERT INTO correos (message_id, uid, de, para, cc, asunto,"
            " fecha, cuerpo, adjuntos, categoria, motivo, situacion, visto,"
            " actualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(message_id) DO NOTHING",
            (correo["message_id"], correo.get("uid"), correo.get("de"),
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


def corregir(cx, message_id, categoria_nueva, explicacion):
    """Guarda que JP dijo otra cosa, y por qué.

    No pisa `categoria`: lo que dijo el sistema hay que conservarlo, si no
    se pierde la comparación que permite medir si mejora."""
    with _CANDADO:
        cx.execute("UPDATE correos SET categoria_jp = ?, explicacion = ?,"
                   " situacion = 'corregido', actualizado = ?"
                   " WHERE message_id = ?",
                   (categoria_nueva, explicacion, _ahora(), message_id))
        cx.commit()


def latido(cx, momento):
    with _CANDADO:
        cx.execute("INSERT OR IGNORE INTO latidos (momento) VALUES (?)",
                   (momento,))
        cx.commit()


def ultimo_latido(cx):
    with _CANDADO:
        f = cx.execute("SELECT MAX(momento) AS m FROM latidos").fetchone()
    return f["m"] if f and f["m"] else None
