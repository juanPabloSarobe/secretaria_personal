import json
import os
import shutil
import tempfile
import unittest

import clasificador


class CadenaDeMotores(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.antes = os.getcwd()
        shutil.copy("motores.json", self.dir)
        os.chdir(self.dir)
        os.environ.setdefault("NVIDIA_BASE_URL", "https://ejemplo/nvidia/v1")
        os.environ.setdefault("NVIDIA_API_KEY", "x")
        os.environ.setdefault("GROQ_BASE_URL", "https://ejemplo/groq/v1")
        os.environ.setdefault("GROQ_API_KEY", "x")

    def tearDown(self):
        os.chdir(self.antes)
        shutil.rmtree(self.dir)

    def test_respeta_el_orden_de_la_configuracion(self):
        self.assertEqual([m[0] for m in clasificador.motores()],
                         ["nvidia", "groq"])

    def test_el_preferido_pasa_al_frente_sin_perder_los_respaldos(self):
        """Elegir un motor no es quedarse sin red: si el elegido se queda
        sin cuota, los otros siguen atrás."""
        self.assertEqual([m[0] for m in clasificador.motores("groq")],
                         ["groq", "nvidia"])

    def test_un_motor_desconocido_avisa_cuales_hay(self):
        with self.assertRaises(ValueError) as e:
            clasificador.motores("inventado")
        self.assertIn("nvidia", str(e.exception))

    def test_ollama_esta_fuera_hasta_que_jp_lo_habilite(self):
        with self.assertRaises(ValueError):
            clasificador.motores("ollama")

    def test_elegir_motor_persiste_y_se_relee(self):
        """JP lo cambia desde Telegram: tiene que sobrevivir sin reiniciar."""
        clasificador.elegir_motor("groq")
        self.assertEqual(clasificador.preferencia()[0], "groq")
        with open("motores.json", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["preferencia"][0], "groq")


class Prompt(unittest.TestCase):
    def test_el_procedimiento_esta_numerado_y_en_orden(self):
        """El orden de los pasos decide más que su contenido: una regla en
        prosa no revierte un paso numerado. Si alguien reordena los pasos
        sin querer, esto lo caza."""
        p = clasificador.prompt_sistema()
        pasos = [p.index(f"PASO {n}") for n in range(1, 6)]
        self.assertEqual(pasos, sorted(pasos))

    def test_el_prompt_incluye_el_roster_y_las_reglas(self):
        p = clasificador.prompt_sistema()
        self.assertIn("Enzo", p)
        self.assertIn("Natalia", p)


if __name__ == "__main__":
    unittest.main()
