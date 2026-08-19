#!/usr/bin/env python3
"""El estado de cada correo, en SQLite.

Dos hilos tocan esto a la vez: el que escucha a JP y el que procesa correo.
Con archivos JSON se corrompe el día que coincidan. SQLite viene con Python
y resuelve el bloqueo solo.

Está en disco y no en memoria a propósito: si el proceso se cae, al volver
retoma sin reclasificar lo que ya clasificó ni volver a avisar lo que ya
avisó.
"""
import json
import os
import sqlite3

SITUACIONES = {"clasificado", "archivado", "en_resumen", "avisado",
               "cerrado", "corregido", "mostrado_sin_clasificar"}

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
    """Anota un correo recién clasificado. False si ya estaba."""
    if situacion(cx, correo["message_id"]) is not None:
        return False
    inicial = ("mostrado_sin_clasificar" if categoria in ("ERROR", "DUDA")
               else "clasificado")
    cx.execute(
        "INSERT INTO correos (message_id, uid, de, para, cc, asunto, fecha,"
        " cuerpo, adjuntos, categoria, motivo, situacion, visto, actualizado)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (correo["message_id"], correo.get("uid"), correo.get("de"),
         correo.get("para"), correo.get("cc"), correo.get("asunto"),
         correo.get("fecha"), (correo.get("cuerpo") or "")[:8000],
         json.dumps(correo.get("adjuntos") or [], ensure_ascii=False),
         categoria, motivo, inicial, _ahora(), _ahora()))
    cx.commit()
    return True


def situacion(cx, message_id):
    f = cx.execute("SELECT situacion FROM correos WHERE message_id = ?",
                   (message_id,)).fetchone()
    return f["situacion"] if f else None


def cambiar(cx, message_id, nueva):
    if nueva not in SITUACIONES:
        raise ValueError(f"situación desconocida: {nueva}. "
                         f"Hay: {', '.join(sorted(SITUACIONES))}")
    cx.execute("UPDATE correos SET situacion = ?, actualizado = ?"
               " WHERE message_id = ?", (nueva, _ahora(), message_id))
    cx.commit()


def pendientes(cx, sit):
    return [dict(f) for f in cx.execute(
        "SELECT * FROM correos WHERE situacion = ? ORDER BY visto", (sit,))]


def del_dia(cx, sit, desde):
    return [dict(f) for f in cx.execute(
        "SELECT * FROM correos WHERE situacion = ? AND visto >= ?"
        " ORDER BY visto", (sit, desde))]


def corregir(cx, message_id, categoria_nueva, explicacion):
    """Guarda que JP dijo otra cosa, y por qué.

    No pisa `categoria`: lo que dijo el sistema hay que conservarlo, si no
    se pierde la comparación que permite medir si mejora."""
    cx.execute("UPDATE correos SET categoria_jp = ?, explicacion = ?,"
               " situacion = 'corregido', actualizado = ?"
               " WHERE message_id = ?",
               (categoria_nueva, explicacion, _ahora(), message_id))
    cx.commit()


def latido(cx, momento):
    cx.execute("INSERT OR IGNORE INTO latidos (momento) VALUES (?)", (momento,))
    cx.commit()


def ultimo_latido(cx):
    f = cx.execute("SELECT MAX(momento) AS m FROM latidos").fetchone()
    return f["m"] if f and f["m"] else None
