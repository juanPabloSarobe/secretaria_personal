import os
import tempfile
import threading
import unittest

import memoria


def un_correo(mid="<a@b.com>", asunto="Prueba"):
    return {"message_id": mid, "uid": "1", "de": "a@b.com",
            "para": "jpsarobe@fullcontrolgps.com.ar", "cc": "",
            "asunto": asunto, "fecha": "Mon, 17 Aug 2026 09:00:00 -0300",
            "cuerpo": "texto", "adjuntos": []}


class Anotar(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.cx = memoria.abrir(self.f.name)

    def tearDown(self):
        self.cx.close()
        os.unlink(self.f.name)

    def test_un_correo_nuevo_se_anota_y_queda_clasificado(self):
        self.assertTrue(memoria.anotar(self.cx, un_correo(), "RUIDO", "promo"))
        self.assertEqual(memoria.situacion(self.cx, "<a@b.com>"), "clasificado")

    def test_el_mismo_correo_no_se_anota_dos_veces(self):
        """Si el proceso se cae y vuelve, no puede reclasificar ni volver a
        avisar lo que ya procesó."""
        memoria.anotar(self.cx, un_correo(), "RUIDO", "promo")
        self.assertFalse(memoria.anotar(self.cx, un_correo(), "RUIDO", "promo"))

    def test_una_situacion_inventada_se_rechaza(self):
        memoria.anotar(self.cx, un_correo(), "RUIDO", "promo")
        with self.assertRaises(ValueError):
            memoria.cambiar(self.cx, "<a@b.com>", "inventada")

    def test_se_listan_los_pendientes_de_una_situacion(self):
        memoria.anotar(self.cx, un_correo("<1@x>", "uno"), "RUIDO", "m")
        memoria.anotar(self.cx, un_correo("<2@x>", "dos"), "TUYO", "m")
        memoria.cambiar(self.cx, "<1@x>", "archivado")
        p = memoria.pendientes(self.cx, "archivado")
        self.assertEqual([c["asunto"] for c in p], ["uno"])


class Concurrencia(unittest.TestCase):
    """El hilo que escucha a JP por Telegram y el que procesa correo tocan
    la misma conexión a la vez. Sin coordinación esto disparaba
    sqlite3.InterfaceError e IntegrityError, y perdía filas en silencio
    (398 de 400 esperadas en la corrida que encontró el problema). Si
    alguien saca el candado de memoria.py, este test tiene que volver a
    fallar."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.cx = memoria.abrir(self.f.name)

    def tearDown(self):
        self.cx.close()
        os.unlink(self.f.name)

    def test_dos_hilos_anotando_y_cambiando_en_paralelo_no_rompen_la_base(self):
        n = 150
        errores = []

        def trabajador(prefijo):
            for i in range(n):
                mid = f"<{prefijo}-{i}@x.com>"
                try:
                    memoria.anotar(self.cx, un_correo(mid, f"{prefijo}-{i}"),
                                    "RUIDO", "m")
                    memoria.cambiar(self.cx, mid, "archivado")
                    if memoria.situacion(self.cx, mid) != "archivado":
                        errores.append(f"{mid}: quedó a medias")
                except Exception as e:
                    errores.append(f"{mid}: {type(e).__name__}: {e}")

        hilos = [threading.Thread(target=trabajador, args=(prefijo,))
                 for prefijo in ("A", "B")]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        self.assertEqual(errores, [])
        total = self.cx.execute(
            "SELECT COUNT(*) AS n FROM correos").fetchone()["n"]
        self.assertEqual(total, 2 * n)


class Correcciones(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.cx = memoria.abrir(self.f.name)
        memoria.anotar(self.cx, un_correo(), "RUIDO", "parecía promo")

    def tearDown(self):
        self.cx.close()
        os.unlink(self.f.name)

    def test_corregir_guarda_lo_que_dijo_jp_y_por_que(self):
        memoria.corregir(self.cx, "<a@b.com>", "NATALIA",
                         "SiPago es mi proveedor de cobros")
        fila = self.cx.execute(
            "SELECT categoria, categoria_jp, explicacion, situacion "
            "FROM correos WHERE message_id = ?", ("<a@b.com>",)).fetchone()
        self.assertEqual(fila["categoria"], "RUIDO")
        self.assertEqual(fila["categoria_jp"], "NATALIA")
        self.assertIn("cobros", fila["explicacion"])
        self.assertEqual(fila["situacion"], "corregido")


class Intentos(unittest.TestCase):
    """El contador de intentos de archivado vive en la base y no en la
    memoria del proceso: los reintentos tienen que sobrevivir a un
    reinicio. Si el contador se perdiera cada vez que launchd levanta el
    proceso de nuevo, el tope no se alcanzaría nunca y el correo se
    reintentaría para siempre, que es justo lo que se está arreglando."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.cx = memoria.abrir(self.f.name)

    def tearDown(self):
        self.cx.close()
        os.unlink(self.f.name)

    def test_un_correo_recien_anotado_no_tiene_intentos(self):
        memoria.anotar(self.cx, un_correo(), "RUIDO", "promo")
        fila = self.cx.execute("SELECT intentos FROM correos WHERE"
                               " message_id = ?", ("<a@b.com>",)).fetchone()
        self.assertEqual(fila["intentos"], 0)

    def test_cada_intento_suma_uno_y_devuelve_cuantos_van(self):
        memoria.anotar(self.cx, un_correo(), "RUIDO", "promo")
        self.assertEqual(memoria.sumar_intento(self.cx, "<a@b.com>"), 1)
        self.assertEqual(memoria.sumar_intento(self.cx, "<a@b.com>"), 2)
        self.assertEqual(memoria.sumar_intento(self.cx, "<a@b.com>"), 3)

    def test_los_intentos_de_un_correo_no_cuentan_para_otro(self):
        memoria.anotar(self.cx, un_correo("<1@x>"), "RUIDO", "promo")
        memoria.anotar(self.cx, un_correo("<2@x>"), "RUIDO", "promo")
        memoria.sumar_intento(self.cx, "<1@x>")
        memoria.sumar_intento(self.cx, "<1@x>")
        self.assertEqual(memoria.sumar_intento(self.cx, "<2@x>"), 1)


class BaseVieja(unittest.TestCase):
    def test_una_base_sin_la_columna_intentos_se_migra_sola(self):
        """La base que ya corre en la Mac mini se creó sin `intentos`, y
        CREATE TABLE IF NOT EXISTS no toca una tabla que ya existe. Sin
        migración, el primer reintento de archivado reventaría con
        OperationalError contra la casilla de verdad y no acá."""
        import sqlite3
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        vieja = sqlite3.connect(f.name)
        vieja.execute(
            "CREATE TABLE correos (message_id TEXT PRIMARY KEY, uid TEXT,"
            " de TEXT, para TEXT, cc TEXT, asunto TEXT, fecha TEXT,"
            " cuerpo TEXT, adjuntos TEXT, categoria TEXT, motivo TEXT,"
            " categoria_jp TEXT, explicacion TEXT, situacion TEXT NOT NULL,"
            " visto TEXT NOT NULL, actualizado TEXT NOT NULL)")
        vieja.commit()
        vieja.close()

        cx = memoria.abrir(f.name)
        memoria.anotar(cx, un_correo(), "RUIDO", "promo")
        self.assertEqual(memoria.sumar_intento(cx, "<a@b.com>"), 1)
        cx.close()
        os.unlink(f.name)


class Latido(unittest.TestCase):
    def test_sin_latidos_devuelve_none(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cx = memoria.abrir(f.name)
        self.assertIsNone(memoria.ultimo_latido(cx))
        memoria.latido(cx, "2026-08-17T09:00:00")
        self.assertEqual(memoria.ultimo_latido(cx), "2026-08-17T09:00:00")
        cx.close()
        os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
