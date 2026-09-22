#!/usr/bin/env python3
"""Los tests de las COSTURAS: donde una tarea se toca con otra.

Los otros archivos prueban por función y por tarea, y así se cerraron
once fallas. Las tres que quedaron vivas hasta el despacho final -y que
pasaron las doce revisiones por tarea- viven todas en el mismo lugar: el
punto donde dos tareas se tocan, con cada revisión mirando un solo lado.

De ahí las dos clases que abren este archivo, que son lo que faltaba:

  * `UnDiaEntero` corre 8:30 → 17:00 → 18:00 EN SECUENCIA sobre la misma
    base. Ningún test hacía eso, y es exactamente donde vivía el CRÍTICO
    1: el resumen de las 8:30 se comía el estado que el de las 18:00
    necesitaba, cosa que sólo se ve si los dos corren, en orden, contra
    los mismos datos.

  * `AtravesDeUnReinicio` arranca una Secretaria, la tira, crea otra
    sobre la misma base y verifica que retoma. Ningún test cruzaba un
    reinicio completo, y ahí vivía el CRÍTICO 3: `__init__` ponía el
    reloj en "ahora" y las 25 líneas de Reloj.momentos_pendientes, que
    existen sólo para recuperar una caída larga, no corrían nunca.

Lo demás de este archivo son las redes que faltaban debajo de mutaciones
que sobrevivían la suite entera: el token de tanda, y `readonly` en
abrir_buzon (esa va en test_correo.py, al lado de la de BODY.PEEK).
"""
import contextlib
import os
import secrets
import socket
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from datetime import datetime, timedelta

import bot
import clasificador
import correo
import memoria
import secretaria
from tests import sin_red
from tests.buzon_falso import BuzonFalso, correo_de


class _Escenario(unittest.TestCase):
    """Base común: una Secretaria de verdad, sobre una base de verdad,
    contra un buzón IMAP con estado, y con Telegram capturado en una
    lista en vez de mockeado por método.

    Todo lo que se prueba acá pasa por el código real de secretaria.py y
    de correo.py: lo único fingido es la red -el servidor IMAP y
    Telegram- y el modelo. Si algo se mockeara más arriba, la costura
    entre dos tareas quedaría justo del lado mockeado, que es cómo se
    escaparon las tres.
    """

    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.ruta = self.f.name
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.ruta))
        self.buzon = BuzonFalso()
        self.mensajes = []          # (texto, teclado) de cada envío
        self.telegram_anda = True
        self.categorias = {}        # message_id -> lo que contesta el modelo

    # ---- dobles ---------------------------------------------------
    def _enviar(self, texto, teclado=None):
        if not self.telegram_anda:
            return None
        self.mensajes.append((texto, teclado))
        return {"ok": True}

    def _clasificar(self, sistema, c):
        return {"categoria": self.categorias[c["message_id"]],
                "motivo": "porque sí", "unanime": True, "pasadas": 2}

    def _entra(self, mid, asunto, categoria, de="promo@ejemplo.com"):
        """Deja un correo en INBOX del buzón falso y devuelve el dict que
        traer_nuevos() dejaría para él."""
        m = self.buzon.agregar("INBOX", de=de, asunto=asunto, message_id=mid)
        self.categorias[mid] = categoria
        return correo_de(m, self.buzon.uidvalidity["INBOX"])

    @contextlib.contextmanager
    def _contexto(self, entrantes=(), en_horario=False):
        """El mundo de afuera fingido: el buzón, el modelo, el reloj de
        pared y Telegram. Nada de secretaria.py se mockea acá adentro:
        si se mockeara, la costura entre dos tareas quedaría justo del
        lado fingido, que es cómo se escaparon las tres."""
        with contextlib.ExitStack() as pila:
            for p in (
                    mock.patch.object(secretaria, "EN_SECO", False),
                    # Se pincha imaplib.IMAP4_SSL y NO correo.abrir_buzon,
                    # que es lo que hace el resto de la suite. Dos
                    # razones. Una: así corre el abrir_buzon de verdad,
                    # con su readonly, en vez de saltearlo. Dos, y es la
                    # que importa: devolver_a_bandeja() no pasa por
                    # abrir_buzon -arma su propia conexión-, así que un
                    # test que parchea abrir_buzon la deja llegar al
                    # servidor DE VERDAD sin decir nada. IMAP4_SSL es el
                    # único cuello por el que no puede pasar nadie.
                    mock.patch.object(correo.imaplib, "IMAP4_SSL",
                                      return_value=self.buzon),
                    mock.patch.object(secretaria.correo, "traer_nuevos",
                                      return_value=list(entrantes)),
                    mock.patch.object(secretaria.clasificador, "clasificar",
                                      side_effect=self._clasificar),
                    mock.patch.object(secretaria, "en_horario",
                                      return_value=en_horario),
                    # reglas.py y el prompt se fingen para que el día sea
                    # DETERMINISTA y no relea reglas.md, roster.md y todo
                    # datos/*.json en cada una de las ~225 vueltas. No es
                    # lo que se prueba acá -reglas tiene sus tests- y
                    # dejarlo real mete dos fuentes de azar en el medio:
                    # el muestreo de control del atajo de ruido conocido,
                    # y protegido(), que decide por remitente y haría que
                    # cambiar un nombre de fantasía en el escenario
                    # cambie lo que hace el sistema.
                    mock.patch.object(secretaria.reglas, "protegido",
                                      return_value=False),
                    mock.patch.object(secretaria.reglas, "es_ruido_conocido",
                                      return_value=None),
                    mock.patch.object(secretaria.reglas, "tiene_codigo",
                                      return_value=None),
                    mock.patch.object(secretaria.clasificador,
                                      "prompt_sistema", return_value="sis"),
                    mock.patch.object(self.s, "enviar",
                                      side_effect=self._enviar)):
                pila.enter_context(p)
            yield pila

    def _vuelta(self, ahora, entrantes=(), en_horario=False, trabajo=0):
        """Una vuelta del ciclo de correo, con el mismo cuerpo que
        ciclo_de_correo: revisar, disparar resúmenes, latir. Que el
        latido entre acá no es decorativo -es lo que el reinicio lee
        después para saber desde cuándo estuvo caída.

        `ahora` es cuándo TERMINA la vuelta y `trabajo` cuánto tardó
        revisar_casilla(), así que la vuelta empezó `trabajo` segundos
        antes. Modelarlo importa: ciclo_de_correo llama a
        _disparar_resumenes DESPUÉS de revisar la casilla, así que la
        distancia entre dos llamadas seguidas es wait(CADA) MÁS lo que
        tardó el trabajo. Un helper que avance en pasos exactos de CADA
        no modela nunca el tiempo de trabajo y hace pasar por
        construcción cualquier test sobre el hueco -que es lo que pasaba
        con test_un_dia_normal_no_dice_que_estuvo_caida-.

        Y no es un caso de laboratorio: ESPERA_RESPUESTA son 180s por
        pedido, dos pasadas por correo, más el backoff de los 429.
        """
        arranque = ahora - timedelta(seconds=trabajo)
        with self._contexto(entrantes, en_horario):
            self.s.revisar_casilla()
            self.s._disparar_resumenes(ahora, arranque)
        memoria.latido(self.s.cx, ahora.isoformat(timespec="seconds"))
        return ahora

    def _una_vuelta_mas(self, fin_anterior, entrantes=(), en_horario=False,
                        trabajo=0):
        """La vuelta siguiente a una que terminó en `fin_anterior`: espera
        CADA y después tarda `trabajo` en revisar la casilla. Devuelve
        cuándo terminó, para encadenar."""
        return self._vuelta(
            fin_anterior + timedelta(seconds=secretaria.CADA + trabajo),
            entrantes, en_horario, trabajo)

    def _hasta(self, desde, hasta, trabajo=0):
        """Avanza el reloj de `desde` a `hasta` vuelta a vuelta, que es
        como avanza el ciclo de verdad.

        A propósito no se salta de un momento al siguiente: un salto de
        horas es indistinguible de una caída, y con razón -es justo lo
        que detecta HUECO_CAIDA-. Un día normal no tiene que decir
        "estuve caída" en ningún resumen, y esta función es lo que hace
        que el test lo pueda afirmar."""
        paso = timedelta(seconds=secretaria.CADA + trabajo)
        ahora = desde
        while ahora < hasta:
            proxima = min(ahora + paso, hasta)
            self._vuelta(proxima, trabajo=min(
                trabajo, (proxima - ahora).total_seconds()))
            ahora = proxima
        return hasta

    def _arrancar_en(self, momento):
        """Como si el proceso hubiera arrancado en ese momento: el reloj
        interno y el latido en la base, que son las dos formas en que
        ese momento se recuerda. Sin esto el reloj arranca en el `ahora`
        de verdad -2026 de este año- y toda la jornada simulada le queda
        en el pasado: momentos_pendientes no devuelve nada y el día no
        cruza ningún corte."""
        memoria.latido(self.s.cx, momento.isoformat(timespec="seconds"))
        self.s.ultimo_reloj = momento

    # ---- ayudas de lectura ----------------------------------------
    def _textos(self):
        return [t for t, _ in self.mensajes]

    def _ultimo(self):
        return self.mensajes[-1]

    def _situaciones(self, *mids):
        return [memoria.situacion(self.s.cx, m) for m in mids]


class UnDiaEntero(_Escenario):
    """El día completo, en orden, sobre la misma base: 8:30 → 17:00 →
    18:00.

    Este es el hueco de prueba que dejó pasar el CRÍTICO 1. Cada resumen
    tenía sus tests y los pasaba todos; lo que nadie probó nunca fue
    correrlos en secuencia, que es la única forma de ver que el de las
    8:30 se comía el estado del que sale a las 18:00.
    """

    MANANA = datetime(2026, 8, 17, 7, 0)      # lunes, antes del primer corte
    MEDIODIA = datetime(2026, 8, 17, 12, 0)

    def _correr_el_dia(self, trabajo=0):
        """La noche deja cinco correos, el mediodía dos más, y el reloj
        cruza los tres cortes del día.

        `trabajo` es cuánto tarda revisar_casilla() en las dos vueltas
        que de verdad clasifican correo -las otras encuentran la casilla
        vacía y vuelven en el acto-. Con 0 el día es el de un helper que
        avanza en pasos exactos; con 13*60 es un lunes con quince
        correos entrando juntos, que es donde se ve si el hueco está
        bien medido."""
        de_la_noche = [
            self._entra("<r1@x>", "Promoción 11 años", "RUIDO",
                        de="M2M Dataglobal <promo@m2m.com>"),
            self._entra("<r2@x>", "Desayuno ESS+ 2026", "RUIDO",
                        de="Ruptela <eventos@ruptela.com>"),
            self._entra("<r3@x>", "4 empleos para vos", "RUIDO",
                        de="LinkedIn <jobs@linkedin.com>"),
            self._entra("<t1@x>", "Cierre de etapa", "TUYO",
                        de="Suhr Ingeniería <suhr@cliente.com>"),
            self._entra("<d1@x>", "RE: IVA JULIO", "DELEGADO",
                        de="administracion@fullcontrolgps.com.ar"),
        ]
        self._arrancar_en(self.MANANA)
        manana = self._una_vuelta_mas(self.MANANA, de_la_noche,
                                      trabajo=trabajo)
        # El reloj avanza tic a tic, como el de verdad: saltar de un
        # corte al siguiente sería indistinguible de una caída -y con
        # razón, es justo lo que detecta HUECO_CAIDA-.
        self._hasta(manana, self.MEDIODIA)

        # El mediodía trae dos más: un ruido y algo para derivar.
        del_mediodia = [
            self._entra("<r4@x>", "Manual de contracargos", "RUIDO",
                        de="Zentra Sales <ventas@zentra.com>"),
            self._entra("<n1@x>", "Factura agosto", "NATALIA",
                        de="Orbcomm <facturacion@orbcomm.com>"),
        ]
        mediodia = self._una_vuelta_mas(self.MEDIODIA, del_mediodia,
                                        trabajo=trabajo)
        return self._hasta(mediodia, datetime(2026, 8, 17, 18, 15))

    def test_el_resumen_de_las_18_lista_todo_lo_que_se_archivo_en_el_dia(self):
        """La falla medida: día con ruidos archivados y, a las 18:00,
        "No archivé nada como ruido" con el teclado vacío. Todo lo
        archivado entre las 18:00 de ayer y las 17:00 de hoy no aparecía
        nunca en una lista numerada y no se podía Rever."""
        self._correr_el_dia()

        texto, teclado = self._ultimo()
        self.assertIn("Son 4.", texto)
        for asunto in ("Promoción 11 años", "Desayuno ESS+ 2026",
                       "4 empleos para vos", "Manual de contracargos"):
            self.assertIn(asunto, texto,
                          f"«{asunto}» se archivó hoy y no está en la lista"
                          f" de las 18:00: dejó de ser reversible")
        self.assertNotIn("No archivé nada", texto)

        # Y con los dos botones que hacen que la lista sirva de algo.
        etiquetas = [b["text"] for f in teclado["inline_keyboard"] for b in f]
        self.assertIn("✅ Confirmar", etiquetas)
        self.assertIn("🔁 Rever", etiquetas)

    def test_todo_lo_archivado_sigue_siendo_reversible_a_las_18(self):
        """La regla que no se puede romper: un correo archivado deja de
        ser reversible sólo cuando JP lo confirma. Nunca por un efecto
        secundario de otro resumen."""
        self._correr_el_dia()

        _, teclado = self._ultimo()
        datos = teclado["inline_keyboard"][0][1]["callback_data"]  # Rever
        tanda = datos.split("|")[1].rsplit("-", 1)[0]
        self.assertEqual(len(self.s.abiertos[tanda]), 4,
                         "los cuatro archivados tienen que estar en la"
                         " tanda, si no el botón Rever no los alcanza")
        self.assertEqual(
            self.s.abiertos[tanda],
            ["<r1@x>", "<r2@x>", "<r3@x>", "<r4@x>"],
            "el orden de la tanda es el de la lista numerada: si no"
            " coinciden, el número que dice JP selecciona otro correo")

    def test_los_resumenes_de_la_manana_y_la_tarde_cuentan_el_ruido_pero_no_lo_consumen(self):
        """El reparto de dueños: "archivado" es del resumen de las
        18:00. Los otros dos lo cuentan -es información: "🗑 Archivado
        como ruido: N"- y no lo tocan."""
        self._correr_el_dia()

        textos = self._textos()
        manana = next(t for t in textos if t.startswith("Buen día."))
        tarde = next(t for t in textos if t.startswith("Cierre del día."))
        self.assertIn("Archivado como ruido: 3", manana)
        self.assertIn("Archivado como ruido: 4", tarde)
        # Contarlos no es listarlos: la lista numerada es una sola.
        self.assertNotIn("Promoción 11 años", manana)
        self.assertNotIn("Promoción 11 años", tarde)

    def test_lo_del_equipo_y_lo_de_jp_si_se_consumen_y_no_se_repiten(self):
        """La otra mitad del reparto: lo que el resumen general SÍ
        consume no vuelve a aparecer en el siguiente. Si esto se
        rompiera al arreglar lo del ruido, el arreglo sería peor que la
        falla."""
        self._correr_el_dia()

        textos = self._textos()
        manana = next(t for t in textos if t.startswith("Buen día."))
        tarde = next(t for t in textos if t.startswith("Cierre del día."))
        self.assertIn("Cierre de etapa", manana)
        self.assertIn("RE: IVA JULIO", manana)
        self.assertNotIn("Cierre de etapa", tarde)
        self.assertNotIn("RE: IVA JULIO", tarde)
        # Lo del mediodía sí, que es nuevo desde el resumen anterior.
        self.assertIn("Factura agosto", tarde)

    def test_al_final_del_dia_cada_correo_esta_donde_corresponde(self):
        self._correr_el_dia()

        # Lo del equipo y lo de JP: contados en un resumen.
        self.assertEqual(self._situaciones("<t1@x>", "<d1@x>", "<n1@x>"),
                         ["en_resumen"] * 3)
        # El ruido: listado a las 18:00, esperando que JP confirme o
        # revea. Y en la casilla, en INBOX.Ruido y fuera de INBOX.
        self.assertEqual(
            self._situaciones("<r1@x>", "<r2@x>", "<r3@x>", "<r4@x>"),
            ["en_resumen"] * 4)
        self.assertEqual(self.buzon.cuenta("INBOX.Ruido"), 4)
        self.assertEqual(self.buzon.cuenta("INBOX"), 3)

    def test_un_dia_normal_no_dice_que_estuvo_caida(self):
        """El aviso de caída tiene que ser información, no ruido de
        fondo: si saliera todos los días, JP dejaría de leerlo justo
        antes del día en que importa.

        El día se corre con vueltas LENTAS -13 minutos de clasificación,
        que con quince correos entrando juntos es perfectamente
        alcanzable: ESPERA_RESPUESTA son 180s por pedido, dos pasadas
        por correo, más el backoff de los 429-. Esto es lo que faltaba:
        antes el helper avanzaba en pasos exactos de CADA y no modelaba
        nunca el tiempo de trabajo, así que este test pasaba por
        construcción. El hueco se medía como `ahora - ultimo_reloj`, y
        ahí `ahora` es DESPUÉS de revisar la casilla: con HUECO_CAIDA de
        900s alcanzaba con que una vuelta tardara más de 12 minutos para
        que el proceso -vivo y trabajando- se avisara a sí mismo como
        caído. Medido: "⚠️ Estuve caída desde el 17/08 a las 08:20 -16
        minutos-" en el resumen de un lunes normal.
        """
        self._correr_el_dia(trabajo=13 * 60)
        for texto in self._textos():
            self.assertNotIn("Estuve caída", texto)

    def test_una_vuelta_lenta_no_apaga_la_deteccion_de_una_caida_de_verdad(self):
        """La contracara, para que el arreglo no sea "no avisar nunca":
        un hueco de verdad -la Mac dormida, el proceso muerto- se sigue
        viendo aunque la vuelta que lo descubre sea lenta."""
        self._arrancar_en(self.MANANA)
        # La Mac durmió tres horas y despertó a las 10: la vuelta que la
        # descubre tarda 13 minutos porque encuentra toda la noche junta.
        self._vuelta(datetime(2026, 8, 17, 10, 13),
                     [self._entra("<t1@x>", "Cierre de etapa", "TUYO",
                                  de="Suhr <suhr@cliente.com>")],
                     trabajo=13 * 60)
        self._hasta(datetime(2026, 8, 17, 10, 13),
                    datetime(2026, 8, 17, 17, 5))

        self.assertTrue(any("Estuve caída" in t for t in self._textos()),
                        "un hueco de tres horas dejó de verse")

    def test_rever_a_las_18_devuelve_el_correo_y_lo_saca_de_ruido(self):
        """El circuito completo, atravesando los tres momentos: JP mira
        la lista de las 18:00, dice que el 4 no era ruido, y el correo
        vuelve a la bandeja SIN LEER y reclasificado. Es lo que el
        resumen de las 18:00 existe para permitir, y lo que no se podía
        hacer mientras la lista llegaba vacía."""
        self._correr_el_dia()
        _, teclado = self._ultimo()
        datos = teclado["inline_keyboard"][0][1]["callback_data"]

        with mock.patch.object(bot, "tg_suave"), \
             mock.patch.object(self.s, "enviar", side_effect=self._enviar):
            self.s.atender_boton({"id": "1", "data": datos})
            self.s.responder_pendiente("4")
            self.s.responder_pendiente("SiPago es mi proveedor de cobros")

        self.assertEqual(memoria.situacion(self.s.cx, "<r4@x>"),
                         "pendiente_de_rever")

        self.categorias["<r4@x>"] = "NATALIA"
        with mock.patch.object(secretaria.clasificador, "clasificar",
                               side_effect=lambda s_, c: {
                                   "categoria": "NATALIA", "motivo": "cobros",
                                   "unanime": True}):
            self._vuelta(datetime(2026, 8, 17, 18, 20))

        self.assertEqual(memoria.situacion(self.s.cx, "<r4@x>"), "clasificado")
        self.assertEqual(self.buzon.cuenta("INBOX.Ruido"), 3)
        volvio = [m for m in self.buzon.carpetas["INBOX"]
                  if m.message_id == "<r4@x>"]
        self.assertEqual(len(volvio), 1, "no volvió a la bandeja")
        self.assertNotIn("\\Seen", volvio[0].flags,
                         "volvió marcado como leído: JP no lo vería")


class AtravesDeUnReinicio(_Escenario):
    """Una Secretaria arranca, se muere, y otra la reemplaza sobre la
    misma base.

    Este es el otro hueco de prueba. Reiniciar no es la excepción: el
    plist de launchd tiene KeepAlive y arrancar() sale con código 1 a
    propósito cuando un hilo se muere. Todo lo que "vive en el objeto"
    tiene que poder reconstruirse de la base, y hasta el despacho final
    el reloj de recuperación no podía: __init__ lo ponía en `ahora` y
    borraba la caída que acababa de pasar.
    """

    MURIO = datetime(2026, 8, 17, 7, 0)       # lunes 07:00, último latido
    VOLVIO = datetime(2026, 8, 17, 19, 0)     # lunes 19:00, launchd la levanta

    def setUp(self):
        super().setUp()
        self._arrancar_en(self.MURIO)

    def _morir_y_volver(self):
        """Lo que hace launchd: el proceso se termina y otro arranca
        sobre la misma base. La Secretaria nueva no comparte NADA en
        memoria con la vieja."""
        self.s.parada.set()
        vieja = self.s
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.ruta))
        return vieja

    def test_el_reloj_se_retoma_del_ultimo_latido_y_no_de_ahora(self):
        self._vuelta(self.MURIO, [self._entra("<a@x>", "Algo", "DELEGADO")])
        self._morir_y_volver()
        self.assertEqual(self.s.ultimo_reloj, self.MURIO)

    def test_los_resumenes_de_la_caida_salen_al_volver(self):
        """Las 25 líneas de Reloj.momentos_pendientes existen sólo para
        esto y a través de un reinicio no corrían nunca. Cayó a las
        07:00 y volvió a las 19:00: se perdió el día entero."""
        self._vuelta(self.MURIO, [
            self._entra("<t1@x>", "Cierre de etapa", "TUYO",
                        de="Suhr <suhr@cliente.com>"),
            self._entra("<r1@x>", "Promo", "RUIDO"),
        ])
        self._morir_y_volver()
        self.mensajes.clear()

        self._vuelta(self.VOLVIO)

        textos = self._textos()
        self.assertTrue(any("Cierre del día." in t for t in textos),
                        f"no salió el resumen general: {textos}")
        self.assertTrue(any("Lo que archivé hoy." in t for t in textos),
                        f"no salió el resumen de ruido: {textos}")

    def test_el_resumen_dice_que_estuvo_caida_y_desde_cuando(self):
        """Diseño §8: "si la secretaria estuvo caída, el resumen lo dice
        y desde cuándo". Es el antídoto que el propio diseño propone
        contra la falla que más preocupa -la silenciosa- y el latido ya
        tenía el dato desde la tarea 6."""
        self._vuelta(self.MURIO, [self._entra("<a@x>", "Algo", "DELEGADO")])
        self._morir_y_volver()
        self.mensajes.clear()

        self._vuelta(self.VOLVIO)

        primero = self._textos()[0]
        self.assertIn("Estuve caída", primero)
        self.assertIn("17/08 a las 07:00", primero)
        self.assertIn("12.0 horas", primero)

    def test_lo_dice_una_sola_vez_y_no_en_cada_resumen(self):
        """Sale con el primer resumen que se entrega de verdad y ahí se
        apaga: repetirlo en el de ruido y en todos los del día
        siguiente lo volvería ruido de fondo."""
        self._vuelta(self.MURIO, [self._entra("<r1@x>", "Promo", "RUIDO")])
        self._morir_y_volver()
        self.mensajes.clear()

        self._vuelta(self.VOLVIO)
        self._hasta(self.VOLVIO, datetime(2026, 8, 18, 9, 0))

        cuantos = sum(1 for t in self._textos() if "Estuve caída" in t)
        self.assertEqual(cuantos, 1, f"lo dijo {cuantos} veces")

    def test_si_el_resumen_no_sale_el_aviso_de_caida_no_se_pierde(self):
        """Mismo criterio que en todo el resto del archivo: nada pasa a
        terminal sin entrega confirmada. Si Telegram estaba caído
        justo cuando volvió, el aviso de la caída tiene que salir con
        el próximo resumen que sí llegue."""
        self._vuelta(self.MURIO, [self._entra("<a@x>", "Algo", "DELEGADO")])
        self._morir_y_volver()
        self.mensajes.clear()

        self.telegram_anda = False
        self._vuelta(self.VOLVIO)
        self.assertEqual(self._textos(), [])

        self.telegram_anda = True
        self._hasta(self.VOLVIO, datetime(2026, 8, 18, 9, 0))
        self.assertTrue(any("Estuve caída" in t for t in self._textos()))

    def test_lo_archivado_antes_del_reinicio_sigue_siendo_reversible(self):
        """El cruce de los dos críticos: si el ruido de antes de la
        caída no sobreviviera al reinicio, el resumen de recuperación
        saldría igual y estaría vacío -exactamente la misma pérdida, por
        otro camino."""
        self._vuelta(self.MURIO, [
            self._entra("<r1@x>", "Promo vieja", "RUIDO"),
            self._entra("<r2@x>", "Otra promo", "RUIDO"),
        ])
        self.assertEqual(self._situaciones("<r1@x>", "<r2@x>"),
                         ["archivado"] * 2)
        self._morir_y_volver()
        self.mensajes.clear()

        self._vuelta(self.VOLVIO)

        de_ruido = next(t for t in self._textos()
                        if "Lo que archivé hoy" in t)
        self.assertIn("Promo vieja", de_ruido)
        self.assertIn("Otra promo", de_ruido)
        self.assertEqual(
            sum(len(v) for v in self.s.abiertos.values()), 2,
            "quedaron listados pero sin tanda: el botón Rever no los"
            " alcanzaría")

    def test_un_aviso_en_cola_sale_despues_del_reinicio(self):
        """La cola de avisos del diseño §8 -"quedan en cola y salen
        cuando vuelve"- tiene que sobrevivir a un reinicio, porque el
        caso normal es que Telegram vuelva después de que launchd ya
        reinició el proceso. Por eso la cola es una situación en la
        base y no una lista en memoria."""
        self.telegram_anda = False
        self._vuelta(self.MURIO,
                     [self._entra("<t1@x>", "Cierre de etapa", "TUYO",
                                  de="Suhr <suhr@cliente.com>")],
                     en_horario=True)
        self.assertEqual(memoria.situacion(self.s.cx, "<t1@x>"),
                         "pendiente_de_avisar")

        self._morir_y_volver()
        self.telegram_anda = True
        self.mensajes.clear()

        self._vuelta(datetime(2026, 8, 17, 9, 0), en_horario=True)

        self.assertTrue(any("Cierre de etapa" in t for t in self._textos()),
                        "el aviso en cola no salió después del reinicio")
        self.assertEqual(memoria.situacion(self.s.cx, "<t1@x>"), "avisado")


class ElResumenGrandeTampocoSeComeElRuido(_Escenario):
    """El camino de MUCHO VOLUMEN, con ruido archivado en la base.

    Es un hueco de prueba de la misma clase que los tres Críticos, y se
    encontró midiendo: sacar el filtro `if c["message_id"] in
    consumibles` del cierre de `_mandar_resumen_grande` **sobrevivía las
    314 pruebas** y reproducía el CRÍTICO 1 entero por el otro camino
    -lo archivado se consume en el resumen general y a las 18:00 no
    queda nada que Rever-.

    `mandar_resumen` tiene dos caminos y el arreglo del CRÍTICO 1 tocó
    los dos, pero sólo uno quedó cubierto: `UnDiaEntero` es un día
    normal, que entra cómodo en un mensaje. Ningún test cruzaba mucho
    volumen CON ruido archivado, y ese cruce no es hipotético: el camino
    de mucho volumen es justo el que corre después de una caída larga
    -por eso `_mandar_resumen_grande` habla de "Volviste con N
    esperando"-, o sea al mismo tiempo que el CRÍTICO 3, y la caída
    larga es también cuando más ruido hay acumulado sin listar.
    """

    MURIO = datetime(2026, 8, 17, 7, 0)
    VOLVIO = datetime(2026, 8, 17, 19, 0)

    def setUp(self):
        super().setUp()
        self._arrancar_en(self.MURIO)

    def _el_atraso(self, cuantos):
        """El atraso que deja una caída larga: correos de JP ya
        clasificados que todavía no entraron en ningún resumen.

        Se anotan derecho en la base y no por el ciclo: lo que se prueba
        acá es el resumen, y hacer pasar ciento veinte correos por
        revisar_casilla no agrega ninguna costura -sí agrega ciento
        veinte vueltas-."""
        for n in range(cuantos):
            memoria.anotar(self.s.cx, {
                "message_id": f"<t{n}@x>", "uid": "1", "uidvalidity": "1",
                "de": f"Cliente Con Nombre Largo {n} <cliente{n}@ejemplo.com>",
                "para": "jp@x", "cc": "",
                "asunto": f"Asunto largo número {n} " + "x" * 40,
                "fecha": "Mon, 17 Aug 2026 09:00:00 -0300",
                "cuerpo": "cuerpo", "adjuntos": []}, "TUYO", "m")

    def _la_caida_larga(self):
        """Tres ruidos archivados de verdad a las 7, ciento veinte
        correos de JP acumulados, y launchd la levanta a las 19."""
        self._vuelta(self.MURIO, [
            self._entra("<r1@x>", "Promoción 11 años", "RUIDO",
                        de="M2M Dataglobal <promo@m2m.com>"),
            self._entra("<r2@x>", "Desayuno ESS+ 2026", "RUIDO",
                        de="Ruptela <eventos@ruptela.com>"),
            self._entra("<r3@x>", "4 empleos para vos", "RUIDO",
                        de="LinkedIn <jobs@linkedin.com>"),
        ])
        self.assertEqual(self._situaciones("<r1@x>", "<r2@x>", "<r3@x>"),
                         ["archivado"] * 3,
                         "el escenario no arrancó: no se archivó nada")
        self._el_atraso(120)

        self.s.parada.set()
        self.s = secretaria.Secretaria(cx=memoria.abrir(self.ruta))
        self.mensajes.clear()
        self._vuelta(self.VOLVIO)

    def _el_de_ruido(self):
        return next((m for m in self.mensajes
                     if "Lo que archivé hoy" in m[0]), None)

    def test_el_resumen_de_recuperacion_va_por_el_camino_de_mucho_volumen(self):
        """Sin esto, los otros dos tests de esta clase podrían pasar sin
        haber entrado nunca a _mandar_resumen_grande -y no dirían nada
        sobre el filtro que cuidan-."""
        self._la_caida_larga()
        partes = [t for t in self._textos() if "parte 1/" in t]
        self.assertTrue(partes,
                        f"el resumen entró en un solo mensaje: este"
                        f" escenario no prueba el camino grande."
                        f" Mensajes: {[t[:60] for t in self._textos()]}")

    def test_lo_archivado_llega_entero_a_la_lista_de_las_18(self):
        """La falla que la mutación reproduce: el resumen general de
        recuperación consume lo archivado en su cierre -donde el ruido
        se cuenta pero no se lista- y a las 18:00 no queda nada."""
        self._la_caida_larga()

        de_ruido = self._el_de_ruido()
        self.assertIsNotNone(de_ruido, "no salió el resumen de las 18:00")
        texto, teclado = de_ruido
        self.assertNotIn("No archivé nada", texto)
        for asunto in ("Promoción 11 años", "Desayuno ESS+ 2026",
                       "4 empleos para vos"):
            self.assertIn(asunto, texto,
                          f"«{asunto}» se archivó y no está en la lista de"
                          f" las 18:00: dejó de ser reversible")
        etiquetas = [b["text"] for f in teclado["inline_keyboard"] for b in f]
        self.assertIn("🔁 Rever", etiquetas)

    def test_el_cierre_del_resumen_grande_cuenta_el_ruido_pero_no_lo_consume(self):
        """El mismo reparto de dueños que en el camino normal: el cierre
        del resumen grande dice "Se archivaron N como ruido" -eso es
        información- y no toca la situación."""
        self._la_caida_larga()

        cierre = next(t for t in self._textos()
                      if "Eso es lo que entra acá" in t)
        self.assertIn("Se archivaron 3 como ruido", cierre)
        # Y después del de las 18:00 sí: ahí sí tienen dueño.
        self.assertEqual(self._situaciones("<r1@x>", "<r2@x>", "<r3@x>"),
                         ["en_resumen"] * 3)


class ElAvisoAlToqueNoSeDaPorEntregado(_Escenario):
    """CRÍTICO 2: enviar() devuelve None cuando falla y no levanta nada,
    y memoria.cambiar(..., "avisado") corría igual.

    Nadie lee "avisado": era la única aparición de ese literal en todo el
    código fuera de donde se escribe. Medido con Telegram caído sobre un
    correo TUYO: quedaba "avisado", el resumen de las 8:30 decía "No
    entró nada nuevo", y el correo de JP desaparecía del sistema entero.

    El contraste estaba 240 líneas más abajo: _avisar_la_falla sí miraba
    el retorno antes de pasar a terminal. La invariante que costó siete
    rondas establecer no se había aplicado al aviso que más importa.
    """

    HORA = datetime(2026, 8, 17, 11, 42)

    def setUp(self):
        super().setUp()
        self._arrancar_en(self.HORA - timedelta(seconds=secretaria.CADA))

    def _entra_algo_de_jp(self):
        return [self._entra("<t1@x>", "Envío lectores", "TUYO",
                            de="Miriam Arcuri <miriam@caesistemas.com.ar>")]

    def test_con_telegram_caido_no_queda_avisado(self):
        self.telegram_anda = False
        self._vuelta(self.HORA, self._entra_algo_de_jp(), en_horario=True)
        self.assertEqual(memoria.situacion(self.s.cx, "<t1@x>"),
                         "pendiente_de_avisar")

    def test_el_correo_de_jp_no_desaparece_del_sistema(self):
        """La consecuencia, que es lo que de verdad importa: si el aviso
        no salió, el correo tiene que llegarle a JP por algún lado."""
        self.telegram_anda = False
        self._vuelta(self.HORA, self._entra_algo_de_jp(), en_horario=True)

        self.telegram_anda = True
        self._vuelta(datetime(2026, 8, 17, 11, 45), en_horario=True)

        self.assertTrue(any("Envío lectores" in t for t in self._textos()))
        self.assertEqual(memoria.situacion(self.s.cx, "<t1@x>"), "avisado")

    def test_si_telegram_no_vuelve_igual_entra_en_el_resumen(self):
        """El segundo camino: la cola sale cuando Telegram vuelve, pero
        si vuelve recién de madrugada -fuera de horario, cuando
        avisar_en_el_momento no interrumpe- el correo tiene que
        aparecer igual en el resumen de las 8:30. Nunca "No entró nada
        nuevo" sobre un correo de JP que está en la base."""
        self.telegram_anda = False
        self._vuelta(self.HORA, self._entra_algo_de_jp(), en_horario=True)

        self.telegram_anda = True
        self._hasta(self.HORA, datetime(2026, 8, 17, 17, 5))  # fuera de horario

        texto = next(t for t in self._textos() if "Cierre del día." in t)
        self.assertIn("Envío lectores", texto)
        self.assertNotIn("No entró nada nuevo", texto)
        self.assertEqual(memoria.situacion(self.s.cx, "<t1@x>"), "en_resumen")

    def test_una_tanda_fantasma_no_queda_abierta(self):
        """Si el mensaje no salió, sus botones no existen en ningún
        lado: registrar la tanda sería un token abierto para siempre
        que nadie va a tocar."""
        self.telegram_anda = False
        self._vuelta(self.HORA, self._entra_algo_de_jp(), en_horario=True)
        self.assertEqual(self.s.abiertos, {})

    def test_lo_que_no_se_pudo_clasificar_tampoco_se_da_por_avisado(self):
        """Peor todavía que el de JP: un correo que ningún motor pudo
        clasificar queda en "mostrado_sin_clasificar", y a esa situación
        no la mira ningún resumen. Si el mensaje no salía, el correo que
        NECESITA que decida JP no volvía a aparecer nunca."""
        self.telegram_anda = False
        c = self._entra("<duda@x>", "Orbcomm — factura y contrato", "DUDA")
        with self._contexto([c], en_horario=True):
            self.s.revisar_casilla()

        self.assertEqual(memoria.situacion(self.s.cx, "<duda@x>"),
                         "pendiente_de_avisar")

        self.telegram_anda = True
        self._vuelta(datetime(2026, 8, 17, 11, 45), en_horario=True)
        self.assertTrue(any("No pude decidir" in t for t in self._textos()))

    def test_el_duda_avisado_no_se_vuelve_a_avisar_en_cada_vuelta(self):
        """La contracara del arreglo de arriba, y ninguno de los 314
        tests la miraba: cuando el aviso SÍ sale, el correo tiene que
        salir de la cola.

        avisar_en_el_momento marca "avisado" al salir bien;
        avisar_para_que_decida no marcaba nada, así que el correo se
        quedaba en "pendiente_de_avisar" y _atender_avisos_pendientes
        -que no tiene tope, y con razón- lo reintentaba en CADA vuelta
        del ciclo. Medido: 5 vueltas con Telegram ya restablecido, 5
        mensajes idénticos y 5 tandas. El ciclo de correo no tiene reja
        de horario: un DUDA que falla a las 17:05 son ~300 mensajes
        durante la noche, y DUDA son 11 de 46.
        """
        self.telegram_anda = False
        c = self._entra("<duda@x>", "Orbcomm — factura y contrato", "DUDA")
        with self._contexto([c], en_horario=True):
            self.s.revisar_casilla()
        self.assertEqual(memoria.situacion(self.s.cx, "<duda@x>"),
                         "pendiente_de_avisar")

        # Telegram vuelve. Cinco vueltas más del ciclo, sin nada nuevo.
        self.telegram_anda = True
        self.mensajes.clear()
        for n in range(1, 6):
            self._vuelta(self.HORA + timedelta(seconds=secretaria.CADA * n),
                         en_horario=True)

        repetidos = [t for t in self._textos() if "No pude decidir" in t]
        self.assertEqual(len(repetidos), 1,
                         f"el aviso salió {len(repetidos)} veces en 5"
                         f" vueltas: a ese ritmo son ~20 por hora hasta"
                         f" el próximo corte")
        self.assertEqual(len(self.s.abiertos), 1,
                         "una tanda nueva por vuelta: todos los botones"
                         " menos los últimos quedan huérfanos")
        self.assertEqual(memoria.situacion(self.s.cx, "<duda@x>"),
                         "mostrado_sin_clasificar",
                         "sigue en la cola después de haberse entregado:"
                         " se va a reintentar para siempre")


class LasTandasNoSePisan(_Escenario):
    """secrets.token_hex(2) son 65.536 tokens y `self.abiertos[tanda] =
    ...` asignaba sin chequear nada.

    Los botones de Telegram no vencen: una colisión hace que un botón
    viejo opere sobre los correos de otra tanda. Medido: JP toca "Listo,
    lo vi" en el aviso del lunes y se cierra el correo de hoy que nunca
    miró. En una tanda de resumen es peor, porque _cerrar_equipo marca
    leído en la casilla real.

    Y no tenía NINGUNA cobertura: la mutación "token fijo" sobrevivía la
    suite entera. Estos tests son esa red.
    """

    def _fila(self, mid, asunto, categoria="DELEGADO"):
        memoria.anotar(self.s.cx, {
            "message_id": mid, "uid": "1", "uidvalidity": "1",
            "de": "alguien@ejemplo.com", "para": "jp@x", "cc": "",
            "asunto": asunto, "fecha": "Mon, 17 Aug 2026 09:00:00 -0300",
            "cuerpo": "cuerpo", "adjuntos": []}, categoria, "m")
        return memoria.obtener(self.s.cx, mid)

    def test_el_token_es_largo(self):
        """El arreglo de un carácter: token_hex(2) son 65.536 valores,
        token_hex(4) son 4.294.967.296. Ocho caracteres hex."""
        self.assertEqual(len(self.s._nueva_tanda()), 8)

    def test_dos_tandas_seguidas_no_coinciden_aunque_el_azar_repita(self):
        """La mutación que sobrevivía la suite entera: token fijo. Con
        el token largo pero sin chequeo, esto pasa igual -por eso hacen
        falta las dos mitades del arreglo, y por eso este test patchea
        el generador en vez de confiar en la probabilidad."""
        with mock.patch.object(secrets, "token_hex", return_value="deadbeef"):
            a = self.s._nueva_tanda()
            self.s.abiertos[a] = ["<1@x>"]
            b = self.s._nueva_tanda()
            self.s.abiertos[b] = ["<2@x>"]
        self.assertNotEqual(a, b)

    def test_un_boton_viejo_no_cierra_los_correos_de_la_tanda_de_hoy(self):
        """El daño, no el mecanismo: JP toca "Leí todo" en el resumen
        del lunes y no puede cerrarse nada del de hoy."""
        vieja = self._fila("<lunes@x>", "Del lunes")
        nueva = self._fila("<hoy@x>", "De hoy")

        with mock.patch.object(secrets, "token_hex", return_value="deadbeef"):
            _, teclado_viejo = self.s.armar_resumen([vieja], "manana")
            _, teclado_nuevo = self.s.armar_resumen([nueva], "manana")

        datos_viejo = teclado_viejo["inline_keyboard"][0][0]["callback_data"]
        with mock.patch.object(bot, "tg_suave"), \
             mock.patch.object(secretaria, "EN_SECO", True), \
             mock.patch.object(self.s, "enviar", side_effect=self._enviar):
            self.s.atender_boton({"id": "1", "data": datos_viejo})

        self.assertEqual(memoria.situacion(self.s.cx, "<lunes@x>"), "cerrado")
        self.assertEqual(memoria.situacion(self.s.cx, "<hoy@x>"),
                         "clasificado",
                         "el botón del resumen viejo cerró un correo de"
                         " la tanda de hoy que JP nunca miró")
        self.assertNotEqual(
            teclado_viejo["inline_keyboard"][0][0]["callback_data"],
            teclado_nuevo["inline_keyboard"][0][0]["callback_data"])

    def test_ningun_camino_que_arma_tanda_se_saltea_el_chequeo(self):
        """Los cuatro lugares que armaban un token: los dos avisos y los
        dos resúmenes. Si alguno vuelve a llamar a secrets.token_hex
        derecho, este test lo agarra."""
        fila = self._fila("<1@x>", "Algo", "TUYO")
        with mock.patch.object(secrets, "token_hex", return_value="deadbeef"), \
             mock.patch.object(secretaria, "en_horario", return_value=True), \
             mock.patch.object(self.s, "enviar", side_effect=self._enviar):
            self.s.armar_resumen([self._fila("<e@x>", "Equipo")], "manana")
            self.s.armar_resumen_de_ruido([self._fila("<r@x>", "Ruido",
                                                      "RUIDO")])
            self.s.avisar_en_el_momento(fila, {"categoria": "TUYO"})
            self.s.avisar_para_que_decida(self._fila("<d@x>", "Duda", "DUDA"))

        self.assertEqual(len(self.s.abiertos), 4,
                         f"se pisaron tandas: {sorted(self.s.abiertos)}")


class SinMotorConfigurado(_Escenario):
    """clasificador.motores() levanta SystemExit -no ValueError- cuando
    el .env no tiene ningún motor, y SystemExit NO hereda de Exception.

    Se escapaba de los dos manejadores (`revisar_casilla` y
    `ciclo_de_correo`), mataba el hilo del correo, arrancar() salía con
    código 1, launchd reiniciaba, y el motor seguía sin estar: un bucle
    de reinicio sin un solo aviso. Ya se sabía -/estado usa (Exception,
    SystemExit) justo por esto- pero se había parchado un call site de
    dos.
    """

    def test_sin_motor_el_correo_se_muestra_en_vez_de_matar_el_hilo(self):
        c = self._entra("<a@x>", "Algo", "TUYO")
        with self._contexto([c], en_horario=True) as pila:
            pila.enter_context(mock.patch.object(
                secretaria.clasificador, "clasificar",
                side_effect=SystemExit("no hay ningún motor configurado")))
            self.s.revisar_casilla()          # no tiene que levantar nada

        self.assertEqual(memoria.situacion(self.s.cx, "<a@x>"),
                         "mostrado_sin_clasificar")
        self.assertTrue(any("No pude decidir" in t for t in self._textos()))

    def test_el_hilo_del_correo_sobrevive_a_un_systemexit(self):
        """La consecuencia real: sin esto el hilo se muere, arrancar()
        fuerza la salida y launchd reinicia en bucle, en silencio."""
        with mock.patch.object(secretaria, "CADA", 0.01), \
             mock.patch.object(self.s, "revisar_casilla",
                               side_effect=SystemExit("sin motores")):
            hilo = threading.Thread(target=self.s.ciclo_de_correo,
                                    daemon=True)
            hilo.start()
            time.sleep(0.2)
            sigue_vivo = hilo.is_alive()
            self.s.parada.set()
            hilo.join(timeout=2)

        self.assertTrue(sigue_vivo,
                        "un SystemExit de clasificador mató el hilo del"
                        " correo: launchd va a reiniciar en bucle")
        self.assertFalse(hilo.is_alive())

    def test_rever_sin_motor_no_mata_el_hilo_y_queda_para_que_decida_jp(self):
        """El tercer call site: rever_ruido() también termina en
        clasificar()."""
        c = self._entra("<r@x>", "Promo", "RUIDO")
        self._vuelta(datetime(2026, 8, 17, 9, 0), [c])
        memoria.preparar_rever(self.s.cx, "<r@x>", "es mi proveedor")

        with self._contexto() as pila:
            pila.enter_context(mock.patch.object(
                secretaria.clasificador, "clasificar",
                side_effect=SystemExit("no hay ningún motor configurado")))
            self.s.revisar_casilla()

        self.assertEqual(memoria.situacion(self.s.cx, "<r@x>"),
                         "mostrado_sin_clasificar")
        # Y salió de Ruido igual: la garantía de orden de rever_ruido no
        # depende de que el modelo conteste.
        self.assertEqual(self.buzon.cuenta("INBOX.Ruido"), 0)


class ElAvisoDeCorreoPerdidoDiceLaVerdad(unittest.TestCase):
    """Menor: el aviso decía "Quedó duplicado: sigue en tu bandeja y ya
    hay una copia en INBOX.Ruido" a renglón seguido de "Falló:
    CorreoPerdido: no está en INBOX y tampoco en INBOX.Ruido".

    Mandaba a JP a buscar el correo en los dos lugares donde el sistema
    acababa de confirmar que no estaba. CorreoPerdido llega por la misma
    vía que el duplicado -borrar_el_original lo levanta y _anotar_falla
    lo manda a "pendiente_de_borrar"- así que el flag `duplicado` no
    alcanzaba para distinguirlos.
    """

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def _aviso(self, falla, duplicado):
        with mock.patch.object(self.s, "enviar",
                               return_value={"ok": True}) as enviar:
            self.s.avisar_que_no_se_pudo_archivar(
                {"de": "a@x", "asunto": "Promo"}, falla, duplicado)
        return enviar.call_args[0][0]

    def test_no_dice_que_esta_en_los_dos_lugares_donde_no_esta(self):
        texto = self._aviso("CorreoPerdido: <1@x>: no está en INBOX y"
                            " tampoco en INBOX.Ruido", duplicado=True)
        self.assertNotIn("Quedó duplicado", texto)
        self.assertNotIn("sigue en tu bandeja", texto)
        self.assertIn("No lo encuentro", texto)

    def test_el_duplicado_de_verdad_sigue_diciendo_que_esta_duplicado(self):
        texto = self._aviso("OperacionAMedias: el EXPUNGE no se confirmó",
                            duplicado=True)
        self.assertIn("Quedó duplicado", texto)

    def test_el_caso_normal_no_cambio(self):
        texto = self._aviso("OSError: la red se cayó", duplicado=False)
        self.assertIn("Quedó en tu bandeja, sin archivar", texto)

    def test_sobrevive_a_un_reinicio_leyendo_la_falla_de_la_base(self):
        """El aviso puede salir después de un reinicio, reconstruido de
        la fila: ahí lo único que queda es el TEXTO de la falla, no el
        tipo de la excepción. Por eso se reconoce por el texto."""
        c = {"message_id": "<p@x>", "uid": "1", "uidvalidity": "1",
             "de": "a@x", "para": "jp@x", "cc": "", "asunto": "Promo",
             "fecha": "Mon, 17 Aug 2026 09:00:00 -0300", "cuerpo": "c",
             "adjuntos": []}
        memoria.anotar(self.s.cx, c, "RUIDO", "promo")
        memoria.cambiar(self.s.cx, "<p@x>", "pendiente_de_borrar")
        memoria.anotar_falla(self.s.cx, "<p@x>",
                             "CorreoPerdido: <p@x>: no está en INBOX y"
                             " tampoco en INBOX.Ruido")
        fila = memoria.obtener(self.s.cx, "<p@x>")

        with mock.patch.object(self.s, "enviar",
                               return_value={"ok": True}) as enviar:
            self.s._avisar_la_falla(fila)

        self.assertIn("No lo encuentro", enviar.call_args[0][0])
        self.assertEqual(memoria.situacion(self.s.cx, "<p@x>"),
                         "no_se_pudo_archivar")


class NingunTestSaleALaRed(unittest.TestCase):
    """La reja de `tests/sin_red.py`, probada.

    Medido antes de ponerla: la suite intentaba salir a api.telegram.org
    20 veces desde dos tests que no parcheaban `Secretaria.enviar`. Con
    la red andando eso no falla: le manda mensajes DE VERDAD al teléfono
    de JP cada vez que alguien corre los tests.

    Parchear esos dos arregla esos dos. Esto es para el próximo que se
    olvide: que se entere en el acto, y no JP por Telegram.
    """

    def test_salir_a_internet_rompe_el_test_en_el_acto(self):
        with self.assertRaises(sin_red.RedDeVerdad):
            socket.create_connection(("api.telegram.org", 443), timeout=1)

    def test_tambien_por_el_socket_armado_a_mano(self):
        """create_connection es el camino de urllib y de imaplib, pero
        no es el único: el que arme el socket a mano tiene que chocar
        con la misma reja."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        with self.assertRaises(sin_red.RedDeVerdad):
            s.connect(("149.154.167.220", 443))   # api.telegram.org

    def test_el_mensaje_dice_que_parchear(self):
        """Un test que salta acá tiene que saber qué hacer sin ir a leer
        el código de la reja: el mensaje es la mitad del arreglo."""
        with self.assertRaises(sin_red.RedDeVerdad) as cm:
            socket.create_connection(("api.telegram.org", 443), timeout=1)
        texto = str(cm.exception)
        self.assertIn("enviar", texto)
        self.assertIn("IMAP4_SSL", texto)

    def test_la_reja_no_se_la_come_el_reintento_de_bot_tg(self):
        """Por qué RedDeVerdad no es un OSError: bot.tg() reintenta cinco
        veces ante un OSError y Secretaria.enviar() se traga cualquier
        Exception y devuelve None. Un corte que se pareciera a "la red
        está caída" dejaría pasar el test igual, en silencio, que es
        justo la mitad del problema que la reja viene a resolver."""
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "x",
                                          "TELEGRAM_CHAT_ID": "1"}):
            with self.assertRaises(sin_red.RedDeVerdad):
                bot.tg("sendMessage", chat_id="1", text="hola")

    def test_tampoco_se_la_tragan_los_except_anchos_de_secretaria(self):
        """La otra mitad, y es la que se aprendió midiendo: el segundo de
        los dos tests que salían a la red seguía saliendo después de
        parchearlo una vez y PASABA IGUAL, porque el intento caía adentro
        del `except (Exception, SystemExit)` de
        _atender_revers_pendientes. Esos manejadores anchos existen para
        que nada mate el hilo del correo y se tragaban también la reja.
        Por eso RedDeVerdad hereda de BaseException."""
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        c = correo_de(BuzonFalso().agregar("INBOX", de="a@x", asunto="Algo",
                                           message_id="<a@x>"), "1")

        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "x",
                                          "TELEGRAM_CHAT_ID": "1"}), \
             mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[c]), \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               side_effect=RuntimeError("sin motores")), \
             mock.patch.object(secretaria.reglas, "es_ruido_conocido",
                               return_value=None):
            # revisar_casilla tiene su propio except (Exception,
            # SystemExit) alrededor de clasificar(): la reja tiene que
            # atravesarlo igual.
            with self.assertRaises(sin_red.RedDeVerdad):
                s.revisar_casilla()

    def test_lo_local_sigue_andando(self):
        """La reja corta internet, no los sockets: un servidor de
        mentira en 127.0.0.1 es exactamente lo que hay que poder hacer
        en vez de salir afuera."""
        servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(servidor.close)
        servidor.bind(("127.0.0.1", 0))
        servidor.listen(1)

        cliente = socket.create_connection(servidor.getsockname(), timeout=2)
        self.addCleanup(cliente.close)
        self.assertIsNotNone(cliente)

    def test_queda_registrado_para_poder_medirlo(self):
        """`sin_red.INTENTOS` es lo que permite medir: la suite verde con
        esa lista vacía es la prueba de que nadie lo intentó, que es
        como se midió el antes (20 intentos) y el después (0)."""
        antes = len(sin_red.INTENTOS)
        with self.assertRaises(sin_red.RedDeVerdad):
            socket.create_connection(("api.telegram.org", 443), timeout=1)
        self.assertEqual(len(sin_red.INTENTOS), antes + 1)
        self.assertEqual(sin_red.INTENTOS[-1][0], "api.telegram.org")


if __name__ == "__main__":
    unittest.main()
