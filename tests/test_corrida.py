#!/usr/bin/env python3
"""La corrida en frío: clasificar el atraso y mostrárselo a JP en tandas.

Lo que se cuida acá es lo que hizo falta construirla: que 141 correos no
se conviertan en 141 mensajes de Telegram, y que el número que JP
contesta seleccione siempre el correo que vio.
"""
import unittest

import corrida


def caso(mid, categoria, de="alguien@x.com", asunto="Un asunto",
         motivo="porque sí"):
    """Un correo ya clasificado, como los que arma la fase 1."""
    return {"message_id": mid, "de": de, "asunto": asunto,
            "categoria": categoria, "motivo": motivo}


class Orden(unittest.TestCase):
    """Las tandas salen ordenadas por lo que más le importa a JP, no por
    fecha: si abandona la revisión a la mitad, lo que alcanzó a mirar
    tiene que ser lo que más valía."""

    def test_lo_tuyo_va_primero_y_el_ruido_al_final(self):
        mezclado = [caso("<1@x>", "RUIDO"), caso("<2@x>", "DELEGADO"),
                    caso("<3@x>", "TUYO")]
        self.assertEqual(
            [c["categoria"] for c in corrida.ordenar_para_revisar(mezclado)],
            ["TUYO", "DELEGADO", "RUIDO"])

    def test_lo_que_no_supo_clasificar_va_arriba_con_lo_tuyo(self):
        """DUDA y ERROR son los casos que más valen para medir: son
        exactamente donde el clasificador no llegó solo."""
        mezclado = [caso("<1@x>", "RUIDO"), caso("<2@x>", "DUDA"),
                    caso("<3@x>", "DELEGADO"), caso("<4@x>", "ERROR")]
        salida = [c["categoria"] for c in corrida.ordenar_para_revisar(mezclado)]
        self.assertEqual(salida[:2], ["DUDA", "ERROR"])

    def test_una_categoria_desconocida_no_se_pierde(self):
        """Si el modelo devuelve algo fuera del orden conocido, va al
        final pero VA: perder un caso en el ordenamiento sería la falla
        silenciosa de siempre."""
        salida = corrida.ordenar_para_revisar(
            [caso("<1@x>", "TUYO"), caso("<2@x>", "MARCIANO")])
        self.assertEqual(len(salida), 2)
        self.assertEqual(salida[-1]["categoria"], "MARCIANO")

    def test_dentro_de_una_categoria_se_respeta_el_orden_de_llegada(self):
        entrada = [caso("<1@x>", "TUYO"), caso("<2@x>", "TUYO"),
                   caso("<3@x>", "TUYO")]
        self.assertEqual([c["message_id"]
                          for c in corrida.ordenar_para_revisar(entrada)],
                         ["<1@x>", "<2@x>", "<3@x>"])


class Tandas(unittest.TestCase):
    """141 correos son ~12 mensajes, no 141. Ése es el punto entero."""

    def test_ciento_cuarenta_y_uno_entran_en_doce_tandas(self):
        lotes = corrida.partir_en_tandas(
            [caso(f"<{n}@x>", "RUIDO") for n in range(141)])
        self.assertEqual(len(lotes), 12)
        self.assertEqual(sum(len(l) for l in lotes), 141)

    def test_ninguna_tanda_pasa_el_tamano(self):
        lotes = corrida.partir_en_tandas(
            [caso(f"<{n}@x>", "RUIDO") for n in range(141)])
        self.assertTrue(all(len(l) <= corrida.TAMANO_TANDA for l in lotes))

    def test_sin_correos_no_hay_tandas(self):
        """Cero tandas, no una tanda vacía: una tanda sin correos detrás
        es un botón que no cierra nada."""
        self.assertEqual(corrida.partir_en_tandas([]), [])

    def test_no_se_pierde_ni_se_repite_ningun_correo(self):
        entrada = [caso(f"<{n}@x>", "RUIDO") for n in range(141)]
        salieron = [c["message_id"] for l in corrida.partir_en_tandas(entrada)
                    for c in l]
        self.assertEqual(salieron, [c["message_id"] for c in entrada])


class ArmarTanda(unittest.TestCase):
    def test_las_lineas_se_numeran_desde_uno(self):
        texto, _ = corrida.armar_tanda(
            [caso("<1@x>", "TUYO", de="ana@x.com"),
             caso("<2@x>", "TUYO", de="beto@x.com")], 1, 3, "0007")
        self.assertIn("1. ana@x.com", texto)
        self.assertIn("2. beto@x.com", texto)

    def test_cada_linea_dice_que_categoria_le_puso_y_por_que(self):
        """Sin el motivo, JP no puede corregir nada: vería una etiqueta
        sin la razón que la explica."""
        texto, _ = corrida.armar_tanda(
            [caso("<1@x>", "ENZO", motivo="reporta falla de unidad")],
            1, 1, "0007")
        self.assertIn("ENZO", texto)
        self.assertIn("reporta falla de unidad", texto)

    def test_dice_en_que_tanda_va_de_cuantas(self):
        texto, _ = corrida.armar_tanda([caso("<1@x>", "TUYO")], 3, 12, "0007")
        self.assertIn("3", texto)
        self.assertIn("12", texto)

    def test_los_dos_botones_llevan_el_token_de_esta_tanda(self):
        """Sin el token, el botón 'Está bien' de una tanda vieja cerraría
        la tanda nueva -- toda tanda numera desde 1."""
        _, teclado = corrida.armar_tanda([caso("<1@x>", "TUYO")], 1, 1, "0007")
        datos = [b["callback_data"] for fila in teclado["inline_keyboard"]
                 for b in fila]
        self.assertEqual(len(datos), 2)
        self.assertTrue(all("0007" in d for d in datos), datos)

    def test_un_lote_grande_se_recorta_avisando_nunca_en_silencio(self):
        """Mismo criterio que armar_resumen_de_ruido: Telegram rechaza el
        mensaje entero pasado el límite, así que se recorta -- pero con
        un aviso explícito de cuántos quedaron sin listar."""
        lote = [caso(f"<{n}@x>", "RUIDO", de="x" * 60, asunto="y" * 90,
                     motivo="z" * 120) for n in range(60)]
        texto, _ = corrida.armar_tanda(lote, 1, 1, "0007")
        self.assertLessEqual(len(texto), corrida.LIMITE_TELEGRAM)
        self.assertIn("más", texto)


class NoTocaLaCasilla(unittest.TestCase):
    """La corrida es de análisis: mide el clasificador y no escribe nada.

    El freno no es una variable que alguien pueda dejar prendida -- es
    que la acción no existe en el archivo."""

    def test_corrida_no_conoce_las_funciones_que_escriben(self):
        for peligrosa in ("mover_a", "marcar_leido", "borrar_el_original",
                          "devolver_a_bandeja"):
            self.assertFalse(
                hasattr(corrida, peligrosa),
                f"corrida.py no debería poder llamar a {peligrosa}")

    def test_el_texto_del_modulo_no_menciona_esas_llamadas(self):
        """hasattr no alcanza: correo.mover_a(...) tampoco tiene que
        aparecer, y eso no lo ve el espacio de nombres del módulo."""
        import inspect
        fuente = inspect.getsource(corrida)
        for peligrosa in ("mover_a", "marcar_leido", "borrar_el_original"):
            self.assertNotIn(f"correo.{peligrosa}", fuente)


if __name__ == "__main__":
    unittest.main()


import os
import tempfile
from unittest import mock

import memoria


def entrante(mid, de="alguien@x.com", asunto="Un asunto", cuerpo="cuerpo"):
    """Un correo como el que deja correo.traer_nuevos()."""
    return {"message_id": mid, "uid": "1", "uidvalidity": "9", "de": de,
            "para": "jp@x", "cc": "", "asunto": asunto,
            "fecha": "Mon, 11 Aug 2026 09:00:00 -0300",
            "cuerpo": cuerpo, "adjuntos": []}


class ConBase(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.cx = memoria.abrir(self.f.name)

    def tearDown(self):
        # Cerrar los dos a mano: si no, la salida de la suite se llena de
        # ResourceWarning y deja de servir para ver lo que importa.
        self.cx.close()
        self.f.close()
        os.unlink(self.f.name)


class ClasificarTodo(ConBase):
    """La fase 1: callada, y con la misma cadena que corre en producción."""

    def test_clasifica_cada_correo_y_lo_anota(self):
        with mock.patch.object(corrida.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO",
                                             "motivo": "promoción"}):
            corrida.clasificar_todo(self.cx, [entrante("<1@x>"),
                                              entrante("<2@x>")], "sistema")
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria"], "RUIDO")
        self.assertEqual(memoria.obtener(self.cx, "<2@x>")["categoria"], "RUIDO")

    def test_no_manda_nada_por_telegram(self):
        """La fase 1 es callada: ése es el punto. Si acá saliera un
        mensaje por correo volveríamos a los 141."""
        with mock.patch.object(corrida.clasificador, "clasificar",
                               return_value={"categoria": "TUYO", "motivo": "m"}), \
             mock.patch.object(corrida.bot, "tg") as tg:
            corrida.clasificar_todo(self.cx, [entrante("<1@x>")], "sistema")
        tg.assert_not_called()

    def test_lo_ya_clasificado_no_se_vuelve_a_pedir_al_modelo(self):
        """Si NVIDIA se cae a mitad de camino, la corrida se retoma sin
        pagar de nuevo las llamadas que ya salieron."""
        with mock.patch.object(corrida.clasificador, "clasificar",
                               return_value={"categoria": "RUIDO", "motivo": "m"}) as cl:
            corrida.clasificar_todo(self.cx, [entrante("<1@x>")], "sistema")
            corrida.clasificar_todo(self.cx, [entrante("<1@x>")], "sistema")
        self.assertEqual(cl.call_count, 1)

    def test_un_correo_que_revienta_no_tira_la_corrida_entera(self):
        """141 correos son 50 minutos de red: que el número 70 falle no
        puede costar los 69 anteriores."""
        with mock.patch.object(corrida.clasificador, "clasificar",
                               side_effect=[RuntimeError("sin motores"),
                                            {"categoria": "TUYO", "motivo": "m"}]):
            corrida.clasificar_todo(self.cx, [entrante("<1@x>"),
                                              entrante("<2@x>")], "sistema")
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria"], "ERROR")
        self.assertEqual(memoria.obtener(self.cx, "<2@x>")["categoria"], "TUYO")

    def test_el_codigo_convenido_gana_sin_consultar_al_modelo(self):
        """Misma cadena que producción: si no, lo que medimos no dice
        nada del sistema que corre."""
        c = entrante("<1@x>", cuerpo="Va a ser tal cual lo charlado")
        with mock.patch.object(corrida.reglas, "codigos_convenidos",
                               return_value=["tal cual lo charlado"]), \
             mock.patch.object(corrida.clasificador, "clasificar") as cl:
            corrida.clasificar_todo(self.cx, [c], "sistema")
        cl.assert_not_called()
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria"], "TUYO")


class LoQueJpDijo(ConBase):
    def _anotar(self, mid, categoria):
        memoria.anotar(self.cx, entrante(mid), categoria, "porque sí")

    def test_confirmar_una_tanda_deja_lo_que_dijo_el_sistema_como_correcto(self):
        self._anotar("<1@x>", "RUIDO")
        corrida.confirmar(self.cx, ["<1@x>"])
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"],
                         "RUIDO")

    def test_corregir_guarda_lo_de_jp_sin_pisar_lo_del_sistema(self):
        """Pisar `categoria` perdería la comparación, que es todo lo que
        esta corrida viene a producir."""
        self._anotar("<1@x>", "RUIDO")
        corrida.corregir(self.cx, "<1@x>", "TUYO")
        fila = memoria.obtener(self.cx, "<1@x>")
        self.assertEqual(fila["categoria"], "RUIDO")
        self.assertEqual(fila["categoria_jp"], "TUYO")


class ElJson(ConBase):
    def _anotar(self, mid, categoria):
        memoria.anotar(self.cx, entrante(mid), categoria, "porque sí")

    def test_tiene_la_forma_que_lee_revisar_reglas(self):
        self._anotar("<1@x>", "RUIDO")
        corrida.confirmar(self.cx, ["<1@x>"])
        d = corrida.armar_json(self.cx, "un/modelo")
        for clave in ("fecha_utc", "modelo", "aciertos", "total", "completo",
                      "casos"):
            self.assertIn(clave, d)
        caso = d["casos"][0]
        for clave in ("de", "para", "cc", "asunto", "cuerpo", "fecha",
                      "adjuntos", "correcto", "prediccion"):
            self.assertIn(clave, caso)
        self.assertIn("categoria", caso["prediccion"])

    def test_lo_que_jp_no_miro_no_entra(self):
        """Lo que nadie revisó no tiene un 'correcto'. Contarlo como
        acierto porque el sistema dijo algo sería medir contra sí mismo:
        el número daría lindo y no significaría nada."""
        self._anotar("<1@x>", "RUIDO")
        self._anotar("<2@x>", "TUYO")
        corrida.confirmar(self.cx, ["<1@x>"])
        d = corrida.armar_json(self.cx, "un/modelo")
        self.assertEqual([c["message_id"] for c in d["casos"]], ["<1@x>"])

    def test_cuenta_los_aciertos_contra_lo_que_dijo_jp(self):
        self._anotar("<1@x>", "RUIDO")
        self._anotar("<2@x>", "RUIDO")
        corrida.confirmar(self.cx, ["<1@x>"])
        corrida.corregir(self.cx, "<2@x>", "TUYO")
        d = corrida.armar_json(self.cx, "un/modelo")
        self.assertEqual((d["aciertos"], d["total"]), (1, 2))


class RevisionFalsa(ConBase):
    """Base para los tests de la fase 2: la Revisión con un `enviar` de
    mentira que anota lo que habría salido, en vez de salir."""

    def setUp(self):
        super().setUp()
        self.enviados = []

        def enviar(texto, teclado=None):
            self.enviados.append((texto, teclado))
            return True

        self.enviar = enviar

    def _anotar(self, mid, categoria="RUIDO"):
        memoria.anotar(self.cx, entrante(mid), categoria, "porque sí")
        return {"message_id": mid, "de": "alguien@x.com",
                "asunto": "Un asunto", "categoria": categoria,
                "motivo": "porque sí"}

    def _revision(self, *lotes):
        return corrida.Revision(self.cx, list(lotes), enviar=self.enviar)

    def _boton(self, tanda, accion, idx=0, valor="ok"):
        return {"callback_query": {"id": "1",
                                   "data": f"{accion}|{tanda}-{idx}|{valor}"}}

    def _texto(self, t):
        return {"message": {"text": t}}


class UnaTandaPorVez(RevisionFalsa):
    """El corazón del asunto: no se manda la siguiente hasta que JP
    cerró la anterior. Si salieran todas juntas volveríamos a la
    andanada, sólo que con menos mensajes."""

    def test_arrancar_manda_una_sola_tanda(self):
        r = self._revision([self._anotar("<1@x>")], [self._anotar("<2@x>")])
        r.arrancar()
        self.assertEqual(len(self.enviados), 1)

    def test_la_segunda_sale_recien_cuando_se_cierra_la_primera(self):
        r = self._revision([self._anotar("<1@x>")], [self._anotar("<2@x>")])
        r.arrancar()
        self.assertEqual(len(self.enviados), 1)
        r.atender(self._boton(r.tanda, "b"))
        self.assertEqual(len(self.enviados), 2)

    def test_cuando_no_quedan_tandas_avisa_que_termino(self):
        r = self._revision([self._anotar("<1@x>")])
        r.arrancar()
        r.atender(self._boton(r.tanda, "b"))
        self.assertTrue(r.termino)


class CerrarTanda(RevisionFalsa):
    def test_esta_bien_da_por_correcto_todo_lo_de_la_tanda(self):
        c1, c2 = self._anotar("<1@x>", "RUIDO"), self._anotar("<2@x>", "TUYO")
        r = self._revision([c1, c2])
        r.arrancar()
        r.atender(self._boton(r.tanda, "b"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"], "RUIDO")
        self.assertEqual(memoria.obtener(self.cx, "<2@x>")["categoria_jp"], "TUYO")

    def test_un_boton_de_otra_tanda_no_cierra_la_de_ahora(self):
        """Toda tanda numera desde 1: sin el token, «Está bien» de un
        mensaje viejo cerraría el de ahora, y JP daría por revisados
        doce correos que no vio."""
        r = self._revision([self._anotar("<1@x>")], [self._anotar("<2@x>")])
        r.arrancar()
        r.atender(self._boton("9999", "b"))
        self.assertIsNone(memoria.obtener(self.cx, "<1@x>")["categoria_jp"])
        self.assertEqual(len(self.enviados), 1)


class Corregir(RevisionFalsa):
    def _hasta_pedir_numero(self, *casos):
        r = self._revision(list(casos))
        r.arrancar()
        r.atender(self._boton(r.tanda, "g"))
        return r

    def test_corregir_pregunta_cual_numero(self):
        r = self._hasta_pedir_numero(self._anotar("<1@x>"))
        self.assertIn("número", self.enviados[-1][0].lower())

    def test_el_numero_muestra_ese_correo_con_los_botones_de_categoria(self):
        r = self._hasta_pedir_numero(self._anotar("<1@x>"),
                                     self._anotar("<2@x>"))
        r.atender(self._texto("2"))
        texto, teclado = self.enviados[-1]
        categorias = [b["text"] for fila in teclado["inline_keyboard"]
                      for b in fila]
        self.assertTrue(any("Ruido" in c or "RUIDO" in c.upper()
                            for c in categorias), categorias)

    def test_elegir_categoria_corrige_ese_correo_y_no_otro(self):
        r = self._hasta_pedir_numero(self._anotar("<1@x>", "RUIDO"),
                                     self._anotar("<2@x>", "RUIDO"))
        r.atender(self._texto("2"))
        r.atender(self._boton(r.tanda, "c", idx=2, valor="TUYO"))
        self.assertEqual(memoria.obtener(self.cx, "<2@x>")["categoria_jp"], "TUYO")
        self.assertIsNone(memoria.obtener(self.cx, "<1@x>")["categoria_jp"])

    def test_se_pueden_corregir_varios_de_la_misma_tanda(self):
        """Corregir uno no cierra la tanda: puede haber más de uno mal,
        y volver a tocar «Corregir» por cada uno sería un mensaje de
        más cada vez. En el medio de cada corrección va el porqué (ver
        ElPorque)."""
        r = self._hasta_pedir_numero(self._anotar("<1@x>"),
                                     self._anotar("<2@x>"))
        r.atender(self._texto("2"))
        r.atender(self._boton(r.tanda, "c", idx=2, valor="TUYO"))
        r.atender(self._texto("me lo mandaron a mí"))
        r.atender(self._texto("1"))
        r.atender(self._boton(r.tanda, "c", idx=1, valor="ENZO"))
        r.atender(self._texto("es una falla de unidad"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"], "ENZO")
        self.assertEqual(memoria.obtener(self.cx, "<2@x>")["categoria_jp"], "TUYO")

    def test_un_numero_que_no_existe_lo_dice_y_no_corrige_nada(self):
        r = self._hasta_pedir_numero(self._anotar("<1@x>"))
        r.atender(self._texto("7"))
        self.assertIn("7", self.enviados[-1][0])
        self.assertIsNone(memoria.obtener(self.cx, "<1@x>")["categoria_jp"])

    def test_algo_que_no_es_un_numero_no_revienta(self):
        r = self._hasta_pedir_numero(self._anotar("<1@x>"))
        r.atender(self._texto("el segundo creo"))
        self.assertIsNone(memoria.obtener(self.cx, "<1@x>")["categoria_jp"])

    def test_el_numero_selecciona_bien_aunque_la_tanda_se_haya_recortado(self):
        """Si el mensaje se recortó por espacio, el número N tiene que
        seguir apuntando al mismo correo -- la tanda los conoce a todos
        aunque el texto no los liste."""
        casos = [self._anotar(f"<{n}@x>") for n in range(1, 41)]
        r = self._revision(casos)
        r.arrancar()
        r.atender(self._boton(r.tanda, "g"))
        r.atender(self._texto("40"))
        r.atender(self._boton(r.tanda, "c", idx=40, valor="TUYO"))
        self.assertEqual(memoria.obtener(self.cx, "<40@x>")["categoria_jp"],
                         "TUYO")


class Informe(unittest.TestCase):
    """El informe es el producto de la corrida: de acá salen las reglas
    nuevas para reglas.md. Si no dice en qué se equivocó y con quién,
    la corrida fue un ejercicio."""

    def _d(self, *casos):
        return {"aciertos": sum(1 for c in casos
                                if c["prediccion"]["categoria"] == c["correcto"]),
                "total": len(casos), "casos": list(casos)}

    def _caso(self, de, dijo, era):
        return {"de": de, "asunto": "x", "correcto": era,
                "prediccion": {"categoria": dijo, "motivo": "m"}}

    def test_dice_cuantos_acerto_sobre_cuantos(self):
        texto = corrida.informe(self._d(self._caso("a@x.com", "RUIDO", "RUIDO"),
                                        self._caso("b@x.com", "RUIDO", "TUYO")))
        self.assertIn("1", texto)
        self.assertIn("2", texto)

    def test_muestra_en_que_se_confunde(self):
        """No alcanza con 'acertó 8 de 10': la regla nueva sale de saber
        que confunde RUIDO con TUYO, que es el error caro."""
        texto = corrida.informe(self._d(
            self._caso("a@x.com", "RUIDO", "TUYO"),
            self._caso("b@x.com", "RUIDO", "TUYO")))
        self.assertIn("RUIDO", texto)
        self.assertIn("TUYO", texto)
        self.assertIn("2", texto)

    def test_nombra_los_remitentes_que_concentran_los_errores(self):
        """Un remitente con tres errores es una regla de reglas.md
        esperando a que la escriban."""
        texto = corrida.informe(self._d(
            self._caso("repetido@x.com", "RUIDO", "TUYO"),
            self._caso("repetido@x.com", "RUIDO", "TUYO"),
            self._caso("suelto@x.com", "RUIDO", "TUYO")))
        self.assertIn("repetido@x.com", texto)

    def test_sin_errores_lo_dice_y_no_inventa_secciones_vacias(self):
        texto = corrida.informe(self._d(self._caso("a@x.com", "TUYO", "TUYO")))
        self.assertNotIn("→", texto)

    def test_sin_casos_no_revienta(self):
        self.assertIsInstance(corrida.informe(self._d()), str)


class RetomarLaRevision(ConBase):
    """JP revisa 3 tandas, se va a entrenar, y vuelve al día siguiente.
    Lo ya revisado no se le muestra de nuevo."""

    def test_lo_ya_dictaminado_no_vuelve_a_la_cola(self):
        memoria.anotar(self.cx, entrante("<1@x>"), "RUIDO", "m")
        memoria.anotar(self.cx, entrante("<2@x>"), "TUYO", "m")
        corrida.confirmar(self.cx, ["<1@x>"])
        self.assertEqual([c["message_id"]
                          for c in corrida.pendientes_de_revisar(self.cx)],
                         ["<2@x>"])

    def test_vienen_con_lo_que_la_tanda_necesita_mostrar(self):
        memoria.anotar(self.cx, entrante("<1@x>", de="ana@x.com"), "TUYO",
                       "la nombra a JP")
        c = corrida.pendientes_de_revisar(self.cx)[0]
        self.assertEqual(c["de"], "ana@x.com")
        self.assertEqual(c["categoria"], "TUYO")
        self.assertEqual(c["motivo"], "la nombra a JP")


class ConfirmarNoPisaLoCorregido(RevisionFalsa):
    """El bug que encontró JP en la primera prueba real, 2026-08-26.

    Corrigió el 3 a ENZO y después tocó «Está bien» para cerrar la
    tanda. confirmar() recorría TODOS los correos de la tanda dando por
    buena la categoría del sistema, así que pisó la corrección recién
    hecha: quedó DELEGADO otra vez, con explicación "confirmado por JP".

    Lo caro no es perder una corrección: es que el informe salió
    diciendo "acertó 3 de 3 (100%)". Sobre 141 correos habría borrado
    en silencio todas las correcciones y devuelto un porcentaje
    perfecto -- justo el número que se iba a usar para decidir si el
    clasificador anda bien.

    «Está bien» significa "el resto está bien", nunca "olvidate de lo
    que te acabo de decir".
    """

    def test_confirmar_la_tanda_no_borra_la_correccion_de_adentro(self):
        r = self._revision([self._anotar("<1@x>", "RUIDO"),
                            self._anotar("<2@x>", "DELEGADO")])
        r.arrancar()
        r.atender(self._boton(r.tanda, "g"))
        r.atender(self._texto("2"))
        r.atender(self._boton(r.tanda, "c", idx=2, valor="ENZO"))
        r.atender(self._boton(r.tanda, "b"))
        self.assertEqual(memoria.obtener(self.cx, "<2@x>")["categoria_jp"],
                         "ENZO")

    def test_los_demas_de_la_tanda_si_quedan_confirmados(self):
        r = self._revision([self._anotar("<1@x>", "RUIDO"),
                            self._anotar("<2@x>", "DELEGADO")])
        r.arrancar()
        r.atender(self._boton(r.tanda, "g"))
        r.atender(self._texto("2"))
        r.atender(self._boton(r.tanda, "c", idx=2, valor="ENZO"))
        r.atender(self._boton(r.tanda, "b"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"],
                         "RUIDO")

    def test_el_informe_cuenta_la_correccion_como_error_no_como_acierto(self):
        """La consecuencia que importa: con la corrección pisada el
        informe decía 100%."""
        r = self._revision([self._anotar("<1@x>", "RUIDO"),
                            self._anotar("<2@x>", "DELEGADO")])
        r.arrancar()
        r.atender(self._boton(r.tanda, "g"))
        r.atender(self._texto("2"))
        r.atender(self._boton(r.tanda, "c", idx=2, valor="ENZO"))
        r.atender(self._boton(r.tanda, "b"))
        d = corrida.armar_json(self.cx, "un/modelo")
        self.assertEqual((d["aciertos"], d["total"]), (1, 2))

    def test_confirmar_dos_veces_tampoco_pisa(self):
        """Por si JP toca «Está bien» en un mensaje viejo después de
        haber corregido en otra vuelta -- que es literalmente lo que
        hizo."""
        self._anotar("<1@x>", "RUIDO")
        corrida.corregir(self.cx, "<1@x>", "TUYO")
        corrida.confirmar(self.cx, ["<1@x>"])
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"],
                         "TUYO")


class ElTokenNoSeRepiteEntreCorridas(RevisionFalsa):
    """El otro hallazgo de la prueba real del 2026-08-26.

    JP scrolleó al mensaje de una corrida ANTERIOR y tocó «Está bien»
    ahí -- y funcionó, cuando no debería. El token salía del índice del
    lote (`f"{i+1:04d}"`), así que la tanda 1 de toda corrida se llamaba
    "0001" y sus botones eran indistinguibles.

    Es la misma falla que `bot.es_de_esta_tanda` existe para evitar, y
    que simulacro.py ya evitaba metiendo el PID en el token. Sobre 141
    correos el costo es concreto: tocar el botón de un mensaje viejo da
    por revisados doce correos que JP no vio, y ésos entran al informe
    como aciertos.
    """

    def test_dos_corridas_no_usan_el_mismo_token_para_su_primera_tanda(self):
        a = self._revision([self._anotar("<1@x>")])
        b = self._revision([self._anotar("<2@x>")])
        a.arrancar()
        b.arrancar()
        self.assertNotEqual(a.tanda, b.tanda)

    def test_el_boton_de_una_corrida_vieja_no_cierra_la_tanda_de_ahora(self):
        vieja = self._revision([self._anotar("<1@x>")])
        vieja.arrancar()
        token_viejo = vieja.tanda

        ahora = self._revision([self._anotar("<2@x>")])
        ahora.arrancar()
        ahora.atender(self._boton(token_viejo, "b"))
        self.assertIsNone(memoria.obtener(self.cx, "<2@x>")["categoria_jp"])

    def test_adentro_de_una_corrida_cada_tanda_tiene_su_token(self):
        r = self._revision([self._anotar("<1@x>")], [self._anotar("<2@x>")])
        r.arrancar()
        primero = r.tanda
        r.atender(self._boton(r.tanda, "b"))
        self.assertNotEqual(r.tanda, primero)


class ElPorque(RevisionFalsa):
    """Lo que JP marcó a mitad de la corrida real, 2026-08-26.

    Corregir sin decir por qué produce etiquetas, no reglas. "Esto era
    TUYO" no se puede escribir en reglas.md; "cuando alguien del equipo
    contesta derivándote a vos, es TUYO aunque haya contestado" sí. El
    caso concreto: Natalia respondió «le reenvío esto a Juan Pablo, que
    él te va a poder dar la respuesta» y el modelo lo leyó como "Natalia
    ya se ocupó" → DELEGADO. Sin el porqué, esa corrección es un dato
    suelto.

    simulacro.py ya lo pedía (pedir_explicacion); la corrida no lo
    trasladó.
    """

    def _hasta_corregir(self, *casos):
        r = self._revision(list(casos))
        r.arrancar()
        r.atender(self._boton(r.tanda, "g"))
        r.atender(self._texto("1"))
        r.atender(self._boton(r.tanda, "c", idx=1, valor="TUYO"))
        return r

    def test_despues_de_corregir_pregunta_por_que(self):
        self._hasta_corregir(self._anotar("<1@x>", "DELEGADO"))
        self.assertIn("por qué", self.enviados[-1][0].lower())

    def test_lo_que_contesta_queda_guardado_como_explicacion(self):
        r = self._hasta_corregir(self._anotar("<1@x>", "DELEGADO"))
        r.atender(self._texto("Natalia me lo derivó a mí, no lo resolvió"))
        self.assertEqual(
            memoria.obtener(self.cx, "<1@x>")["explicacion"],
            "Natalia me lo derivó a mí, no lo resolvió")

    def test_el_motivo_no_cambia_la_categoria_que_eligio(self):
        r = self._hasta_corregir(self._anotar("<1@x>", "DELEGADO"))
        r.atender(self._texto("porque me lo derivó"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"],
                         "TUYO")

    def test_se_puede_saltear_el_porque_sin_perder_la_correccion(self):
        """JP no siempre tiene ganas de explicar, y obligarlo a hacerlo
        haría que deje de corregir -- que es peor."""
        r = self._hasta_corregir(self._anotar("<1@x>", "DELEGADO"))
        r.atender(self._texto("-"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"],
                         "TUYO")

    def test_despues_del_porque_vuelve_a_esperar_otro_numero(self):
        r = self._hasta_corregir(self._anotar("<1@x>", "DELEGADO"),
                                 self._anotar("<2@x>", "RUIDO"))
        r.atender(self._texto("porque sí"))
        r.atender(self._texto("2"))
        r.atender(self._boton(r.tanda, "c", idx=2, valor="ENZO"))
        self.assertEqual(memoria.obtener(self.cx, "<2@x>")["categoria_jp"],
                         "ENZO")

    def test_cerrar_la_tanda_con_un_porque_pendiente_no_pierde_la_correccion(self):
        """JP puede tocar «Está bien» sin contestar el porqué. La
        corrección vale igual; el motivo se le pregunta en el repaso."""
        r = self._hasta_corregir(self._anotar("<1@x>", "DELEGADO"))
        r.atender(self._boton(r.tanda, "b"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"],
                         "TUYO")


class RepasoDeMotivos(RevisionFalsa):
    """Las correcciones que quedaron sin porqué se preguntan al final.

    Cubre las seis que JP ya había hecho antes de que existiera esta
    pregunta, y las que saltea en el momento."""

    def test_al_terminar_las_tandas_pregunta_por_las_que_no_tienen_motivo(self):
        self._anotar("<1@x>", "DELEGADO")
        corrida.corregir(self.cx, "<1@x>", "TUYO")
        r = self._revision([self._anotar("<2@x>", "RUIDO")])
        r.arrancar()
        r.atender(self._boton(r.tanda, "b"))
        self.assertFalse(r.termino)
        self.assertIn("por qué", self.enviados[-1][0].lower())

    def test_guarda_el_motivo_del_repaso(self):
        self._anotar("<1@x>", "DELEGADO")
        corrida.corregir(self.cx, "<1@x>", "TUYO")
        r = self._revision([self._anotar("<2@x>", "RUIDO")])
        r.arrancar()
        r.atender(self._boton(r.tanda, "b"))
        r.atender(self._texto("me lo derivó Natalia"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["explicacion"],
                         "me lo derivó Natalia")

    def test_cuando_no_quedan_motivos_pendientes_ahi_si_termina(self):
        r = self._revision([self._anotar("<1@x>", "RUIDO")])
        r.arrancar()
        r.atender(self._boton(r.tanda, "b"))
        self.assertTrue(r.termino)

    def test_lo_confirmado_sin_cambio_no_entra_al_repaso(self):
        """Confirmar no necesita explicación: la explicación es que el
        sistema acertó."""
        self._anotar("<1@x>", "RUIDO")
        corrida.confirmar(self.cx, ["<1@x>"])
        self.assertEqual(corrida.correcciones_sin_motivo(self.cx), [])


class UnCorreoEntero(ConBase):
    """El formato uno a uno, que JP pidió a mitad de la corrida real.

    La línea de la tanda no alcanzaba para juzgar: sin fecha ni hora, en
    un hilo de ida y vuelta no se sabe quién contestó qué, y el cuerpo
    recortado a 600 caracteres obligaba a ir a la casilla para entender
    de qué se trataba. JP lo dijo con un caso: un correo de aspecto
    judicial que resultó ser trucho, y que tuvo que abrir en el correo
    real para no marcar un error que quizá no lo era.
    """

    def _fila(self, mid="<1@x>", categoria="TUYO", cuerpo="cuerpo largo"):
        c = entrante(mid, cuerpo=cuerpo)
        memoria.anotar(self.cx, c, categoria, "la nombra a JP")
        return memoria.obtener(self.cx, mid)

    def test_trae_la_fecha_y_la_hora(self):
        """Lo primero que faltaba: en un ida y vuelta, sin fecha no se
        sabe cuál mensaje es cuál."""
        texto, _ = corrida.armar_uno(self._fila(), 1, 39)
        self.assertIn("11 Aug 2026", texto)
        self.assertIn("09:00", texto)

    def test_trae_los_destinatarios(self):
        texto, _ = corrida.armar_uno(self._fila(), 1, 39)
        self.assertIn("jp@x", texto)

    def test_trae_el_cuerpo_entero_no_un_recorte_corto(self):
        largo = "linea de texto. " * 150          # ~2400 caracteres
        texto, _ = corrida.armar_uno(self._fila(cuerpo=largo), 1, 39)
        self.assertGreater(len(texto), 2000)

    def test_un_cuerpo_gigante_se_recorta_avisando(self):
        texto, _ = corrida.armar_uno(self._fila(cuerpo="x" * 9000), 1, 39)
        self.assertLessEqual(len(texto), corrida.LIMITE_TELEGRAM)
        self.assertIn("…", texto)

    def test_los_adjuntos_se_muestran_legibles(self):
        """correo.adjuntos() devuelve diccionarios (nombre, tipo, kb),
        no textos. Juntarlos con ', '.join() reventaba con TypeError en
        el primer correo que trajera un adjunto -- y habría reventado
        delante de JP, en el segundo de los 36."""
        fila = self._fila()
        fila["adjuntos"] = [{"nombre": "viajes-agosto.xlsx",
                             "tipo": "application/vnd.ms-excel", "kb": 42}]
        texto, _ = corrida.armar_uno(fila, 1, 39)
        self.assertIn("viajes-agosto.xlsx", texto)

    def test_sin_adjuntos_no_aparece_la_linea(self):
        texto, _ = corrida.armar_uno(self._fila(), 1, 39)
        self.assertNotIn("Adjuntos", texto)

    def test_dice_que_puso_el_sistema_y_por_que(self):
        texto, _ = corrida.armar_uno(self._fila(categoria="TUYO"), 1, 39)
        self.assertIn("TUYO", texto)
        self.assertIn("la nombra a JP", texto)

    def test_el_veredicto_va_antes_del_cuerpo_no_despues(self):
        """En el teléfono, con un correo de 4.000 caracteres, tener la
        pregunta al final obliga a scrollear el mail entero para saber
        qué se está preguntando. El veredicto va arriba, con la
        cabecera; el cuerpo abajo, para leer lo que haga falta."""
        fila = self._fila(cuerpo="CUERPODELCORREO " * 60)
        texto, _ = corrida.armar_uno(fila, 1, 39)
        self.assertLess(texto.index("Yo dije"), texto.index("CUERPODELCORREO"))

    def test_dice_en_cual_va_de_cuantos(self):
        texto, _ = corrida.armar_uno(self._fila(), 7, 39)
        self.assertIn("7", texto)
        self.assertIn("39", texto)

    def test_se_puede_mirar_sin_dictaminar_nada(self):
        """El defecto que JP marcó: «Corregir» lo obligaba a elegir una
        categoría para poder VER el correo. Acá el correo se muestra
        siempre, y «Está bien así» es un botón más -- mirar no cuesta
        una decisión."""
        _, teclado = corrida.armar_uno(self._fila(), 1, 39)
        botones = [b["text"] for fila in teclado["inline_keyboard"] for b in fila]
        self.assertTrue(any("bien" in b.lower() for b in botones), botones)

    def test_ofrece_las_seis_categorias(self):
        _, teclado = corrida.armar_uno(self._fila(), 1, 39)
        datos = [b["callback_data"] for fila in teclado["inline_keyboard"]
                 for b in fila]
        for cat in ("RUIDO", "DELEGADO", "ENZO", "NATALIA", "TUYO", "DUDA"):
            self.assertTrue(any(d.endswith(f"|{cat}") for d in datos), cat)


class LosAccionables(ConBase):
    def test_son_los_que_necesitan_criterio_de_jp(self):
        for mid, cat in [("<1@x>", "TUYO"), ("<2@x>", "RUIDO"),
                         ("<3@x>", "NATALIA"), ("<4@x>", "DELEGADO"),
                         ("<5@x>", "DUDA"), ("<6@x>", "ENZO")]:
            memoria.anotar(self.cx, entrante(mid), cat, "m")
        self.assertEqual(
            sorted(c["message_id"] for c in corrida.accionables(self.cx)),
            ["<1@x>", "<3@x>", "<5@x>", "<6@x>"])

    def test_entra_lo_que_jp_convirtio_en_accionable(self):
        """El DELEGADO que JP corrigió a TUYO en la revisión por tandas:
        el sistema no lo cree accionable, pero JP sí, y es justo el caso
        que más enseña."""
        memoria.anotar(self.cx, entrante("<1@x>"), "DELEGADO", "m")
        corrida.corregir(self.cx, "<1@x>", "TUYO")
        self.assertEqual([c["message_id"] for c in corrida.accionables(self.cx)],
                         ["<1@x>"])

    def test_no_entra_lo_que_jp_bajo_a_ruido(self):
        """Al revés: el sistema dijo TUYO, JP dijo RUIDO. Sigue siendo
        un caso que enseña -- se equivocó para el lado caro."""
        memoria.anotar(self.cx, entrante("<1@x>"), "TUYO", "m")
        corrida.corregir(self.cx, "<1@x>", "RUIDO")
        self.assertEqual([c["message_id"] for c in corrida.accionables(self.cx)],
                         ["<1@x>"])


class DeAUno(RevisionFalsa):
    """La conversación uno a uno sobre los accionables."""

    def _fila(self, mid, categoria="TUYO"):
        memoria.anotar(self.cx, entrante(mid), categoria, "la nombra a JP")
        return memoria.obtener(self.cx, mid)

    def _uno(self, *mids):
        filas = [self._fila(m) for m in mids]
        return corrida.UnoAUno(self.cx, filas, enviar=self.enviar)

    def _cat(self, u, n, valor):
        return {"callback_query": {"id": "1",
                                   "data": f"c|{u.sesion}-{n}|{valor}"}}

    def _ok(self, u, n):
        return {"callback_query": {"id": "1",
                                   "data": f"b|{u.sesion}-{n}|ok"}}

    def test_manda_uno_solo_y_espera(self):
        u = self._uno("<1@x>", "<2@x>")
        u.arrancar()
        self.assertEqual(len(self.enviados), 1)

    def test_esta_bien_asi_lo_confirma_y_pasa_al_siguiente(self):
        u = self._uno("<1@x>", "<2@x>")
        u.arrancar()
        u.atender(self._ok(u, 1))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"],
                         "TUYO")
        self.assertEqual(len(self.enviados), 2)

    def test_elegir_otra_categoria_corrige_y_pregunta_por_que(self):
        u = self._uno("<1@x>")
        u.arrancar()
        u.atender(self._cat(u, 1, "RUIDO"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"],
                         "RUIDO")
        self.assertIn("por qué", self.enviados[-1][0].lower())

    def test_el_por_que_queda_guardado_y_sigue_con_el_siguiente(self):
        u = self._uno("<1@x>", "<2@x>")
        u.arrancar()
        u.atender(self._cat(u, 1, "RUIDO"))
        u.atender(self._texto("es spam disfrazado de cédula judicial"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["explicacion"],
                         "es spam disfrazado de cédula judicial")
        self.assertEqual(len(self.enviados), 3)

    def test_confirmar_la_misma_categoria_tambien_pregunta_por_que(self):
        """Si JP elige a mano la categoría que el sistema ya había
        puesto, está diciendo algo más que «está bien»: está diciendo
        «está bien POR ESTO». Ese porqué vale igual."""
        u = self._uno("<1@x>")
        u.arrancar()
        u.atender(self._cat(u, 1, "TUYO"))
        self.assertIn("por qué", self.enviados[-1][0].lower())

    def test_se_puede_saltear_el_por_que(self):
        u = self._uno("<1@x>", "<2@x>")
        u.arrancar()
        u.atender(self._cat(u, 1, "RUIDO"))
        u.atender(self._texto("-"))
        self.assertEqual(memoria.obtener(self.cx, "<1@x>")["categoria_jp"],
                         "RUIDO")
        self.assertEqual(len(self.enviados), 3)

    def test_al_terminar_avisa(self):
        u = self._uno("<1@x>")
        u.arrancar()
        u.atender(self._ok(u, 1))
        self.assertTrue(u.termino)

    def test_un_boton_de_otra_sesion_no_hace_nada(self):
        """La misma falla que ya apareció con las tandas: sin token de
        sesión, el botón 3 de una corrida vieja contesta por el 3 de
        ahora."""
        u = self._uno("<1@x>")
        u.arrancar()
        u.atender({"callback_query": {"id": "1", "data": "b|9999-1|ok"}})
        self.assertIsNone(memoria.obtener(self.cx, "<1@x>")["categoria_jp"])

    def test_un_boton_de_otro_correo_de_esta_sesion_tampoco(self):
        """El 2 no puede contestar por el 1: JP scrollea, y el mensaje
        de arriba sigue teniendo sus botones."""
        u = self._uno("<1@x>", "<2@x>")
        u.arrancar()
        u.atender(self._ok(u, 2))
        self.assertIsNone(memoria.obtener(self.cx, "<1@x>")["categoria_jp"])


class LosArgumentos(unittest.TestCase):
    """_argumentos() no tenía un solo test, y se rompió apenas se le
    agregó una opción: --uno-a-uno seteaba una clave que el diccionario
    de defaults no tenía, así que TODA invocación que no fuera
    --uno-a-uno reventaba con KeyError en main(). Incluida la corrida
    completa sin opciones.

    Lo caro no es el KeyError -- es que salta recién al arrancar el
    programa de verdad, con JP esperando la tanda del otro lado.
    """

    def test_sin_opciones_hace_las_dos_fases_y_no_el_uno_a_uno(self):
        o = corrida._argumentos([])
        self.assertEqual((o["clasificar"], o["revisar"], o["uno_a_uno"]),
                         (True, True, False))

    def test_solo_clasificar_no_revisa(self):
        o = corrida._argumentos(["--solo-clasificar"])
        self.assertEqual((o["clasificar"], o["revisar"], o["uno_a_uno"]),
                         (True, False, False))

    def test_solo_revisar_no_clasifica(self):
        o = corrida._argumentos(["--solo-revisar"])
        self.assertEqual((o["clasificar"], o["revisar"], o["uno_a_uno"]),
                         (False, True, False))

    def test_uno_a_uno_no_clasifica_y_no_manda_tandas(self):
        o = corrida._argumentos(["--uno-a-uno"])
        self.assertEqual((o["clasificar"], o["uno_a_uno"]), (False, True))

    def test_toda_invocacion_define_las_mismas_claves(self):
        """La raíz del bug: una opción que agrega una clave que las
        demás no tienen. main() lee todas en cualquier camino."""
        variantes = [[], ["--solo-clasificar"], ["--solo-revisar"],
                     ["--uno-a-uno"], ["--tamano", "5"],
                     ["--desde", "2026-08-11"]]
        claves = [set(corrida._argumentos(v)) for v in variantes]
        self.assertEqual(len(set(map(frozenset, claves))), 1, claves)

    def test_la_fecha_define_los_nombres_de_archivo(self):
        o = corrida._argumentos(["--desde", "2026-08-11"])
        self.assertEqual(o["base"], "datos/corrida-20260811.db")
        self.assertEqual(o["salida"], "datos/corrida-20260811.json")

    def test_una_opcion_desconocida_no_arranca_nada(self):
        with self.assertRaises(SystemExit):
            corrida._argumentos(["--inventada"])


class LaTandaTieneQueAlcanzarParaJuzgar(ConBase):
    """Lo que JP reclamó dos veces: la línea de la tanda decía remitente
    y asunto y nada más.

    «no sé si es de hoy o de hace una semana. Además estaría bueno que
    muestre una pequeña parte del cuerpo como para poder entender el
    mail sin tener que buscarlo en el correo». Es el mismo reclamo que
    ya había motivado correo.fecha_legible() -- cuyo docstring dice "JP
    se topó con un correo del jueves anterior sin ninguna forma de
    saberlo" -- y que la tanda no usaba.
    """

    def _caso(self, mid="<1@x>", cuerpo="El cuerpo del correo, que hay "
                                        "que poder espiar sin ir a la casilla."):
        memoria.anotar(self.cx, entrante(mid, cuerpo=cuerpo), "RUIDO", "promoción")
        return corrida.pendientes_de_revisar(self.cx)[0]

    def test_la_cola_trae_la_fecha_y_el_cuerpo(self):
        c = self._caso()
        self.assertIn("fecha", c)
        self.assertIn("cuerpo", c)

    def test_la_linea_dice_cuando_llego_y_hace_cuanto(self):
        texto, _ = corrida.armar_tanda([self._caso()], 1, 1, "0007")
        self.assertIn("ago", texto)          # "mar 11 ago 09:00 · hace N…"
        self.assertIn("hace", texto)

    def test_la_linea_deja_espiar_el_cuerpo(self):
        texto, _ = corrida.armar_tanda([self._caso()], 1, 1, "0007")
        self.assertIn("hay que poder espiar", texto)

    def test_con_doce_correos_largos_no_se_omite_ninguno(self):
        """El recorte tiene que caer sobre el cuerpo espiado, nunca
        sobre la lista: un correo que no se lista es un número que JP no
        puede corregir."""
        lote = [self._caso(f"<{n}@x>", cuerpo="palabra " * 300)
                for n in range(1, 13)]
        texto, _ = corrida.armar_tanda(lote, 1, 7, "0007")
        self.assertLessEqual(len(texto), corrida.LIMITE_TELEGRAM)
        for n in range(1, 13):
            self.assertIn(f"{n}.", texto, f"falta el número {n}")
        self.assertNotIn("sin listar", texto)

    def test_un_cuerpo_vacio_no_rompe_la_linea(self):
        texto, _ = corrida.armar_tanda([self._caso(cuerpo="")], 1, 1, "0007")
        self.assertIn("1.", texto)
