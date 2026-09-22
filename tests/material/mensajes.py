"""Mensajes de correo armados a mano para los tests. Sin red, sin casilla."""
import email.policy

CON_LOGO_DE_FIRMA = """From: administracion@fullcontrolgps.com.ar
To: jpsarobe@fullcontrolgps.com.ar
Subject: RE: Instalacion de Tacografos
Message-ID: <logo-001@fullcontrolgps.com.ar>
Date: Wed, 12 Aug 2026 09:14:00 -0300
MIME-Version: 1.0
Content-Type: multipart/related; boundary="LIM"

--LIM
Content-Type: text/plain; charset="utf-8"

Confirmado, quedan instalados el jueves.
--LIM
Content-Type: image/jpeg; name="image001.jpg"
Content-ID: <image001.jpg@01DD23F2.91D90680>
Content-Transfer-Encoding: base64

/9j/4AAQSkZJRg==
--LIM--
"""

ESCANEADO_DEL_CELULAR = """From: miriam arcuri <miriam.arcuri@caesistemas.com.ar>
To: jpsarobe@fullcontrolgps.com.ar
Subject: =?utf-8?q?Env=C3=ADo_lectores?=
Message-ID: <scan-002@caesistemas.com.ar>
Date: Mon, 27 Jul 2026 20:54:00 -0300
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="LIM"

--LIM
Content-Type: text/plain; charset="utf-8"

Enviado con Genius Scan para Android
--LIM
Content-Type: application/pdf; name="2026-07-27 17-52.pdf"
Content-Disposition: attachment; filename="2026-07-27 17-52.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQK
--LIM--
"""

SIN_MESSAGE_ID = """From: Alguien <alguien@ejemplo.com>
To: jpsarobe@fullcontrolgps.com.ar
Subject: Sin identificador
Date: Tue, 5 Aug 2026 11:00:00 -0300
Content-Type: text/plain; charset="utf-8"

Este mensaje no trae Message-ID.
"""

SOLO_HTML = """From: Boletin <no-reply@ejemplo.com>
To: jpsarobe@fullcontrolgps.com.ar
Subject: Novedades
Message-ID: <html-003@ejemplo.com>
Date: Tue, 5 Aug 2026 11:00:00 -0300
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"

<html><body><p>Hola&nbsp;JP</p><p>Ten&eacute;s 3 novedades</p></body></html>
"""


def como_mensaje(texto):
    """Convierte uno de los textos de arriba en un objeto de correo."""
    return email.message_from_string(texto, policy=email.policy.default)
