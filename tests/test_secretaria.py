import os
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from datetime import datetime
from unittest.mock import patch

import correo
import memoria
import secretaria
from tests.buzon_falso import BuzonFalso, correo_de, hace
from tests.test_correo import _IMAPFalso

UID = "77"
VALIDEZ = "1000"


def correo_falso(mid, asunto, de="promo@ejemplo.com"):
    """Un correo como el que deja traer_nuevos().

    Trae `uid` y `uidvalidity` porque desde la ronda 6 son parte de la
    referencia con la que se ubica el mensaje en el servidor: el
    Message-ID no alcanza -no todos los correos lo traen- y el UID solo
    tampoco, porque vale mientras el UIDVALIDITY de la carpeta no cambie.
    """
    return {"message_id": mid, "uid": UID, "uidvalidity": VALIDEZ, "de": de,
            "para": "jp@x", "cc": "", "asunto": asunto,
            "fecha": "Mon, 17 Aug 2026 09:00:00 -0300",
            "cuerpo": "cuerpo", "adjuntos": []}


def buzon_con(c, **como_contesta):
    """El doble de IMAP con ESE correo adentro de INBOX.

    Que el mensaje del buzón sea el mismo que el de la base no es un
    detalle de armado: si no coinciden, el código no lo encuentra -y tiene
    que no encontrarlo-. El doble viejo contestaba que sí a cualquier
    búsqueda y por eso no se notaba la diferencia.
    """
    como_contesta.setdefault("uid_buscado", c["uid"].encode())
    return _IMAPFalso(mid=c["message_id"], de=c["de"], asunto=c["asunto"],
                      fecha=c["fecha"], **como_contesta)


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
        # Recibe el correo entero y no un Message-ID: mover_a necesita el
        # UID y el UIDVALIDITY para poder ubicar también los correos que
        # no traen Message-ID (ver ronda 6, hallazgo 1).
        mover.assert_called_once()
        pasado, carpeta = mover.call_args[0]
        self.assertEqual(pasado["message_id"], "<3@x>")
        self.assertEqual(carpeta, "INBOX.Ruido")
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

    def test_una_operacion_a_medias_deja_pendiente_de_borrar(self):
        """OperacionAMedias dice algo que ninguna otra excepción dice: la
        copia YA está hecha en Ruido y lo único que falta es borrar el
        original de INBOX. Por eso no deja "pendiente_de_archivar" -que
        reintentaría el mover_a entero, con COPY nuevo y un duplicado por
        vuelta- sino "pendiente_de_borrar", que reintenta sólo el borrado.
        (Este test antes esperaba "pendiente_de_archivar": esa expectativa
        es justo la que causaba la acumulación de duplicados.)"""
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
                         "pendiente_de_borrar")

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



class TopeDeReintentos(unittest.TestCase):
    """El reintento de la ronda 3 no tenía tope: si archivar fallaba
    siempre, revisar_casilla lo reintentaba cada 180 segundos para
    siempre, sin avisarle a JP. Y cuando la falla era del lado del
    borrado, cada vuelta hacía un COPY nuevo: varias copias por hora en
    Ruido, indefinidamente y en silencio."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def test_una_falla_de_borrado_persistente_no_acumula_copias(self):
        """El caso que motiva todo esto, contado en COPY: con el EXPUNGE
        fallando siempre, el correo se copia UNA sola vez a Ruido. Antes,
        cada vuelta del ciclo reintentaba el mover_a completo y dejaba
        una copia nueva."""
        c = correo_falso("<17@x>", "Promo")
        fake = buzon_con(c, copy_ok=True, store_ok=True, expunge_ok=False)
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "abrir_buzon",
                               return_value=fake), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(self.s, "enviar") as enviar:
            for _ in range(10):
                self.s.revisar_casilla()

        copias = [x for x in fake.comandos if x[0] == "COPY"]
        self.assertEqual(len(copias), 1,
                         f"se hicieron {len(copias)} COPY: cada uno deja un"
                         " duplicado en Ruido")
        # Sí se reintentó: un intento de borrado por vuelta, hasta el tope.
        expurgos = [x for x in fake.comandos if x[0] == "EXPUNGE"]
        self.assertEqual(len(expurgos), secretaria.INTENTOS_MAXIMOS)
        self.assertEqual(memoria.situacion(self.s.cx, "<17@x>"),
                         "no_se_pudo_archivar")
        self.assertEqual(enviar.call_count, 1)

    def test_el_tope_frena_los_reintentos(self):
        c = correo_falso("<18@x>", "Promo")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(secretaria.correo, "mover_a",
                               side_effect=RuntimeError("red caída")) as mover, \
             mock.patch.object(self.s, "enviar"):
            for _ in range(20):
                self.s.revisar_casilla()
        self.assertEqual(mover.call_count, secretaria.INTENTOS_MAXIMOS)
        self.assertEqual(memoria.situacion(self.s.cx, "<18@x>"),
                         "no_se_pudo_archivar")

    def test_el_aviso_a_jp_sale_una_sola_vez(self):
        """Veinte vueltas del ciclo, un solo mensaje: si avisara en cada
        vuelta, JP recibiría un aviso cada 180 segundos y en dos días
        apagaría las notificaciones -que es la manera más rápida de
        convertir esto otra vez en una falla silenciosa."""
        c = correo_falso("<19@x>", "Promo")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(secretaria.correo, "mover_a",
                               side_effect=RuntimeError("red caída")), \
             mock.patch.object(self.s, "enviar") as enviar:
            for _ in range(20):
                self.s.revisar_casilla()
        self.assertEqual(enviar.call_count, 1)

    def test_el_aviso_dice_de_quien_es_de_que_se_trata_y_que_fallo(self):
        c = correo_falso("<20@x>", "Factura de agosto",
                         de="cobranzas@proveedor.com")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(
                 secretaria.correo, "mover_a",
                 side_effect=secretaria.correo.OperacionAMedias(
                     "cuota agotada en INBOX.Ruido")), \
             mock.patch.object(
                 secretaria.correo, "borrar_el_original",
                 side_effect=secretaria.correo.OperacionAMedias(
                     "cuota agotada en INBOX.Ruido")), \
             mock.patch.object(self.s, "enviar") as enviar:
            for _ in range(secretaria.INTENTOS_MAXIMOS + 3):
                self.s.revisar_casilla()
        texto = enviar.call_args[0][0]
        self.assertIn("Factura de agosto", texto)
        self.assertIn("cobranzas@proveedor.com", texto)
        self.assertIn("OperacionAMedias", texto)
        self.assertIn("cuota agotada", texto)

    def test_mientras_no_se_agote_el_tope_sigue_reintentando(self):
        """Un tope de 5 no puede volverse un tope de 1: una caída corta de
        red -o el servidor IMAP reiniciándose- tiene que resolverse sola,
        sin molestar a JP."""
        c = correo_falso("<21@x>", "Promo")
        fallas = [RuntimeError("red caída")] * (secretaria.INTENTOS_MAXIMOS - 1)
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(secretaria.correo, "mover_a",
                               side_effect=fallas + [True]), \
             mock.patch.object(self.s, "enviar") as enviar:
            for _ in range(secretaria.INTENTOS_MAXIMOS):
                self.s.revisar_casilla()
        self.assertEqual(memoria.situacion(self.s.cx, "<21@x>"), "archivado")
        enviar.assert_not_called()

    def test_el_reintento_a_medias_no_vuelve_a_consultar_al_modelo(self):
        c = correo_falso("<22@x>", "Promo")
        fake = buzon_con(c, copy_ok=True, store_ok=True, expunge_ok=False)
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "abrir_buzon",
                               return_value=fake), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}) as cl, \
             mock.patch.object(self.s, "enviar"):
            for _ in range(6):
                nuevos = self.s.revisar_casilla()
        cl.assert_called_once()
        self.assertEqual(nuevos, 0)

    def test_si_el_borrado_del_reintento_sale_bien_queda_archivado(self):
        """El EXPUNGE falla la primera vez (queda a medias) y anda la
        segunda: el correo termina archivado, con UN solo COPY."""
        c = correo_falso("<23@x>", "Promo")
        fake = buzon_con(c, copy_ok=True, store_ok=True, expunge_ok=False)
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "abrir_buzon",
                               return_value=fake), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(self.s, "enviar") as enviar:
            self.s.revisar_casilla()
            self.assertEqual(memoria.situacion(self.s.cx, "<23@x>"),
                             "pendiente_de_borrar")
            fake.expunge_ok = True
            self.s.revisar_casilla()
        self.assertEqual(memoria.situacion(self.s.cx, "<23@x>"), "archivado")
        self.assertEqual(len([x for x in fake.comandos if x[0] == "COPY"]), 1)
        enviar.assert_not_called()



class CopyRechazado(unittest.TestCase):
    """La última de la familia, y entraba por la puerta de al lado: si el
    servidor rechazaba el COPY con NO, mover_a devolvía el mismo False
    que cuando el correo simplemente ya no está en INBOX. _archivar leía
    ese False como "no hay nada que hacer", marcaba clasificado, y el
    correo se quedaba sin archivar para siempre: sin reintento, sin gastar
    intentos y sin que nadie se enterara."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def _intentos(self, message_id):
        fila = self.s.cx.execute("SELECT intentos FROM correos WHERE"
                                 " message_id = ?", (message_id,)).fetchone()
        return fila["intentos"] if fila else None

    def _ciclos(self, c, fake, vueltas):
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "abrir_buzon",
                               return_value=fake), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(self.s, "enviar") as enviar:
            for _ in range(vueltas):
                self.s.revisar_casilla()
        return enviar

    def test_un_copy_rechazado_persistente_agota_el_tope_y_avisa_una_vez(self):
        c = correo_falso("<24@x>", "Promo")
        fake = buzon_con(c, copy_ok=False)
        enviar = self._ciclos(c, fake, 20)

        # Reintentó, pero nunca de más: un COPY por vuelta hasta el tope.
        intentos_de_copia = [x for x in fake.comandos if x[0] == "COPY"]
        self.assertEqual(len(intentos_de_copia), secretaria.INTENTOS_MAXIMOS)
        # Y ninguno se copió, así que tampoco hay nada que borrar.
        self.assertNotIn("STORE", [x[0] for x in fake.comandos])
        self.assertNotIn("EXPUNGE", [x[0] for x in fake.comandos])
        self.assertEqual(memoria.situacion(self.s.cx, "<24@x>"),
                         "no_se_pudo_archivar")
        self.assertEqual(enviar.call_count, 1)

    def test_el_aviso_dice_que_fue_el_servidor_y_por_que(self):
        c = correo_falso("<25@x>", "Factura de agosto",
                         de="cobranzas@proveedor.com")
        enviar = self._ciclos(c, buzon_con(c, copy_ok=False),
                              secretaria.INTENTOS_MAXIMOS + 2)
        texto = enviar.call_args[0][0]
        self.assertIn("Factura de agosto", texto)
        self.assertIn("cobranzas@proveedor.com", texto)
        self.assertIn("CopiaRechazada", texto)
        self.assertIn("TRYCREATE", texto)

    def test_un_correo_que_ya_no_esta_no_hace_ruido_ni_gasta_intentos(self):
        """El caso benigno tiene que seguir siendo silencioso: no es una
        falla, así que ni reintenta, ni cuenta intentos, ni le escribe a
        JP. Si esto se volviera ruidoso, el aviso que sí importa se
        perdería entre los que no."""
        c = correo_falso("<26@x>", "Promo")
        fake = buzon_con(c, uid_buscado=None)
        enviar = self._ciclos(c, fake, 10)

        # Sólo pregunta -FETCH del UID guardado, búsqueda en INBOX y
        # búsqueda en el destino-, ninguna escritura.
        self.assertEqual([x[0] for x in fake.comandos],
                         ["FETCH", "SEARCH", "SEARCH"])
        self.assertEqual(memoria.situacion(self.s.cx, "<26@x>"), "clasificado")
        self.assertEqual(self._intentos("<26@x>"), 0)
        enviar.assert_not_called()

    def test_si_el_copy_anda_a_la_segunda_no_molesta_a_jp(self):
        """Una falla corta del servidor se resuelve sola dentro del tope,
        como cualquier otra."""
        c = correo_falso("<27@x>", "Promo")
        fake = buzon_con(c, copy_ok=False)
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "abrir_buzon",
                               return_value=fake), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(self.s, "enviar") as enviar:
            self.s.revisar_casilla()
            self.assertEqual(memoria.situacion(self.s.cx, "<27@x>"),
                             "pendiente_de_archivar")
            fake.copy_ok = True
            self.s.revisar_casilla()
        self.assertEqual(memoria.situacion(self.s.cx, "<27@x>"), "archivado")
        enviar.assert_not_called()



class PendientesDeLaBase(unittest.TestCase):
    """Hallazgo 2 de la ronda 6: el reintento funcionaba de casualidad.

    Nadie llamaba a memoria.pendientes(). Un correo en
    pendiente_de_archivar se reintentaba sólo porque volvía a aparecer en
    la ventana de un día de traer_nuevos(); si el proceso se reiniciaba
    cruzando ese borde -y launchd lo reinicia por diseño- el correo
    quedaba abandonado para siempre: no llegaba al tope, no generaba
    aviso, no lo miraba nadie. Medido: 10 vueltas del ciclo, cero
    intentos de escritura. Ahora lo que falta hacer sale del estado, que
    es la fuente de verdad; la ventana de búsqueda es una casualidad."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def _dejar_pendiente(self, mid, situacion):
        c = correo_falso(mid, "Promo")
        memoria.anotar(self.s.cx, c, "RUIDO", "promo")
        memoria.cambiar(self.s.cx, mid, situacion)
        return c

    def _ciclos(self, vueltas, **parches):
        # traer_nuevos vacío: el correo YA no aparece entre los entrantes.
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[]), \
             mock.patch.object(secretaria.clasificador, "clasificar") as cl, \
             mock.patch.object(self.s, "enviar") as enviar:
            for nombre, valor in parches.items():
                self.addCleanup(mock.patch.object(
                    secretaria.correo, nombre, valor).stop)
            with mock.patch.multiple(secretaria.correo, **parches):
                for _ in range(vueltas):
                    self.s.revisar_casilla()
        return cl, enviar

    def test_un_pendiente_de_archivar_fuera_de_la_ventana_se_reintenta(self):
        self._dejar_pendiente("<p1@x>", "pendiente_de_archivar")
        mover = mock.Mock(return_value=True)
        cl, enviar = self._ciclos(1, mover_a=mover)
        mover.assert_called_once()
        self.assertEqual(memoria.situacion(self.s.cx, "<p1@x>"), "archivado")
        # y sin volver a consultar al modelo: ya se sabe que es RUIDO
        cl.assert_not_called()

    def test_un_pendiente_de_borrar_fuera_de_la_ventana_se_reintenta(self):
        """Y se reintenta SÓLO el borrado: la copia ya está hecha, un COPY
        nuevo dejaría un duplicado."""
        self._dejar_pendiente("<p2@x>", "pendiente_de_borrar")
        borrar = mock.Mock(return_value=True)
        mover = mock.Mock()
        self._ciclos(1, borrar_el_original=borrar, mover_a=mover)
        borrar.assert_called_once()
        mover.assert_not_called()
        self.assertEqual(memoria.situacion(self.s.cx, "<p2@x>"), "archivado")

    def test_el_tope_y_el_aviso_valen_igual_fuera_de_la_ventana(self):
        """Lo que antes quedaba abandonado en silencio ahora llega a un
        estado final: se agotan los intentos y JP se entera."""
        self._dejar_pendiente("<p3@x>", "pendiente_de_archivar")
        mover = mock.Mock(side_effect=RuntimeError("red caída"))
        cl, enviar = self._ciclos(20, mover_a=mover)
        self.assertEqual(mover.call_count, secretaria.INTENTOS_MAXIMOS)
        self.assertEqual(memoria.situacion(self.s.cx, "<p3@x>"),
                         "no_se_pudo_archivar")
        self.assertEqual(enviar.call_count, 1)

    def test_no_se_reintenta_dos_veces_en_la_misma_vuelta(self):
        """El correo está en la base COMO pendiente y además sigue
        cayendo en la ventana de traer_nuevos. Es un intento por vuelta,
        no dos: si no, el tope se quemaría al doble de velocidad y el
        aviso llegaría antes de tiempo."""
        c = self._dejar_pendiente("<p4@x>", "pendiente_de_archivar")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.correo, "mover_a",
                               side_effect=RuntimeError("red caída")) as mover, \
             mock.patch.object(self.s, "enviar"):
            self.s.revisar_casilla()
        mover.assert_called_once()

    def test_un_pendiente_sobrevive_a_un_reinicio_del_proceso(self):
        """launchd levanta el proceso de nuevo y la Secretaria es otra:
        lo que falta hacer tiene que salir de la base, no de la memoria
        del proceso que se murió."""
        self._dejar_pendiente("<p5@x>", "pendiente_de_archivar")
        otra = secretaria.Secretaria(cx=memoria.abrir(self.f.name))
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[]), \
             mock.patch.object(secretaria.correo, "mover_a",
                               return_value=True) as mover, \
             mock.patch.object(otra, "enviar"):
            otra.revisar_casilla()
        mover.assert_called_once()
        self.assertEqual(memoria.situacion(otra.cx, "<p5@x>"), "archivado")


class ElAvisoNoSePierde(unittest.TestCase):
    """Hallazgo 3: el correo pasaba a estado terminal ANTES de que el
    aviso saliera, y enviar() se traga cualquier excepción devolviendo
    None sin que nadie mire el retorno. Con Telegram caído en ese momento
    el aviso no se reintentaba nunca y JP no se enteraba: medido, 30
    vueltas del ciclo y un solo intento de envío."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))
        self.c = correo_falso("<t1@x>", "Promo")

    def _ciclos(self, vueltas, enviar):
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[self.c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(secretaria.correo, "mover_a",
                               side_effect=RuntimeError("red caída")) as mover, \
             mock.patch.object(self.s, "enviar", enviar):
            for _ in range(vueltas):
                self.s.revisar_casilla()
        return mover

    def test_con_telegram_caido_el_aviso_se_reintenta(self):
        caido = mock.Mock(return_value=None)      # enviar() no pudo
        self._ciclos(30, caido)
        self.assertGreater(caido.call_count, 1)
        # y el correo NO quedó en un estado que nadie vuelve a mirar
        self.assertEqual(memoria.situacion(self.s.cx, "<t1@x>"),
                         "pendiente_de_archivar")

    def test_con_el_tope_agotado_no_se_toca_mas_la_casilla(self):
        """Reintentar el aviso no es reintentar la escritura: el aviso no
        duplica nada, un COPY sí."""
        mover = self._ciclos(30, mock.Mock(return_value=None))
        self.assertEqual(mover.call_count, secretaria.INTENTOS_MAXIMOS)

    def test_cuando_telegram_vuelve_sale_el_aviso_y_recien_ahi_cierra(self):
        self._ciclos(10, mock.Mock(return_value=None))
        self.assertEqual(memoria.situacion(self.s.cx, "<t1@x>"),
                         "pendiente_de_archivar")
        anda = mock.Mock(return_value={"ok": True})
        self._ciclos(3, anda)
        self.assertEqual(anda.call_count, 1)
        self.assertEqual(memoria.situacion(self.s.cx, "<t1@x>"),
                         "no_se_pudo_archivar")

    def test_el_aviso_atrasado_sigue_diciendo_que_fue_lo_que_fallo(self):
        """Aunque el proceso se haya reiniciado en el medio: por eso la
        falla se guarda en la base y no en una variable."""
        self._ciclos(10, mock.Mock(return_value=None))
        otra = secretaria.Secretaria(cx=memoria.abrir(self.f.name))
        anda = mock.Mock(return_value={"ok": True})
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[]), \
             mock.patch.object(otra, "enviar", anda):
            otra.revisar_casilla()
        texto = anda.call_args[0][0]
        self.assertIn("RuntimeError", texto)
        self.assertIn("red caída", texto)
        self.assertIn("Promo", texto)
        self.assertEqual(memoria.situacion(otra.cx, "<t1@x>"),
                         "no_se_pudo_archivar")

    def test_un_ok_false_de_telegram_no_cuenta_como_enviado(self):
        """Telegram puede contestar 200 con {"ok": false} -un chat_id que
        no existe, el bot bloqueado-. Eso no es un aviso entregado, y
        darlo por bueno sería perderlo igual que antes."""
        with mock.patch.object(secretaria.bot, "tg",
                               return_value={"ok": False,
                                             "description": "chat not found"}), \
             mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "1"}):
            self._ciclos(10, secretaria.Secretaria.enviar.__get__(self.s))
        self.assertEqual(memoria.situacion(self.s.cx, "<t1@x>"),
                         "pendiente_de_archivar")


class ElAvisoDiceDondeQuedo(unittest.TestCase):
    """Hallazgo 5: el aviso decía siempre "quedó en tu bandeja, sin
    archivar", que en el caso a medias es falso -está en la bandeja Y en
    Ruido-. Mandar a JP a archivarlo de nuevo es dejarlo con dos copias."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def _texto(self, falla, situacion):
        c = correo_falso("<d1@x>", "Promo")
        memoria.anotar(self.s.cx, c, "RUIDO", "promo")
        memoria.anotar_falla(self.s.cx, "<d1@x>", falla)
        memoria.cambiar(self.s.cx, "<d1@x>", situacion)
        for _ in range(secretaria.INTENTOS_MAXIMOS):
            memoria.sumar_intento(self.s.cx, "<d1@x>")
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[]), \
             mock.patch.object(self.s, "enviar") as enviar:
            self.s.revisar_casilla()
        return enviar.call_args[0][0]

    def test_si_quedo_duplicado_el_aviso_lo_dice(self):
        texto = self._texto("OperacionAMedias: no se pudo borrar",
                            "pendiente_de_borrar")
        self.assertIn("duplicado", texto)
        self.assertIn("INBOX.Ruido", texto)

    def test_si_no_se_copio_nada_dice_que_sigue_en_la_bandeja(self):
        texto = self._texto("CopiaRechazada: cuota agotada",
                            "pendiente_de_archivar")
        self.assertIn("Quedó en tu bandeja, sin archivar", texto)
        self.assertNotIn("duplicado", texto)


class ContraElServidorConEstado(unittest.TestCase):
    """El ciclo entero contra un buzón de verdad -carpetas, UIDs,
    búsqueda-, que es lo que hacía falta para ver los hallazgos 1 y 4:
    con un doble que contesta que sí a cualquier búsqueda, los dos pasan
    desapercibidos."""

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.f.name))

    def _ciclos(self, buzon, entrantes, vueltas):
        with mock.patch.object(secretaria, "EN_SECO", False), \
             mock.patch.object(secretaria.correo, "abrir_buzon",
                               return_value=buzon), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=entrantes), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promo",
                                             "unanime": True}), \
             mock.patch.object(self.s, "enviar") as enviar:
            for _ in range(vueltas):
                self.s.revisar_casilla()
        return enviar

    def test_un_correo_sin_message_id_se_archiva(self):
        """Hallazgo 1, de punta a punta: antes, 10 vueltas del ciclo lo
        dejaban en la bandeja con situación "clasificado", cero
        reintentos y cero avisos."""
        buzon = BuzonFalso()
        m = buzon.agregar("INBOX", de="no-reply@facturas.com",
                          asunto="Tu resumen", fecha=hace(0), message_id="")
        c = correo_de(m)
        enviar = self._ciclos(buzon, [c], 10)
        self.assertEqual(buzon.cuenta("INBOX"), 0)
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)
        self.assertEqual(memoria.situacion(self.s.cx, correo.identidad(c)),
                         "archivado")
        enviar.assert_not_called()

    def test_un_corte_despues_del_copy_no_deja_dos_copias(self):
        """Hallazgo 4, de punta a punta: antes, una sola caída de red en
        ese punto dejaba dos copias en Ruido, situación "archivado" y
        cero avisos -- completamente silencioso."""
        buzon = BuzonFalso()
        buzon.plan["COPY"] = ["RED_DESPUES"]
        m = buzon.agregar("INBOX", asunto="Promo", fecha=hace(0),
                          message_id="<corte@x>")
        c = correo_de(m)
        enviar = self._ciclos(buzon, [c], 5)
        self.assertEqual(len(buzon.de_comando("COPY")), 1)
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)
        self.assertEqual(buzon.cuenta("INBOX"), 0)
        self.assertEqual(memoria.situacion(self.s.cx, "<corte@x>"),
                         "archivado")
        enviar.assert_not_called()

    def test_si_cambio_el_uidvalidity_no_archiva_a_ciegas_y_avisa(self):
        """Los UID guardados dejaron de valer. Lo que no puede pasar es
        que se toque el mensaje equivocado; lo segundo que no puede pasar
        es que nadie se entere."""
        buzon = BuzonFalso()
        m = buzon.agregar("INBOX", message_id="<uv@x>", fecha=hace(0))
        c = correo_de(m)
        buzon.uidvalidity["INBOX"] = "9999"
        enviar = self._ciclos(buzon, [c], 20)
        self.assertEqual(buzon.cuenta("INBOX"), 1)
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 0)
        self.assertEqual(memoria.situacion(self.s.cx, "<uv@x>"),
                         "no_se_pudo_archivar")
        self.assertEqual(enviar.call_count, 1)
        self.assertIn("UidsVencidos", enviar.call_args[0][0])



if __name__ == "__main__":
    unittest.main()
