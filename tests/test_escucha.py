import tempfile
import unittest
import unittest.mock as mock

import memoria
import secretaria


def correo_falso(mid, asunto, de="alguien@ejemplo.com"):
    """Como lo deja traer_nuevos(): lo que memoria.anotar() necesita.
    Copiado de tests/test_resumen.py para no acoplar los dos archivos."""
    return {"message_id": mid, "uid": "1", "uidvalidity": "1", "de": de,
            "para": "jp@x", "cc": "", "asunto": asunto,
            "fecha": "Mon, 17 Aug 2026 09:00:00 -0300", "cuerpo": "cuerpo",
            "adjuntos": []}


def mensaje(texto):
    return {"message": {"text": texto, "chat": {"id": 1}}}


def boton(data):
    return {"callback_query": {"id": "1", "data": data}}


def voz(file_id="f1"):
    return {"message": {"voice": {"file_id": file_id}, "chat": {"id": 1}}}


class Comandos(unittest.TestCase):
    """Los cuatro tests del brief de la tarea 11, sin cambios."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def _mensaje(self, texto):
        return mensaje(texto)

    def test_pausa_frena_lo_que_toca_la_casilla(self):
        with mock.patch.object(secretaria.bot, "tg"):
            self.s.atender(self._mensaje("/pausa"))
        self.assertTrue(self.s.pausada)

    def test_sigo_la_despausa(self):
        self.s.pausada = True
        with mock.patch.object(secretaria.bot, "tg"):
            self.s.atender(self._mensaje("/sigo"))
        self.assertFalse(self.s.pausada)

    def test_motor_cambia_la_preferencia_y_lo_confirma(self):
        with mock.patch.object(secretaria.bot, "tg") as enviar, \
             mock.patch.object(secretaria.clasificador, "elegir_motor",
                               return_value=["groq", "nvidia"]) as elegir:
            self.s.atender(self._mensaje("/motor groq"))
        elegir.assert_called_once_with("groq")
        self.assertIn("groq", enviar.call_args.kwargs["text"])

    def test_un_motor_inventado_no_rompe_nada_y_avisa(self):
        with mock.patch.object(secretaria.bot, "tg") as enviar, \
             mock.patch.object(
                 secretaria.clasificador, "elegir_motor",
                 side_effect=ValueError(
                     "motor desconocido: x. Hay: groq, nvidia")):
            self.s.atender(self._mensaje("/motor x"))
        self.assertIn("nvidia", enviar.call_args.kwargs["text"])

    def test_pausada_no_toca_la_casilla(self):
        self.s.pausada = True
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[]) as traer, \
             mock.patch.object(secretaria.correo, "mover_a") as mover:
            self.s.revisar_casilla()
        mover.assert_not_called()

    def test_sin_argumento_pide_cual(self):
        with mock.patch.object(secretaria.bot, "tg") as enviar:
            self.s.atender(self._mensaje("/motor"))
        self.assertIn("Decime cuál", enviar.call_args.kwargs["text"])

    def test_comando_desconocido_lista_los_que_hay(self):
        with mock.patch.object(secretaria.bot, "tg") as enviar:
            self.s.atender(self._mensaje("/inventado"))
        texto = enviar.call_args.kwargs["text"]
        for c in ("/pausa", "/sigo", "/motor", "/estado"):
            self.assertIn(c, texto)


class Estado(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def test_usa_memoria_contar_desde_y_no_cx_execute_suelto(self):
        """Antes esto hacía self.cx.execute() a pelo, sin el candado que
        memoria.py exige para cualquier lectura sobre la conexión
        compartida entre los dos hilos (ver el docstring de memoria.py):
        el mismo problema que le costó dos rondas arreglar a la tarea 6.
        memoria.contar_desde() lo hace con el candado, como cualquier
        otra función de ese módulo."""
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.memoria, "contar_desde",
                               return_value=3) as contar, \
             mock.patch.object(secretaria.clasificador, "motores",
                               return_value=[("nvidia", "u", "k", "m")]):
            self.s.atender(mensaje("/estado"))
        contar.assert_called_once()
        self.assertEqual(contar.call_args[0][0], self.s.cx)
        texto = tg.call_args.kwargs["text"]
        self.assertIn("3", texto)
        self.assertIn("nvidia", texto)
        self.assertIn("Pausada: no", texto)

    def test_sin_ningun_motor_configurado_no_rompe_el_hilo(self):
        """clasificador.motores() no tira ValueError acá: tira SystemExit,
        que no hereda de Exception. Un "except Exception" a secas lo
        dejaba pasar de largo y hubiera tirado abajo ciclo_de_escucha."""
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.clasificador, "motores",
                               side_effect=SystemExit(
                                   "no hay ningún motor configurado")):
            self.s.atender(mensaje("/estado"))
        self.assertIn("ninguno configurado", tg.call_args.kwargs["text"])


class LeiTodo(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        for i in range(2):
            memoria.anotar(self.s.cx, correo_falso(f"<e{i}@x>", f"Asunto {i}"),
                           "DELEGADO", "motivo")
        equipo = memoria.del_dia(self.s.cx, "clasificado", "")
        _, teclado = self.s.armar_resumen(equipo, "manana")
        self.leido_cb = teclado["inline_keyboard"][0][0]["callback_data"]
        self.mio_cb = teclado["inline_keyboard"][0][1]["callback_data"]
        self.tanda = self.leido_cb.split("|")[1].split("-")[0]

    def test_marca_leido_solo_lo_del_equipo_y_cierra_la_tanda(self):
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo, "marcar_leido",
                               return_value=True) as marcar:
            self.s.atender(boton(self.leido_cb))
        self.assertEqual(marcar.call_count, 2)
        for i in range(2):
            self.assertEqual(memoria.situacion(self.s.cx, f"<e{i}@x>"),
                             "cerrado")
        self.assertNotIn(self.tanda, self.s.abiertos)

    def test_en_seco_no_marca_leido_pero_igual_cierra(self):
        """Toda escritura pasa por puede_escribir(): en seco no se toca
        la casilla, pero JP igual dijo que las vio -"cerrado" es eso, no
        una confirmación de que el \\Seen se escribió de verdad."""
        with mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo, "marcar_leido") as marcar:
            self.s.atender(boton(self.leido_cb))
        marcar.assert_not_called()
        self.assertEqual(memoria.situacion(self.s.cx, "<e0@x>"), "cerrado")

    def test_pausada_tampoco_toca_la_casilla(self):
        self.s.pausada = True
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo, "marcar_leido") as marcar:
            self.s.atender(boton(self.leido_cb))
        marcar.assert_not_called()
        self.assertEqual(memoria.situacion(self.s.cx, "<e0@x>"), "cerrado")

    def test_un_fallo_de_marcar_leido_no_frena_el_resto_y_avisa(self):
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo, "marcar_leido",
                               side_effect=RuntimeError("IMAP caído")):
            self.s.atender(boton(self.leido_cb))
        # las dos situaciones igual pasan a "cerrado": JP dijo que las vio
        for i in range(2):
            self.assertEqual(memoria.situacion(self.s.cx, f"<e{i}@x>"),
                             "cerrado")
        self.assertIn("2", tg.call_args.kwargs["text"])


class BotonDeOtraTanda(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def test_una_tanda_que_no_esta_abierta_se_rechaza(self):
        with mock.patch.object(secretaria.bot, "tg_suave") as suave:
            self.s.atender(boton("t|vieja-0|equipo"))
        suave.assert_called_once()
        self.assertIn("ya pasó", suave.call_args.kwargs["text"])

    def test_un_boton_con_el_idx_de_otro_correo_tambien_se_rechaza(self):
        """Mismo dato de tanda, pero con otro idx: es exactamente lo que
        bot.es_de_esta_tanda existe para distinguir, y por eso
        atender_boton lo usa en vez de comparar sólo el token de tanda."""
        for i in range(2):
            memoria.anotar(self.s.cx, correo_falso(f"<e{i}@x>", f"A {i}"),
                           "DELEGADO", "m")
        equipo = memoria.del_dia(self.s.cx, "clasificado", "")
        _, teclado = self.s.armar_resumen(equipo, "manana")
        cb = teclado["inline_keyboard"][0][0]["callback_data"]
        tanda = cb.split("|")[1].split("-")[0]
        con_otro_idx = f"t|{tanda}-3|equipo"
        with mock.patch.object(secretaria.bot, "tg_suave") as suave:
            self.s.atender(boton(con_otro_idx))
        self.assertIn("ya pasó", suave.call_args.kwargs["text"])


class UnoEsMio(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        for i in range(3):
            memoria.anotar(self.s.cx, correo_falso(f"<e{i}@x>", f"Asunto {i}"),
                           "DELEGADO", "motivo")
        equipo = memoria.del_dia(self.s.cx, "clasificado", "")
        _, teclado = self.s.armar_resumen(equipo, "manana")
        botones = teclado["inline_keyboard"][0]
        self.leido_cb = botones[0]["callback_data"]
        self.mio_cb = botones[1]["callback_data"]
        self.tanda = self.mio_cb.split("|")[1].split("-")[0]

    def test_abre_la_lista_numerada(self):
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.mio_cb))
        lista = tg.call_args.kwargs["text"]
        for i in range(3):
            self.assertIn(f"Asunto {i}", lista)
        self.assertEqual(self.s.pendiente,
                         {"tipo": "uno_es_mio", "tanda": self.tanda})

    def test_elegir_un_numero_registra_la_correccion(self):
        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.mio_cb))
            self.s.atender(mensaje("2"))
        f = self.s.cx.execute(
            "SELECT categoria, categoria_jp, situacion FROM correos"
            " WHERE message_id='<e1@x>'").fetchone()
        self.assertEqual(f["categoria"], "DELEGADO")  # lo que dijo el sistema
        self.assertEqual(f["categoria_jp"], "TUYO")   # lo que corrigió JP
        self.assertEqual(f["situacion"], "clasificado")
        self.assertIsNone(self.s.pendiente)

    def test_el_corregido_sale_de_abiertos_pero_los_demas_quedan(self):
        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.mio_cb))
            self.s.atender(mensaje("1"))
        self.assertNotIn("<e0@x>", self.s.abiertos[self.tanda])
        self.assertIn("<e1@x>", self.s.abiertos[self.tanda])
        self.assertIn("<e2@x>", self.s.abiertos[self.tanda])

    def test_leer_todo_sigue_andando_para_el_resto_despues_de_corregir_uno(self):
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo, "marcar_leido",
                               return_value=True) as marcar:
            self.s.atender(boton(self.mio_cb))
            self.s.atender(mensaje("1"))
            self.s.atender(boton(self.leido_cb))
        self.assertEqual(marcar.call_count, 2)  # los dos que quedaban
        self.assertNotIn(self.tanda, self.s.abiertos)

    def test_numero_invalido_no_rompe_y_deja_la_pregunta_abierta(self):
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.mio_cb))
            self.s.atender(mensaje("99"))
        self.assertIsNotNone(self.s.pendiente)
        self.assertIn("1 al 3", tg.call_args.kwargs["text"])

    def test_texto_no_numerico_tampoco_rompe(self):
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.mio_cb))
            self.s.atender(mensaje("el segundo"))
        self.assertIsNotNone(self.s.pendiente)
        self.assertIn("1 al 3", tg.call_args.kwargs["text"])


class CircuitoDeRever(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        memoria.anotar(self.s.cx, correo_falso("<r@x>", "Manual",
                                               de="sipago@x"),
                       "RUIDO", "parecía promo")
        memoria.cambiar(self.s.cx, "<r@x>", "archivado")
        _, teclado = self.s.armar_resumen_de_ruido(
            memoria.del_dia(self.s.cx, "archivado", ""))
        botones = teclado["inline_keyboard"][0]
        self.confirmar_cb = botones[0]["callback_data"]
        self.rever_cb = botones[1]["callback_data"]
        self.tanda = self.rever_cb.split("|")[1].split("-")[0]

    def test_confirmar_cierra_la_tanda_sin_tocar_nada_mas(self):
        with mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.confirmar_cb))
        self.assertNotIn(self.tanda, self.s.abiertos)
        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"), "cerrado")

    def test_rever_abre_la_lista_numerada(self):
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.rever_cb))
        self.assertIn("Manual", tg.call_args.kwargs["text"])
        self.assertEqual(self.s.pendiente,
                         {"tipo": "rever_elegir", "tanda": self.tanda})

    def test_circuito_completo_por_texto(self):
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True) as devolver, \
             mock.patch.object(
                 secretaria.clasificador, "clasificar",
                 return_value={"categoria": "NATALIA", "motivo": "cobros",
                              "unanime": True}):
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            pregunta = tg.call_args.kwargs["text"]
            self.s.atender(mensaje("SiPago es mi proveedor de cobros"))
        self.assertIn("por qué", pregunta.lower())
        devolver.assert_called_once()
        f = self.s.cx.execute(
            "SELECT categoria_jp, explicacion FROM correos"
            " WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["categoria_jp"], "NATALIA")
        self.assertIn("cobros", f["explicacion"])
        self.assertIsNone(self.s.pendiente)
        self.assertNotIn(self.tanda, self.s.abiertos)

    def test_circuito_completo_por_audio(self):
        """bot.transcribir() convierte el audio a texto antes de que
        atender() lo mande por el mismo camino que el texto -es lo que
        garantiza que el motivo se acepte por las dos vías."""
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.bot, "transcribir",
                               return_value="por audio, es de cobros"
                               ) as transcribir, \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True), \
             mock.patch.object(
                 secretaria.clasificador, "clasificar",
                 return_value={"categoria": "NATALIA", "motivo": "m",
                              "unanime": True}):
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            self.s.atender(voz())
        transcribir.assert_called_once_with("f1")
        f = self.s.cx.execute("SELECT explicacion FROM correos"
                              " WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["explicacion"], "por audio, es de cobros")

    def test_con_el_freno_puesto_avisa_en_vez_de_fallar_en_silencio(self):
        """SecretariaFrenada existe justo para este momento: JP tocó
        Rever y contestó el motivo, así que quedarse callado sería
        hacerle creer que funcionó cuando no pasó nada."""
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo,
                               "devolver_a_bandeja") as devolver:
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            self.s.atender(mensaje("explicación"))
        devolver.assert_not_called()
        self.assertIn("freno", tg.call_args.kwargs["text"].lower())
        self.assertIsNone(self.s.pendiente)

    def test_motivo_vacio_no_ejecuta_rever_ruido(self):
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo,
                               "devolver_a_bandeja") as devolver:
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            self.s.atender(mensaje("   "))
        devolver.assert_not_called()
        self.assertIn("motivo", tg.call_args.kwargs["text"].lower())

    def test_numero_invalido_al_rever_no_rompe(self):
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("9"))
        self.assertIsNotNone(self.s.pendiente)
        self.assertIn("1 al 1", tg.call_args.kwargs["text"])


class RespuestaTardia(unittest.TestCase):
    """JP contesta cuando puede: una tanda real tardó 78 minutos y otra
    11 horas (ver el docstring de bot.tg). Ni self.pendiente ni
    self.abiertos expiran por tiempo, y un resumen nuevo -que arma su
    propia tanda, otra clave- no invalida uno viejo que sigue abierto."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        memoria.anotar(self.s.cx, correo_falso("<r@x>", "Manual",
                                               de="sipago@x"),
                       "RUIDO", "parecía promo")
        memoria.cambiar(self.s.cx, "<r@x>", "archivado")

    def test_la_respuesta_sigue_valiendo_aunque_salga_otro_resumen_antes(self):
        _, teclado = self.s.armar_resumen_de_ruido(
            memoria.del_dia(self.s.cx, "archivado", ""))
        rever_cb = teclado["inline_keyboard"][0][1]["callback_data"]
        with mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(rever_cb))

        # Pasa el tiempo -horas, en la realidad- y sale un resumen nuevo,
        # sin ninguna relación con esta tanda: otra clave en abiertos.
        memoria.anotar(self.s.cx, correo_falso("<o@x>", "Otro"), "TUYO", "m")
        with mock.patch.object(secretaria.bot, "tg"):
            self.s.armar_resumen(memoria.del_dia(self.s.cx, "clasificado", ""),
                                 "tarde")

        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True) as devolver, \
             mock.patch.object(
                 secretaria.clasificador, "clasificar",
                 return_value={"categoria": "NATALIA", "motivo": "m",
                              "unanime": True}):
            self.s.atender(mensaje("1"))
            self.s.atender(mensaje("SiPago es mi proveedor, once horas después"))
        devolver.assert_called_once()
        f = self.s.cx.execute("SELECT categoria_jp FROM correos"
                              " WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["categoria_jp"], "NATALIA")


class RespuestaLibre(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def test_sin_nada_pendiente_no_miente_sobre_lo_que_sabe_hacer(self):
        with mock.patch.object(secretaria.bot, "tg") as tg:
            self.s.atender(mensaje("¿Enzo está de vacaciones?"))
        texto = tg.call_args.kwargs["text"]
        self.assertNotIn("no entiendo", texto.lower())
        self.assertIn("/pausa", texto)
        self.assertIn("Etapa 4", texto)


class Audio(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def test_si_no_puede_transcribir_avisa_y_no_rompe(self):
        with mock.patch.object(secretaria.bot, "transcribir",
                               side_effect=RuntimeError("Telegram caído")), \
             mock.patch.object(secretaria.bot, "tg") as tg:
            self.s.atender(voz())
        self.assertIn("audio", tg.call_args.kwargs["text"].lower())

    def test_transcripcion_vacia_avisa_en_vez_de_quedarse_callada(self):
        """Silencio de acá en más sería la misma falla que este archivo
        entero existe para evitar: JP mandó un audio, cree que llegó, y
        no pasa nada."""
        with mock.patch.object(secretaria.bot, "transcribir",
                               return_value="   "), \
             mock.patch.object(secretaria.bot, "tg") as tg:
            self.s.atender(voz())
        self.assertIn("audio", tg.call_args.kwargs["text"].lower())

    def test_un_sticker_no_genera_ninguna_respuesta(self):
        with mock.patch.object(secretaria.bot, "tg") as tg:
            self.s.atender({"message": {"sticker": {"file_id": "x"},
                                        "chat": {"id": 1}}})
        tg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
