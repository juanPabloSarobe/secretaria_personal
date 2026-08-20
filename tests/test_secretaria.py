import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from datetime import datetime
from unittest.mock import patch

import memoria
import secretaria


def correo_falso(mid, asunto, de="promo@ejemplo.com"):
    return {"message_id": mid, "uid": "1", "de": de, "para": "jp@x", "cc": "",
            "asunto": asunto, "fecha": "Mon, 17 Aug 2026 09:00:00 -0300",
            "cuerpo": "cuerpo", "adjuntos": []}


class _CxMuda:
    """Reemplazo de la conexión de memoria.py para tests que no necesitan
    una base de verdad: memoria.latido() se llama al final de cada vuelta
    del ciclo de correo, y le alcanza con algo que acepte execute()/
    commit() sin hacer nada."""

    def execute(self, *a, **k):
        pass

    def commit(self):
        pass


class Reloj(unittest.TestCase):
    def setUp(self):
        self.r = secretaria.Reloj()

    def test_dispara_el_resumen_al_pasar_la_hora(self):
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 17, 8, 31),
            ultimo=datetime(2026, 8, 17, 8, 29))
        self.assertEqual(pendientes, ["manana"])

    def test_no_lo_dispara_dos_veces(self):
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 17, 8, 45),
            ultimo=datetime(2026, 8, 17, 8, 31))
        self.assertEqual(pendientes, [])

    def test_si_estuvo_caida_dispara_lo_que_se_perdio_en_orden(self):
        """launchd la levanta después de una caída larga. Los resúmenes que
        se perdió tienen que salir, y en orden."""
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 17, 19, 0),
            ultimo=datetime(2026, 8, 17, 7, 0))
        self.assertEqual(pendientes, ["manana", "tarde", "ruido"])

    def test_caida_de_mas_de_un_dia_no_pierde_los_del_medio(self):
        """Regresión: la primera versión calculaba el corte siempre sobre
        la fecha de `ahora`, así que una caída que cruzara la medianoche
        perdía para siempre los resúmenes de los días intermedios. Acá la
        caída va del 17 a la mañana del 19: tienen que salir los tres del
        17, los tres del 18, y el de la mañana del 19 (los otros dos del
        19 todavía no llegaron)."""
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 19, 10, 0),
            ultimo=datetime(2026, 8, 17, 7, 0))
        self.assertEqual(pendientes, [
            "manana", "tarde", "ruido",
            "manana", "tarde", "ruido",
            "manana",
        ])

    def test_caida_de_varios_dias(self):
        """Del 17 ya habían pasado los tres antes de la caída (`ultimo`
        es las 19:00, después del último corte de ese día), así que no
        se repiten. Quedan los tres de cada uno de los tres días
        siguientes: 18, 19 y 20."""
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 21, 0, 0),
            ultimo=datetime(2026, 8, 17, 19, 0))
        self.assertEqual(len(pendientes), 9)
        self.assertEqual(pendientes[:3], ["manana", "tarde", "ruido"])
        self.assertEqual(pendientes[-3:], ["manana", "tarde", "ruido"])

    def test_dispara_justo_en_el_minuto_del_corte(self):
        """El corte es inclusive del lado de `ahora`: si el reloj marca
        exactamente el minuto, ya corresponde."""
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 17, 8, 30, 0),
            ultimo=datetime(2026, 8, 17, 8, 29, 59))
        self.assertEqual(pendientes, ["manana"])

    def test_no_dispara_si_ultimo_ya_es_el_corte_exacto(self):
        """Y es exclusivo del lado de `ultimo`: si la última consulta ya
        fue justo en el corte, no se repite en la próxima."""
        pendientes = self.r.momentos_pendientes(
            ahora=datetime(2026, 8, 17, 8, 30, 1),
            ultimo=datetime(2026, 8, 17, 8, 30, 0))
        self.assertEqual(pendientes, [])


class Horario(unittest.TestCase):
    def test_un_martes_al_mediodia_esta_en_horario(self):
        self.assertTrue(secretaria.en_horario(datetime(2026, 8, 18, 12, 0)))

    def test_un_martes_a_las_seis_de_la_manana_no(self):
        self.assertFalse(secretaria.en_horario(datetime(2026, 8, 18, 6, 0)))

    def test_un_domingo_no(self):
        self.assertFalse(secretaria.en_horario(datetime(2026, 8, 16, 12, 0)))


class NoSeBloqueaLaEscucha(unittest.TestCase):
    """La razón de ser de los dos hilos: si revisar_casilla se queda
    trabajando (clasificar tarda hasta 180s), el hilo que escucha a JP
    tiene que seguir atendiendo updates igual. Sin esto, un solo hilo
    dejaría a JP mudo todo ese rato.

    Nada de sleep con número mágico de segundos para simular la demora:
    revisar_casilla se bloquea con un threading.Event que el test controla,
    y se libera explícitamente. El único tiempo de espera "a ciegas" es un
    polling acotado a 2s como techo de seguridad, no como duración
    esperada."""

    def test_la_escucha_sigue_atendiendo_mientras_el_correo_tarda(self):
        s = secretaria.Secretaria(cx=_CxMuda())

        adentro = threading.Event()
        liberar = threading.Event()

        def revisar_casilla_lenta():
            adentro.set()
            liberar.wait(5)  # "tarda mucho": no vuelve hasta que el test avise

        atendidos = []
        s.revisar_casilla = revisar_casilla_lenta
        s.atender = lambda update: atendidos.append(update["update_id"])

        contador = {"n": 0}

        def tg_falso(metodo, **params):
            # Sin red: cada llamada a getUpdates devuelve un update nuevo
            # al toque, como si Telegram contestara instantáneo.
            contador["n"] += 1
            return {"result": [{"update_id": contador["n"]}]}

        with patch("secretaria.bot.tg", side_effect=tg_falso):
            hilo_correo = threading.Thread(target=s.ciclo_de_correo,
                                            daemon=True)
            hilo_escucha = threading.Thread(target=s.ciclo_de_escucha,
                                             daemon=True)
            hilo_correo.start()
            hilo_escucha.start()
            try:
                self.assertTrue(adentro.wait(2),
                                 "revisar_casilla nunca arrancó")

                # Techo de 2s para juntar unos cuantos updates; en la
                # práctica se junta en milisegundos porque nada bloquea al
                # hilo de escucha.
                limite = time.monotonic() + 2
                while len(atendidos) < 3 and time.monotonic() < limite:
                    time.sleep(0.01)
            finally:
                s.parada.set()
                liberar.set()
                hilo_correo.join(2)
                hilo_escucha.join(2)

        self.assertGreaterEqual(
            len(atendidos), 3,
            "el hilo de escucha no atendió nada mientras el de correo"
            " seguía adentro de revisar_casilla: se estarían bloqueando"
            " entre sí")


class ElRelojInternoSoloAvanza(unittest.TestCase):
    """Importante 3: si la hora del sistema salta hacia atrás (típico de
    un NTP que corrige después de un suspend largo), `self.ultimo_reloj`
    no puede retroceder, o el resumen ya mandado se dispara de nuevo en la
    vuelta siguiente."""

    def test_un_salto_hacia_atras_no_pisa_el_reloj_ni_duplica_avisos(self):
        s = secretaria.Secretaria(cx=_CxMuda())
        disparados = []
        s.mandar_resumen = disparados.append

        s.ultimo_reloj = datetime(2026, 8, 17, 8, 29)
        s._disparar_resumenes(datetime(2026, 8, 17, 8, 31))
        self.assertEqual(disparados, ["manana"])
        self.assertEqual(s.ultimo_reloj, datetime(2026, 8, 17, 8, 31))

        # El reloj del sistema "salta hacia atrás": una vuelta siguiente
        # ve un `ahora` anterior al `ultimo_reloj` que ya se había
        # guardado. No tiene que disparar nada de nuevo, y `ultimo_reloj`
        # no puede retroceder.
        s._disparar_resumenes(datetime(2026, 8, 17, 8, 30))
        self.assertEqual(disparados, ["manana"])
        self.assertEqual(s.ultimo_reloj, datetime(2026, 8, 17, 8, 31))

        # Cuando el reloj retoma para adelante, no se repite "manana": el
        # salto hacia atrás no debe haber dejado una ventana abierta.
        s._disparar_resumenes(datetime(2026, 8, 17, 8, 35))
        self.assertEqual(disparados, ["manana"])
        self.assertEqual(s.ultimo_reloj, datetime(2026, 8, 17, 8, 35))


class ElManejadorDeExcepcionesNoTiraNada(unittest.TestCase):
    """Crítico 2 (primera mitad): si algo revienta *adentro* del `except`
    —el propio print, por un pipe roto o el disco lleno; nada exótico en
    un proceso que corre meses— la excepción se escapaba del `while` y el
    hilo moría en silencio. El otro hilo seguía vivo, así que ni
    `arrancar()` se enteraba."""

    def test_el_ciclo_de_correo_sigue_vivo_aunque_reviente_el_logueo(self):
        s = secretaria.Secretaria(cx=_CxMuda())
        vueltas = {"n": 0}

        def revisar_que_siempre_falla():
            vueltas["n"] += 1
            raise RuntimeError("falla realista de revisar_casilla")

        s.revisar_casilla = revisar_que_siempre_falla

        # CADA a 0 para que las vueltas no esperen 180s entre sí, y print
        # parcheado para que reviente como lo probó el revisor (OSError,
        # lo que pasa con un pipe roto o un disco lleno).
        with patch("secretaria.CADA", 0), \
             patch("secretaria.print", side_effect=OSError("pipe roto"),
                   create=True):
            hilo = threading.Thread(target=s.ciclo_de_correo, daemon=True)
            hilo.start()
            limite = time.monotonic() + 2
            while vueltas["n"] < 3 and time.monotonic() < limite:
                time.sleep(0.01)
            s.parada.set()
            hilo.join(2)

        self.assertGreaterEqual(
            vueltas["n"], 3,
            "el ciclo se murió antes de la tercera vuelta: algo se"
            " escapó del manejador de excepciones")
        self.assertFalse(hilo.is_alive())

    def test_registrar_no_propaga_si_el_propio_print_revienta(self):
        """Prueba directa y rápida de _registrar, la pieza que hace
        posible el test de arriba."""
        with patch("secretaria.print", side_effect=OSError("disco lleno"),
                    create=True):
            try:
                secretaria._registrar("correo", RuntimeError("boom"))
            except Exception as e:
                self.fail(f"_registrar dejó escapar {e!r}")


class ArrancarVigilaLosHilos(unittest.TestCase):
    """Crítico 2 (segunda mitad): join() a ciegas sobre los dos hilos
    hace que, si uno se muere, el otro lo mantenga bloqueado para
    siempre — el proceso no se cae, y entonces launchd nunca lo
    reinicia. arrancar() tiene que notar la muerte y terminar el proceso
    con código distinto de cero."""

    def test_termina_con_codigo_distinto_de_cero_si_se_muere_un_hilo(self):
        s = secretaria.Secretaria(cx=_CxMuda())
        # ciclo_de_correo "se muere" apenas arranca: un target que
        # simplemente vuelve simula un hilo que reventó y no fue
        # atajado por nada.
        s.ciclo_de_correo = lambda: None

        vivo = threading.Event()

        def escucha_viva():
            vivo.set()
            s.parada.wait(5)

        s.ciclo_de_escucha = escucha_viva

        # Poll rápido para que el test no dependa de esperar el intervalo
        # de producción (1s): con esto alcanza con milisegundos.
        with patch("secretaria.POLL_HILOS", 0.01):
            with self.assertRaises(SystemExit) as cm:
                s.arrancar()

        self.assertNotEqual(cm.exception.code, 0)
        self.assertTrue(vivo.wait(2), "ni siquiera llegó a arrancar el"
                         " otro hilo")
        # arrancar() tiene que haber pedido la parada: un hilo bien
        # comportado como escucha_viva se entera y no queda huérfano.
        self.assertTrue(s.parada.is_set())


class RevisarCasilla(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def test_lo_sin_clasificar_nunca_se_archiva(self):
        """Si no respondió ningún motor, el correo se le muestra a JP. El
        silencio jamás significa 'era ruido'."""
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[correo_falso("<1@x>", "Algo")]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               side_effect=RuntimeError("sin motores")), \
             mock.patch.object(secretaria.correo, "mover_a") as mover:
            self.s.revisar_casilla()
        mover.assert_not_called()
        self.assertEqual(memoria.situacion(self.s.cx, "<1@x>"),
                         "mostrado_sin_clasificar")

    def test_en_seco_no_toca_la_casilla(self):
        with mock.patch.object(secretaria, "EN_SECO", True), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[correo_falso("<2@x>", "Promo")]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(secretaria.correo, "mover_a") as mover:
            self.s.revisar_casilla()
        mover.assert_not_called()

    def test_con_el_freno_sacado_el_ruido_se_archiva(self):
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[correo_falso("<3@x>", "Promo")]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(secretaria.correo, "mover_a",
                               return_value=True) as mover:
            self.s.revisar_casilla()
        mover.assert_called_once_with("<3@x>", "INBOX.Ruido")
        self.assertEqual(memoria.situacion(self.s.cx, "<3@x>"), "archivado")

    def test_un_correo_ya_procesado_no_se_reprocesa(self):
        c = correo_falso("<4@x>", "Repetido")
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c, c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "TUYO",
                                             "motivo": "m", "unanime": True}) as cl:
            self.s.revisar_casilla()
        self.assertEqual(cl.call_count, 1)

    def test_duda_se_muestra_a_jp_y_no_se_archiva(self):
        """Crítico 2: DUDA es un resultado NORMAL de clasificar() (dos
        pasadas discrepan), no una excepción. Antes no caía en ninguna
        rama del if/elif y quedaba enterrado en la base para siempre,
        marcado 'mostrado_sin_clasificar' sin que JP lo hubiera visto
        nunca -- la falla silenciosa que más preocupa, y la más difícil
        de notar porque no imprime ningún error."""
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[correo_falso("<5@x>", "Ambiguo")]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "DUDA",
                                             "motivo": "sin acuerdo",
                                             "unanime": False}), \
             mock.patch.object(secretaria.correo, "mover_a") as mover, \
             mock.patch.object(secretaria.Secretaria,
                               "avisar_para_que_decida") as avisar:
            self.s.revisar_casilla()
        mover.assert_not_called()
        avisar.assert_called_once()
        self.assertEqual(memoria.situacion(self.s.cx, "<5@x>"),
                         "mostrado_sin_clasificar")


class AtajoRuidoConocido(unittest.TestCase):
    """Importante 5: si JP ya marcó este remitente como ruido, dos veces o
    más y siempre, no hace falta gastar una llamada al único motor
    configurado (con cuota limitada) para que le diga lo mismo. El atajo
    replica exactamente los resguardos de simulacro.py: protegido() manda
    sobre el atajo, y 1 de cada MUESTREO_CONTROL igual se pregunta para no
    dejar de medir si el criterio se degrada."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def test_remitente_conocido_no_gasta_una_llamada_al_modelo(self):
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[correo_falso("<6@x>", "Publi")]), \
             mock.patch.object(secretaria.reglas, "remitentes_ruido",
                               return_value=({"promo@ejemplo.com"}, set())), \
             mock.patch.object(secretaria.reglas, "es_ruido_conocido",
                               return_value="remitente promo@ejemplo.com"), \
             mock.patch.object(secretaria.reglas, "protegido",
                               return_value=False), \
             mock.patch.object(secretaria.random, "randrange",
                               return_value=1), \
             mock.patch.object(secretaria.clasificador, "clasificar") as cl:
            self.s.revisar_casilla()
        cl.assert_not_called()
        self.assertEqual(memoria.situacion(self.s.cx, "<6@x>"), "clasificado")

    def test_protegido_manda_sobre_el_atajo(self):
        """Sobre lo que JP ya se pronunció por escrito (roster.md o
        reglas.md), el sistema no decide solo -- ni siquiera con el
        atajo. El caso real: el filtro archivó una promoción de SiPago
        pese a que reglas.md decía explícitamente que no era ruido."""
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[correo_falso("<7@x>", "Publi")]), \
             mock.patch.object(secretaria.reglas, "remitentes_ruido",
                               return_value=({"promo@ejemplo.com"}, set())), \
             mock.patch.object(secretaria.reglas, "es_ruido_conocido",
                               return_value="remitente promo@ejemplo.com"), \
             mock.patch.object(secretaria.reglas, "protegido",
                               return_value=True), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "m",
                                             "unanime": True}) as cl:
            self.s.revisar_casilla()
        cl.assert_called_once()

    def test_uno_de_cada_n_igual_se_pregunta(self):
        """El muestreo de control: aunque el remitente sea ruido conocido,
        una fracción de las veces se le pregunta igual al modelo, para no
        perder de vista si el criterio se degradó."""
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[correo_falso("<8@x>", "Publi")]), \
             mock.patch.object(secretaria.reglas, "remitentes_ruido",
                               return_value=({"promo@ejemplo.com"}, set())), \
             mock.patch.object(secretaria.reglas, "es_ruido_conocido",
                               return_value="remitente promo@ejemplo.com"), \
             mock.patch.object(secretaria.reglas, "protegido",
                               return_value=False), \
             mock.patch.object(secretaria.random, "randrange",
                               return_value=0), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "m",
                                             "unanime": True}) as cl:
            self.s.revisar_casilla()
        cl.assert_called_once()


class PuedeEscribir(unittest.TestCase):
    """Menor 6: un solo lugar junta los dos frenos (EN_SECO y pausada),
    así cada punto nuevo que escriba en la casilla (tarea 9 a 11) no
    tiene que acordarse de repetir el chequeo."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def test_en_seco_no_puede_escribir(self):
        with mock.patch.object(secretaria, "EN_SECO", True):
            self.assertFalse(self.s.puede_escribir())

    def test_con_el_freno_sacado_y_sin_pausa_si_puede(self):
        with mock.patch.object(secretaria, "EN_SECO", False):
            self.s.pausada = False
            self.assertTrue(self.s.puede_escribir())

    def test_pausada_no_puede_escribir_aunque_el_freno_este_sacado(self):
        with mock.patch.object(secretaria, "EN_SECO", False):
            self.s.pausada = True
            self.assertFalse(self.s.puede_escribir())


class CodigoConvenido(unittest.TestCase):
    """Los códigos convenidos son frases que JP acuerda con sus
    interlocutores ("tal cual lo charlado", hay siete en codigos.md).
    Cuando aparecen, el correo es de JP sin discusión y sin consultar al
    modelo -- JP descartó explícitamente la alternativa de tener que
    avisarle al bot cuando espera algo importante ("mi idea es que el bot
    me ayude a mí, no que yo le tenga que avisar cosas"). Gana sobre todo
    lo demás: sobre el ruido conocido, sobre protegido(), sobre el
    clasificador."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def _categoria_guardada(self, message_id):
        fila = self.s.cx.execute(
            "SELECT categoria FROM correos WHERE message_id = ?",
            (message_id,)).fetchone()
        return fila["categoria"] if fila else None

    def test_codigo_convenido_es_tuyo_sin_consultar_al_modelo(self):
        c = correo_falso("<9@x>", "Consulta")
        c["cuerpo"] = "Va a ser tal cual lo charlado, gracias"
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.reglas, "codigos_convenidos",
                               return_value=["tal cual lo charlado"]), \
             mock.patch.object(secretaria.clasificador, "clasificar") as cl, \
             mock.patch.object(secretaria.correo, "mover_a") as mover:
            self.s.revisar_casilla()
        cl.assert_not_called()
        mover.assert_not_called()
        self.assertEqual(self._categoria_guardada("<9@x>"), "TUYO")

    def test_el_codigo_gana_sobre_el_ruido_conocido(self):
        """Un remitente que suele mandar ruido, pero que esta vez usa el
        código convenido, es de JP -- la señal deliberada de una persona
        gana sobre el histórico estadístico."""
        c = correo_falso("<10@x>", "Consulta", de="promo@ejemplo.com")
        c["cuerpo"] = "de acuerdo a lo conversado te mando esto"
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.reglas, "codigos_convenidos",
                               return_value=["de acuerdo a lo conversado"]), \
             mock.patch.object(secretaria.reglas, "es_ruido_conocido",
                               return_value="remitente promo@ejemplo.com"), \
             mock.patch.object(secretaria.reglas, "protegido",
                               return_value=False), \
             mock.patch.object(secretaria.clasificador, "clasificar") as cl, \
             mock.patch.object(secretaria.correo, "mover_a") as mover:
            self.s.revisar_casilla()
        cl.assert_not_called()
        mover.assert_not_called()
        self.assertEqual(self._categoria_guardada("<10@x>"), "TUYO")

    def test_la_comparacion_no_distingue_acentos_ni_mayusculas(self):
        """JP escribe desde el celular y a veces sin tildes. reglas.py ya
        normaliza los dos lados de la comparación (ver test_reglas.py);
        esto confirma que la conexión en revisar_casilla no lo rompe."""
        c = correo_falso("<11@x>", "Consulta")
        c["cuerpo"] = "TAL CUAL LO CHARLADO, como dijimos"
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.reglas, "codigos_convenidos",
                               return_value=["tal cual lo charlado"]), \
             mock.patch.object(secretaria.clasificador, "clasificar") as cl:
            self.s.revisar_casilla()
        cl.assert_not_called()
        self.assertEqual(self._categoria_guardada("<11@x>"), "TUYO")



class Reintento(unittest.TestCase):
    """Importante 2 de la ronda 3: si mover_a revienta, el correo no
    puede quedar invisible para siempre. memoria.anotar() ya corrió antes
    del intento de archivar, así que sin este resguardo el chequeo de
    "¿ya lo vi?" de la vuelta siguiente lo saltea para siempre -- se
    queda en la bandeja de JP, sin archivar y sin que nadie lo
    reintente."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def test_un_mover_a_que_revienta_deja_pendiente_de_archivar(self):
        c = correo_falso("<12@x>", "Promo")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(secretaria.correo, "mover_a",
                               side_effect=RuntimeError("red caída")):
            self.s.revisar_casilla()
        self.assertEqual(memoria.situacion(self.s.cx, "<12@x>"),
                         "pendiente_de_archivar")

    def test_una_operacion_a_medias_tambien_deja_pendiente_de_archivar(self):
        """correo.OperacionAMedias es una excepción más para este
        propósito: tampoco puede confirmarse como archivado, así que
        también deja el correo pendiente de reintento."""
        c = correo_falso("<13@x>", "Promo")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(
                 secretaria.correo, "mover_a",
                 side_effect=secretaria.correo.OperacionAMedias("a medias")):
            self.s.revisar_casilla()
        self.assertEqual(memoria.situacion(self.s.cx, "<13@x>"),
                         "pendiente_de_archivar")

    def test_el_ciclo_siguiente_reintenta_sin_volver_a_clasificar(self):
        c = correo_falso("<14@x>", "Promo")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}) as cl, \
             mock.patch.object(
                 secretaria.correo, "mover_a",
                 side_effect=[RuntimeError("red caída"), True]) as mover:
            self.s.revisar_casilla()
            self.assertEqual(memoria.situacion(self.s.cx, "<14@x>"),
                             "pendiente_de_archivar")
            nuevos_segundo_ciclo = self.s.revisar_casilla()
        self.assertEqual(mover.call_count, 2)
        cl.assert_called_once()
        self.assertEqual(memoria.situacion(self.s.cx, "<14@x>"), "archivado")
        self.assertEqual(nuevos_segundo_ciclo, 0)

    def test_si_el_freno_esta_puesto_el_reintento_no_llama_a_mover_a(self):
        c = correo_falso("<15@x>", "Promo")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(secretaria.correo, "mover_a",
                               side_effect=RuntimeError("red caída")):
            self.s.revisar_casilla()
        with mock.patch.object(secretaria, "EN_SECO", True), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.correo, "mover_a") as mover:
            self.s.revisar_casilla()
        mover.assert_not_called()
        self.assertEqual(memoria.situacion(self.s.cx, "<15@x>"),
                         "pendiente_de_archivar")

    def test_si_ya_no_esta_mas_en_inbox_deja_de_reintentar(self):
        """Un mover_a que ya no encuentra el correo (alguien lo movió o
        lo borró a mano) no es un fallo para reintentar: no hay nada que
        reintentar, así que vuelve a "clasificado" en vez de quedar dando
        vueltas en pendiente_de_archivar para siempre."""
        c = correo_falso("<16@x>", "Promo")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(
                 secretaria.correo, "mover_a",
                 side_effect=[RuntimeError("red caída"), False]):
            self.s.revisar_casilla()
            self.s.revisar_casilla()
        self.assertEqual(memoria.situacion(self.s.cx, "<16@x>"),
                         "clasificado")



if __name__ == "__main__":
    unittest.main()
