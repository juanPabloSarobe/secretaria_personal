import unittest

import bot


class BotonesDeOtraTanda(unittest.TestCase):
    """Los botones de Telegram no vencen nunca. Un resumen de ayer sigue
    teniendo botones vivos, y como todo resumen numera desde 1, sin el
    token del envío el botón '3' de ayer contesta por el correo 3 de hoy.
    Ya casi nos pasa una vez."""

    def test_el_boton_de_esta_tanda_se_acepta(self):
        t = bot.teclado(3, "a1b2")
        dato = t["inline_keyboard"][0][0]["callback_data"]
        self.assertTrue(bot.es_de_esta_tanda(dato, 3, "a1b2"))

    def test_el_boton_de_otra_tanda_se_rechaza(self):
        dato = bot.teclado(3, "vieja")["inline_keyboard"][0][0]["callback_data"]
        self.assertFalse(bot.es_de_esta_tanda(dato, 3, "a1b2"))

    def test_el_boton_de_otro_correo_se_rechaza(self):
        dato = bot.teclado(3, "a1b2")["inline_keyboard"][0][0]["callback_data"]
        self.assertFalse(bot.es_de_esta_tanda(dato, 7, "a1b2"))

    def test_un_dato_con_forma_rara_no_revienta(self):
        self.assertFalse(bot.es_de_esta_tanda("basura", 3, "a1b2"))
        self.assertFalse(bot.es_de_esta_tanda("", 3, "a1b2"))

    def test_es_de_esta_tanda_no_distingue_la_accion(self):
        """es_de_esta_tanda valida tanda e índice, no qué acción es. Un botón
        de categoría (acción 'c') de esta misma tanda-idx pasa la función
        igual que uno de 'Saltear' (acción 'x') la pasaría. Por eso
        pedir_explicacion no puede reemplazar su chequeo de 'x|' por
        es_de_esta_tanda a secas: tiene que componer las dos cosas, o
        aceptaría como Saltear cualquier botón de la misma tanda-idx."""
        dato = bot.teclado(3, "a1b2")["inline_keyboard"][0][0]["callback_data"]
        self.assertTrue(dato.startswith("c|"))
        self.assertTrue(bot.es_de_esta_tanda(dato, 3, "a1b2"))
        self.assertFalse(dato.startswith("x|"))


class Teclado(unittest.TestCase):
    def test_estan_las_seis_categorias_y_la_estrella(self):
        t = bot.teclado(1, "a1b2")
        textos = [b["text"] for fila in t["inline_keyboard"] for b in fila]
        self.assertEqual(len(textos), 7)
        self.assertIn("⭐ Cliente importante", textos)

    def test_callback_data_entra_en_los_64_bytes_de_telegram(self):
        t = bot.teclado(999, "a1b2")
        for fila in t["inline_keyboard"]:
            for b in fila:
                self.assertLessEqual(len(b["callback_data"].encode()), 64)


if __name__ == "__main__":
    unittest.main()
