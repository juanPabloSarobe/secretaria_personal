import tempfile
import unittest
import unittest.mock as mock

import memoria
import secretaria


def fila(mid, asunto, categoria, de="alguien@ejemplo.com"):
    return {"message_id": mid, "asunto": asunto, "categoria": categoria,
            "de": de, "motivo": "porque sí", "cc": "", "adjuntos": "[]",
            "fecha": "Mon, 17 Aug 2026 09:00:00 -0300"}


def correo_falso(mid, asunto, de="alguien@ejemplo.com"):
    """Como lo deja traer_nuevos(): lo que memoria.anotar() necesita."""
    return {"message_id": mid, "uid": "1", "uidvalidity": "1", "de": de,
            "para": "jp@x", "cc": "", "asunto": asunto,
            "fecha": "Mon, 17 Aug 2026 09:00:00 -0300", "cuerpo": "cuerpo",
            "adjuntos": []}


class Resumen(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def test_lo_de_jp_va_primero_y_lo_del_equipo_al_final(self):
        texto, _ = self.s.armar_resumen(
            [fila("<1@x>", "Del equipo", "DELEGADO"),
             fila("<2@x>", "Mío", "TUYO")], "manana")
        self.assertLess(texto.index("Mío"), texto.index("Del equipo"))

    def test_sin_nada_igual_manda_un_mensaje(self):
        """No recibir avisos se parece mucho a un día tranquilo. Que la
        secretaria esté rota tiene que verse."""
        texto, _ = self.s.armar_resumen([], "manana")
        self.assertIn("no entró nada", texto.lower())

    def test_el_boton_de_leer_todo_solo_aparece_si_hay_del_equipo(self):
        _, teclado = self.s.armar_resumen([fila("<1@x>", "Mío", "TUYO")], "manana")
        textos = [b["text"] for f in teclado["inline_keyboard"] for b in f]
        self.assertNotIn("✓ Leí todo", textos)

    def test_cada_resumen_lleva_su_propio_token(self):
        """Dos resúmenes del mismo día no pueden compartir token, si no el
        botón 1 del de la mañana contesta por el de la tarde."""
        _, a = self.s.armar_resumen([fila("<1@x>", "A", "DELEGADO")], "manana")
        _, b = self.s.armar_resumen([fila("<2@x>", "B", "DELEGADO")], "tarde")
        da = a["inline_keyboard"][0][0]["callback_data"]
        db = b["inline_keyboard"][0][0]["callback_data"]
        self.assertNotEqual(da.split("|")[1], db.split("|")[1])


class MandarResumen(unittest.TestCase):
    """mandar_resumen es quien decide QUÉ entra al resumen -armar_resumen
    sólo sabe convertir una lista en texto y botones-, lo manda, y
    actualiza la base según si el envío salió de verdad."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def test_junta_lo_clasificado_y_lo_archivado_pero_no_lo_ya_avisado(self):
        """Lo "avisado" ya interrumpió a JP al toque -avisar_en_el_momento-,
        así que repetirlo en el resumen sería contarle dos veces lo mismo.
        Lo "clasificado" (JP, derivar, equipo) y lo "archivado" (ruido) sí
        entran: es justo lo que no interrumpió."""
        memoria.anotar(self.s.cx, correo_falso("<1@x>", "Mío"), "TUYO", "m")
        memoria.anotar(self.s.cx, correo_falso("<2@x>", "Ruido"), "RUIDO", "r")
        memoria.cambiar(self.s.cx, "<2@x>", "archivado")
        memoria.anotar(self.s.cx, correo_falso("<3@x>", "Ya avisado"), "TUYO", "m")
        memoria.cambiar(self.s.cx, "<3@x>", "avisado")

        with mock.patch.object(self.s, "enviar",
                                return_value={"ok": True}) as enviar:
            self.s.mandar_resumen("manana")

        texto = enviar.call_args[0][0]
        self.assertIn("Mío", texto)
        self.assertIn("Archivado como ruido", texto)
        self.assertNotIn("Ya avisado", texto)

    def test_si_el_envio_sale_los_correos_pasan_a_en_resumen(self):
        """Consumidos: así el resumen de las 17:00 no repite lo que ya
        contó el de las 8:30."""
        memoria.anotar(self.s.cx, correo_falso("<1@x>", "Mío"), "TUYO", "m")
        with mock.patch.object(self.s, "enviar", return_value={"ok": True}):
            self.s.mandar_resumen("manana")
        self.assertEqual(memoria.situacion(self.s.cx, "<1@x>"), "en_resumen")

    def test_si_telegram_esta_caido_no_se_pierden(self):
        """Un aviso que no salió no puede dar el correo por contado: si no,
        se pierde en silencio -la falla que más preocupa- en vez de
        aparecer en el próximo resumen."""
        memoria.anotar(self.s.cx, correo_falso("<1@x>", "Mío"), "TUYO", "m")
        with mock.patch.object(self.s, "enviar", return_value=None):
            self.s.mandar_resumen("manana")
        self.assertEqual(memoria.situacion(self.s.cx, "<1@x>"), "clasificado")

    def test_sin_nada_pendiente_igual_manda_el_aviso_de_dia_tranquilo(self):
        with mock.patch.object(self.s, "enviar",
                                return_value={"ok": True}) as enviar:
            self.s.mandar_resumen("manana")
        self.assertIn("no entró nada", enviar.call_args[0][0].lower())


if __name__ == "__main__":
    unittest.main()
