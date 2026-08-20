import email.utils
import os
import unittest
import unittest.mock as mock
from datetime import datetime, timedelta, timezone

import correo
from tests.material import mensajes


class _IMAPFalso:
    """Doble de imaplib.IMAP4_SSL que registra los comandos UID recibidos.

    Existe porque los tests anteriores de mover_a/marcar_leido/
    devolver_a_bandeja mockeaban `secretaria.correo.mover_a` entero: nunca
    llegaban a ejecutar una sola línea de correo.py, y por ese agujero pasó
    el bug real (COPY que devuelve NO sin excepción, seguido igual de
    STORE +Deleted y EXPUNGE, borrando el correo sin haberlo copiado a
    ningún lado). Este doble deja pasar el código real y solo finge la
    respuesta del servidor.

    `copy_ok=False` simula justo eso: un COPY que el servidor rechaza con
    NO -imaplib únicamente levanta excepción ante un BAD, un NO vuelve en
    silencio- para confirmar que el código de acá no sigue de largo como
    si hubiera funcionado. `store_ok` y `expunge_ok` hacen lo mismo para
    esos dos comandos -el hermano del mismo bug, encontrado en la
    re-revisión: el COPY se chequeaba, el STORE y el EXPUNGE no.

    `store_ok` puede ser un bool (aplica a todos los STORE por igual) o
    una lista de bools consumida en orden: devolver_a_bandeja hace DOS
    STORE distintos (uno para sacar \\Seen, otro para marcar \\Deleted),
    y algunos tests necesitan que fallen en momentos distintos.
    """

    def __init__(self, uid_buscado=b"77", copy_ok=True,
                 store_ok=True, expunge_ok=True):
        self.comandos = []
        self.seleccionadas = []
        self.uid_buscado = uid_buscado
        self.copy_ok = copy_ok
        self.expunge_ok = expunge_ok
        if isinstance(store_ok, (list, tuple)):
            self._store_ok_secuencia = list(store_ok)
            self._store_ok_fijo = None
        else:
            self._store_ok_secuencia = None
            self._store_ok_fijo = store_ok

    def _siguiente_store_ok(self):
        if self._store_ok_secuencia is not None:
            # Una vez consumida la lista, sigue confirmando OK -ningún
            # test de acá necesita más de dos STORE por llamada.
            return (self._store_ok_secuencia.pop(0)
                    if self._store_ok_secuencia else True)
        return self._store_ok_fijo

    def login(self, *a, **k):
        pass

    def select(self, carpeta, readonly=True):
        self.seleccionadas.append(carpeta)
        return ("OK", [b"1"])

    def uid(self, comando, *args):
        self.comandos.append((comando, *args))
        if comando == "SEARCH":
            return ("OK", [self.uid_buscado if self.uid_buscado else b""])
        if comando == "COPY":
            return (("OK", [None]) if self.copy_ok
                    else ("NO", [b"[TRYCREATE] no existe la carpeta"]))
        if comando == "STORE":
            return (("OK", [None]) if self._siguiente_store_ok()
                    else ("NO", [b"no se pudo marcar"]))
        if comando == "EXPUNGE":
            return (("OK", [None]) if self.expunge_ok
                    else ("NO", [b"no se pudo expurgar"]))
        raise AssertionError(f"comando UID no esperado por el doble: {comando}")

    def logout(self):
        pass


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


class MoverA(unittest.TestCase):
    """Crítico 1 + Crítico 3: mover_a() nunca puede borrar de INBOX un
    correo que no se copió de verdad."""

    def test_con_copy_ok_borra_de_inbox_en_orden(self):
        fake = _IMAPFalso(copy_ok=True)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.mover_a("<x@y>", "INBOX.Ruido")
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in fake.comandos],
                         ["SEARCH", "COPY", "STORE", "EXPUNGE"])

    def test_con_copy_no_no_borra_nada(self):
        """El bug real: un COPY rechazado con NO no tira excepción, así
        que sin este chequeo el código seguía con STORE +Deleted y
        EXPUNGE igual, borrando el correo sin haberlo copiado a ningún
        lado. Lo que no puede pasar -y es lo que sigue probando este
        test- es que se borre algo. (Antes esto devolvía False; ahora
        levanta CopiaRechazada, ver el test de abajo: el que no se
        borre nada no cambió.)"""
        fake = _IMAPFalso(copy_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.CopiaRechazada):
                correo.mover_a("<x@y>", "INBOX.Ruido")
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["SEARCH", "COPY"])
        self.assertNotIn("STORE", nombres)
        self.assertNotIn("EXPUNGE", nombres)

    def test_un_copy_rechazado_no_se_confunde_con_un_correo_que_no_esta(self):
        """Los dos casos devolvían False y no son la misma cosa: que el
        mensaje ya no esté en INBOX es benigno -alguien lo movió, o ya se
        archivó- y no hay nada para reintentar ni para avisar; que el
        servidor rechace el COPY (cuota agotada en Ruido, carpeta
        renombrada, permisos) es una falla real que hay que reintentar y,
        si no se arregla, contarle a JP. Leerlos igual dejaba el segundo
        caso marcado como resuelto, sin reintento y sin aviso: un correo
        sin archivar del que nadie se entera nunca."""
        vacio = _IMAPFalso(uid_buscado=None)
        with mock.patch.object(correo, "abrir_buzon", return_value=vacio):
            self.assertIs(correo.mover_a("<no-existe@x>", "INBOX.Ruido"),
                          False)

        rechaza = _IMAPFalso(copy_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=rechaza):
            with self.assertRaises(correo.CopiaRechazada):
                correo.mover_a("<x@y>", "INBOX.Ruido")

    def test_el_error_repite_lo_que_contesto_el_servidor(self):
        """"No se pudo copiar" no le sirve a nadie; "[TRYCREATE] no existe
        la carpeta" o un OVERQUOTA se entienden y se arreglan. Ese texto
        es el que termina en el aviso de Telegram."""
        fake = _IMAPFalso(copy_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.CopiaRechazada) as caso:
                correo.mover_a("<x@y>", "INBOX.Ruido")
        self.assertIn("TRYCREATE", str(caso.exception))
        self.assertIn("INBOX.Ruido", str(caso.exception))

    def test_busca_por_message_id_no_por_numero(self):
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            correo.mover_a("<abc@x>", "INBOX.Ruido")
        busqueda = fake.comandos[0]
        self.assertEqual(busqueda[0], "SEARCH")
        self.assertIn("HEADER", busqueda)
        self.assertIn("Message-ID", busqueda)

    def test_usa_uid_expunge_nunca_expunge_a_secas(self):
        """Un EXPUNGE sin UID borra TODOS los mensajes marcados de la
        carpeta, no solo el nuestro. El doble solo entiende comandos que
        pasan por M.uid(...); si el código llamara a M.expunge() directo,
        el doble no tiene ese método y la llamada explota."""
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.mover_a("<abc@x>", "INBOX.Ruido")
        self.assertTrue(ok)
        self.assertIn("EXPUNGE", [c[0] for c in fake.comandos])

    def test_si_no_encuentra_el_correo_no_toca_nada(self):
        fake = _IMAPFalso(uid_buscado=None)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.mover_a("<no-existe@x>", "INBOX.Ruido")
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["SEARCH"])

    def test_con_store_no_queda_a_medias(self):
        """El hermano del Crítico 1: el COPY se chequeaba, el STORE no.
        Con el COPY ya confirmado, un STORE +Deleted rechazado con NO no
        puede devolver True (no terminó) ni False (sí se copió algo):
        el mensaje ya existe en destino. Tampoco sigue de largo hacia el
        EXPUNGE -eso expurgaría sin nada marcado, un no-op que sólo
        agrega ruido al log."""
        fake = _IMAPFalso(copy_ok=True, store_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.mover_a("<x@y>", "INBOX.Ruido")
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["SEARCH", "COPY", "STORE"])
        self.assertNotIn("EXPUNGE", nombres)

    def test_con_expunge_no_queda_a_medias(self):
        fake = _IMAPFalso(copy_ok=True, store_ok=True, expunge_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.mover_a("<x@y>", "INBOX.Ruido")
        self.assertEqual([c[0] for c in fake.comandos],
                         ["SEARCH", "COPY", "STORE", "EXPUNGE"])


class BorrarElOriginal(unittest.TestCase):
    """El reintento del caso a medias: la copia YA está en el destino, así
    que lo único que falta es sacar el original de INBOX.

    Si este reintento volviera a copiar -o sea, si se reintentara mover_a()
    entero- cada vuelta del ciclo agregaría un duplicado nuevo en Ruido, y
    con una falla sostenida del lado del borrado (cuota agotada en esa
    carpeta, por ejemplo) eso acumula varias copias por hora sin que nadie
    se entere. Por eso esta función existe aparte y no hace COPY nunca."""

    def test_borra_sin_volver_a_copiar(self):
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.borrar_el_original("<x@y>")
        self.assertTrue(ok)
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["SEARCH", "STORE", "EXPUNGE"])
        self.assertNotIn("COPY", nombres)
        self.assertEqual(fake.comandos[1][2:], ("+FLAGS", "(\\Deleted)"))

    def test_busca_por_message_id_no_por_numero(self):
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            correo.borrar_el_original("<abc@x>")
        busqueda = fake.comandos[0]
        self.assertEqual(busqueda[0], "SEARCH")
        self.assertIn("HEADER", busqueda)
        self.assertIn("Message-ID", busqueda)

    def test_si_ya_no_esta_en_inbox_no_hay_nada_que_borrar(self):
        """No es un fallo: el original ya no está donde se lo iba a borrar
        -el EXPUNGE anterior sí había salido y no nos enteramos, o JP lo
        borró a mano- y la copia sigue en Ruido. El estado final es el que
        se quería."""
        fake = _IMAPFalso(uid_buscado=None)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.borrar_el_original("<no-existe@x>")
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["SEARCH"])

    def test_si_el_store_no_confirma_sigue_a_medias(self):
        fake = _IMAPFalso(store_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.borrar_el_original("<x@y>")
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["SEARCH", "STORE"])
        self.assertNotIn("EXPUNGE", nombres)

    def test_si_el_expunge_no_confirma_sigue_a_medias(self):
        fake = _IMAPFalso(expunge_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.borrar_el_original("<x@y>")
        self.assertEqual([c[0] for c in fake.comandos],
                         ["SEARCH", "STORE", "EXPUNGE"])

    def test_usa_uid_expunge_nunca_expunge_a_secas(self):
        """El doble sólo entiende comandos que pasan por M.uid(...): si el
        código llamara a M.expunge() directo -que borra TODOS los mensajes
        marcados de la carpeta, no sólo el nuestro- no existe ese método y
        la llamada explota sola."""
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            self.assertTrue(correo.borrar_el_original("<x@y>"))
        self.assertIn("EXPUNGE", [c[0] for c in fake.comandos])


class MarcarLeido(unittest.TestCase):
    def test_marca_seen_buscando_por_message_id(self):
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.marcar_leido("<x@y>")
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["SEARCH", "STORE"])
        store = fake.comandos[1]
        self.assertIn("+FLAGS", store)
        self.assertIn("(\\Seen)", store)

    def test_si_no_encuentra_el_correo_no_toca_nada(self):
        fake = _IMAPFalso(uid_buscado=None)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.marcar_leido("<no-existe@x>")
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["SEARCH"])

    def test_si_el_store_no_confirma_devuelve_false(self):
        """Mismo patrón que en mover_a/devolver_a_bandeja: acá no hay
        COPY de por medio, así que un STORE que el servidor no confirma
        no deja ningún duplicado -alcanza con False, sin necesidad de
        OperacionAMedias."""
        fake = _IMAPFalso(store_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.marcar_leido("<x@y>")
        self.assertFalse(ok)


class DevolverABandeja(unittest.TestCase):
    """Crítico 1 (mismo patrón que mover_a) + Importante 4: el mensaje
    tiene que quedar SIN LEER en INBOX incluso si el proceso se corta a
    mitad de camino."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "IMAP_HOST": "imap.test", "IMAP_PORT": "993",
            "IMAP_USER": "u", "IMAP_PASSWORD": "p"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_con_copy_ok_queda_sin_leer_en_inbox(self):
        fake = _IMAPFalso(copy_ok=True)
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            ok = correo.devolver_a_bandeja("<r@x>")
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in fake.comandos],
                         ["SEARCH", "STORE", "COPY", "STORE", "EXPUNGE"])
        self.assertEqual(fake.seleccionadas, ["INBOX.Ruido"])
        # el \Seen se saca ANTES del COPY -- así la copia nace sin leer,
        # sin depender de un segundo paso después de mover el mensaje.
        primer_store = fake.comandos[1]
        self.assertIn("-FLAGS", primer_store)
        self.assertIn("(\\Seen)", primer_store)
        segundo_store = fake.comandos[3]
        self.assertIn("+FLAGS", segundo_store)
        self.assertIn("(\\Deleted)", segundo_store)

    def test_con_copy_no_el_correo_sigue_en_ruido(self):
        """Mismo bug que mover_a: si el COPY a INBOX no se confirma, el
        correo no puede desaparecer de Ruido sin haber llegado a ningún
        lado."""
        fake = _IMAPFalso(copy_ok=False)
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            ok = correo.devolver_a_bandeja("<r@x>")
        self.assertFalse(ok)
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["SEARCH", "STORE", "COPY"])
        self.assertNotIn("EXPUNGE", nombres)

    def test_busca_por_message_id_no_por_numero(self):
        fake = _IMAPFalso()
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            correo.devolver_a_bandeja("<r@x>")
        busqueda = fake.comandos[0]
        self.assertEqual(busqueda[0], "SEARCH")
        self.assertIn("HEADER", busqueda)
        self.assertIn("Message-ID", busqueda)

    def test_si_no_encuentra_el_correo_no_toca_nada(self):
        fake = _IMAPFalso(uid_buscado=None)
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            ok = correo.devolver_a_bandeja("<no-existe@x>")
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["SEARCH"])

    def test_si_el_store_menos_seen_no_confirma_no_copia_nada(self):
        """Todavía no se tocó Ruido de ningún modo -el COPY ni siquiera
        se intentó-, así que un NO acá es simplemente False: seguro
        reintentar desde cero."""
        fake = _IMAPFalso(store_ok=False)
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            ok = correo.devolver_a_bandeja("<r@x>")
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["SEARCH", "STORE"])

    def test_con_store_deleted_no_queda_a_medias(self):
        """El hermano del Crítico 1, acá también: con el COPY a INBOX ya
        confirmado -el mensaje ya existe ahí, sin leer-, un STORE
        +Deleted que el servidor no confirma en Ruido no puede
        reportarse como éxito ni como "no pasó nada"."""
        fake = _IMAPFalso(copy_ok=True, store_ok=[True, False])
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.devolver_a_bandeja("<r@x>")
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["SEARCH", "STORE", "COPY", "STORE"])
        self.assertNotIn("EXPUNGE", nombres)

    def test_con_expunge_no_queda_a_medias(self):
        fake = _IMAPFalso(copy_ok=True, store_ok=True, expunge_ok=False)
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.devolver_a_bandeja("<r@x>")
        self.assertEqual([c[0] for c in fake.comandos],
                         ["SEARCH", "STORE", "COPY", "STORE", "EXPUNGE"])


if __name__ == "__main__":
    unittest.main()
