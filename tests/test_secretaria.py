import threading
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import secretaria


class Reloj(unittest.TestCase):
    def setUp(self):
        self.r = secretaria.Reloj()

    def test_dispara_el_resumen_al_pasar_la_hora(self):
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 17, 8, 31),
            ultimo=datetime(2026, 8, 17, 8, 29))
        self.assertEqual(pendientes, ["manana"])

    def test_no_lo_dispara_dos_veces(self):
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 17, 8, 45),
            ultimo=datetime(2026, 8, 17, 8, 31))
        self.assertEqual(pendientes, [])

    def test_si_estuvo_caida_dispara_lo_que_se_perdio_en_orden(self):
        """launchd la levanta después de una caída larga. Los resúmenes que
        se perdió tienen que salir, y en orden."""
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 17, 19, 0),
            ultimo=datetime(2026, 8, 17, 7, 0))
        self.assertEqual(pendientes, ["manana", "tarde", "ruido"])


class Horario(unittest.TestCase):
    def test_un_martes_al_mediodia_esta_en_horario(self):
        self.assertTrue(secretaria.en_horario(datetime(2026, 8, 18, 12, 0)))

    def test_un_martes_a_las_seis_de_la_manana_no(self):
        self.assertFalse(secretaria.en_horario(datetime(2026, 8, 18, 6, 0)))

    def test_un_domingo_no(self):
        self.assertFalse(secretaria.en_horario(datetime(2026, 8, 16, 12, 0)))


class NoSeBloqueaLaEscucha(unittest.TestCase):
    """La razón de ser de los dos hilos: si revisar_casilla se queda
    trabajando (clasificar tarda hasta 180s), el hilo que escucha a JP
    tiene que seguir atendiendo updates igual. Sin esto, un solo hilo
    dejaría a JP mudo todo ese rato.

    Nada de sleep con número mágico de segundos para simular la demora:
    revisar_casilla se bloquea con un threading.Event que el test controla,
    y se libera explícitamente. El único tiempo de espera "a ciegas" es un
    polling acotado a 2s como techo de seguridad, no como duración
    esperada."""

    def test_la_escucha_sigue_atendiendo_mientras_el_correo_tarda(self):
        # memoria.latido() se llama al final de cada vuelta del ciclo de
        # correo; le alcanza con un objeto que acepte execute()/commit()
        # sin hacer nada, no hace falta una base de datos de verdad.
        class _CxMuda:
            def execute(self, *a, **k):
                pass

            def commit(self):
                pass

        s = secretaria.Secretaria(cx=_CxMuda())

        adentro = threading.Event()
        liberar = threading.Event()

        def revisar_casilla_lenta():
            adentro.set()
            liberar.wait(5)  # "tarda mucho": no vuelve hasta que el test avise

        atendidos = []
        s.revisar_casilla = revisar_casilla_lenta
        s.atender = lambda update: atendidos.append(update["update_id"])

        contador = {"n": 0}

        def tg_falso(metodo, **params):
            # Sin red: cada llamada a getUpdates devuelve un update nuevo
            # al toque, como si Telegram contestara instantáneo.
            contador["n"] += 1
            return {"result": [{"update_id": contador["n"]}]}

        with patch("secretaria.bot.tg", side_effect=tg_falso):
            hilo_correo = threading.Thread(target=s.ciclo_de_correo,
                                            daemon=True)
            hilo_escucha = threading.Thread(target=s.ciclo_de_escucha,
                                             daemon=True)
            hilo_correo.start()
            hilo_escucha.start()
            try:
                self.assertTrue(adentro.wait(2),
                                 "revisar_casilla nunca arrancó")

                # Techo de 2s para juntar unos cuantos updates; en la
                # práctica se junta en milisegundos porque nada bloquea al
                # hilo de escucha.
                limite = time.monotonic() + 2
                while len(atendidos) < 3 and time.monotonic() < limite:
                    time.sleep(0.01)
            finally:
                s.parada.set()
                liberar.set()
                hilo_correo.join(2)
                hilo_escucha.join(2)

        self.assertGreaterEqual(
            len(atendidos), 3,
            "el hilo de escucha no atendió nada mientras el de correo"
            " seguía adentro de revisar_casilla: se estarían bloqueando"
            " entre sí")


if __name__ == "__main__":
    unittest.main()
