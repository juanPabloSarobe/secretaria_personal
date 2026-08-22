import email.utils
import os
import unittest
import unittest.mock as mock
from datetime import datetime, timedelta, timezone

import correo
from tests.buzon_falso import BuzonFalso, Mensaje, correo_de, hace
from tests.material import mensajes

MID = "<x@y>"
UID = b"77"
VALIDEZ = "1000"
FECHA = "Mon, 17 Aug 2026 09:00:00 -0300"


def un_correo(mid=MID, uid=UID, uidvalidity=VALIDEZ,
              de="promo@ejemplo.com", asunto="Promo", fecha=FECHA):
    """El correo tal como lo dejó traer_nuevos(): con UID y UIDVALIDITY.

    Las funciones de escritura reciben el correo entero y no un
    Message-ID, porque el Message-ID no siempre existe -y cuando no
    existe, la identidad es un `sha:...` que el servidor no entiende-.
    El UID sí lo entiende, y el UIDVALIDITY es lo que dice hasta cuándo
    ese UID significa algo.
    """
    return {"message_id": mid, "uid": uid.decode() if uid else "",
            "uidvalidity": uidvalidity, "de": de, "para": "jp@x", "cc": "",
            "asunto": asunto, "fecha": fecha, "cuerpo": "cuerpo",
            "adjuntos": []}


class _IMAPFalso(BuzonFalso):
    """Doble de imaplib.IMAP4_SSL con un mensaje adentro.

    Existe porque los tests anteriores de mover_a/marcar_leido/
    devolver_a_bandeja mockeaban `secretaria.correo.mover_a` entero: nunca
    llegaban a ejecutar una sola línea de correo.py, y por ese agujero pasó
    el bug real (COPY que devuelve NO sin excepción, seguido igual de
    STORE +Deleted y EXPUNGE, borrando el correo sin haberlo copiado a
    ningún lado). Este doble deja pasar el código real y sólo finge la
    respuesta del servidor.

    Antes no modelaba ningún buzón: contestaba que sí a CUALQUIER
    búsqueda, y por eso no vio el peor hallazgo de la ronda 6 -los correos
    sin Message-ID nunca se archivaban, porque el SEARCH que los buscaba
    no encuentra nada en un servidor de verdad-. Ahora hereda de
    BuzonFalso, que tiene carpetas, UIDs, UIDVALIDITY y búsqueda de
    verdad, y sólo agrega la comodidad que usan los tests viejos: un
    único mensaje, en `carpeta` (INBOX salvo que se diga otra cosa) y en
    ninguna otra. Que el destino esté vacío es lo que hace que la
    pregunta "¿ya está copiado allá?" conteste que no, como corresponde
    en el caso normal.

    `copy_ok=False` simula un COPY que el servidor rechaza con NO
    -imaplib únicamente levanta excepción ante un BAD, un NO vuelve en
    silencio- para confirmar que el código no sigue de largo como si
    hubiera funcionado. `store_ok` y `expunge_ok` hacen lo mismo para
    esos dos comandos. `store_ok` puede ser un bool (todos los STORE por
    igual) o una lista consumida en orden: devolver_a_bandeja hace DOS
    STORE distintos y algunos tests necesitan que fallen en momentos
    distintos.

    `uid_buscado=None` significa que el mensaje no está en ninguna
    carpeta.
    """

    def __init__(self, uid_buscado=UID, copy_ok=True, store_ok=True,
                 expunge_ok=True, mid=MID, de="promo@ejemplo.com",
                 asunto="Promo", fecha=FECHA, carpeta="INBOX"):
        super().__init__(uidvalidity=VALIDEZ)
        self.uid_buscado = uid_buscado
        if uid_buscado:
            self.carpetas[carpeta].append(
                Mensaje(uid_buscado, de, asunto, fecha, mid))
        self.copy_ok = copy_ok
        self.expunge_ok = expunge_ok
        if isinstance(store_ok, (list, tuple)):
            self.plan["STORE"] = ["OK" if x else "NO" for x in store_ok]
        else:
            self.store_ok = store_ok

    # `copy_ok`/`store_ok`/`expunge_ok` se leen en cada comando (algunos
    # tests los cambian a mitad de camino, para que el servidor se
    # recupere) en vez de fijarse una sola vez en el constructor.
    def _resultado(self, comando):
        cola = self.plan.get(comando)
        if cola:
            return cola.pop(0)
        return {"COPY": "OK" if self.copy_ok else "NO",
                "STORE": "OK" if getattr(self, "store_ok", True) else "NO",
                "EXPUNGE": "OK" if self.expunge_ok else "NO"}.get(comando, "OK")




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
    correo que no se copió de verdad.

    La secuencia de comandos cambió en la ronda 6 y por eso cambiaron las
    expectativas de varios de estos tests (ninguno se borró). Ahora es:
    FETCH para confirmar que el UID guardado es el correo que creemos,
    SEARCH en el destino para no copiar algo que ya está copiado, y recién
    después COPY, STORE y EXPUNGE. Los dos comandos nuevos no son
    decoración: el primero es lo que permite archivar los correos sin
    Message-ID, y el segundo es lo que evita el duplicado cuando la red se
    corta justo después de un COPY que sí salió.
    """

    def test_con_copy_ok_borra_de_inbox_en_orden(self):
        fake = _IMAPFalso(copy_ok=True)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.mover_a(un_correo(), "INBOX.Ruido")
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in fake.comandos],
                         ["FETCH", "SEARCH", "COPY", "STORE", "EXPUNGE"])

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
                correo.mover_a(un_correo(), "INBOX.Ruido")
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["FETCH", "SEARCH", "COPY"])
        self.assertNotIn("STORE", nombres)
        self.assertNotIn("EXPUNGE", nombres)
        self.assertEqual(fake.cuenta("INBOX"), 1)

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
            self.assertIs(correo.mover_a(un_correo("<no-existe@x>"),
                                         "INBOX.Ruido"), False)

        rechaza = _IMAPFalso(copy_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=rechaza):
            with self.assertRaises(correo.CopiaRechazada):
                correo.mover_a(un_correo(), "INBOX.Ruido")

    def test_el_error_repite_lo_que_contesto_el_servidor(self):
        """"No se pudo copiar" no le sirve a nadie; "[TRYCREATE] no existe
        la carpeta" o un OVERQUOTA se entienden y se arreglan. Ese texto
        es el que termina en el aviso de Telegram."""
        fake = _IMAPFalso(copy_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.CopiaRechazada) as caso:
                correo.mover_a(un_correo(), "INBOX.Ruido")
        self.assertIn("TRYCREATE", str(caso.exception))
        self.assertIn("INBOX.Ruido", str(caso.exception))

    def test_busca_por_message_id_no_por_numero(self):
        """Nunca por número de secuencia, que se corre en cuanto se borra
        algo de la carpeta. El UID sí sirve -no se corre- y es lo primero
        que se usa; la búsqueda que queda, la del destino, va por
        Message-ID."""
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            correo.mover_a(un_correo(), "INBOX.Ruido")
        busqueda = [c for c in fake.comandos if c[0] == "SEARCH"][0]
        self.assertIn("HEADER", busqueda)
        self.assertIn("Message-ID", busqueda)
        # y lo que se copia es el UID confirmado, no un número de orden
        copia = [c for c in fake.comandos if c[0] == "COPY"][0]
        self.assertEqual(copia[1], UID)

    def test_usa_uid_expunge_nunca_expunge_a_secas(self):
        """Un EXPUNGE sin UID borra TODOS los mensajes marcados de la
        carpeta, no solo el nuestro. El doble solo entiende comandos que
        pasan por M.uid(...); si el código llamara a M.expunge() directo,
        el doble no tiene ese método y la llamada explota."""
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.mover_a(un_correo(), "INBOX.Ruido")
        self.assertTrue(ok)
        self.assertIn("EXPUNGE", [c[0] for c in fake.comandos])

    def test_si_no_encuentra_el_correo_no_toca_nada(self):
        fake = _IMAPFalso(uid_buscado=None)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.mover_a(un_correo("<no-existe@x>"), "INBOX.Ruido")
        self.assertFalse(ok)
        # Sólo pregunta: el FETCH del UID guardado (que ya no existe), la
        # búsqueda por Message-ID en INBOX y la del destino -si estuviera
        # copiado allá, la mudanza estaría terminada y no sería False-.
        # Ninguna escritura.
        self.assertEqual([c[0] for c in fake.comandos],
                         ["FETCH", "SEARCH", "SEARCH"])

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
                correo.mover_a(un_correo(), "INBOX.Ruido")
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["FETCH", "SEARCH", "COPY", "STORE"])
        self.assertNotIn("EXPUNGE", nombres)

    def test_con_expunge_no_queda_a_medias(self):
        fake = _IMAPFalso(copy_ok=True, store_ok=True, expunge_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.mover_a(un_correo(), "INBOX.Ruido")
        self.assertEqual([c[0] for c in fake.comandos],
                         ["FETCH", "SEARCH", "COPY", "STORE", "EXPUNGE"])


class SeConfirmaAntesDeEscribir(unittest.TestCase):
    """El invariante de la ronda 6: nunca se escribe sobre un mensaje sin
    haber confirmado que es el que creíamos.

    El fondo del hallazgo era que se estaban mezclando dos
    identificadores. El `sha:...` que inventa identidad() sirve para
    reconocer un correo entre corridas -es la clave de la base- pero no
    existe del lado del servidor: buscarlo con SEARCH HEADER Message-ID no
    encuentra nada nunca. El UID sí existe, no se corre como los números
    de secuencia, y traer_nuevos() ya lo traía. Ahora se usa el UID para
    ubicar y las cabeceras para confirmar, en vez del Message-ID para las
    dos cosas."""

    def _archivar(self, buzon, c):
        with mock.patch.object(correo, "abrir_buzon", return_value=buzon):
            return correo.mover_a(c, "INBOX.Ruido")

    def test_un_correo_sin_message_id_se_archiva(self):
        """El hallazgo, en una línea: los remitentes automáticos -o sea el
        ruido, que es lo que más queremos archivar- suelen no mandar
        Message-ID. Antes de esto, mover_a buscaba `HEADER Message-ID
        "sha:..."`, no encontraba nada, devolvía False y el correo se
        quedaba en la bandeja para siempre: cero reintentos, cero avisos."""
        buzon = BuzonFalso()
        m = buzon.agregar("INBOX", de="no-reply@facturas.com",
                          asunto="Tu resumen de agosto", message_id="")
        c = correo_de(m)
        self.assertTrue(correo.identidad(c).startswith("sha:"))
        self.assertTrue(self._archivar(buzon, c))
        self.assertEqual(buzon.cuenta("INBOX"), 0)
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)

    def test_no_se_escribe_sobre_otro_mensaje_si_el_uid_quedo_viejo(self):
        """Si el UID guardado hoy apunta a otro correo, no se lo toca. Es
        el peligro concreto de ubicar por número: el mensaje que se archiva
        o se borra podría ser cualquiera."""
        buzon = BuzonFalso()
        mio = buzon.agregar("INBOX", de="promo@ejemplo.com",
                            asunto="Promo", message_id="<mio@x>")
        ajeno = buzon.agregar("INBOX", de="cliente@importante.com",
                              asunto="Contrato firmado",
                              message_id="<ajeno@x>")
        # El correo que la base recuerda dice tener el UID del OTRO.
        c = correo_de(mio)
        c["uid"] = ajeno.uid.decode()
        self._archivar(buzon, c)
        # El del cliente sigue intacto en INBOX y no se copió a Ruido.
        en_inbox = [m.message_id for m in buzon.carpetas["INBOX"]]
        self.assertIn("<ajeno@x>", en_inbox)
        self.assertNotIn("<ajeno@x>",
                         [m.message_id for m in buzon.carpetas["INBOX.Ruido"]])

    def test_si_el_uid_es_de_otro_el_correo_igual_se_ubica_por_identidad(self):
        """Y no sólo no toca el equivocado: encuentra el correcto. El UID
        es la primera opción, no la única."""
        buzon = BuzonFalso()
        mio = buzon.agregar("INBOX", de="promo@ejemplo.com", asunto="Promo",
                            fecha=hace(0), message_id="")
        ajeno = buzon.agregar("INBOX", de="cliente@importante.com",
                              asunto="Contrato", fecha=hace(0),
                              message_id="<ajeno@x>")
        c = correo_de(mio)
        c["uid"] = ajeno.uid.decode()
        self.assertTrue(self._archivar(buzon, c))
        self.assertEqual([m.message_id for m in buzon.carpetas["INBOX"]],
                         ["<ajeno@x>"])
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)

    def test_si_cambio_el_uidvalidity_no_se_toca_nada(self):
        """UIDVALIDITY distinto significa que los UID se repartieron de
        nuevo: el 1043 de hoy puede ser cualquier otro correo. Eso hay que
        decirlo -la excepción llega hasta el aviso a JP- y no arreglarlo
        adivinando."""
        buzon = BuzonFalso()
        m = buzon.agregar("INBOX", message_id="<uno@x>")
        c = correo_de(m)
        buzon.uidvalidity["INBOX"] = "9999"     # la carpeta se recreó
        with mock.patch.object(correo, "abrir_buzon", return_value=buzon):
            with self.assertRaises(correo.UidsVencidos):
                correo.mover_a(c, "INBOX.Ruido")
        self.assertEqual([x[0] for x in buzon.comandos], [])
        self.assertEqual(buzon.cuenta("INBOX"), 1)
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 0)

    def test_sin_uid_guardado_todavia_se_ubica_por_message_id(self):
        """Las filas viejas de la base no tienen UID. Siguen andando: el
        Message-ID sigue siendo una forma válida de ubicar, sólo que no
        la única."""
        buzon = BuzonFalso()
        m = buzon.agregar("INBOX", message_id="<viejo@x>")
        c = correo_de(m, con_uid=False)
        self.assertTrue(self._archivar(buzon, c))
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)

    def test_sin_uid_y_sin_message_id_no_adivina(self):
        """Sin nada con qué preguntarle al servidor, la respuesta correcta
        no es False -que quien llama lee como "listo, nada que hacer"-
        sino una excepción, que reintenta y termina avisándole a JP."""
        buzon = BuzonFalso()
        c = {"message_id": "", "uid": "", "uidvalidity": "",
             "de": "", "asunto": "Algo", "fecha": "no es una fecha"}
        with mock.patch.object(correo, "abrir_buzon", return_value=buzon):
            with self.assertRaises(correo.IdentidadIncierta):
                correo.mover_a(c, "INBOX.Ruido")
        self.assertEqual(buzon.cuenta("INBOX"), 0)


class NoDuplicaEnElDestino(unittest.TestCase):
    """Un COPY que sale bien y una conexión que se corta antes de que
    llegue el OK son indistinguibles desde el cliente: los dos se ven como
    un OSError. Adivinar por el tipo de excepción no alcanzaba, y el
    reintento volvía a copiar. Ahora se le pregunta al servidor."""

    def test_si_no_esta_en_inbox_pero_si_en_el_destino_ya_esta_archivado(self):
        """False significa "no hay nada que hacer" y quien llama lo anota
        como sin archivar. Si la copia está en Ruido y el original ya no
        está en INBOX, la mudanza terminó: decir False dejaría la base
        diciendo lo contrario de lo que muestra la casilla."""
        buzon = BuzonFalso()
        m = buzon.agregar("INBOX.Ruido", message_id="<terminado@x>")
        c = correo_de(m)
        with mock.patch.object(correo, "abrir_buzon", return_value=buzon):
            self.assertTrue(correo.mover_a(c, "INBOX.Ruido"))
        self.assertEqual(len(buzon.de_comando("COPY")), 0)
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)

    def test_si_ya_esta_en_el_destino_no_lo_copia_de_nuevo(self):
        buzon = BuzonFalso()
        m = buzon.agregar("INBOX", message_id="<dup@x>")
        buzon.agregar("INBOX.Ruido", message_id="<dup@x>")   # la copia ya está
        with mock.patch.object(correo, "abrir_buzon", return_value=buzon):
            self.assertTrue(correo.mover_a(correo_de(m), "INBOX.Ruido"))
        self.assertEqual(len(buzon.de_comando("COPY")), 0)
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)
        self.assertEqual(buzon.cuenta("INBOX"), 0)

    def test_un_corte_justo_despues_del_copy_no_deja_dos_copias(self):
        """El caso medido: el servidor copió, la respuesta no llegó, y el
        reintento copiaba otra vez."""
        buzon = BuzonFalso()
        buzon.plan["COPY"] = ["RED_DESPUES"]
        m = buzon.agregar("INBOX", message_id="<corte@x>")
        c = correo_de(m)
        with mock.patch.object(correo, "abrir_buzon", return_value=buzon):
            with self.assertRaises(OSError):
                correo.mover_a(c, "INBOX.Ruido")
            self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)
            self.assertTrue(correo.mover_a(c, "INBOX.Ruido"))
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)
        self.assertEqual(buzon.cuenta("INBOX"), 0)

    def test_tambien_sin_message_id(self):
        """La pregunta al destino no depende del Message-ID: acota por
        remitente y fecha, y confirma cada candidato por sus cabeceras."""
        buzon = BuzonFalso()
        buzon.plan["COPY"] = ["RED_DESPUES"]
        m = buzon.agregar("INBOX", de="no-reply@facturas.com",
                          asunto="Resumen", fecha=hace(0), message_id="")
        c = correo_de(m)
        with mock.patch.object(correo, "abrir_buzon", return_value=buzon):
            with self.assertRaises(OSError):
                correo.mover_a(c, "INBOX.Ruido")
            self.assertTrue(correo.mover_a(c, "INBOX.Ruido"))
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 1)

    def test_un_correo_parecido_en_el_destino_no_lo_confunde(self):
        """Mismo remitente y mismo día, otro asunto: no es el mismo correo
        y hay que copiarlo igual. Si la pregunta al destino se conformara
        con el SEARCH, este correo no se archivaría nunca."""
        buzon = BuzonFalso()
        m = buzon.agregar("INBOX", de="no-reply@facturas.com",
                          asunto="Resumen de agosto", fecha=hace(0),
                          message_id="")
        buzon.agregar("INBOX.Ruido", de="no-reply@facturas.com",
                      asunto="Resumen de julio", fecha=hace(0),
                      message_id="")
        with mock.patch.object(correo, "abrir_buzon", return_value=buzon):
            self.assertTrue(correo.mover_a(correo_de(m), "INBOX.Ruido"))
        self.assertEqual(len(buzon.de_comando("COPY")), 1)
        self.assertEqual(buzon.cuenta("INBOX.Ruido"), 2)


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
            ok = correo.borrar_el_original(un_correo())
        self.assertTrue(ok)
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["FETCH", "STORE", "EXPUNGE"])
        self.assertNotIn("COPY", nombres)
        self.assertEqual(fake.comandos[1][2:], ("+FLAGS", "(\\Deleted)"))

    def test_busca_por_message_id_no_por_numero(self):
        """Sin UID utilizable cae en la búsqueda por Message-ID, que es lo
        único que no depende de la posición del mensaje en la carpeta."""
        fake = _IMAPFalso(mid="<abc@x>")
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            correo.borrar_el_original(un_correo("<abc@x>", uid=None))
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
            ok = correo.borrar_el_original(un_correo("<no-existe@x>"))
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["FETCH", "SEARCH"])

    def test_si_el_store_no_confirma_sigue_a_medias(self):
        fake = _IMAPFalso(store_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.borrar_el_original(un_correo())
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["FETCH", "STORE"])
        self.assertNotIn("EXPUNGE", nombres)

    def test_si_el_expunge_no_confirma_sigue_a_medias(self):
        fake = _IMAPFalso(expunge_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.borrar_el_original(un_correo())
        self.assertEqual([c[0] for c in fake.comandos],
                         ["FETCH", "STORE", "EXPUNGE"])

    def test_usa_uid_expunge_nunca_expunge_a_secas(self):
        """El doble sólo entiende comandos que pasan por M.uid(...): si el
        código llamara a M.expunge() directo -que borra TODOS los mensajes
        marcados de la carpeta, no sólo el nuestro- no existe ese método y
        la llamada explota sola."""
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            self.assertTrue(correo.borrar_el_original(un_correo()))
        self.assertIn("EXPUNGE", [c[0] for c in fake.comandos])


class MarcarLeido(unittest.TestCase):
    def test_marca_seen_buscando_por_message_id(self):
        fake = _IMAPFalso()
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.marcar_leido(un_correo())
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["FETCH", "STORE"])
        store = fake.comandos[1]
        self.assertIn("+FLAGS", store)
        self.assertIn("(\\Seen)", store)

    def test_si_no_encuentra_el_correo_no_toca_nada(self):
        fake = _IMAPFalso(uid_buscado=None)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.marcar_leido(un_correo("<no-existe@x>"))
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["FETCH", "SEARCH"])

    def test_si_el_store_no_confirma_devuelve_false(self):
        """Mismo patrón que en mover_a/devolver_a_bandeja: acá no hay
        COPY de por medio, así que un STORE que el servidor no confirma
        no deja ningún duplicado -alcanza con False, sin necesidad de
        OperacionAMedias."""
        fake = _IMAPFalso(store_ok=False)
        with mock.patch.object(correo, "abrir_buzon", return_value=fake):
            ok = correo.marcar_leido(un_correo())
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
        fake = _IMAPFalso(copy_ok=True, mid="<r@x>", carpeta="INBOX.Ruido")
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            ok = correo.devolver_a_bandeja(un_correo("<r@x>"))
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

    def test_el_uid_guardado_es_de_inbox_y_aca_no_se_usa(self):
        """El UID que quedó guardado es el que el correo tenía en INBOX;
        en INBOX.Ruido ese mismo número es otro mensaje. Por eso acá se
        ubica siempre por identidad, y por eso ubicar en INBOX y ubicar en
        otra carpeta son dos funciones distintas y no un parámetro."""
        buzon = BuzonFalso()
        mio = buzon.agregar("INBOX.Ruido", de="promo@ejemplo.com",
                            asunto="Promo", message_id="<mio@x>")
        ajeno = buzon.agregar("INBOX.Ruido", de="cliente@importante.com",
                              asunto="Contrato", message_id="<ajeno@x>")
        c = correo_de(mio)
        c["uid"] = ajeno.uid.decode()     # el UID que tenía en INBOX
        with mock.patch.object(correo.imaplib, "IMAP4_SSL",
                               return_value=buzon):
            self.assertTrue(correo.devolver_a_bandeja(c))
        self.assertEqual([m.message_id for m in buzon.carpetas["INBOX"]],
                         ["<mio@x>"])
        self.assertEqual([m.message_id for m in buzon.carpetas["INBOX.Ruido"]],
                         ["<ajeno@x>"])

    def test_con_copy_no_el_correo_sigue_en_ruido(self):
        """Mismo bug que mover_a: si el COPY a INBOX no se confirma, el
        correo no puede desaparecer de Ruido sin haber llegado a ningún
        lado."""
        fake = _IMAPFalso(copy_ok=False, mid="<r@x>", carpeta="INBOX.Ruido")
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            ok = correo.devolver_a_bandeja(un_correo("<r@x>"))
        self.assertFalse(ok)
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["SEARCH", "STORE", "COPY"])
        self.assertNotIn("EXPUNGE", nombres)
        self.assertEqual(fake.cuenta("INBOX.Ruido"), 1)

    def test_busca_por_message_id_no_por_numero(self):
        fake = _IMAPFalso(mid="<r@x>", carpeta="INBOX.Ruido")
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            correo.devolver_a_bandeja(un_correo("<r@x>"))
        busqueda = fake.comandos[0]
        self.assertEqual(busqueda[0], "SEARCH")
        self.assertIn("HEADER", busqueda)
        self.assertIn("Message-ID", busqueda)

    def test_si_no_encuentra_el_correo_no_toca_nada(self):
        fake = _IMAPFalso(uid_buscado=None, carpeta="INBOX.Ruido")
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            ok = correo.devolver_a_bandeja(un_correo("<no-existe@x>"))
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["SEARCH"])

    def test_si_el_store_menos_seen_no_confirma_no_copia_nada(self):
        """Todavía no se tocó Ruido de ningún modo -el COPY ni siquiera
        se intentó-, así que un NO acá es simplemente False: seguro
        reintentar desde cero."""
        fake = _IMAPFalso(store_ok=False, mid="<r@x>", carpeta="INBOX.Ruido")
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            ok = correo.devolver_a_bandeja(un_correo("<r@x>"))
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in fake.comandos], ["SEARCH", "STORE"])

    def test_con_store_deleted_no_queda_a_medias(self):
        """El hermano del Crítico 1, acá también: con el COPY a INBOX ya
        confirmado -el mensaje ya existe ahí, sin leer-, un STORE
        +Deleted que el servidor no confirma en Ruido no puede
        reportarse como éxito ni como "no pasó nada"."""
        fake = _IMAPFalso(copy_ok=True, store_ok=[True, False], mid="<r@x>", carpeta="INBOX.Ruido")
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.devolver_a_bandeja(un_correo("<r@x>"))
        nombres = [c[0] for c in fake.comandos]
        self.assertEqual(nombres, ["SEARCH", "STORE", "COPY", "STORE"])
        self.assertNotIn("EXPUNGE", nombres)

    def test_con_expunge_no_queda_a_medias(self):
        fake = _IMAPFalso(copy_ok=True, store_ok=True, expunge_ok=False,
                          mid="<r@x>", carpeta="INBOX.Ruido")
        with mock.patch.object(correo.imaplib, "IMAP4_SSL", return_value=fake):
            with self.assertRaises(correo.OperacionAMedias):
                correo.devolver_a_bandeja(un_correo("<r@x>"))
        self.assertEqual([c[0] for c in fake.comandos],
                         ["SEARCH", "STORE", "COPY", "STORE", "EXPUNGE"])


class ElUidvalidityNoSeLeeDosVeces(unittest.TestCase):
    """`imaplib.response()` CONSUME la respuesta: la segunda llamada
    devuelve None. Encontrado contra la casilla real -la primera lectura
    daba 603289753 y la segunda None-, y no es un detalle: con None la
    comparación de UIDVALIDITY se saltea, o sea que el resguardo dejaría
    de estar sin que nada lo diga. Por eso se lee una sola vez, al
    seleccionar la carpeta, y se guarda."""

    class _ComoImaplib:
        """Un doble que se comporta como imaplib: response() saca la
        respuesta de la bolsa y no la devuelve nunca más."""

        def __init__(self):
            self.por_carpeta = {"INBOX": b"603289753",
                                "INBOX.Ruido": b"777"}
            self.sin_leer = {"UIDVALIDITY": [self.por_carpeta["INBOX"]]}

        def select(self, carpeta, readonly=True):
            self.sin_leer = {"UIDVALIDITY": [self.por_carpeta[carpeta]]}
            return ("OK", [b"1"])

        def response(self, clave):
            return (clave, self.sin_leer.pop(clave, [None]))

    def test_la_segunda_lectura_devuelve_lo_mismo_que_la_primera(self):
        M = self._ComoImaplib()
        correo.seleccionar(M, "INBOX", readonly=True)
        self.assertEqual(correo.uidvalidity(M), "603289753")
        self.assertEqual(correo.uidvalidity(M), "603289753")

    def test_al_cambiar_de_carpeta_se_relee(self):
        """El UIDVALIDITY es por carpeta: la referencia vieja hay que
        tirarla en el momento de cambiar, no después."""
        M = self._ComoImaplib()
        correo.seleccionar(M, "INBOX", readonly=True)
        self.assertEqual(correo.uidvalidity(M), "603289753")
        correo.seleccionar(M, "INBOX.Ruido", readonly=True)
        self.assertEqual(correo.uidvalidity(M), "777")
        correo.seleccionar(M, "INBOX", readonly=True)
        self.assertEqual(correo.uidvalidity(M), "603289753")



class NuncaSePierdeUnCorreo(unittest.TestCase):
    """La propiedad que está por encima de todas las demás: pase lo que
    pase, el correo tiene que seguir existiendo en alguna carpeta.

    Duplicado es feo pero recuperable; perdido no se deshace. Se prueba a
    la fuerza bruta: cada comando fallando de cada forma posible -NO, red
    caída antes, y red caída DESPUÉS de que el servidor ya hizo el
    trabajo- en cada posición, con reintento incluido. El buzón lleva la
    cuenta y revienta con PerdidaDeCorreo si en algún momento el mensaje
    no está en ninguna de las dos carpetas."""

    def test_ninguna_combinacion_de_fallas_deja_el_correo_en_la_nada(self):
        combinaciones = 0
        for comando in ("SEARCH", "FETCH", "COPY", "STORE", "EXPUNGE"):
            for falla in ("NO", "RED", "RED_DESPUES"):
                for con_mid in (True, False):
                    for vueltas in (1, 2, 3):
                        combinaciones += 1
                        buzon = BuzonFalso()
                        buzon.vigilar = True
                        m = buzon.agregar(
                            "INBOX", fecha=hace(0),
                            message_id="<falla@x>" if con_mid else "")
                        c = correo_de(m)
                        buzon.plan[comando] = [falla]
                        with mock.patch.object(correo, "abrir_buzon",
                                               return_value=buzon):
                            for _ in range(vueltas):
                                try:
                                    correo.mover_a(c, "INBOX.Ruido")
                                except Exception:
                                    pass    # el ciclo las atrapa y reintenta
                        self.assertGreaterEqual(
                            buzon.cuenta("INBOX") + buzon.cuenta("INBOX.Ruido"),
                            1, f"se perdió con {comando}={falla}")
        self.assertEqual(combinaciones, 90)

    def test_y_tampoco_termina_con_dos_copias_en_ruido(self):
        """El otro lado de la misma moneda: con el reintento, ninguna
        falla puede dejar dos copias en el destino."""
        for comando in ("SEARCH", "FETCH", "COPY", "STORE", "EXPUNGE"):
            for falla in ("NO", "RED", "RED_DESPUES"):
                buzon = BuzonFalso()
                m = buzon.agregar("INBOX", fecha=hace(0), message_id="")
                c = correo_de(m)
                buzon.plan[comando] = [falla]
                with mock.patch.object(correo, "abrir_buzon",
                                       return_value=buzon):
                    for _ in range(4):
                        try:
                            correo.mover_a(c, "INBOX.Ruido")
                        except Exception:
                            pass
                self.assertLessEqual(
                    buzon.cuenta("INBOX.Ruido"), 1,
                    f"quedaron duplicados con {comando}={falla}")



if __name__ == "__main__":
    unittest.main()
