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


class Volumen(unittest.TestCase):
    """Ronda de arreglo 1: medido, 80 correos dan 4.573 caracteres y 100
    dan 5.684, contra el límite de Telegram de 4.096. Sin colapsar el
    bloque del equipo, el mensaje no entra y Telegram lo rechaza entero
    -ni resumen recortado, CERO resumen-, así que el atraso se vuelve a
    juntar la próxima vez y falla exactamente igual: determinista, no se
    autocorrige nunca."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def _equipo(self, n):
        # El asunto tiene que entrar entero en los 40 caracteres que
        # armar_resumen no recorta por línea, si no el número que lo
        # distingue queda afuera del texto y el test no puede contarlo.
        return [fila(f"<e{i}@x>", f"Asunto correo {i}", "DELEGADO")
                for i in range(n)]

    def test_con_100_correos_el_texto_entra_en_el_limite(self):
        texto, _ = self.s.armar_resumen(self._equipo(100), "manana")
        self.assertLessEqual(len(texto), secretaria.LIMITE_TELEGRAM)

    def test_el_recorte_se_anuncia_con_la_cuenta_exacta_de_lo_omitido(self):
        correos = self._equipo(100)
        texto, _ = self.s.armar_resumen(correos, "manana")
        # El encabezado sigue diciendo el total real, no el listado.
        self.assertIn("El equipo lo maneja (100)", texto)
        listadas = sum(1 for c in correos if c["asunto"] in texto)
        self.assertLess(listadas, 100, "con 100 no debería entrar la lista completa")
        omitidas = 100 - listadas
        self.assertIn(f"{omitidas} más", texto)

    def test_lo_accionable_nunca_se_recorta_aunque_el_equipo_sea_enorme(self):
        """Lo de JP y lo para derivar son "poco" -es la premisa del
        diseño-, así que tienen que aparecer siempre, entero, sin
        importar cuánto equipo haya alrededor."""
        mios = [fila(f"<m{i}@x>", f"Mío número {i} bien distintivo", "TUYO")
                for i in range(5)]
        derivar = [fila(f"<d{i}@x>", f"Derivar número {i} bien distintivo",
                        "ENZO") for i in range(5)]
        correos = mios + derivar + self._equipo(150)

        texto, _ = self.s.armar_resumen(correos, "manana")

        self.assertLessEqual(len(texto), secretaria.LIMITE_TELEGRAM)
        for c in mios + derivar:
            self.assertIn(c["asunto"], texto,
                          f"se recortó algo accionable: {c['asunto']!r}")


class MandarResumenPorLotes(unittest.TestCase):
    """El caso extremo: ni colapsando el equipo entra en un solo mensaje
    -en la práctica hace falta un LIMITE_TELEGRAM chico a propósito para
    provocarlo en un test, porque el colapso normal ya absorbe cualquier
    volumen de equipo-. Ahí mandar_resumen tiene que partir en varios
    mensajes y marcar cada lote como hecho recién cuando ESE lote salió
    de verdad."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        for i in range(40):
            memoria.anotar(self.s.cx, correo_falso(f"<m{i}@x>", f"Mío {i}"),
                           "TUYO", "m")

    def test_manda_varios_mensajes_y_marca_todo_si_todos_salen(self):
        with mock.patch.object(secretaria, "LIMITE_TELEGRAM", 400), \
             mock.patch.object(secretaria, "TAMANO_LOTE_RESUMEN", 5), \
             mock.patch.object(self.s, "enviar",
                               return_value={"ok": True}) as enviar:
            self.s.mandar_resumen("manana")

        self.assertGreater(enviar.call_count, 1)
        for i in range(40):
            self.assertEqual(memoria.situacion(self.s.cx, f"<m{i}@x>"),
                             "en_resumen")

    def test_si_un_lote_falla_ni_ese_ni_los_siguientes_quedan_marcados(self):
        """Nunca se marca como enviado algo que no salió: si Telegram se
        cae a mitad de la tanda, ni el lote que falló ni los que
        vendrían después se dan por vistos -se reintentan enteros la
        próxima vez-, pero los que ya habían salido antes de la falla
        quedan marcados, porque esos JP los vio de verdad."""
        salidas = [{"ok": True}, {"ok": True}, None, {"ok": True}]
        with mock.patch.object(secretaria, "LIMITE_TELEGRAM", 400), \
             mock.patch.object(secretaria, "TAMANO_LOTE_RESUMEN", 5), \
             mock.patch.object(self.s, "enviar", side_effect=salidas):
            self.s.mandar_resumen("manana")

        # Los primeros dos lotes (10 correos) salieron: marcados.
        for i in range(10):
            self.assertEqual(memoria.situacion(self.s.cx, f"<m{i}@x>"),
                             "en_resumen")
        # El tercer lote falló, y nada después de él se intentó ni se
        # marcó: sigue "clasificado", listo para el próximo resumen.
        for i in range(10, 40):
            self.assertEqual(memoria.situacion(self.s.cx, f"<m{i}@x>"),
                             "clasificado")


if __name__ == "__main__":
    unittest.main()
