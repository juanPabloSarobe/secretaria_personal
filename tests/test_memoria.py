import os
import tempfile
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
