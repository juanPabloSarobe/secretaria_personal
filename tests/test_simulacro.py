import os
import unittest
from unittest.mock import patch

import simulacro


class PedirExplicacion(unittest.TestCase):
    """pedir_explicacion() reconoce el botón 'Saltear' comparando dos cosas:
    que sea de esta tanda-idx (es_de_esta_tanda) Y que la acción sea 'x|'.
    Un test que solo llame a es_de_esta_tanda() por separado no alcanza para
    probar la línea real: hay que ejercitar la función compuesta, con un
    callback_query que tenga la tanda-idx correcta pero OTRA acción, y
    confirmar que no se confunde con Saltear."""

    def setUp(self):
        # pedir_explicacion lee TELEGRAM_CHAT_ID directo de os.environ antes
        # de llamar a tg (mockeado); sin esto el KeyError pasa antes que el
        # mock entre en juego.
        self.env = patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "12345"})
        self.env.start()
        self.addCleanup(self.env.stop)

    @patch("simulacro.tg_suave")
    @patch("simulacro.tg")
    def test_un_boton_de_categoria_no_se_acepta_como_saltear(self, mock_tg, mock_tg_suave):
        idx, tanda = 3, simulacro.TANDA
        mock_tg.side_effect = [
            {"ok": True},  # respuesta al sendMessage inicial
            {"result": [
                # mismo tanda-idx que espera la función, pero acción 'c' (categoría)
                {"update_id": 10, "callback_query":
                 {"id": "cb1", "data": f"c|{tanda}-{idx}|RUIDO"}},
                # en el mismo lote, la explicación de verdad
                {"update_id": 11, "message": {"text": "porque sí"}},
            ]},
        ]
        resultado = simulacro.pedir_explicacion(idx, 0, "TUYO", "RUIDO")
        # si la línea 159 tratara el botón de categoría como Saltear, acá
        # devolvería (None, 11) sin llegar a leer el mensaje de texto
        self.assertEqual(resultado, ("porque sí", 12))

    @patch("simulacro.tg_suave")
    @patch("simulacro.tg")
    def test_un_boton_de_saltear_si_se_acepta(self, mock_tg, mock_tg_suave):
        idx, tanda = 3, simulacro.TANDA
        mock_tg.side_effect = [
            {"ok": True},  # respuesta al sendMessage inicial
            {"result": [
                {"update_id": 20, "callback_query":
                 {"id": "cb2", "data": f"x|{tanda}-{idx}|0"}},
            ]},
        ]
        resultado = simulacro.pedir_explicacion(idx, 0, "TUYO", "RUIDO")
        self.assertEqual(resultado, (None, 21))


if __name__ == "__main__":
    unittest.main()
