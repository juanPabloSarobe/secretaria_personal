import unittest

import clasificador


class OrdenDelProcedimiento(unittest.TestCase):
    def test_el_pedido_concreto_se_evalua_antes_que_quien_esta_en_copia(self):
        """El defecto que tuvo a ENZO en 0%: 'el responsable está en copia'
        contestaba DELEGADO y frenaba, antes de preguntarse si el correo
        era un pedido que alguien tiene que ejecutar."""
        p = clasificador.prompt_sistema()
        self.assertLess(p.index("PASO 4"), p.index("PASO 5"))
        self.assertIn("pedido concreto", p.lower())

    def test_dice_que_estar_en_copia_no_cancela_la_tarea(self):
        p = clasificador.prompt_sistema().lower()
        self.assertIn("estar en copia no", p)


if __name__ == "__main__":
    unittest.main()
