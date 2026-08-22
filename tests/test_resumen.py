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


class MandarResumenCaminoGrande(unittest.TestCase):
    """Ronda 2: el caso en que ni lo accionable solo entra en un mensaje
    -en la práctica hace falta un LIMITE_TELEGRAM chico a propósito para
    provocarlo con pocos correos, porque en volumen real hacen falta
    muchos accionables para que esto se active-. Ahí mandar_resumen
    trocea SOLO lo accionable (nunca mezcla equipo/ruido en los lotes de
    detalle, esa mezcla era el bug de la ronda 1) y marca cada lote
    recién cuando ESE lote salió de verdad."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        for i in range(40):
            memoria.anotar(self.s.cx, correo_falso(f"<m{i}@x>", f"Mío {i}"),
                           "TUYO", "m")

    def test_manda_como_mucho_el_tope_y_marca_lo_que_entro(self):
        correos_antes = memoria.del_dia(self.s.cx, "clasificado", "")
        with mock.patch.object(secretaria, "LIMITE_TELEGRAM", 300), \
             mock.patch.object(self.s, "enviar",
                               return_value={"ok": True}) as enviar:
            lotes, sobran = self.s._empacar_accionable(
                correos_antes, secretaria.TOPE_MENSAJES - 1)
            self.s.mandar_resumen("manana")

        # Se ejercita de verdad el camino grande: hay lotes de detalle
        # Y sobrante -si no, el test no probaría nada nuevo.
        self.assertGreater(len(lotes), 0)
        self.assertGreater(len(sobran), 0)
        self.assertLessEqual(enviar.call_count, secretaria.TOPE_MENSAJES)
        for lote in lotes:
            for c in lote:
                self.assertEqual(memoria.situacion(self.s.cx, c["message_id"]),
                                 "en_resumen")
        # Lo que no entró en el detalle queda "clasificado": vuelve
        # completo -no como conteo- en el próximo resumen.
        for c in sobran:
            self.assertEqual(memoria.situacion(self.s.cx, c["message_id"]),
                             "clasificado")

    def test_si_un_lote_de_detalle_falla_no_se_manda_el_cierre_ni_se_marca_nada_despues(self):
        """Nunca se marca como enviado algo que no salió: si Telegram se
        cae a mitad de la tanda, ni el lote que falló, ni los que
        vendrían después, ni el cierre se dan por vistos -se reintenta
        todo entero la próxima vez-, pero lo que ya había salido antes
        de la falla queda marcado, porque eso JP lo vio de verdad."""
        correos_antes = memoria.del_dia(self.s.cx, "clasificado", "")
        with mock.patch.object(secretaria, "LIMITE_TELEGRAM", 300), \
             mock.patch.object(secretaria, "TOPE_MENSAJES", 4):
            lotes, sobran = self.s._empacar_accionable(
                correos_antes, secretaria.TOPE_MENSAJES - 1)
            self.assertGreaterEqual(len(lotes), 2,
                                    "hace falta 2+ lotes para probar la"
                                    " falla a mitad de camino")
            salidas = [{"ok": True}, None] + [{"ok": True}] * 10
            with mock.patch.object(self.s, "enviar", side_effect=salidas) as enviar:
                self.s.mandar_resumen("manana")

        self.assertEqual(enviar.call_count, 2)  # el 2do lote falla, ahí se corta
        for c in lotes[0]:
            self.assertEqual(memoria.situacion(self.s.cx, c["message_id"]),
                             "en_resumen")
        for c in lotes[1] + sobran:
            self.assertEqual(memoria.situacion(self.s.cx, c["message_id"]),
                             "clasificado")

    def test_un_corregido_por_rever_aparece_tambien_en_el_camino_grande(self):
        """Ronda de arreglo 1: _mandar_resumen_grande volvía a filtrar
        `lote` por la categoría CRUDA (c["categoria"]), así que un correo
        con categoria="RUIDO" y categoria_jp="NATALIA" -exactamente lo
        que deja rever_ruido()- no entraba en mios_l ni en derivar_l, no
        aparecía en ningún mensaje, y cambiar_lote() lo marcaba
        "en_resumen" como si JP lo hubiera visto. Ningún test cruzaba
        antes "corregido por Rever" con "camino grande".

        Base propia -no la de 40 TUYO del setUp-, y pocos TUYO a
        propósito: _empacar_accionable arma `accionable = mios +
        derivar`, así que TODOS los "TUYO" preceden al corregido
        (categoría efectiva NATALIA) sin importar en qué orden se
        anotaron -si hubiera 40 TUYO como en el resto de esta clase, el
        corregido caería en `sobran` -que sí se cuenta pero no se manda
        como texto- y el test no distinguiría "cayó en sobran" de "el
        bug volvió"."""
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        memoria.anotar(s.cx, correo_falso("<corregido@x>", "Cobros SiPago"),
                       "RUIDO", "parecía promo")
        memoria.corregir(s.cx, "<corregido@x>", "NATALIA",
                         "SiPago es mi proveedor de cobros")
        memoria.cambiar(s.cx, "<corregido@x>", "clasificado")
        for i in range(3):
            memoria.anotar(s.cx, correo_falso(f"<m{i}@x>", f"Mío {i}"),
                           "TUYO", "m")

        enviados = []

        def enviar_falso(texto, teclado=None):
            enviados.append(texto)
            return {"ok": True}

        with mock.patch.object(secretaria, "LIMITE_TELEGRAM", 300), \
             mock.patch.object(s, "enviar", side_effect=enviar_falso):
            s.mandar_resumen("manana")

        texto_junto = "\n".join(enviados)
        self.assertIn("Cobros SiPago", texto_junto,
                      "el corregido por Rever no apareció en ningún mensaje")
        self.assertIn("Natalia", texto_junto)
        self.assertEqual(memoria.situacion(s.cx, "<corregido@x>"),
                         "en_resumen")


class LaAndanadaDeLaRonda2(unittest.TestCase):
    """Los números tal cual los midió el revisor: 1000 correos, 70 de
    ellos accionables, mezclados con el resto entre equipo y ruido.
    Antes de este arreglo, partir todo en lotes parejos de 15 -sin
    distinguir categorías- mandaba 67 mensajes de una sola tanda. Ahora
    el troceo respeta la jerarquía: lo accionable primero y completo, el
    resto -que es el grueso, y no necesita revisión uno por uno- nunca
    se enumera en este camino, sólo se cuenta."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        # Asuntos largos a propósito -del orden de un asunto real, no
        # "Mío 3"-: con asuntos cortos 70 accionables pueden entrar en
        # un solo mensaje sin necesidad del camino grande, y este test
        # existe justamente para ejercitarlo.
        # El asunto tiene que entrar entero en los 48 caracteres que la
        # línea de "Tuyo" no recorta, si no el número que lo distingue
        # queda afuera del texto y el test no puede contarlo (ver el
        # mismo ajuste en Volumen._equipo). Es largo -no "Mío 3"- a
        # propósito: con asuntos cortos, 70 accionables entran en un
        # solo mensaje sin necesidad del camino grande, y este test
        # existe para ejercitarlo.
        for i in range(70):
            memoria.anotar(self.s.cx, correo_falso(
                f"<a{i}@x>",
                f"Consulta sobre facturación cliente {i:03d}"), "TUYO", "m")
        for i in range(900):
            memoria.anotar(self.s.cx, correo_falso(f"<e{i}@x>", f"Equipo {i}"),
                           "DELEGADO", "m")
        for i in range(30):
            memoria.anotar(self.s.cx, correo_falso(f"<r{i}@x>", f"Ruido {i}"),
                           "RUIDO", "m")
            memoria.cambiar(self.s.cx, f"<r{i}@x>", "archivado")

    def _mandar_y_juntar_textos(self):
        enviados = []

        def enviar_falso(texto, teclado=None):
            enviados.append(texto)
            return {"ok": True}

        with mock.patch.object(self.s, "enviar", side_effect=enviar_falso) as enviar:
            self.s.mandar_resumen("manana")
        return enviados, enviar

    def test_1000_correos_con_70_accionables_no_superan_el_tope(self):
        enviados, enviar = self._mandar_y_juntar_textos()
        self.assertLessEqual(enviar.call_count, secretaria.TOPE_MENSAJES)
        self.assertGreaterEqual(enviar.call_count, 1)

    def test_lo_accionable_llega_completo_en_el_detalle(self):
        """Con estos números entra completo -70 asuntos largos siguen
        siendo pocos comparados con 900 correos de equipo-, así que ni
        siquiera hace falta diferir ninguno: el tope se respeta y todo
        lo accionable se ve igual."""
        enviados, _ = self._mandar_y_juntar_textos()
        texto_junto = "\n".join(enviados)
        for i in range(70):
            self.assertIn(f"cliente {i:03d}", texto_junto,
                          f"el accionable {i} no llegó completo")

    def test_el_equipo_y_el_ruido_nunca_se_enumeran_en_el_camino_grande(self):
        enviados, _ = self._mandar_y_juntar_textos()
        texto_junto = "\n".join(enviados)
        self.assertNotIn("Equipo 0", texto_junto)
        self.assertNotIn("Ruido 0", texto_junto)
        # pero el conteo sí tiene que estar, si no es un recorte callado
        self.assertIn("900", texto_junto)
        self.assertIn("30", texto_junto)

    def test_lo_que_se_muestra_completo_queda_marcado(self):
        self._mandar_y_juntar_textos()
        for i in range(70):
            self.assertEqual(memoria.situacion(self.s.cx, f"<a{i}@x>"),
                             "en_resumen")
        for i in range(900):
            self.assertEqual(memoria.situacion(self.s.cx, f"<e{i}@x>"),
                             "en_resumen")


class ArmarResumenDeRuido(unittest.TestCase):
    """El resumen de las 18:00: sólo lo archivado, en lista NUMERADA -a
    diferencia del pie de armar_resumen(), que sólo cuenta- y con
    exactamente dos botones: Confirmar (todos fueron ruido) y Rever."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def _ruido(self, n):
        return [fila(f"<r{i}@x>", f"Promo bien distintiva {i}", "RUIDO",
                     de=f"promo{i}@ejemplo.com") for i in range(n)]

    def test_sin_nada_no_hay_botones(self):
        texto, teclado = self.s.armar_resumen_de_ruido([])
        self.assertIn("no archivé nada", texto.lower())
        self.assertEqual(teclado["inline_keyboard"], [])

    def test_lista_numerada_con_confirmar_y_rever(self):
        correos = self._ruido(3)
        texto, teclado = self.s.armar_resumen_de_ruido(correos)
        for i, c in enumerate(correos, 1):
            self.assertIn(f"{i}.", texto)
            self.assertIn(c["asunto"], texto)
        botones = teclado["inline_keyboard"][0]
        textos = [b["text"] for b in botones]
        self.assertEqual(len(botones), 2)
        self.assertTrue(any("onfirmar" in t for t in textos))
        self.assertTrue(any("ever" in t for t in textos))

    def test_la_tanda_queda_registrada_para_los_tres_correos(self):
        correos = self._ruido(3)
        _, teclado = self.s.armar_resumen_de_ruido(correos)
        callback = teclado["inline_keyboard"][0][0]["callback_data"]
        tanda = callback.split("|")[1].split("-")[0]
        self.assertEqual(self.s.abiertos[tanda],
                         [c["message_id"] for c in correos])

    def test_callback_data_entra_en_64_bytes(self):
        _, teclado = self.s.armar_resumen_de_ruido(self._ruido(1))
        for boton in teclado["inline_keyboard"][0]:
            self.assertLessEqual(len(boton["callback_data"].encode()), 64)

    def test_con_muchos_no_supera_el_limite_y_avisa_lo_omitido(self):
        correos = self._ruido(200)
        texto, _ = self.s.armar_resumen_de_ruido(correos)
        self.assertLessEqual(len(texto), secretaria.LIMITE_TELEGRAM)
        self.assertIn("más", texto)


class MandarResumenDeRuido(unittest.TestCase):
    """mandar_resumen("ruido") usa armar_resumen_de_ruido() con SÓLO lo
    archivado -no el combinado de manana/tarde-, que es justo lo que
    esta tarea agrega: antes "ruido" mandaba el mismo resumen general,
    sólo con otro saludo."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        memoria.anotar(self.s.cx, correo_falso("<1@x>", "Promo"), "RUIDO", "r")
        memoria.cambiar(self.s.cx, "<1@x>", "archivado")
        # Un correo TUYO todavía sin resumir: no tiene que aparecer en
        # el resumen de ruido -eso confundiría "esto es lo que archivé"
        # con la bandeja de JP-, y tiene que seguir "clasificado" para
        # que lo levante el próximo resumen general.
        memoria.anotar(self.s.cx, correo_falso("<2@x>", "Mío"), "TUYO", "m")

    def test_solo_manda_lo_archivado(self):
        with mock.patch.object(self.s, "enviar",
                                return_value={"ok": True}) as enviar:
            self.s.mandar_resumen("ruido")
        texto = enviar.call_args[0][0]
        self.assertIn("Promo", texto)
        self.assertNotIn("Mío", texto)

    def test_marca_lo_archivado_pero_no_toca_lo_pendiente(self):
        with mock.patch.object(self.s, "enviar", return_value={"ok": True}):
            self.s.mandar_resumen("ruido")
        self.assertEqual(memoria.situacion(self.s.cx, "<1@x>"), "en_resumen")
        self.assertEqual(memoria.situacion(self.s.cx, "<2@x>"), "clasificado")

    def test_si_telegram_esta_caido_no_se_pierde(self):
        with mock.patch.object(self.s, "enviar", return_value=None):
            self.s.mandar_resumen("ruido")
        self.assertEqual(memoria.situacion(self.s.cx, "<1@x>"), "archivado")


class ReverRuido(unittest.TestCase):
    """El circuito de corrección: JP dice que un correo archivado no era
    ruido. Adaptado del test del brief -que mockea devolver_a_bandeja
    con `return_value=True` y comprueba `assert_called_once_with(
    "<r@x>")`, un Message-ID pelado-: correo.devolver_a_bandeja(c)
    necesita el correo ENTERO (identidad(c) hace c.get(...), y un string
    no tiene .get), así que acá se verifica que se lo llama con el
    correo reconstruido desde la base, no con el Message-ID solo. Con el
    freno seco puesto (el valor por defecto) puede_escribir() da False,
    así que estos tests sacan el freno como cualquier test que escribe
    en la casilla (ver EnSecoLoImpide en test_secretaria.py)."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        memoria.anotar(self.s.cx,
                       {"message_id": "<r@x>", "uid": "1", "de": "sipago@x",
                        "para": "jp@x", "cc": "", "asunto": "Manual",
                        "fecha": "", "cuerpo": "texto", "adjuntos": []},
                       "RUIDO", "parecía promo")
        memoria.cambiar(self.s.cx, "<r@x>", "archivado")

    def test_vuelve_a_la_bandeja_se_reclasifica_y_guarda_el_motivo(self):
        """Sacarlo de Ruido y dejarlo ahí sería devolverle el trabajo a JP."""
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True) as devolver, \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "NATALIA",
                                             "motivo": "cobros", "unanime": True}):
            nueva = self.s.rever_ruido("<r@x>", "SiPago es mi proveedor de cobros")
        devolver.assert_called_once()
        self.assertEqual(devolver.call_args[0][0]["message_id"], "<r@x>")
        self.assertEqual(nueva, "NATALIA")
        f = self.s.cx.execute("SELECT categoria, categoria_jp, explicacion"
                              " FROM correos WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["categoria"], "RUIDO")
        self.assertEqual(f["categoria_jp"], "NATALIA")
        self.assertIn("cobros", f["explicacion"])

    def test_si_es_natalia_queda_pendiente_de_derivar(self):
        """La tercera cosa que Rever tiene que garantizar: no alcanza con
        sacarlo de Ruido, tiene que APARECER para derivar en el próximo
        resumen -si no, es trabajo que vuelve a caerle a JP."""
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "NATALIA",
                                             "motivo": "cobros", "unanime": True}):
            self.s.rever_ruido("<r@x>", "SiPago es mi proveedor de cobros")
        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"), "clasificado")
        correos = memoria.del_dia(self.s.cx, "clasificado", "")
        texto, _ = self.s.armar_resumen(correos, "tarde")
        self.assertIn("Para derivar", texto)
        self.assertIn("Manual", texto)
        self.assertNotIn("Archivado como ruido", texto)

    def test_el_orden_manda_si_la_reclasificacion_revienta_igual_vuelve(self):
        """Si la reclasificación falla, el correo vuelve igual -nunca se
        queda en Ruido después de que JP dijo que no era ruido-. La
        garantía es de ORDEN: devolver_a_bandeja corre primero y sin
        condicionarse a lo que pase después."""
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True) as devolver, \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               side_effect=RuntimeError("cuota agotada")), \
             mock.patch.object(self.s, "avisar_para_que_decida") as avisar:
            nueva = self.s.rever_ruido("<r@x>", "no me acuerdo bien por qué")
        devolver.assert_called_once()
        self.assertEqual(nueva, "ERROR")
        avisar.assert_called_once()
        f = self.s.cx.execute("SELECT categoria_jp, explicacion, situacion"
                              " FROM correos WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["categoria_jp"], "ERROR")
        self.assertIn("no me acuerdo", f["explicacion"])
        self.assertEqual(f["situacion"], "mostrado_sin_clasificar")

    def test_si_devolver_a_la_bandeja_revienta_no_se_guarda_nada(self):
        """Al revés del caso anterior: si el que revienta es el paso 1
        -una falla real del servidor, no el benigno "no estaba"- no hay
        nada confirmado todavía. Guardar la corrección igual mentiría:
        diría "ya no es ruido" de un correo que puede seguir en Ruido."""
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(
                 secretaria.correo, "devolver_a_bandeja",
                 side_effect=secretaria.correo.CopiaRechazada("no hay cuota")), \
             mock.patch.object(secretaria.clasificador, "clasificar") as cl:
            with self.assertRaises(secretaria.correo.CopiaRechazada):
                self.s.rever_ruido("<r@x>", "SiPago es mi proveedor de cobros")
        cl.assert_not_called()
        f = self.s.cx.execute("SELECT categoria_jp, situacion FROM correos"
                              " WHERE message_id='<r@x>'").fetchone()
        self.assertIsNone(f["categoria_jp"])
        self.assertEqual(f["situacion"], "archivado")

    def test_la_explicacion_se_guarda_igual_venga_de_texto_o_de_audio(self):
        """bot.transcribir() -tarea 11- ya convirtió el audio a texto
        antes de llegar acá: para rever_ruido() las dos formas son la
        misma cadena, y se guarda tal cual, sin tocarla."""
        transcripcion = "esto lo dije por audio, sipago es de cobros"
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "NATALIA",
                                             "motivo": "m", "unanime": True}):
            self.s.rever_ruido("<r@x>", transcripcion)
        f = self.s.cx.execute("SELECT explicacion FROM correos"
                              " WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["explicacion"], transcripcion)

    def test_con_el_freno_puesto_no_toca_la_casilla(self):
        """rever_ruido() lo dispara JP a mano, ahora: quedarse callado
        con el freno puesto sería la misma falla silenciosa de siempre,
        JP cree que tocó Rever y no pasó nada."""
        with mock.patch.object(secretaria, "EN_SECO", True), \
             mock.patch.object(secretaria.correo,
                               "devolver_a_bandeja") as devolver:
            with self.assertRaises(secretaria.SecretariaFrenada):
                self.s.rever_ruido("<r@x>", "explicación")
        devolver.assert_not_called()

    def test_pausada_tampoco_toca_la_casilla(self):
        self.s.pausada = True
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo,
                               "devolver_a_bandeja") as devolver:
            with self.assertRaises(secretaria.SecretariaFrenada):
                self.s.rever_ruido("<r@x>", "explicación")
        devolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
