import email.utils
import unittest
from datetime import datetime, timedelta, timezone

import correo
from tests.material import mensajes


class Adjuntos(unittest.TestCase):
    def test_el_logo_de_firma_no_es_un_adjunto(self):
        """Content-ID significa que el cuerpo HTML lo referencia con cid:.
        Va incrustado, no adjunto. Sin este filtro le mostrábamos a JP
        'image001.jpg' en dos de cada tres correos."""
        m = mensajes.como_mensaje(mensajes.CON_LOGO_DE_FIRMA)
        self.assertEqual(correo.adjuntos(m), [])

    def test_el_pdf_escaneado_si_es_un_adjunto(self):
        m = mensajes.como_mensaje(mensajes.ESCANEADO_DEL_CELULAR)
        a = correo.adjuntos(m)
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["nombre"], "2026-07-27 17-52.pdf")
        self.assertEqual(a[0]["tipo"], "application/pdf")

    def test_se_leen_con_nombre_de_tipo_en_castellano(self):
        lista = [{"nombre": "remito.pdf", "tipo": "application/pdf", "kb": 302}]
        self.assertEqual(correo.adjuntos_legibles(lista),
                         "📎 remito.pdf — PDF, 302 KB")

    def test_sin_adjuntos_devuelve_cadena_vacia(self):
        self.assertEqual(correo.adjuntos_legibles([]), "")
        self.assertEqual(correo.adjuntos_legibles(None), "")


class Cuerpo(unittest.TestCase):
    def test_se_prefiere_el_texto_plano(self):
        m = mensajes.como_mensaje(mensajes.CON_LOGO_DE_FIRMA)
        self.assertIn("quedan instalados el jueves", correo.texto_plano(m))

    def test_si_solo_hay_html_se_limpian_las_etiquetas(self):
        m = mensajes.como_mensaje(mensajes.SOLO_HTML)
        t = correo.texto_plano(m)
        self.assertIn("3 novedades", t)
        self.assertNotIn("<p>", t)


class Identidad(unittest.TestCase):
    def test_usa_el_message_id_cuando_existe(self):
        c = {"message_id": "<scan-002@caesistemas.com.ar>",
             "de": "x", "asunto": "y", "fecha": "z"}
        self.assertEqual(correo.identidad(c), "<scan-002@caesistemas.com.ar>")

    def test_sin_message_id_dos_correos_distintos_no_se_confunden(self):
        """Hay remitentes que no mandan Message-ID. Sin una identidad de
        reserva, JP contestó dos veces el mismo correo — pero la reserva no
        puede colapsar dos correos distintos en la misma identidad."""
        base = {"message_id": "", "de": "a@b.com", "asunto": "Hola",
                "fecha": "Tue, 5 Aug 2026 11:00:00 -0300"}
        otro = dict(base, asunto="Otra cosa")
        self.assertNotEqual(correo.identidad(base), correo.identidad(otro))

    def test_sin_message_id_el_mismo_correo_da_siempre_lo_mismo(self):
        """Estable entre corridas: si cambiara, cada arranque volvería a
        procesar correo ya procesado."""
        c = {"message_id": "", "de": "a@b.com", "asunto": "Hola",
             "fecha": "Tue, 5 Aug 2026 11:00:00 -0300"}
        self.assertEqual(correo.identidad(c),
                         "sha:" + __import__("hashlib").sha256(
                             "a@b.com|Hola|Tue, 5 Aug 2026 11:00:00 -0300"
                             .encode()).hexdigest()[:32])


class FechaLegible(unittest.TestCase):
    """Se mudó acá desde simulacro.py (tarea 8): avisar_en_el_momento la
    necesita en secretaria.py, así que no tenía sentido que un módulo del
    proceso real dependiera del script de simulacro."""

    def test_un_correo_de_hoy_dice_hoy(self):
        cabecera = email.utils.format_datetime(datetime.now(timezone.utc))
        self.assertIn("· hoy", correo.fecha_legible(cabecera))

    def test_un_correo_de_ayer_dice_ayer(self):
        cabecera = email.utils.format_datetime(
            datetime.now(timezone.utc) - timedelta(days=1, hours=1))
        self.assertIn("· ayer", correo.fecha_legible(cabecera))

    def test_un_correo_de_hace_varios_dias_cuenta_los_dias(self):
        cabecera = email.utils.format_datetime(
            datetime.now(timezone.utc) - timedelta(days=3, hours=1))
        self.assertIn("· hace 3 días", correo.fecha_legible(cabecera))

    def test_una_cabecera_ilegible_no_revienta(self):
        """Sin este resguardo, un correo con fecha mal formada tira una
        excepción justo al armar el aviso, y JP no se entera de nada."""
        self.assertEqual(correo.fecha_legible("esto no es una fecha"),
                         "esto no es una fecha"[:30])

    def test_sin_cabecera_avisa_que_no_hay_fecha(self):
        self.assertEqual(correo.fecha_legible(""), "(sin fecha)")
        self.assertEqual(correo.fecha_legible(None), "(sin fecha)")


if __name__ == "__main__":
    unittest.main()
