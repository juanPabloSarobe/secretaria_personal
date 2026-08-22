import tempfile
import threading
import time
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

    def test_contestar_el_motivo_no_llama_a_clasificar_ni_bloquea(self):
        """Ronda 1, hallazgo CRÍTICO: contestar el motivo NO ejecuta
        rever_ruido() en el momento -eso termina en
        clasificador.clasificar(), que corre en el hilo que escucha si
        se llama desde acá, y hasta tres minutos con nvidia lo dejarían
        mudo para cualquier otra cosa. Acá sólo se anota el motivo y se
        avisa que se va a hacer -no al toque-.
        """
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo,
                               "devolver_a_bandeja") as devolver, \
             mock.patch.object(
                 secretaria.clasificador, "clasificar",
                 side_effect=AssertionError(
                     "clasificar no debería llamarse desde el hilo que"
                     " escucha")) as clasificar:
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            pregunta = tg.call_args.kwargs["text"]
            self.s.atender(mensaje("SiPago es mi proveedor de cobros"))
        self.assertIn("por qué", pregunta.lower())
        clasificar.assert_not_called()
        devolver.assert_not_called()
        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"),
                         "pendiente_de_rever")
        f = self.s.cx.execute("SELECT explicacion FROM correos"
                              " WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["explicacion"], "SiPago es mi proveedor de cobros")
        self.assertIsNone(self.s.pendiente)
        self.assertNotIn(self.tanda, self.s.abiertos)
        self.assertIn("confirmo", tg.call_args.kwargs["text"].lower())

    def test_ciclo_de_correo_es_el_que_ejecuta_de_verdad_el_rever(self):
        """La otra mitad del circuito -_atender_revers_pendientes(), que
        corre dentro de revisar_casilla/ciclo_de_correo, no de
        ciclo_de_escucha- es quien de verdad llama a rever_ruido() y,
        con eso, a clasificador.clasificar()."""
        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            self.s.atender(mensaje("SiPago es mi proveedor de cobros"))

        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True) as devolver, \
             mock.patch.object(
                 secretaria.clasificador, "clasificar",
                 return_value={"categoria": "NATALIA", "motivo": "cobros",
                              "unanime": True}) as clasificar:
            self.s._atender_revers_pendientes()
        devolver.assert_called_once()
        clasificar.assert_called_once()
        f = self.s.cx.execute(
            "SELECT categoria_jp, explicacion, situacion FROM correos"
            " WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["categoria_jp"], "NATALIA")
        self.assertIn("cobros", f["explicacion"])
        self.assertEqual(f["situacion"], "clasificado")

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

    def test_con_el_freno_puesto_el_ciclo_no_toca_nada_y_reintenta_despues(self):
        """Ronda 1: el freno ya no se chequea al contestar el motivo -eso
        pasó a ser sólo "anotar y avisar que se va a hacer"-, se
        chequea cuando ciclo_de_correo retoma el pendiente. Mismo
        criterio que _atender_pendientes() con los archivados a medias:
        mientras está frenado no se toca nada y no se gasta ningún
        intento -la fila se queda en pendiente_de_rever, lista para
        cuando se saque el freno."""
        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            self.s.atender(mensaje("explicación"))
        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"),
                         "pendiente_de_rever")

        # EN_SECO sigue en True (el valor por defecto): el freno está
        # puesto sin que nadie lo haya tocado a propósito acá.
        with mock.patch.object(secretaria.correo,
                               "devolver_a_bandeja") as devolver:
            self.s._atender_revers_pendientes()
        devolver.assert_not_called()
        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"),
                         "pendiente_de_rever")
        f = self.s.cx.execute("SELECT intentos FROM correos"
                              " WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["intentos"], 0)

    def test_una_falla_real_reintenta_y_al_agotarse_avisa(self):
        """Si devolver_a_bandeja revienta de verdad -no el freno-, se
        cuenta como intento y, agotados, se avisa en vez de reintentar
        en silencio para siempre: mismo criterio que
        avisar_que_no_se_pudo_archivar."""
        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            self.s.atender(mensaje("explicación"))

        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               side_effect=RuntimeError("IMAP caído")):
            for _ in range(secretaria.INTENTOS_MAXIMOS - 1):
                self.s._atender_revers_pendientes()
            self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"),
                             "pendiente_de_rever")

            with mock.patch.object(self.s, "enviar",
                                   return_value={"ok": True}) as enviar:
                self.s._atender_revers_pendientes()
        enviar.assert_called_once()
        self.assertIn("Rever", enviar.call_args[0][0])
        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"),
                         "no_se_pudo_rever")

    def test_si_el_aviso_no_sale_no_pasa_a_terminal(self):
        """Igual que _avisar_la_falla: un aviso que Telegram no confirma
        no puede dar el correo por terminal -si no, un Telegram caído
        justo en ese instante se lleva el aviso para siempre."""
        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            self.s.atender(mensaje("explicación"))

        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               side_effect=RuntimeError("IMAP caído")):
            for _ in range(secretaria.INTENTOS_MAXIMOS - 1):
                self.s._atender_revers_pendientes()
            with mock.patch.object(self.s, "enviar", return_value=None):
                self.s._atender_revers_pendientes()
        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"),
                         "pendiente_de_rever")

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

        with mock.patch.object(secretaria.bot, "tg"):
            self.s.atender(mensaje("1"))
            self.s.atender(mensaje("SiPago es mi proveedor, once horas después"))
        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"),
                         "pendiente_de_rever")

        # Recién ahora -en el otro hilo, en la próxima vuelta del ciclo
        # de correo- se ejecuta de verdad.
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True) as devolver, \
             mock.patch.object(
                 secretaria.clasificador, "clasificar",
                 return_value={"categoria": "NATALIA", "motivo": "m",
                              "unanime": True}):
            self.s._atender_revers_pendientes()
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


class ReverNoBloqueaElHiloQueEscucha(unittest.TestCase):
    """Ronda 1, hallazgo CRÍTICO: antes, contestar el motivo de un Rever
    ejecutaba rever_ruido() -y con eso clasificador.clasificar(), hasta
    tres minutos de red- adentro de ciclo_de_escucha. Se prueba con DOS
    HILOS DE VERDAD, no con un mock instantáneo que sólo demuestra que
    algo no se llamó: clasificar() se bloquea con un threading.Event que
    el test controla, igual que NoSeBloqueaLaEscucha (test_secretaria.py)
    prueba el caso general de los dos hilos. Acá se prueba el camino
    específico que se coló: Rever."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))
        memoria.anotar(self.s.cx, correo_falso("<r@x>", "Manual",
                                               de="sipago@x"),
                       "RUIDO", "parecía promo")
        memoria.cambiar(self.s.cx, "<r@x>", "archivado")
        _, teclado = self.s.armar_resumen_de_ruido(
            memoria.del_dia(self.s.cx, "archivado", ""))
        self.rever_cb = teclado["inline_keyboard"][0][1]["callback_data"]

    def test_atender_sigue_respondiendo_mientras_el_ciclo_de_correo_clasifica(self):
        adentro = threading.Event()
        liberar = threading.Event()

        def clasificar_lento(*a, **kw):
            adentro.set()
            liberar.wait(5)  # "tarda de verdad": no vuelve hasta que el test avise
            return {"categoria": "NATALIA", "motivo": "cobros",
                    "unanime": True}

        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[]), \
             mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               side_effect=clasificar_lento), \
             mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):

            # Deja el Rever anotado y esperando -como si JP ya hubiera
            # contestado el motivo hace un rato.
            self.s.atender(boton(self.rever_cb))
            self.s.atender(mensaje("1"))
            self.s.atender(mensaje("SiPago es mi proveedor de cobros"))
            self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"),
                             "pendiente_de_rever")

            hilo_correo = threading.Thread(target=self.s.ciclo_de_correo,
                                           daemon=True)
            hilo_correo.start()
            try:
                self.assertTrue(
                    adentro.wait(2),
                    "clasificar() nunca arrancó en el hilo de correo")
                # Mientras el otro hilo sigue adentro de clasificar(),
                # atender() -lo que hace ciclo_de_escucha con cada
                # update- tiene que devolver el control ya, no quedarse
                # esperando a que el otro hilo termine.
                inicio = time.monotonic()
                self.s.atender(mensaje("/estado"))
                elapsed = time.monotonic() - inicio
            finally:
                self.s.parada.set()
                liberar.set()
                hilo_correo.join(2)

        self.assertLess(
            elapsed, 0.5,
            "atender() tardó como si estuviera bloqueado por clasificar():"
            " el hilo que escucha se hubiera quedado mudo para JP")


class AvisoAlMomento(unittest.TestCase):
    """Ronda 1, hallazgo Importante 1: avisar_en_el_momento no
    registraba su tanda, así que sus tres botones -Listo lo vi, No era
    mío, Cliente importante, los tres del diseño §7.2- contestaban "ya
    pasó" sobre un correo que acababa de llegar."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        self.c = dict(correo_falso("<t@x>", "Cierre de etapa",
                                   de="cliente@x"), cc="", cuerpo="texto")
        memoria.anotar(self.s.cx, self.c, "TUYO", "m")

    def _mandar(self):
        with mock.patch.object(secretaria.bot, "tg") as tg:
            self.s.avisar_en_el_momento(self.c, {"categoria": "TUYO"})
        return tg.call_args.kwargs["reply_markup"]

    def test_tiene_los_tres_botones_del_diseno(self):
        teclado = self._mandar()
        textos = [b["text"] for fila in teclado["inline_keyboard"]
                 for b in fila]
        self.assertIn("Listo, lo vi", textos)
        self.assertIn("No era mío", textos)
        self.assertIn("⭐ Cliente importante", textos)

    def test_registra_la_tanda(self):
        teclado = self._mandar()
        cb = teclado["inline_keyboard"][0][0]["callback_data"]
        tanda = cb.split("|")[1].split("-")[0]
        self.assertEqual(self.s.abiertos[tanda], ["<t@x>"])

    def _sin_mentir_ya_paso(self, suave):
        for llamada in suave.call_args_list:
            self.assertNotIn("ya pasó", llamada.kwargs.get("text") or "")

    def test_listo_lo_vi_funciona_y_no_dice_ya_paso(self):
        teclado = self._mandar()
        cb = teclado["inline_keyboard"][0][0]["callback_data"]
        with mock.patch.object(secretaria.bot, "tg_suave") as suave:
            self.s.atender(boton(cb))
        self._sin_mentir_ya_paso(suave)
        self.assertEqual(memoria.situacion(self.s.cx, "<t@x>"), "cerrado")

    def test_no_era_mio_abre_el_teclado_de_categorias(self):
        teclado = self._mandar()
        cb = teclado["inline_keyboard"][0][1]["callback_data"]
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave") as suave:
            self.s.atender(boton(cb))
        self._sin_mentir_ya_paso(suave)
        nuevo = tg.call_args.kwargs["reply_markup"]
        textos = [b["text"] for fila in nuevo["inline_keyboard"] for b in fila]
        self.assertIn("✅ Ya está en copia", textos)  # DELEGADO

    def test_elegir_categoria_tras_no_era_mio_registra_la_correccion(self):
        teclado = self._mandar()
        no_era_mio = teclado["inline_keyboard"][0][1]["callback_data"]
        tanda = no_era_mio.split("|")[1].split("-")[0]
        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):
            self.s.atender(boton(no_era_mio))
            self.s.atender(boton(f"c|{tanda}-1|DELEGADO"))
        f = self.s.cx.execute(
            "SELECT categoria, categoria_jp, situacion FROM correos"
            " WHERE message_id='<t@x>'").fetchone()
        self.assertEqual(f["categoria"], "TUYO")       # lo que dijo el sistema
        self.assertEqual(f["categoria_jp"], "DELEGADO")  # lo que corrigió JP
        self.assertEqual(f["situacion"], "clasificado")
        self.assertNotIn(tanda, self.s.abiertos)

    def test_elegir_ruido_tras_no_era_mio_lo_archiva(self):
        """A diferencia de DELEGADO/ENZO/NATALIA/TUYO, elegir RUIDO acá
        tiene que archivar de verdad -reusando self._archivar(), el
        mismo camino con reintentos del ciclo automático- y no sólo
        anotar la corrección."""
        teclado = self._mandar()
        no_era_mio = teclado["inline_keyboard"][0][1]["callback_data"]
        tanda = no_era_mio.split("|")[1].split("-")[0]
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.correo, "mover_a",
                               return_value=True) as mover:
            self.s.atender(boton(no_era_mio))
            self.s.atender(boton(f"c|{tanda}-1|RUIDO"))
        mover.assert_called_once()
        self.assertEqual(memoria.situacion(self.s.cx, "<t@x>"), "archivado")

    def test_cliente_importante_abre_el_submenu_de_direcciones(self):
        teclado = self._mandar()
        cb = teclado["inline_keyboard"][1][0]["callback_data"]
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave") as suave, \
             mock.patch.object(secretaria.reglas, "direcciones_externas",
                               return_value=[("Cliente", "cliente@x")]):
            self.s.atender(boton(cb))
        self._sin_mentir_ya_paso(suave)
        teclado_dir = tg.call_args.kwargs["reply_markup"]
        textos = [b["text"] for fila in teclado_dir["inline_keyboard"]
                 for b in fila]
        self.assertTrue(any("Cliente" in t for t in textos))

    def test_elegir_una_direccion_la_guarda_y_vuelve_a_categorias(self):
        teclado = self._mandar()
        importante = teclado["inline_keyboard"][1][0]["callback_data"]
        tanda = importante.split("|")[1].split("-")[0]
        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.reglas, "direcciones_externas",
                               return_value=[("Cliente", "cliente@x")]), \
             mock.patch.object(secretaria.reglas, "marcar_importante",
                               return_value=True) as marcar:
            self.s.atender(boton(importante))
            self.s.atender(boton(f"d|{tanda}-1|0"))
        marcar.assert_called_once_with("Cliente", "cliente@x")
        # el último mensaje vuelve a mostrar el teclado de categorías
        ultimo = tg.call_args.kwargs["reply_markup"]
        textos = [b["text"] for fila in ultimo["inline_keyboard"] for b in fila]
        self.assertIn("🔴 Es mío", textos)


class AvisoParaQueDecida(unittest.TestCase):
    """Mismo hallazgo Importante 1, del lado de avisar_para_que_decida
    -DUDA o ningún motor respondió-: tampoco registraba su tanda."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        self.c = dict(correo_falso("<d@x>", "Rareza"), cuerpo="texto")
        memoria.anotar(self.s.cx, self.c, "ERROR", "ningún motor respondió")

    def test_registra_la_tanda_y_elegir_categoria_funciona(self):
        with mock.patch.object(secretaria.bot, "tg") as tg:
            self.s.avisar_para_que_decida(self.c)
        cb = tg.call_args.kwargs["reply_markup"]
        delegado = cb["inline_keyboard"][0][1]["callback_data"]
        tanda = delegado.split("|")[1].split("-")[0]
        self.assertEqual(self.s.abiertos[tanda], ["<d@x>"])

        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave") as suave:
            self.s.atender(boton(delegado))
        for llamada in suave.call_args_list:
            self.assertNotIn("ya pasó", llamada.kwargs.get("text") or "")
        f = self.s.cx.execute(
            "SELECT categoria_jp, situacion FROM correos"
            " WHERE message_id='<d@x>'").fetchone()
        self.assertEqual(f["categoria_jp"], "DELEGADO")
        self.assertEqual(f["situacion"], "clasificado")


class EscrituraFallidaNoPierdeLaTanda(unittest.TestCase):
    """Ronda 1, hallazgo Importante 2: antes, _cerrar_equipo y
    _confirmar_ruido sacaban la tanda de self.abiertos ANTES de
    confirmar la escritura. Si memoria.cambiar()/cambiar_lote()
    reventaba, la excepción subía con la tanda ya vacía: quedaba sin
    cerrar y, si JP volvía a tocar el botón, el mensaje mentía "ya
    pasó" sobre algo que nunca terminó."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def test_cerrar_equipo_no_pierde_la_tanda_si_memoria_cambiar_revienta(self):
        for i in range(2):
            memoria.anotar(self.s.cx, correo_falso(f"<e{i}@x>", f"A {i}"),
                           "DELEGADO", "m")
        equipo = memoria.del_dia(self.s.cx, "clasificado", "")
        _, teclado = self.s.armar_resumen(equipo, "manana")
        cb = teclado["inline_keyboard"][0][0]["callback_data"]
        tanda = cb.split("|")[1].split("-")[0]

        original = memoria.cambiar

        def cambiar_que_revienta(cx, mid, nueva):
            if mid == "<e0@x>":
                raise RuntimeError("disco lleno")
            return original(cx, mid, nueva)

        with mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.memoria, "cambiar",
                               side_effect=cambiar_que_revienta):
            self.s.atender(boton(cb))

        # <e0@x> no se pudo cerrar de verdad: sigue en la tanda, lista
        # para un segundo toque del botón. <e1@x> sí se cerró y ya no
        # está.
        self.assertIn(tanda, self.s.abiertos)
        self.assertIn("<e0@x>", self.s.abiertos[tanda])
        self.assertNotIn("<e1@x>", self.s.abiertos[tanda])
        self.assertEqual(memoria.situacion(self.s.cx, "<e1@x>"), "cerrado")

    def test_confirmar_ruido_no_pierde_la_tanda_si_memoria_revienta(self):
        memoria.anotar(self.s.cx, correo_falso("<r@x>", "Promo"), "RUIDO", "m")
        memoria.cambiar(self.s.cx, "<r@x>", "archivado")
        _, teclado = self.s.armar_resumen_de_ruido(
            memoria.del_dia(self.s.cx, "archivado", ""))
        cb = teclado["inline_keyboard"][0][0]["callback_data"]
        tanda = cb.split("|")[1].split("-")[0]

        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.memoria, "cambiar_lote",
                               side_effect=RuntimeError("disco lleno")):
            self.s.atender(boton(cb))

        self.assertIn(tanda, self.s.abiertos)
        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"), "archivado")
        self.assertIn("de nuevo", tg.call_args.kwargs["text"].lower())

    def test_marcar_como_mio_no_pierde_la_tanda_si_memoria_revienta(self):
        for i in range(2):
            memoria.anotar(self.s.cx, correo_falso(f"<e{i}@x>", f"A {i}"),
                           "DELEGADO", "m")
        equipo = memoria.del_dia(self.s.cx, "clasificado", "")
        _, teclado = self.s.armar_resumen(equipo, "manana")
        mio_cb = teclado["inline_keyboard"][0][1]["callback_data"]
        tanda = mio_cb.split("|")[1].split("-")[0]

        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"), \
             mock.patch.object(secretaria.memoria, "corregir",
                               side_effect=RuntimeError("disco lleno")):
            self.s.atender(boton(mio_cb))
            self.s.atender(mensaje("1"))
        self.assertIn(tanda, self.s.abiertos)
        self.assertIn("<e0@x>", self.s.abiertos[tanda])
        self.assertIn("de nuevo", tg.call_args.kwargs["text"].lower())


class SobreviveUnReinicio(unittest.TestCase):
    """Ronda 1, hallazgo Importante 3: self.pendiente se persiste
    (memoria.guardar_pendiente/cargar_pendiente), así que un reinicio
    del proceso entre que JP contestó un paso de Rever y el siguiente
    no le hace perder la pregunta -y si igual la pierde (self.abiertos,
    que NO se persiste), el mensaje sigue siendo honesto, nunca "no
    entiendo"."""

    def _armar_rever_pendiente(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        vieja = secretaria.Secretaria(cx=memoria.abrir(f.name))
        memoria.anotar(vieja.cx, correo_falso("<r@x>", "Manual",
                                              de="sipago@x"),
                       "RUIDO", "parecía promo")
        memoria.cambiar(vieja.cx, "<r@x>", "archivado")
        _, teclado = vieja.armar_resumen_de_ruido(
            memoria.del_dia(vieja.cx, "archivado", ""))
        rever_cb = teclado["inline_keyboard"][0][1]["callback_data"]
        return vieja, rever_cb

    def test_reinicio_a_mitad_del_motivo_retoma_la_pregunta(self):
        vieja, rever_cb = self._armar_rever_pendiente()
        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):
            vieja.atender(boton(rever_cb))
            vieja.atender(mensaje("1"))
        self.assertEqual(vieja.pendiente["tipo"], "rever_motivo")

        # Acá, en la realidad, el proceso se reinicia -launchd, o una
        # caída-. Una Secretaria nueva sobre la MISMA base es el
        # equivalente de test: lee de memoria.cargar_pendiente() en vez
        # de partir de self.pendiente=None.
        nueva = secretaria.Secretaria(cx=vieja.cx)
        self.assertEqual(nueva.pendiente, vieja.pendiente)

        with mock.patch.object(secretaria.bot, "tg") as tg, \
             mock.patch.object(secretaria.bot, "tg_suave"):
            nueva.atender(mensaje("SiPago es mi proveedor de cobros"))
        texto = tg.call_args.kwargs["text"].lower()
        self.assertNotIn("no sé responder", texto)
        self.assertEqual(memoria.situacion(nueva.cx, "<r@x>"),
                         "pendiente_de_rever")
        f = nueva.cx.execute("SELECT explicacion FROM correos"
                             " WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["explicacion"], "SiPago es mi proveedor de cobros")

    def test_reinicio_a_mitad_de_elegir_numero_no_miente_aunque_pierda_la_tanda(self):
        """self.abiertos no se persiste -self.pendiente sí-, así que
        tras un reinicio en este paso no hay contra qué validar el
        número. El mensaje tiene que seguir siendo honesto."""
        vieja, rever_cb = self._armar_rever_pendiente()
        with mock.patch.object(secretaria.bot, "tg"), \
             mock.patch.object(secretaria.bot, "tg_suave"):
            vieja.atender(boton(rever_cb))
        self.assertEqual(vieja.pendiente["tipo"], "rever_elegir")

        nueva = secretaria.Secretaria(cx=vieja.cx)
        self.assertEqual(nueva.pendiente["tipo"], "rever_elegir")
        with mock.patch.object(secretaria.bot, "tg") as tg:
            nueva.atender(mensaje("1"))
        texto = tg.call_args.kwargs["text"].lower()
        self.assertNotIn("no sé responder", texto)
        self.assertIn("ya se cerró", texto)


if __name__ == "__main__":
    unittest.main()
