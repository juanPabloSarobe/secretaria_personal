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


class ProtegidoLogica(unittest.TestCase):
    """El comportamiento de protegido(): OR entre roster.md y reglas.md. Esto
    es lo que se rompió una vez —cuando protegido() solo miraba el roster— y
    lo que hay que fijar con certeza. Usa archivos temporales con contenido
    inventado, así el resultado no depende de una palabra en particular
    dentro de roster.md/reglas.md reales, que JP edita a mano y reorganiza
    sin avisar."""

    def setUp(self):
        # protegido() abre "roster.md" y "reglas.md" por nombre relativo, sin
        # recibir la ruta como parámetro — para aislarlo hay que pararse en
        # un directorio con esos dos archivos, y volver al original después.
        dir_original = os.getcwd()
        tmp = tempfile.TemporaryDirectory()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, dir_original)
        self.addCleanup(tmp.cleanup)

    def _escribir_archivos(self, roster="", reglas_texto=""):
        with open("roster.md", "w", encoding="utf-8") as f:
            f.write(roster)
        with open("reglas.md", "w", encoding="utf-8") as f:
            f.write(reglas_texto)

    def test_protegido_si_el_remitente_esta_solo_en_el_roster(self):
        self._escribir_archivos(roster="cliente@ejemplo.com", reglas_texto="sin relación")
        self.assertTrue(reglas.protegido({"de": "x <cliente@ejemplo.com>"}))

    def test_protegido_si_el_remitente_esta_solo_en_las_reglas(self):
        self._escribir_archivos(roster="sin relación", reglas_texto="cliente@ejemplo.com")
        self.assertTrue(reglas.protegido({"de": "x <cliente@ejemplo.com>"}))

    def test_no_protegido_si_no_esta_en_ninguno(self):
        self._escribir_archivos(roster="sin relación", reglas_texto="tampoco")
        self.assertFalse(reglas.protegido({"de": "x <cliente@ejemplo.com>"}))


class ProtegidoCanario(unittest.TestCase):
    """A diferencia de ProtegidoLogica, esto SÍ corre contra roster.md y
    reglas.md reales. No fija el comportamiento de protegido() —eso ya lo
    hace ProtegidoLogica con datos sintéticos— sino que dos remitentes
    concretos, sobre los que JP ya se pronunció por escrito, siguen
    protegidos hoy. Un rojo acá no es necesariamente un bug de protegido():
    puede ser que esos remitentes se hayan sacado de los archivos."""

    def test_los_remitentes_marcados_hoy_siguen_protegidos(self):
        """Sobre lo que JP ya se pronunció, el sistema no decide solo. Una
        vez el filtro automático archivó una promo de SiPago que tenía una
        regla escrita, porque protegido() solo miraba roster.md."""
        self.assertTrue(reglas.protegido({"de": "x <cobros@sipago.com.ar>"}),
                         msg="si esto falla, lo más probable es que "
                             "sipago.com.ar ya no figure en roster.md/reglas.md "
                             "(JP los edita a mano) y no que protegido() esté "
                             "rota — la lógica de protegido() la cubre "
                             "ProtegidoLogica con datos inventados")
        self.assertTrue(reglas.protegido({"de": "x <algo@imseg.com>"}),
                         msg="si esto falla, lo más probable es que imseg.com "
                             "ya no figure en roster.md/reglas.md (JP los "
                             "edita a mano) y no que protegido() esté rota — "
                             "la lógica de protegido() la cubre ProtegidoLogica "
                             "con datos inventados")

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


class LasCorridasTambienEnsenan(unittest.TestCase):
    """remitentes_ruido() sólo miraba datos/simulacro-*.json.

    Las corridas (corrida.py, desde el 2026-08-26) producen el mismo
    formato y son hoy el set etiquetado más grande y más fresco -- 141
    correos del atraso de agosto contra los 133 de todos los simulacros
    juntos. Con el glob viejo eran invisibles: JP etiquetaba y el
    sistema no se enteraba, que es justo el circuito que pidió cerrar
    ("lo más rico es que la secretaria se retroalimente de la
    respuesta").
    """

    def _con_archivos(self, nombres):
        import json
        import os
        import tempfile
        caso = {"de": "promo@ejemplo.com", "correcto": "RUIDO"}
        d = tempfile.mkdtemp()
        os.mkdir(os.path.join(d, "datos"))
        for n in nombres:
            with open(os.path.join(d, "datos", n), "w", encoding="utf-8") as f:
                json.dump({"casos": [caso, caso]}, f)
        anterior = os.getcwd()
        os.chdir(d)
        try:
            return reglas.remitentes_ruido()
        finally:
            os.chdir(anterior)

    def test_una_corrida_cuenta_igual_que_un_simulacro(self):
        direcciones, _ = self._con_archivos(["corrida-20260811.json"])
        self.assertIn("promo@ejemplo.com", direcciones)

    def test_los_simulacros_siguen_contando(self):
        direcciones, _ = self._con_archivos(["simulacro-20260808-222124.json"])
        self.assertIn("promo@ejemplo.com", direcciones)

    def test_no_se_lee_cualquier_json_que_ande_dando_vueltas(self):
        """datos/ tiene también candidatos-*.json, que son otra cosa:
        no llevan el dictamen de JP."""
        direcciones, _ = self._con_archivos(["candidatos-20260812-202429.json"])
        self.assertNotIn("promo@ejemplo.com", direcciones)


class NoDejaArchivosAbiertos(unittest.TestCase):
    """reglas.py leía todo con open(...).read() sin cerrar.

    No es cosmético: remitentes_ruido() abre CADA simulacro guardado
    -veinte y subiendo- y ninguno se cerraba. Se notó porque la salida
    de la suite se llenó de ResourceWarning, y una salida llena de ruido
    es exactamente lo que este proyecto no puede permitirse: el log es
    la única forma de ver que algo anda mal.
    """

    def _sin_avisos(self, fn):
        """Los ResourceWarning que salgan DE reglas.py al llamar a `fn`.

        Filtrar por archivo no es una tibieza: el gc.collect() barre
        también lo que dejaron abierto otros módulos en tests
        anteriores, y sin el filtro este test acusaba a reglas.py de
        fugas ajenas -- pasaba solo y fallaba en la suite completa, que
        es la peor forma de fallar.
        """
        import gc
        import os.path
        import warnings
        with warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter("always", ResourceWarning)
            fn()
            gc.collect()
        return [a for a in avisos
                if issubclass(a.category, ResourceWarning)
                and os.path.basename(a.filename) == "reglas.py"]

    def test_codigos_convenidos_cierra_lo_que_abre(self):
        self.assertEqual(self._sin_avisos(reglas.codigos_convenidos), [])

    def test_remitentes_ruido_cierra_lo_que_abre(self):
        self.assertEqual(self._sin_avisos(reglas.remitentes_ruido), [])

    def test_texto_de_conocimiento_cierra_lo_que_abre(self):
        self.assertEqual(self._sin_avisos(reglas.texto_de_conocimiento), [])
