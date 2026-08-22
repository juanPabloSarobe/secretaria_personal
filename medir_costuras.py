#!/usr/bin/env python3
"""Mide las tres fallas de costura del despacho final, con números.

No es un test: es el instrumento con el que se midió el ANTES y el
DESPUÉS de cada arreglo, con la misma forma en que se venía midiendo en
todo el proyecto. Los tests que dejan la red permanente están en
tests/test_costuras.py; esto se corre a mano y escupe los números.

    set -a; . ./.env; set +a; python3 medir_costuras.py
"""
import tempfile
import unittest.mock as mock
from datetime import datetime, timedelta

import memoria
import secretaria


def _correo(mid, asunto, de="promo@ejemplo.com"):
    return {"message_id": mid, "uid": "1", "uidvalidity": "1", "de": de,
            "para": "jp@x", "cc": "", "asunto": asunto,
            "fecha": "Mon, 17 Aug 2026 09:00:00 -0300", "cuerpo": "cuerpo",
            "adjuntos": []}


def _nueva(ruta=None):
    ruta = ruta or tempfile.NamedTemporaryFile(suffix=".db",
                                               delete=False).name
    return secretaria.Secretaria(cx=memoria.abrir(ruta)), ruta


def critico_1():
    """Día con 3 ruidos: ¿el resumen de las 18:00 los lista?"""
    s, _ = _nueva()
    for n in range(3):
        memoria.anotar(s.cx, _correo(f"<r{n}@x>", f"Promo {n}"), "RUIDO", "p")
        memoria.cambiar(s.cx, f"<r{n}@x>", "archivado")
    memoria.anotar(s.cx, _correo("<t1@x>", "Cierre de etapa"), "TUYO", "m")

    mensajes = []
    with mock.patch.object(s, "enviar",
                           side_effect=lambda t, k=None: mensajes.append(t)
                           or {"ok": True}):
        s.mandar_resumen("manana")
        s.mandar_resumen("tarde")
        s.mandar_resumen("ruido")

    de_las_18 = mensajes[-1]
    listados = sum(1 for n in range(3) if f"Promo {n}" in de_las_18)
    reversibles = sum(len(v) for v in s.abiertos.values())
    print(f"  ruidos archivados en el día:            3")
    print(f"  listados en el mensaje de las 18:00:    {listados}")
    print(f"  correos reversibles con Rever:          {reversibles}")
    print(f"  dice 'No archivé nada como ruido':      "
          f"{'sí' if 'No archivé nada' in de_las_18 else 'no'}")

    # Segundo camino: proceso caído de 16:00 a 18:30.
    s2, _ = _nueva()
    memoria.anotar(s2.cx, _correo("<r9@x>", "Promo tarde"), "RUIDO", "p")
    memoria.cambiar(s2.cx, "<r9@x>", "archivado")
    s2.ultimo_reloj = datetime(2026, 8, 17, 16, 0)
    mandados = []
    with mock.patch.object(s2, "mandar_resumen",
                           side_effect=lambda m: mandados.append(m)):
        s2._disparar_resumenes(datetime(2026, 8, 17, 18, 30))
    print(f"  caída 16:00-18:30, resúmenes mandados:  {mandados}")


def critico_2():
    """Telegram caído sobre un correo TUYO: ¿desaparece del sistema?"""
    s, _ = _nueva()
    c = _correo("<mio@x>", "Suhr Ingeniería — cierre", de="suhr@cliente.com")

    with mock.patch.object(secretaria.correo, "traer_nuevos",
                           return_value=[c]), \
         mock.patch.object(secretaria.clasificador, "clasificar",
                           return_value={"categoria": "TUYO", "motivo": "m",
                                         "unanime": True}), \
         mock.patch.object(secretaria, "en_horario", return_value=True), \
         mock.patch.object(secretaria.bot, "tg",
                           side_effect=RuntimeError("Telegram caído")):
        s.revisar_casilla()

    sit = memoria.situacion(s.cx, "<mio@x>")
    print(f"  situación tras el aviso que no salió:   {sit}")

    # Telegram vuelve: la vuelta siguiente del ciclo, y después el
    # resumen de las 8:30.
    mensajes = []
    with mock.patch.object(secretaria.correo, "traer_nuevos",
                           return_value=[]), \
         mock.patch.object(secretaria, "en_horario", return_value=True), \
         mock.patch.object(s, "enviar",
                           side_effect=lambda t, k=None: mensajes.append(t)
                           or {"ok": True}):
        s.revisar_casilla()
        s.mandar_resumen("manana")
    texto = "\n".join(mensajes)
    print(f"  el correo de JP reaparece cuando vuelve:"
          f" {'sí' if 'Suhr' in texto else 'NO'}")
    print(f"  el resumen dice 'No entró nada nuevo':  "
          f"{'sí' if 'No entró nada nuevo' in texto else 'no'}")
    print(f"  situación final:                        "
          f"{memoria.situacion(s.cx, '<mio@x>')}")


def critico_3():
    """Reinicio del proceso: ¿el reloj de recuperación sobrevive?

    Escenario fijo y en día hábil a propósito: el lunes 17/08 la
    secretaria late por última vez a las 07:00 y se muere. launchd la
    levanta a las 19:00 del mismo día. Se perdieron los tres cortes.
    Con `datetime.now()` el escenario sería el de hoy y podría caer en
    fin de semana, donde no corresponde ningún corte y el número no
    diría nada.
    """
    murio = datetime(2026, 8, 17, 7, 0)
    volvio = datetime(2026, 8, 17, 19, 0)

    s, ruta = _nueva()
    memoria.latido(s.cx, murio.isoformat(timespec="seconds"))

    otra, _ = _nueva(ruta)          # launchd la levanta de nuevo
    print(f"  último latido:                          {murio}")
    print(f"  ultimo_reloj de la Secretaria nueva:    {otra.ultimo_reloj}")
    print(f"  lo retomó de la base:                   "
          f"{'sí' if otra.ultimo_reloj == murio else 'NO'}")

    mandados = []
    with mock.patch.object(otra, "mandar_resumen",
                           side_effect=lambda m: mandados.append(m)):
        otra._disparar_resumenes(volvio)
    print(f"  resúmenes recuperados tras el reinicio: {mandados}")

    mensajes = []
    with mock.patch.object(otra, "enviar",
                           side_effect=lambda t, k=None: mensajes.append(t)
                           or {"ok": True}):
        otra.mandar_resumen("tarde")
        otra.mandar_resumen("ruido")
    dice = [m for m in mensajes if "Estuve caída" in m]
    print(f"  el resumen dice que estuvo caída:       "
          f"{'sí' if dice else 'NO'}")
    print(f"  y lo dice UNA vez, no en cada resumen:  "
          f"{'sí' if len(dice) == 1 else f'no ({len(dice)})'}")
    if dice:
        print(f"    → {dice[0].splitlines()[0]}")


if __name__ == "__main__":
    for titulo, f in (("CRÍTICO 1 — el resumen de ruido de las 18:00", critico_1),
                      ("CRÍTICO 2 — el aviso que Telegram no entregó", critico_2),
                      ("CRÍTICO 3 — el reloj de recuperación", critico_3)):
        print(f"\n{titulo}")
        f()
    print()
