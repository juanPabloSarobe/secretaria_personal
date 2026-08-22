#!/usr/bin/env python3
"""Ningún test sale a la red de verdad.

Esto no es un test: es la reja, y se instala sola desde `tests/__init__.py`,
así que vale para toda la suite -y también para correr un archivo suelto
con `python3 -m unittest tests.test_secretaria`, que importa el paquete
igual-.

**Por qué hace falta.** Medido antes de esta reja: la suite intentaba
salir a `api.telegram.org` 20 veces, desde dos tests que se olvidaban de
parchear `Secretaria.enviar`. Con la red andando eso no falla ni dice
nada: manda mensajes DE VERDAD al teléfono de JP, dos por corrida. O sea
que cada vez que alguien corría los tests, a JP le llegaban mensajes de
su propia secretaria hablando de correos inventados.

Parchear esos dos tests arregla esos dos tests. Esta reja arregla el
próximo: el que se olvide se entera en el acto, con un mensaje que dice
qué parchear, en vez de que se entere JP por Telegram.

**Dónde corta.** En `socket.create_connection` -por donde pasan urllib
(Telegram) e imaplib (la casilla)- y, como red de atrás,
en `socket.socket.connect`, para el que arme el socket a mano. No se
toca `getaddrinfo`: resolver un nombre no saca datos de la máquina, y
pincharlo rompe cosas que no tienen nada que ver (`socket.getfqdn`, que
usa el módulo `email` para armar un Message-ID).

**Lo local sigue andando**: 127.0.0.1, ::1 y los sockets de archivo. Un
servidor de mentira levantado por un test es exactamente lo que hay que
poder hacer en vez de salir a internet.

**Qué se levanta, y por qué hereda de `BaseException`.** `RedDeVerdad`
no es un `OSError` ni un `Exception`, y las dos cosas son a propósito:

  * `OSError` no, porque `bot.tg()` reintenta cinco veces ante un OSError
    y `Secretaria.enviar()` devuelve None sin decir nada. Un corte que se
    pareciera a "la red está caída" haría pasar el test igual, en
    silencio, que es la mitad del problema que esto viene a resolver.

  * `Exception` tampoco, y esto se aprendió midiendo: el segundo de los
    dos tests que salían a la red seguía saliendo después de parchearlo
    una vez, y **pasaba igual**, porque el intento caía adentro del
    `except (Exception, SystemExit)` de `_atender_revers_pendientes` —
    esos manejadores anchos existen para que nada mate el hilo del
    correo, y se tragan también esto—. Una reja que el propio código
    puede tragarse no es una reja. Como `BaseException` no la atrapa
    ninguno de los `except Exception` de este proyecto (no hay ningún
    `except:` pelado, está chequeado), sube hasta unittest, que la
    reporta como error del test con el nombre del culpable.
"""
import socket

#: Cada intento que se frenó: (destino, ). Sirve para medir -la suite
#: verde con esta lista vacía es la prueba de que nadie lo intentó- y
#: para que el mensaje de error pueda decir a dónde iba.
INTENTOS = []

LOCALES = ("127.0.0.1", "::1", "localhost", "")


class RedDeVerdad(BaseException):
    """Un test intentó salir a la red. Nunca es lo que se quería.

    BaseException y no Exception: ver el encabezado del módulo. Los
    `except (Exception, SystemExit)` de secretaria.py -que están para
    que nada mate el hilo del correo- se tragaban esto y el test pasaba
    lo mismo."""


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_create_connection = socket.create_connection


def _es_local(direccion):
    """Los sockets de archivo (AF_UNIX, la dirección es un str o bytes) y
    el loopback pasan. Todo lo demás no."""
    if not isinstance(direccion, tuple) or not direccion:
        return True
    return str(direccion[0]) in LOCALES


def _frenar(direccion):
    INTENTOS.append(direccion)
    raise RedDeVerdad(
        f"un test intentó salir a la red de verdad: {direccion!r}.\n"
        f"        Los tests no tocan servicios reales -api.telegram.org es"
        f" el teléfono de JP-.\n"
        f"        Parcheá lo que corresponda antes de llamar:\n"
        f"          · Telegram: mock.patch.object(self.s, 'enviar', ...) o"
        f" mock.patch.object(secretaria.bot, 'tg')\n"
        f"          · la casilla: mock.patch.object(correo.imaplib,"
        f" 'IMAP4_SSL', return_value=BuzonFalso())\n"
        f"          · el modelo: mock.patch.object(secretaria.clasificador,"
        f" 'clasificar', ...)")


def _mi_connect(self, direccion):
    if _es_local(direccion):
        return _connect(self, direccion)
    _frenar(direccion)


def _mi_connect_ex(self, direccion):
    if _es_local(direccion):
        return _connect_ex(self, direccion)
    _frenar(direccion)


def _mi_create_connection(direccion, *args, **kwargs):
    if _es_local(direccion):
        return _create_connection(direccion, *args, **kwargs)
    _frenar(direccion)


def cortar():
    """Instala la reja. Idempotente: llamarla dos veces no encadena
    parches -si ya está puesta, no hace nada-."""
    if socket.create_connection is not _mi_create_connection:
        socket.create_connection = _mi_create_connection
        socket.socket.connect = _mi_connect
        socket.socket.connect_ex = _mi_connect_ex
