import os
import tempfile
import unittest

import reglas


class Codigos(unittest.TestCase):
    def test_reconoce_la_frase_sin_importar_los_acentos(self):
        """JP escribe desde el celular y a veces sin tildes. Un código que
        solo matchea con tilde no sirve para nada."""
        c = {"asunto": "Consulta", "cuerpo": "Tal cual lo charlado, te mando"}
        self.assertIsNotNone(reglas.tiene_codigo(c, ["tal cual lo charlado"]))
        c2 = {"asunto": "", "cuerpo": "TAL CUAL LO CHARLADO"}
        self.assertIsNotNone(reglas.tiene_codigo(c2, ["tal cual lo charlado"]))

    def test_tambien_lo_busca_en_el_asunto(self):
        c = {"asunto": "De acuerdo a lo conversado", "cuerpo": ""}
        self.assertIsNotNone(reglas.tiene_codigo(c, ["de acuerdo a lo conversado"]))

    def test_sin_codigo_devuelve_none(self):
        c = {"asunto": "Hola", "cuerpo": "Nada que ver"}
        self.assertIsNone(reglas.tiene_codigo(c, ["tal cual lo charlado"]))


class Protegido(unittest.TestCase):
    def test_mira_el_roster_y_tambien_las_reglas(self):
        """Sobre lo que JP ya se pronunció, el sistema no decide solo. Una
        vez el filtro automático archivó una promo de SiPago que tenía una
        regla escrita, porque protegido() solo miraba roster.md."""
        self.assertTrue(reglas.protegido({"de": "x <cobros@sipago.com.ar>"}),
                         msg="si esto falla, puede ser que sipago.com.ar ya no "
                             "figure en roster.md/reglas.md (edición manual de "
                             "JP), no necesariamente que protegido() esté rota")
        self.assertTrue(reglas.protegido({"de": "x <algo@imseg.com>"}),
                         msg="si esto falla, puede ser que imseg.com ya no "
                             "figure en roster.md/reglas.md (edición manual de "
                             "JP), no necesariamente que protegido() esté rota")

    def test_un_remitente_desconocido_no_esta_protegido(self):
        self.assertFalse(reglas.protegido({"de": "x <nadie@ejemplo-raro.com>"}))


class Conocimiento(unittest.TestCase):
    def test_junta_los_tres_archivos(self):
        t = reglas.texto_de_conocimiento()
        self.assertIn("Enzo", t)
        self.assertIn("Natalia", t)
        self.assertGreater(len(t), 5000)


if __name__ == "__main__":
    unittest.main()
