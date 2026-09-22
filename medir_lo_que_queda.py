#!/usr/bin/env python3
"""Mide las tres cosas que quedaron después del despacho de arreglo, con
números.

Mismo papel que `medir_costuras.py` y misma forma: no es un test, es el
instrumento con el que se midió el ANTES y el DESPUÉS. Los tests que
dejan la red permanente están en `tests/test_costuras.py`.

    set -a; . ./.env; set +a; python3 medir_lo_que_queda.py

Las tres son de la misma familia que los tres Críticos —algo sale mal y
nadie se entera— pero al revés: acá el sistema **habla de más**. Un aviso
que se repite para siempre y una caída que no existió son las dos formas
que tiene un sistema de avisos de volverse ruido de fondo, que es la
manera de perder el aviso del día en que sí importa.
"""
import tempfile
import unittest.mock as mock
from datetime import datetime, timedelta

import memoria
import secretaria
from tests import sin_red

# Este instrumento corre el código real y manda todo por dobles, pero un
# olvido acá cuesta lo mismo que en un test: mensajes de verdad al
# teléfono de JP. La misma reja que la suite (ver tests/sin_red.py).
sin_red.cortar()


def _correo(mid, asunto, de="promo@ejemplo.com"):
    return {"message_id": mid, "uid": "1", "uidvalidity": "1", "de": de,
            "para": "jp@x", "cc": "", "asunto": asunto,
            "fecha": "Mon, 17 Aug 2026 09:00:00 -0300", "cuerpo": "cuerpo",
            "adjuntos": []}


def _nueva(ruta=None):
    ruta = ruta or tempfile.NamedTemporaryFile(suffix=".db",
                                               delete=False).name
    return secretaria.Secretaria(cx=memoria.abrir(ruta)), ruta


def el_duda_que_se_repite():
    """Un DUDA cuyo aviso falló una vez: ¿cuántos mensajes manda después?

    `avisar_en_el_momento` marca "avisado" cuando el envío sale bien.
    `avisar_para_que_decida` no marcaba nada, así que el correo se
    quedaba en "pendiente_de_avisar" para siempre y
    `_atender_avisos_pendientes` -que no tiene tope, y con razón- lo
    reintentaba en cada vuelta del ciclo.
    """
    s, _ = _nueva()
    c = _correo("<duda@x>", "Orbcomm — factura y contrato",
                de="Orbcomm <facturacion@orbcomm.com>")

    # La vuelta en la que el aviso no salió.
    with mock.patch.object(secretaria.correo, "traer_nuevos",
                           return_value=[c]), \
         mock.patch.object(secretaria.clasificador, "clasificar",
                           return_value={"categoria": "DUDA", "motivo": "m",
                                         "unanime": False}), \
         mock.patch.object(secretaria, "en_horario", return_value=True), \
         mock.patch.object(s, "enviar", return_value=None):
        s.revisar_casilla()
    print(f"  situación con el aviso caído:           "
          f"{memoria.situacion(s.cx, '<duda@x>')}")

    # Telegram vuelve. Cinco vueltas más del ciclo, sin nada nuevo.
    mensajes = []
    with mock.patch.object(secretaria.correo, "traer_nuevos",
                           return_value=[]), \
         mock.patch.object(secretaria, "en_horario", return_value=True), \
         mock.patch.object(s, "enviar",
                           side_effect=lambda t, k=None: mensajes.append(t)
                           or {"ok": True}):
        for _ in range(5):
            s.revisar_casilla()

    repetidos = sum(1 for m in mensajes if "No pude decidir" in m)
    print(f"  vueltas con Telegram ya restablecido:   5")
    print(f"  mensajes 'No pude decidir' que salieron:{repetidos:>3}")
    print(f"  tandas abiertas en self.abiertos:       {len(s.abiertos)}")
    print(f"  situación final:                        "
          f"{memoria.situacion(s.cx, '<duda@x>')}")

    # Lo que se ve del lado de JP: el ciclo de correo no tiene reja de
    # horario, así que si sigue en la cola sigue toda la noche, hasta el
    # próximo corte.
    if memoria.situacion(s.cx, "<duda@x>") == "pendiente_de_avisar":
        por_hora = repetidos / 5 * (3600 / secretaria.CADA)
        horas = 15                  # de las 17:05 a las 08:30 del día siguiente
        print(f"  → sigue en la cola: a ese ritmo, de las 17:05 a las"
              f" 08:30, {int(por_hora * horas)} mensajes")
    else:
        print(f"  → salió de la cola: no se reintenta nunca más")


def la_caida_que_no_fue():
    """Una vuelta lenta -no una caída-: ¿el resumen dice "Estuve caída"?

    El hueco se mide como `ahora - ultimo_reloj`, y en `ciclo_de_correo`
    esa distancia es `wait(CADA)` MÁS lo que tardó `revisar_casilla()`.
    Con HUECO_CAIDA = 5*CADA = 900s, alcanza con que una vuelta de
    clasificación tarde más de 12 minutos.

    Y 12 minutos no es raro: ESPERA_RESPUESTA es 180s por pedido, dos
    pasadas por correo, más el backoff de los 429.
    """
    lunes = datetime(2026, 8, 17)
    fin_de_la_anterior = lunes.replace(hour=8, minute=20)

    def una_vuelta(trabajo, como_antes):
        """Una vuelta del ciclo con el mismo cuerpo que ciclo_de_correo:
        empieza CADA segundos después de la anterior y tarda `trabajo`
        en revisar la casilla. `como_antes` mide el hueco contra el
        final de la vuelta -lo que se hacía- en vez de contra su
        arranque."""
        s, _ = _nueva()
        memoria.anotar(s.cx, _correo("<t1@x>", "Cierre de etapa",
                                     de="Suhr <suhr@cliente.com>"),
                       "TUYO", "m")
        s.ultimo_reloj = fin_de_la_anterior
        arranque = fin_de_la_anterior + timedelta(seconds=secretaria.CADA)
        ahora = arranque + timedelta(seconds=trabajo)

        mensajes = []
        with mock.patch.object(s, "enviar",
                               side_effect=lambda t, k=None: mensajes.append(t)
                               or {"ok": True}):
            if como_antes:
                s._disparar_resumenes(ahora)
            else:
                s._disparar_resumenes(ahora, arranque)
        return [m for m in mensajes if "Estuve caída" in m]

    for trabajo in (60, 12 * 60, 13 * 60, 30 * 60):
        antes = una_vuelta(trabajo, como_antes=True)
        ahora = una_vuelta(trabajo, como_antes=False)
        print(f"  revisar_casilla tardó {trabajo // 60:>2} min"
              f" → midiendo contra el fin de la vuelta:"
              f" {'DICE que estuvo caída' if antes else 'no dice nada    '}"
              f" | contra el arranque:"
              f" {'DICE que estuvo caída' if ahora else 'no dice nada'}")
        if antes:
            print(f"      → {antes[0].splitlines()[0]}")

    # Y la contracara: un hueco de verdad se sigue viendo.
    s, _ = _nueva()
    memoria.anotar(s.cx, _correo("<t2@x>", "Cierre de etapa",
                                 de="Suhr <suhr@cliente.com>"), "TUYO", "m")
    s.ultimo_reloj = lunes.replace(hour=7, minute=0)
    arranque = lunes.replace(hour=19, minute=0)
    mensajes = []
    with mock.patch.object(s, "enviar",
                           side_effect=lambda t, k=None: mensajes.append(t)
                           or {"ok": True}):
        s._disparar_resumenes(arranque + timedelta(seconds=13 * 60), arranque)
    dice = [m for m in mensajes if "Estuve caída" in m]
    print(f"  caída DE VERDAD de 12 h, descubierta por una vuelta lenta:"
          f" {'la ve' if dice else 'NO LA VE'}")
    if dice:
        print(f"      → {dice[0].splitlines()[0]}")


def el_resumen_grande_con_ruido():
    """Mucho volumen Y ruido archivado en la base: ¿el resumen general
    se come lo archivado?

    El camino de `_mandar_resumen_grande` es el que corre después de una
    caída larga -o sea, al mismo tiempo que el CRÍTICO 3- y es el único
    de los dos caminos del resumen que ningún test cruzaba con ruido
    archivado. Sacar el filtro `if c["message_id"] in consumibles` del
    cierre reproduce el CRÍTICO 1 entero por este otro camino.
    """
    s, _ = _nueva()
    # Suficiente accionable para que ni lo accionable solo entre en un
    # mensaje: ahí es donde arranca _mandar_resumen_grande.
    for n in range(120):
        memoria.anotar(s.cx, _correo(
            f"<t{n}@x>", f"Asunto largo número {n} " + "x" * 40,
            de=f"Cliente Con Nombre Largo {n} <cliente{n}@ejemplo.com>"),
            "TUYO", "m")
    for n in range(3):
        memoria.anotar(s.cx, _correo(f"<r{n}@x>", f"Promo {n}"), "RUIDO", "p")
        memoria.cambiar(s.cx, f"<r{n}@x>", "archivado")

    mensajes = []
    with mock.patch.object(s, "enviar",
                           side_effect=lambda t, k=None: mensajes.append(t)
                           or {"ok": True}):
        s.mandar_resumen("tarde")
    por_el_grande = len(mensajes) > 1
    quedan = len(memoria.del_dia(s.cx, "archivado", ""))
    print(f"  mensajes del resumen de las 17:00:      {len(mensajes)}"
          f" ({'camino de mucho volumen' if por_el_grande else 'NO tomó el camino grande'})")
    print(f"  ruidos archivados antes del resumen:    3")
    print(f"  siguen en 'archivado' después:          {quedan}")

    mensajes.clear()
    with mock.patch.object(s, "enviar",
                           side_effect=lambda t, k=None: mensajes.append(t)
                           or {"ok": True}):
        s.mandar_resumen("ruido")
    de_las_18 = mensajes[-1]
    listados = sum(1 for n in range(3) if f"Promo {n}" in de_las_18)
    print(f"  listados en el mensaje de las 18:00:    {listados}")
    print(f"  dice 'No archivé nada como ruido':      "
          f"{'sí' if 'No archivé nada' in de_las_18 else 'no'}")


if __name__ == "__main__":
    for titulo, f in (
            ("1 — el DUDA que se avisa una y otra vez", el_duda_que_se_repite),
            ("2 — la caída que fue una vuelta lenta", la_caida_que_no_fue),
            ("3 — el resumen de mucho volumen con ruido archivado",
             el_resumen_grande_con_ruido)):
        print(f"\n{titulo}")
        f()
    print()
