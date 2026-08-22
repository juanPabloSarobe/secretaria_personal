"""Un servidor IMAP falso CON ESTADO, para probar identidad y duplicados.

El doble viejo (`_IMAPFalso`, en test_correo.py) sólo cuenta comandos:
contesta que sí a CUALQUIER búsqueda y no modela ningún buzón. Por ese
agujero pasó el peor hallazgo de la ronda 6 -los correos sin Message-ID
nunca se archivaban, porque el SEARCH por `HEADER Message-ID "sha:..."`
no encuentra nada en un servidor de verdad y el doble decía que sí-.

Éste modela dos carpetas con mensajes de verdad: cada mensaje tiene
cabeceras, UID y flags, la carpeta tiene UIDVALIDITY, el SEARCH busca
sobre eso y el FETCH devuelve las cabeceras que el mensaje realmente
tiene. Con eso se pueden probar las tres preguntas que el doble viejo no
podía contestar:

  - ¿se ubicó el mensaje correcto, o se escribió sobre otro?
  - ¿quedaron dos copias en Ruido?
  - ¿el correo dejó de existir en las dos carpetas a la vez?

Nada de esto abre un socket: no toca la casilla real.

Sale de `auditoria_r6_servidor.py`, el servidor con el que se hizo la
auditoría, con tres agregados que hacían falta acá: UIDVALIDITY, FETCH de
cabeceras (que es como se confirma la identidad de un mensaje) y varios
mensajes por carpeta.
"""
import email.utils
from datetime import datetime, timedelta, timezone

MESES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class PerdidaDeCorreo(AssertionError):
    """El mensaje no quedó en ninguna carpeta: se perdió."""


class Mensaje:
    def __init__(self, uid, de, asunto, fecha, message_id="", flags=()):
        self.uid = uid if isinstance(uid, bytes) else str(uid).encode()
        self.de = de
        self.asunto = asunto
        self.fecha = fecha
        self.message_id = message_id
        self.flags = set(flags)

    def cabeceras(self):
        """Lo que devuelve un FETCH de HEADER.FIELDS, en bytes."""
        lineas = [f"From: {self.de}", f"Subject: {self.asunto}",
                  f"Date: {self.fecha}"]
        if self.message_id:
            lineas.append(f"Message-ID: {self.message_id}")
        return ("\r\n".join(lineas) + "\r\n\r\n").encode("utf-8")

    def copia(self, uid):
        return Mensaje(uid, self.de, self.asunto, self.fecha,
                       self.message_id, self.flags)


def _fecha_de(cabecera):
    try:
        return email.utils.parsedate_to_datetime(cabecera)
    except Exception:
        return None


class BuzonFalso:
    """Dos carpetas con estado. Se le programan fallas comando por comando.

    `plan` es {comando: [resultado, ...]}, consumido en orden; lo que no
    esté programado sale como dice `defecto`. Los resultados posibles:
      "OK"           -- el comando anda
      "NO"           -- el servidor lo rechaza (imaplib NO levanta excepción)
      "RED"          -- se corta la conexión antes de hacer nada
      "RED_DESPUES"  -- el servidor SÍ hizo el trabajo y la conexión se
                        corta antes de que llegue la respuesta. Desde el
                        cliente es indistinguible de "no pasó nada": es el
                        caso que decide si un reintento duplica.
    """

    def __init__(self, mensajes=(), uidvalidity="1000", proximo_uid=101):
        self.carpetas = {"INBOX": [], "INBOX.Ruido": []}
        self.uidvalidity = {"INBOX": str(uidvalidity),
                            "INBOX.Ruido": str(uidvalidity)}
        self._proximo_uid = proximo_uid
        for carpeta, m in mensajes:
            self.carpetas[carpeta].append(m)
        self.comandos = []
        self.seleccionadas = []
        # INBOX arranca seleccionada porque así deja la conexión
        # `correo.abrir_buzon()`, que es lo que los tests reemplazan por
        # este doble: sin esto, un test que parchea abrir_buzon nunca
        # llamaría a select() y la carpeta quedaría sin definir.
        self.seleccionada = "INBOX"
        self.plan = {}
        self.defecto = {}
        self.historia = []
        self.vigilar = None      # identidad que no puede perderse

    # ---- ayudas para armar el escenario ---------------------------
    def agregar(self, carpeta, de="promo@ejemplo.com", asunto="Promo",
                fecha="Mon, 17 Aug 2026 09:00:00 -0300", message_id="",
                flags=()):
        self._proximo_uid += 1
        m = Mensaje(self._proximo_uid, de, asunto, fecha, message_id, flags)
        self.carpetas[carpeta].append(m)
        return m

    def cuenta(self, carpeta):
        return len(self.carpetas[carpeta])

    def de_comando(self, nombre):
        return [c for c in self.comandos if c[0] == nombre]

    def _chequear(self):
        n = (self.cuenta("INBOX"), self.cuenta("INBOX.Ruido"))
        self.historia.append(n)
        if self.vigilar and n == (0, 0):
            raise PerdidaDeCorreo(
                f"el correo no está ni en INBOX ni en INBOX.Ruido;"
                f" historia={self.historia}")

    # ---- protocolo ------------------------------------------------
    def _resultado(self, comando):
        cola = self.plan.get(comando)
        if cola:
            return cola.pop(0)
        return self.defecto.get(comando, "OK")

    def login(self, *a, **k):
        pass

    def logout(self):
        pass

    def select(self, carpeta, readonly=True):
        self.seleccionada = carpeta
        self.seleccionadas.append(carpeta)
        return ("OK", [str(len(self.carpetas[carpeta])).encode()])

    def response(self, clave):
        if clave == "UIDVALIDITY" and self.seleccionada:
            return ("UIDVALIDITY",
                    [self.uidvalidity[self.seleccionada].encode()])
        return (clave, [None])

    def uid(self, comando, *args):
        self.comandos.append((comando, *args))
        r = self._resultado(comando)
        if r == "RED":
            raise OSError(f"la red se cayó durante {comando}")
        carpeta = self.seleccionada or "INBOX"

        if comando == "SEARCH":
            return self._buscar(carpeta, args, r)
        if comando == "FETCH":
            return self._traer(carpeta, args, r)
        if comando == "COPY":
            return self._copiar(carpeta, args, r)
        if comando == "STORE":
            return self._marcar(carpeta, args, r)
        if comando == "EXPUNGE":
            return self._expurgar(carpeta, args, r)
        raise AssertionError(f"comando UID inesperado: {comando}")

    # ---- comandos -------------------------------------------------
    def _buscar(self, carpeta, args, r):
        if r != "OK":
            return ("NO", [b""])
        criterios = [a for a in args[1:]]      # args[0] es el charset (None)
        salida = []
        for m in self.carpetas[carpeta]:
            if self._coincide(m, criterios):
                salida.append(m.uid)
        return ("OK", [b" ".join(salida)])

    def _coincide(self, m, criterios):
        i = 0
        while i < len(criterios):
            c = str(criterios[i]).upper()
            if c == "HEADER":
                campo = str(criterios[i + 1]).upper()
                valor = str(criterios[i + 2]).strip('"')
                actual = {"MESSAGE-ID": m.message_id,
                          "SUBJECT": m.asunto}.get(campo, "")
                if valor.lower() not in (actual or "").lower():
                    return False
                i += 3
            elif c == "FROM":
                if str(criterios[i + 1]).strip('"').lower() not in m.de.lower():
                    return False
                i += 2
            elif c in ("SINCE", "BEFORE", "SENTSINCE", "SENTBEFORE"):
                limite = self._como_fecha(str(criterios[i + 1]))
                fecha = _fecha_de(m.fecha)
                if limite and fecha:
                    fecha = fecha.astimezone(timezone.utc).date()
                    if c.endswith("SINCE") and fecha < limite:
                        return False
                    if c.endswith("BEFORE") and fecha >= limite:
                        return False
                i += 2
            elif c == "ALL":
                i += 1
            else:
                raise AssertionError(f"criterio de SEARCH no soportado: {c}")
        return True

    @staticmethod
    def _como_fecha(texto):
        try:
            d, mes, a = texto.split("-")
            return datetime(int(a), MESES.index(mes) + 1, int(d),
                            tzinfo=timezone.utc).date()
        except Exception:
            return None

    def _traer(self, carpeta, args, r):
        if r != "OK":
            return ("NO", [None])
        uid = args[0] if isinstance(args[0], bytes) else str(args[0]).encode()
        for m in self.carpetas[carpeta]:
            if m.uid == uid:
                cuerpo = m.cabeceras()
                return ("OK", [(uid + b" (BODY[HEADER.FIELDS (...)] {"
                                + str(len(cuerpo)).encode() + b"}", cuerpo),
                               b")"])
        # Un FETCH sobre un UID que ya no existe: el servidor contesta OK
        # y no devuelve nada. Que eso no se confunda con "es otro mensaje"
        # es justamente lo que decide si se puede reintentar.
        return ("OK", [None])

    def _copiar(self, carpeta, args, r):
        uid = args[0] if isinstance(args[0], bytes) else str(args[0]).encode()
        destino = args[1]
        origen = [m for m in self.carpetas[carpeta] if m.uid == uid]
        if r == "RED_DESPUES":
            if origen:
                self._proximo_uid += 1
                self.carpetas[destino].append(origen[0].copia(self._proximo_uid))
                self._chequear()
            raise OSError("la conexión se cortó después del COPY")
        if r != "OK":
            return ("NO", [b"[TRYCREATE] no existe la carpeta"])
        if not origen:
            return ("NO", [b"no such message"])
        self._proximo_uid += 1
        self.carpetas[destino].append(origen[0].copia(self._proximo_uid))
        self._chequear()
        return ("OK", [None])

    def _marcar(self, carpeta, args, r):
        if r != "OK":
            return ("NO", [b"no se pudo marcar"])
        uid = args[0] if isinstance(args[0], bytes) else str(args[0]).encode()
        modo, flags = args[1], args[2]
        nombres = {f.strip() for f in flags.strip("()").split()}
        for m in self.carpetas[carpeta]:
            if m.uid == uid:
                if modo == "+FLAGS":
                    m.flags |= nombres
                else:
                    m.flags -= nombres
        self._chequear()
        return ("OK", [None])

    def _expurgar(self, carpeta, args, r):
        if r != "OK":
            return ("NO", [b"no se pudo expurgar"])
        uid = args[0] if isinstance(args[0], bytes) else str(args[0]).encode()
        self.carpetas[carpeta] = [
            m for m in self.carpetas[carpeta]
            if not (m.uid == uid and "\\Deleted" in m.flags)]
        self._chequear()
        return ("OK", [None])


def correo_de(m, carpeta_uidvalidity="1000", con_uid=True):
    """El dict que la secretaria maneja, armado desde un mensaje del buzón.

    Es lo que dejaría `traer_nuevos()` para ese mensaje: el UID real de la
    carpeta y el UIDVALIDITY del momento en que se lo leyó.
    """
    return {"uid": m.uid.decode() if con_uid else "",
            "uidvalidity": carpeta_uidvalidity if con_uid else "",
            "de": m.de, "para": "jp@x", "cc": "", "asunto": m.asunto,
            "fecha": m.fecha, "message_id": m.message_id,
            "cuerpo": "cuerpo", "adjuntos": [], "ya_leido": False}


def hace(dias):
    """Una cabecera Date válida de hace tantos días."""
    return email.utils.format_datetime(
        datetime.now(timezone.utc) - timedelta(days=dias))
