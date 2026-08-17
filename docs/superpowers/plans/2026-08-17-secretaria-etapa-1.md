# Secretaria Etapa 1 — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un proceso permanente que clasifica el correo entrante de JP, archiva el ruido, manda tres resúmenes diarios y avisa en el momento lo que es suyo — sin enviar un solo correo.

**Architecture:** Se parte `simulacro.py` (1.211 líneas) en cinco módulos con una responsabilidad cada uno, y se escribe `secretaria.py` encima: un proceso con dos hilos (uno escucha Telegram, otro trabaja) que comparten estado en SQLite. Las herramientas de entrenamiento existentes siguen funcionando importando los mismos módulos, para que la regresión mida el sistema que corre de verdad.

**Tech Stack:** Python 3.14, solo biblioteca estándar (`imaplib`, `sqlite3`, `urllib`, `threading`, `unittest`). Sin dependencias externas. `launchd` para supervisión.

## Global Constraints

- **Solo biblioteca estándar.** No agregar dependencias. No hay `pytest`: los tests son `unittest` y se corren con `python3 -m unittest`.
- **Nunca marcar leído sin acción explícita de JP.** Al leer, siempre `BODY.PEEK[]` y `readonly=True`.
- **Nunca `EXPUNGE` a secas.** Solo `UID EXPUNGE` (el servidor tiene UIDPLUS, no tiene MOVE).
- **Buscar siempre por Message-ID**, nunca por número de secuencia: el número cambia al archivar.
- **Un correo sin clasificar nunca se archiva ni se esconde.**
- **Carpetas IMAP con prefijo `INBOX.`** y punto como separador. La de ruido es `INBOX.Ruido`.
- **SMTP puerto 465 = TLS implícito**, `SMTP_SSL`, nunca STARTTLS sobre 587. (No se usa en esta etapa; queda anotado para no equivocarse en la 3.)
- **`.env` no se puede leer con herramientas de archivo.** Para correr algo: `set -a; . ./.env; set +a; python3 …`. Los cambios en `.env` los hace JP.
- **`datos/` no se versiona.** Contiene correo real de clientes.
- **Los permisos globales del equipo deniegan por subcadena** `*exec*`, `*production*`, `*sudo*`. No usar esas letras en nombres de archivo ni de función.
- **El texto que ve JP va en castellano rioplatense**, igual que el resto del proyecto.
- **Commits frecuentes**, uno por tarea como mínimo.

---

### Task 1: Base de tests y módulo `correo.py`

Saca del monolito todo lo que habla IMAP y lo que interpreta un mensaje. Es la primera extracción, así que además crea la carpeta de tests y el patrón que van a seguir las demás.

**Files:**
- Create: `correo.py`
- Create: `tests/__init__.py` (vacío)
- Create: `tests/test_correo.py`
- Create: `tests/material/__init__.py` (vacío)
- Create: `tests/material/mensajes.py`
- Modify: `simulacro.py` (borrar lo movido, importar de `correo`)

**Interfaces:**
- Consumes: nada (primera tarea)
- Produces:
  - `adjuntos(msg: email.message.Message) -> list[dict]` — cada dict con claves `nombre: str`, `tipo: str`, `kb: int`
  - `adjuntos_legibles(lista: list[dict] | None) -> str`
  - `texto_plano(msg) -> str`
  - `identidad(c: dict) -> str`
  - `traer_correos(n: int, desde: datetime.date | None = None) -> list[dict]` — cada correo es un dict con claves `uid, de, para, cc, asunto, fecha, message_id, cuerpo, adjuntos`
  - `completar_adjuntos(correos: list[dict]) -> list[dict]`
  - `abrir_buzon(readonly: bool = True) -> imaplib.IMAP4_SSL`
  - `TIPOS_ADJUNTO: dict[str, str]`, `MESES_IMAP: list[str]`

- [ ] **Step 1: Crear el material de prueba**

Mensajes reales armados a mano, sin red. Reproducen los dos casos que ya nos mordieron: el logo de firma de Outlook con `Content-ID`, y el escaneo de Genius Scan cuyo cuerpo no dice nada.

```python
# tests/material/mensajes.py
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
```

- [ ] **Step 2: Escribir los tests que fallan**

```python
# tests/test_correo.py
import unittest

import correo
from tests.material import mensajes


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

    def test_sin_message_id_igual_devuelve_algo_estable(self):
        """Hay remitentes que no lo mandan. Sin esto, JP contestó dos veces
        el mismo correo."""
        c = {"message_id": "", "de": "a@b.com", "asunto": "Hola",
             "fecha": "Tue, 5 Aug 2026 11:00:00 -0300"}
        self.assertEqual(correo.identidad(c), correo.identidad(dict(c)))
        self.assertTrue(correo.identidad(c))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Correr los tests para ver que fallan**

Run: `cd /Users/juanpablosarobe/Documents/secretaria_personal && python3 -m unittest tests.test_correo -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'correo'`

- [ ] **Step 4: Crear `correo.py` moviendo el código existente**

Mover **sin reescribir** desde `simulacro.py`: `TIPOS_ADJUNTO`, `adjuntos`, `adjuntos_legibles`, `texto_plano`, `identidad`, `MESES_IMAP`, `traer_correos`, `completar_adjuntos`. Encabezado del módulo:

```python
#!/usr/bin/env python3
"""Todo lo que habla con la casilla, y todo lo que interpreta un mensaje.

Nadie más abre una conexión IMAP. Las dos reglas que no se negocian viven
acá adentro: se lee con BODY.PEEK[] para no marcar como leído lo que no lo
estaba, y se busca por Message-ID y nunca por número de secuencia, porque
el número cambia en cuanto se archiva algo.
"""
import email, email.policy, email.utils, hashlib, html, imaplib, os, re
```

Y agregar la función que centraliza la conexión, que hoy está repetida en tres archivos:

```python
def abrir_buzon(readonly=True):
    """Una conexión a la casilla, ya autenticada y con INBOX seleccionado.

    readonly=True no es un detalle: con readonly=False un FETCH marca como
    leídos los mensajes que no lo estaban, y eso no se puede deshacer
    cómodamente en la casilla real de JP.
    """
    M = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"],
                          int(os.environ["IMAP_PORT"]), timeout=40)
    M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    M.select("INBOX", readonly=readonly)
    return M
```

Reescribir `traer_correos` y `completar_adjuntos` para que usen `abrir_buzon()`.

- [ ] **Step 5: Dejar `simulacro.py` importando del módulo nuevo**

Borrar de `simulacro.py` todo lo movido y poner arriba:

```python
from correo import (TIPOS_ADJUNTO, MESES_IMAP, abrir_buzon, adjuntos,  # noqa: F401
                    adjuntos_legibles, completar_adjuntos, identidad,
                    texto_plano, traer_correos)
```

- [ ] **Step 6: Correr los tests y verificar que las herramientas siguen vivas**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 9 tests

Run: `python3 -c "import simulacro, explorar, revisar_reglas, archivar_ruido; print('las cuatro herramientas importan bien')"`
Expected: imprime el mensaje, sin excepción

- [ ] **Step 7: Commit**

```bash
git add correo.py tests/ simulacro.py
git commit -m "Sacar el correo del monolito, con tests que no tocan la casilla

Primera de cinco extracciones. correo.py se queda con todo lo que abre una
conexión IMAP y todo lo que interpreta un mensaje, y es el único lugar
donde eso pasa: abrir_buzon() centraliza las tres conexiones que estaban
repetidas en simulacro, explorar y archivar_ruido.

Los tests usan mensajes armados a mano, así que corren sin red y sin
casilla. Cubren los dos errores que ya nos costaron tiempo: el logo de
firma de Outlook que se colaba como adjunto, y el correo sin Message-ID
que hizo que JP contestara dos veces lo mismo."
```

---

### Task 2: Módulo `bot.py`

Todo lo que habla con Telegram. Incluye el token de tanda, que es lo que impide que un botón viejo conteste por el correo de hoy.

**Files:**
- Create: `bot.py`
- Create: `tests/test_bot.py`
- Modify: `simulacro.py`

**Interfaces:**
- Consumes: nada de tareas anteriores
- Produces:
  - `tg(metodo: str, _intentos: int = 5, **params) -> dict`
  - `tg_suave(metodo: str, **params) -> dict | None`
  - `teclado(idx: int, tanda: str) -> dict`
  - `teclado_direcciones(idx: int, direcciones: list[tuple[str, str]], tanda: str) -> dict`
  - `es_de_esta_tanda(data: str, idx: int, tanda: str) -> bool`
  - `transcribir(file_id: str) -> str`
  - `CATEGORIAS: dict[str, str]`, `UA: dict[str, str]`

**Cambio respecto del código actual:** `TANDA` deja de ser una constante global calculada del PID y pasa a ser un parámetro. Una constante de módulo no sirve cuando el proceso vive semanas y manda un resumen por día — todos los resúmenes tendrían el mismo token. Cada resumen genera el suyo.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_bot.py
import unittest

import bot


class BotonesDeOtraTanda(unittest.TestCase):
    """Los botones de Telegram no vencen nunca. Un resumen de ayer sigue
    teniendo botones vivos, y como todo resumen numera desde 1, sin el
    token del envío el botón '3' de ayer contesta por el correo 3 de hoy.
    Ya casi nos pasa una vez."""

    def test_el_boton_de_esta_tanda_se_acepta(self):
        t = bot.teclado(3, "a1b2")
        dato = t["inline_keyboard"][0][0]["callback_data"]
        self.assertTrue(bot.es_de_esta_tanda(dato, 3, "a1b2"))

    def test_el_boton_de_otra_tanda_se_rechaza(self):
        dato = bot.teclado(3, "vieja")["inline_keyboard"][0][0]["callback_data"]
        self.assertFalse(bot.es_de_esta_tanda(dato, 3, "a1b2"))

    def test_el_boton_de_otro_correo_se_rechaza(self):
        dato = bot.teclado(3, "a1b2")["inline_keyboard"][0][0]["callback_data"]
        self.assertFalse(bot.es_de_esta_tanda(dato, 7, "a1b2"))

    def test_un_dato_con_forma_rara_no_revienta(self):
        self.assertFalse(bot.es_de_esta_tanda("basura", 3, "a1b2"))
        self.assertFalse(bot.es_de_esta_tanda("", 3, "a1b2"))


class Teclado(unittest.TestCase):
    def test_estan_las_seis_categorias_y_la_estrella(self):
        t = bot.teclado(1, "a1b2")
        textos = [b["text"] for fila in t["inline_keyboard"] for b in fila]
        self.assertEqual(len(textos), 7)
        self.assertIn("⭐ Cliente importante", textos)

    def test_callback_data_entra_en_los_64_bytes_de_telegram(self):
        t = bot.teclado(999, "a1b2")
        for fila in t["inline_keyboard"]:
            for b in fila:
                self.assertLessEqual(len(b["callback_data"].encode()), 64)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr para ver que falla**

Run: `python3 -m unittest tests.test_bot -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'bot'`

- [ ] **Step 3: Crear `bot.py`**

Mover `tg`, `tg_suave`, `teclado`, `teclado_direcciones`, `transcribir`, `CATEGORIAS`, `UA`. Agregar `es_de_esta_tanda` y cambiar las firmas para recibir `tanda`:

```python
def es_de_esta_tanda(data, idx, tanda):
    """¿Este botón pertenece a este envío y a este correo?

    Un botón lleva 'accion|<tanda>-<idx>|valor'. Comparar solo el idx no
    alcanza: todo envío numera desde 1, así que el botón 3 de un resumen
    viejo pasaría por el 3 del de hoy.
    """
    partes = (data or "").split("|")
    return len(partes) == 3 and partes[1] == f"{tanda}-{idx}"
```

`esperar_respuesta` y `pedir_explicacion` **no se mueven**: son el bucle interactivo del simulacro y no los usa la secretaria, que escucha de otra manera. Quedan en `simulacro.py` importando de `bot`.

- [ ] **Step 4: Dejar `simulacro.py` importando, y generando su token por corrida**

```python
from bot import (CATEGORIAS, UA, es_de_esta_tanda, teclado,  # noqa: F401
                 teclado_direcciones, tg, tg_suave, transcribir)

TANDA = f"{os.getpid() % 10000:04d}"
```

Actualizar las llamadas de `simulacro.py` para pasar `TANDA`, y reemplazar la comparación manual dentro de `esperar_respuesta` y `pedir_explicacion` por `es_de_esta_tanda(cq["data"], idx, TANDA)`.

- [ ] **Step 5: Correr todo**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 15 tests

- [ ] **Step 6: Commit**

```bash
git add bot.py tests/test_bot.py simulacro.py
git commit -m "Sacar Telegram del monolito, y que el token de tanda sea por envío

El token que distingue un botón viejo de uno de ahora se calculaba del PID
al arrancar el proceso. Eso alcanzaba para una corrida de veinte minutos;
la secretaria va a vivir semanas y mandar un resumen por día, así que todos
los resúmenes compartirían token y el botón de ayer volvería a contestar
por el de hoy. Ahora cada envío genera el suyo y se compara con
es_de_esta_tanda(), que además tolera un callback_data con forma rara en
vez de reventar.

El bucle interactivo del simulacro no se mueve: la secretaria escucha de
otra manera y no lo usa."
```

---

### Task 3: Módulo `reglas.py`

Lo que lee y escribe los archivos de conocimiento: el roster, las reglas, los códigos convenidos y el ruido conocido.

**Files:**
- Create: `reglas.py`
- Create: `tests/test_reglas.py`
- Modify: `simulacro.py`

**Interfaces:**
- Consumes: nada
- Produces:
  - `codigos_convenidos() -> list[str]`
  - `tiene_codigo(correo: dict, frases: list[str]) -> str | None`
  - `remitentes_ruido() -> tuple[set[str], set[str]]` — (direcciones, dominios)
  - `es_ruido_conocido(correo: dict, direcciones: set, dominios: set) -> bool`
  - `protegido(correo: dict) -> bool`
  - `direcciones_externas(correo: dict) -> list[tuple[str, str]]`
  - `marcar_importante(etiqueta: str, direccion: str) -> bool`
  - `texto_de_conocimiento() -> str` — roster + reglas + códigos concatenados, para el prompt
  - `DOMINIOS_GENERICOS: set[str]`, `MARCAS_PARA_AUTOMATIZAR: int`, `MUESTREO_CONTROL: int`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_reglas.py
import os
import tempfile
import unittest

import reglas


class Codigos(unittest.TestCase):
    def test_reconoce_la_frase_sin_importar_los_acentos(self):
        """JP escribe desde el celular y a veces sin tildes. Un código que
        solo matchea con tilde no sirve para nada."""
        c = {"asunto": "Consulta", "cuerpo": "Tal cual lo charlado, te mando"}
        self.assertIsNotNone(reglas.tiene_codigo(c, ["tal cual lo charlado"]))
        c2 = {"asunto": "", "cuerpo": "TAL CUAL LO CHARLADO"}
        self.assertIsNotNone(reglas.tiene_codigo(c2, ["tal cual lo charlado"]))

    def test_tambien_lo_busca_en_el_asunto(self):
        c = {"asunto": "De acuerdo a lo conversado", "cuerpo": ""}
        self.assertIsNotNone(reglas.tiene_codigo(c, ["de acuerdo a lo conversado"]))

    def test_sin_codigo_devuelve_none(self):
        c = {"asunto": "Hola", "cuerpo": "Nada que ver"}
        self.assertIsNone(reglas.tiene_codigo(c, ["tal cual lo charlado"]))


class Protegido(unittest.TestCase):
    def test_mira_el_roster_y_tambien_las_reglas(self):
        """Sobre lo que JP ya se pronunció, el sistema no decide solo. Una
        vez el filtro automático archivó una promo de SiPago que tenía una
        regla escrita, porque protegido() solo miraba roster.md."""
        self.assertTrue(reglas.protegido({"de": "x <cobros@sipago.com.ar>"}))
        self.assertTrue(reglas.protegido({"de": "x <algo@imseg.com>"}))

    def test_un_remitente_desconocido_no_esta_protegido(self):
        self.assertFalse(reglas.protegido({"de": "x <nadie@ejemplo-raro.com>"}))


class Conocimiento(unittest.TestCase):
    def test_junta_los_tres_archivos(self):
        t = reglas.texto_de_conocimiento()
        self.assertIn("Enzo", t)
        self.assertIn("Natalia", t)
        self.assertGreater(len(t), 5000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr para ver que falla**

Run: `python3 -m unittest tests.test_reglas -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'reglas'`

- [ ] **Step 3: Crear `reglas.py`**

Mover `_sin_acentos`, `codigos_convenidos`, `tiene_codigo`, `DOMINIOS_GENERICOS`, `MARCAS_PARA_AUTOMATIZAR`, `MUESTREO_CONTROL`, `remitentes_ruido`, `es_ruido_conocido`, `protegido`, `direcciones_externas`, `marcar_importante`. Agregar:

```python
def texto_de_conocimiento():
    """El roster, las reglas y los códigos, tal como van al prompt.

    Estaba adentro de prompt_sistema(). Sale acá porque quien sabe leer
    estos archivos es este módulo, y porque así el clasificador no necesita
    saber en qué archivo vive cada cosa.
    """
    partes = [open("roster.md", encoding="utf-8").read(),
              open("reglas.md", encoding="utf-8").read()]
    try:
        partes.append(open("codigos.md", encoding="utf-8").read())
    except FileNotFoundError:
        pass
    return "\n\n".join(partes)
```

- [ ] **Step 4: Dejar `simulacro.py` importando**

```python
from reglas import (DOMINIOS_GENERICOS, MARCAS_PARA_AUTOMATIZAR,  # noqa: F401
                    MUESTREO_CONTROL, codigos_convenidos, direcciones_externas,
                    es_ruido_conocido, marcar_importante, protegido,
                    remitentes_ruido, texto_de_conocimiento, tiene_codigo)
```

- [ ] **Step 5: Correr todo**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 22 tests

- [ ] **Step 6: Commit**

```bash
git add reglas.py tests/test_reglas.py simulacro.py
git commit -m "Sacar el conocimiento del monolito

reglas.py es el único que sabe en qué archivo vive cada cosa: el roster,
las reglas aprendidas, los códigos convenidos y el ruido conocido. El
clasificador ahora pide texto_de_conocimiento() y no abre un archivo.

Los tests fijan las dos cosas que ya fallaron: que un código convenido se
reconozca sin tildes, porque JP escribe desde el celular, y que protegido()
mire reglas.md además de roster.md — cuando solo miraba el roster, el
filtro automático archivó una promo sobre la que JP ya se había
pronunciado."
```

---

### Task 4: Módulo `clasificador.py` con la cadena de motores en configuración

**Files:**
- Create: `clasificador.py`
- Create: `motores.json`
- Create: `tests/test_clasificador.py`
- Modify: `simulacro.py`
- Modify: `.gitignore` (no hace falta: `motores.json` **sí** se versiona, no tiene secretos)

**Interfaces:**
- Consumes: `reglas.texto_de_conocimiento()`
- Produces:
  - `prompt_sistema() -> str`
  - `motores(preferido: str | None = None) -> list[tuple[str, str, str, str]]` — (nombre, base_url, api_key, modelo)
  - `clasificar(sistema: str, correo: dict, preferido=None, pasadas: int = 3) -> dict` — con claves `categoria, motivo, confianza, pasadas, unanime` y opcional `emitidas`
  - `clasificar_una_vez(sistema: str, correo: dict, preferido=None) -> dict`
  - `preferencia() -> list[str]` y `elegir_motor(nombre: str) -> list[str]` — lee y escribe `motores.json`
  - `ESPERA_RESPUESTA: int`, `ESPERA_MAXIMA: int`

- [ ] **Step 1: Crear la configuración**

`motores.json` reemplaza el diccionario escrito a mano en el código. JP lo va a cambiar desde Telegram, así que tiene que ser un archivo que el proceso relea, no constantes de Python.

```json
{
  "preferencia": ["nvidia", "groq"],
  "catalogo": {
    "nvidia": {
      "url": "NVIDIA_BASE_URL",
      "clave": "NVIDIA_API_KEY",
      "modelo": "nvidia/nemotron-3-super-120b-a12b"
    },
    "groq": {
      "url": "GROQ_BASE_URL",
      "clave": "GROQ_API_KEY",
      "modelo": "llama-3.3-70b-versatile"
    }
  },
  "fuera": {
    "ollama": {
      "url": "OLLAMA_BASE_URL",
      "clave": "OLLAMA_API_KEY",
      "modelo": "qwen2.5:14b",
      "motivo": "Sacado el 2026-08-12 a pedido de JP: la Mac no puede tener dos modelos cargados y lo necesita para otro proyecto. Se vuelve a habilitar moviendo esta entrada a catalogo."
    }
  }
}
```

- [ ] **Step 2: Escribir los tests que fallan**

```python
# tests/test_clasificador.py
import json
import os
import shutil
import tempfile
import unittest

import clasificador


class CadenaDeMotores(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.antes = os.getcwd()
        shutil.copy("motores.json", self.dir)
        os.chdir(self.dir)
        os.environ.setdefault("NVIDIA_BASE_URL", "https://ejemplo/nvidia/v1")
        os.environ.setdefault("NVIDIA_API_KEY", "x")
        os.environ.setdefault("GROQ_BASE_URL", "https://ejemplo/groq/v1")
        os.environ.setdefault("GROQ_API_KEY", "x")

    def tearDown(self):
        os.chdir(self.antes)
        shutil.rmtree(self.dir)

    def test_respeta_el_orden_de_la_configuracion(self):
        self.assertEqual([m[0] for m in clasificador.motores()],
                         ["nvidia", "groq"])

    def test_el_preferido_pasa_al_frente_sin_perder_los_respaldos(self):
        """Elegir un motor no es quedarse sin red: si el elegido se queda
        sin cuota, los otros siguen atrás."""
        self.assertEqual([m[0] for m in clasificador.motores("groq")],
                         ["groq", "nvidia"])

    def test_un_motor_desconocido_avisa_cuales_hay(self):
        with self.assertRaises(ValueError) as e:
            clasificador.motores("inventado")
        self.assertIn("nvidia", str(e.exception))

    def test_ollama_esta_fuera_hasta_que_jp_lo_habilite(self):
        with self.assertRaises(ValueError):
            clasificador.motores("ollama")

    def test_elegir_motor_persiste_y_se_relee(self):
        """JP lo cambia desde Telegram: tiene que sobrevivir sin reiniciar."""
        clasificador.elegir_motor("groq")
        self.assertEqual(clasificador.preferencia()[0], "groq")
        with open("motores.json", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["preferencia"][0], "groq")


class Prompt(unittest.TestCase):
    def test_el_procedimiento_esta_numerado_y_en_orden(self):
        """El orden de los pasos decide más que su contenido: una regla en
        prosa no revierte un paso numerado. Si alguien reordena los pasos
        sin querer, esto lo caza."""
        p = clasificador.prompt_sistema()
        pasos = [p.index(f"PASO {n}") for n in range(1, 6)]
        self.assertEqual(pasos, sorted(pasos))

    def test_el_prompt_incluye_el_roster_y_las_reglas(self):
        p = clasificador.prompt_sistema()
        self.assertIn("Enzo", p)
        self.assertIn("Natalia", p)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Correr para ver que falla**

Run: `python3 -m unittest tests.test_clasificador -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'clasificador'`

- [ ] **Step 4: Crear `clasificador.py`**

Mover `prompt_sistema`, `motores`, `_pedir`, `clasificar`, `clasificar_una_vez`, `_adjuntos_para_modelo`, `ESPERA_RESPUESTA`, `ESPERA_MAXIMA`. Borrar los diccionarios `MOTORES` y `PREFERENCIA` del código y leer de `motores.json`:

```python
CONFIG = "motores.json"


def _config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def preferencia():
    return _config()["preferencia"]


def elegir_motor(nombre):
    """Pone `nombre` al frente de la cadena y lo deja escrito.

    JP lo cambia desde Telegram con /motor, así que el cambio tiene que
    sobrevivir sin reiniciar el proceso: por eso se guarda en el archivo y
    se relee en cada clasificación, en vez de vivir en una variable.
    """
    c = _config()
    if nombre not in c["catalogo"]:
        raise ValueError(f"motor desconocido: {nombre}. "
                         f"Hay: {', '.join(sorted(c['catalogo']))}")
    c["preferencia"] = [nombre] + [m for m in c["preferencia"] if m != nombre]
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    return c["preferencia"]


def motores(preferido=None):
    c = _config()
    cat = c["catalogo"]
    orden = c["preferencia"]
    if preferido:
        if preferido not in cat:
            raise ValueError(f"motor desconocido: {preferido}. "
                             f"Hay: {', '.join(sorted(cat))}")
        orden = [preferido] + [m for m in orden if m != preferido]
    lista = []
    for nombre in orden:
        d = cat.get(nombre)
        if not d:
            continue
        url = os.environ.get(d["url"])
        if url:
            lista.append((nombre, url, os.environ.get(d["clave"], ""), d["modelo"]))
    if not lista:
        raise SystemExit("no hay ningún motor configurado en el .env")
    return lista
```

`prompt_sistema` pasa a usar `reglas.texto_de_conocimiento()` en vez de abrir archivos.

- [ ] **Step 5: Dejar `simulacro.py`, `explorar.py` y `revisar_reglas.py` importando**

```python
from clasificador import (ESPERA_MAXIMA, ESPERA_RESPUESTA, clasificar,  # noqa: F401
                          clasificar_una_vez, motores, prompt_sistema)
```

- [ ] **Step 6: Correr todo, con el `.env` cargado**

Run: `set -a; . ./.env; set +a; python3 -m unittest discover tests -v`
Expected: PASS, 30 tests

Run: `set -a; . ./.env; set +a; python3 -c "import clasificador as c; print([m[0] for m in c.motores()])"`
Expected: `['nvidia', 'groq']`

- [ ] **Step 7: Commit**

```bash
git add clasificador.py motores.json tests/test_clasificador.py simulacro.py explorar.py revisar_reglas.py
git commit -m "Sacar el clasificador del monolito, y la cadena de motores del código

La cadena estaba escrita a mano en Python: cambiar de motor era editar
código. Pasa a motores.json, que el clasificador relee en cada
clasificación, así /motor desde Telegram va a poder cambiarla sin
reiniciar nada y sin que JP abra una terminal.

Elegir un motor lo pone al frente pero no borra los respaldos: si el
elegido se queda sin cuota, los otros siguen atrás. Ollama queda en una
sección 'fuera' con el motivo escrito, para que se habilite moviendo una
entrada y no descubriendo por qué no está.

Un test fija que los PASO del procedimiento estén en orden creciente. No
es paranoia: el orden decide más que el contenido, y una regla en prosa no
revierte un paso numerado."
```

---

### Task 5: Arreglar el PROCEDIMIENTO que tiene ENZO en 0%

No es refactor: es el defecto medido más caro del sistema. Tres de tres correos que eran de Enzo dieron DELEGADO, porque el paso que pregunta quién está en el hilo contesta antes y frena.

**Files:**
- Modify: `clasificador.py` (el texto del PROCEDIMIENTO dentro de `prompt_sistema`)
- Create: `tests/test_procedimiento.py`

**Interfaces:**
- Consumes: `clasificador.prompt_sistema()`
- Produces: nada nuevo

- [ ] **Step 1: Escribir el test de estructura**

```python
# tests/test_procedimiento.py
import unittest

import clasificador


class OrdenDelProcedimiento(unittest.TestCase):
    def test_el_pedido_concreto_se_evalua_antes_que_quien_esta_en_copia(self):
        """El defecto que tuvo a ENZO en 0%: 'el responsable está en copia'
        contestaba DELEGADO y frenaba, antes de preguntarse si el correo
        era un pedido que alguien tiene que ejecutar."""
        p = clasificador.prompt_sistema()
        self.assertLess(p.index("PASO 4"), p.index("PASO 5"))
        self.assertIn("pedido concreto", p.lower())

    def test_dice_que_estar_en_copia_no_cancela_la_tarea(self):
        p = clasificador.prompt_sistema().lower()
        self.assertIn("estar en copia no", p)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr para ver que falla**

Run: `python3 -m unittest tests.test_procedimiento -v`
Expected: FAIL en `test_el_pedido_concreto_se_evalua_antes_que_quien_esta_en_copia`

- [ ] **Step 3: Insertar el paso nuevo en el PROCEDIMIENTO**

Antes del paso que evalúa quién está en el hilo, agregar:

```
PASO 4 — ¿Es un PEDIDO CONCRETO que alguien tiene que ejecutar?
  Si el correo pide una tarea que todavía no está hecha —una programación
  remota, un cambio de zona o de velocidad de zona, los IMEI de unos equipos,
  un certificado de calibración, un turno de mantenimiento— la respuesta es la
  categoría del RESPONSABLE de esa tarea, y terminás acá.
  ESTAR EN COPIA NO CANCELA LA TAREA. Que Enzo o Natalia ya figuren en el
  correo no significa que el asunto esté resuelto: significa que se enteraron.
  DELEGADO es para cuando el equipo YA ACTUÓ —contestó, confirmó, avisó que
  está hecho—, no para cuando el pedido recién llega.
  Si el correo es la respuesta del equipo a un pedido anterior, esto no
  aplica: seguí al paso siguiente.
```

Renumerar los pasos siguientes.

- [ ] **Step 4: Correr el test de estructura**

Run: `python3 -m unittest tests.test_procedimiento -v`
Expected: PASS

- [ ] **Step 5: Correr la regresión completa, que es la verificación de verdad**

Run: `set -a; . ./.env; set +a; for f in datos/simulacro-*.json; do echo "### $f"; python3 -u revisar_reglas.py "$f" 2 --motor nvidia; done 2>&1 | tee /tmp/regresion-enzo.log`

Expected: los tres casos con `JP dijo ENZO` pasan de `sigue mal` a `APRENDIÓ`, y el total no baja de 122/133.

Verificar con:

```bash
grep -E "ENZO" /tmp/regresion-enzo.log
grep -cE "SE ROMPIÓ" /tmp/regresion-enzo.log
```

**Si el total baja, no seguir.** Volver al paso 3 y ajustar la redacción del paso. Es exactamente lo que pasó la vez anterior: la regla estaba escrita y no arreglaba nada.

- [ ] **Step 6: Commit**

```bash
git add clasificador.py tests/test_procedimiento.py
git commit -m "Arreglar donde de verdad estaba el defecto de ENZO: en el orden

Tres de tres correos que eran de Enzo dieron DELEGADO. La regla que los
corrige ya estaba escrita en reglas.md desde hace días, en prosa, y no
servía: el prompt tiene un procedimiento numerado que se ejecuta antes, y
el paso que pregunta quién está en el hilo contestaba DELEGADO y frenaba
ahí. Una regla en prosa no revierte un paso numerado — ya lo sabíamos y lo
repetimos igual.

El paso nuevo pregunta si el correo es un pedido concreto que alguien tiene
que ejecutar, y en ese caso la categoría es la del responsable aunque ya
esté en copia. DELEGADO queda para cuando el equipo ya actuó.

De ahí sale el seguimiento, que es lo que JP pidió: los cambios de zona
tardan días y hay que asegurarse de que la tarea se cumpla."
```

---

### Task 6: Módulo `memoria.py` — el estado en SQLite

**Files:**
- Create: `memoria.py`
- Create: `tests/test_memoria.py`

**Interfaces:**
- Consumes: nada
- Produces:
  - `abrir(ruta: str = "datos/secretaria.db") -> sqlite3.Connection`
  - `anotar(cx, correo: dict, categoria: str, motivo: str) -> bool` — devuelve `False` si ya estaba anotado
  - `situacion(cx, message_id: str) -> str | None`
  - `cambiar(cx, message_id: str, situacion: str) -> None`
  - `pendientes(cx, situacion: str) -> list[dict]`
  - `del_dia(cx, situacion: str, desde: str) -> list[dict]`
  - `corregir(cx, message_id: str, categoria_nueva: str, explicacion: str) -> None`
  - `latido(cx, momento: str) -> None` y `ultimo_latido(cx) -> str | None`
  - `SITUACIONES: set[str]`

**Situaciones:** `clasificado`, `archivado`, `en_resumen`, `avisado`, `cerrado`, `corregido`, `mostrado_sin_clasificar`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_memoria.py
import os
import tempfile
import unittest

import memoria


def un_correo(mid="<a@b.com>", asunto="Prueba"):
    return {"message_id": mid, "uid": "1", "de": "a@b.com",
            "para": "jpsarobe@fullcontrolgps.com.ar", "cc": "",
            "asunto": asunto, "fecha": "Mon, 17 Aug 2026 09:00:00 -0300",
            "cuerpo": "texto", "adjuntos": []}


class Anotar(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.cx = memoria.abrir(self.f.name)

    def tearDown(self):
        self.cx.close()
        os.unlink(self.f.name)

    def test_un_correo_nuevo_se_anota_y_queda_clasificado(self):
        self.assertTrue(memoria.anotar(self.cx, un_correo(), "RUIDO", "promo"))
        self.assertEqual(memoria.situacion(self.cx, "<a@b.com>"), "clasificado")

    def test_el_mismo_correo_no_se_anota_dos_veces(self):
        """Si el proceso se cae y vuelve, no puede reclasificar ni volver a
        avisar lo que ya procesó."""
        memoria.anotar(self.cx, un_correo(), "RUIDO", "promo")
        self.assertFalse(memoria.anotar(self.cx, un_correo(), "RUIDO", "promo"))

    def test_una_situacion_inventada_se_rechaza(self):
        memoria.anotar(self.cx, un_correo(), "RUIDO", "promo")
        with self.assertRaises(ValueError):
            memoria.cambiar(self.cx, "<a@b.com>", "inventada")

    def test_se_listan_los_pendientes_de_una_situacion(self):
        memoria.anotar(self.cx, un_correo("<1@x>", "uno"), "RUIDO", "m")
        memoria.anotar(self.cx, un_correo("<2@x>", "dos"), "TUYO", "m")
        memoria.cambiar(self.cx, "<1@x>", "archivado")
        p = memoria.pendientes(self.cx, "archivado")
        self.assertEqual([c["asunto"] for c in p], ["uno"])


class Correcciones(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.cx = memoria.abrir(self.f.name)
        memoria.anotar(self.cx, un_correo(), "RUIDO", "parecía promo")

    def tearDown(self):
        self.cx.close()
        os.unlink(self.f.name)

    def test_corregir_guarda_lo_que_dijo_jp_y_por_que(self):
        memoria.corregir(self.cx, "<a@b.com>", "NATALIA",
                         "SiPago es mi proveedor de cobros")
        fila = self.cx.execute(
            "SELECT categoria, categoria_jp, explicacion, situacion "
            "FROM correos WHERE message_id = ?", ("<a@b.com>",)).fetchone()
        self.assertEqual(fila["categoria"], "RUIDO")
        self.assertEqual(fila["categoria_jp"], "NATALIA")
        self.assertIn("cobros", fila["explicacion"])
        self.assertEqual(fila["situacion"], "corregido")


class Latido(unittest.TestCase):
    def test_sin_latidos_devuelve_none(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cx = memoria.abrir(f.name)
        self.assertIsNone(memoria.ultimo_latido(cx))
        memoria.latido(cx, "2026-08-17T09:00:00")
        self.assertEqual(memoria.ultimo_latido(cx), "2026-08-17T09:00:00")
        cx.close()
        os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr para ver que falla**

Run: `python3 -m unittest tests.test_memoria -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'memoria'`

- [ ] **Step 3: Escribir `memoria.py`**

```python
#!/usr/bin/env python3
"""El estado de cada correo, en SQLite.

Dos hilos tocan esto a la vez: el que escucha a JP y el que procesa correo.
Con archivos JSON se corrompe el día que coincidan. SQLite viene con Python
y resuelve el bloqueo solo.

Está en disco y no en memoria a propósito: si el proceso se cae, al volver
retoma sin reclasificar lo que ya clasificó ni volver a avisar lo que ya
avisó.
"""
import json
import os
import sqlite3

SITUACIONES = {"clasificado", "archivado", "en_resumen", "avisado",
               "cerrado", "corregido", "mostrado_sin_clasificar"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS correos (
    message_id   TEXT PRIMARY KEY,
    uid          TEXT,
    de           TEXT,
    para         TEXT,
    cc           TEXT,
    asunto       TEXT,
    fecha        TEXT,
    cuerpo       TEXT,
    adjuntos     TEXT,
    categoria    TEXT,
    motivo       TEXT,
    categoria_jp TEXT,
    explicacion  TEXT,
    situacion    TEXT NOT NULL,
    visto        TEXT NOT NULL,
    actualizado  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS por_situacion ON correos(situacion);
CREATE TABLE IF NOT EXISTS latidos (momento TEXT PRIMARY KEY);
"""


def abrir(ruta="datos/secretaria.db"):
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    cx = sqlite3.connect(ruta, check_same_thread=False, timeout=30)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")   # dos hilos sin pisarse
    cx.executescript(ESQUEMA)
    cx.commit()
    return cx


def _ahora():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def anotar(cx, correo, categoria, motivo):
    """Anota un correo recién clasificado. False si ya estaba."""
    if situacion(cx, correo["message_id"]) is not None:
        return False
    inicial = ("mostrado_sin_clasificar" if categoria in ("ERROR", "DUDA")
               else "clasificado")
    cx.execute(
        "INSERT INTO correos (message_id, uid, de, para, cc, asunto, fecha,"
        " cuerpo, adjuntos, categoria, motivo, situacion, visto, actualizado)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (correo["message_id"], correo.get("uid"), correo.get("de"),
         correo.get("para"), correo.get("cc"), correo.get("asunto"),
         correo.get("fecha"), (correo.get("cuerpo") or "")[:8000],
         json.dumps(correo.get("adjuntos") or [], ensure_ascii=False),
         categoria, motivo, inicial, _ahora(), _ahora()))
    cx.commit()
    return True


def situacion(cx, message_id):
    f = cx.execute("SELECT situacion FROM correos WHERE message_id = ?",
                   (message_id,)).fetchone()
    return f["situacion"] if f else None


def cambiar(cx, message_id, nueva):
    if nueva not in SITUACIONES:
        raise ValueError(f"situación desconocida: {nueva}. "
                         f"Hay: {', '.join(sorted(SITUACIONES))}")
    cx.execute("UPDATE correos SET situacion = ?, actualizado = ?"
               " WHERE message_id = ?", (nueva, _ahora(), message_id))
    cx.commit()


def pendientes(cx, sit):
    return [dict(f) for f in cx.execute(
        "SELECT * FROM correos WHERE situacion = ? ORDER BY visto", (sit,))]


def del_dia(cx, sit, desde):
    return [dict(f) for f in cx.execute(
        "SELECT * FROM correos WHERE situacion = ? AND visto >= ?"
        " ORDER BY visto", (sit, desde))]


def corregir(cx, message_id, categoria_nueva, explicacion):
    """Guarda que JP dijo otra cosa, y por qué.

    No pisa `categoria`: lo que dijo el sistema hay que conservarlo, si no
    se pierde la comparación que permite medir si mejora."""
    cx.execute("UPDATE correos SET categoria_jp = ?, explicacion = ?,"
               " situacion = 'corregido', actualizado = ?"
               " WHERE message_id = ?",
               (categoria_nueva, explicacion, _ahora(), message_id))
    cx.commit()


def latido(cx, momento):
    cx.execute("INSERT OR IGNORE INTO latidos (momento) VALUES (?)", (momento,))
    cx.commit()


def ultimo_latido(cx):
    f = cx.execute("SELECT MAX(momento) AS m FROM latidos").fetchone()
    return f["m"] if f and f["m"] else None
```

- [ ] **Step 4: Correr todo**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 40 tests

- [ ] **Step 5: Commit**

```bash
git add memoria.py tests/test_memoria.py
git commit -m "El estado de cada correo, en SQLite

Dos hilos van a tocar esto a la vez: el que escucha a JP y el que procesa
correo. Con archivos JSON se corrompe el día que coincidan.

En disco y no en memoria a propósito: si el proceso se cae, al volver
retoma sin reclasificar ni volver a avisar. anotar() devuelve False cuando
el correo ya estaba, que es lo que hace la reanudación segura.

Una corrección de JP no pisa lo que dijo el sistema, lo guarda al lado. Si
lo pisara perderíamos la comparación, que es lo único que nos deja medir si
el criterio mejora."
```

---

### Task 7: `secretaria.py` — el esqueleto, los dos hilos y el modo seco

**Files:**
- Create: `secretaria.py`
- Create: `tests/test_secretaria.py`
- Create: `com.fullcontrolgps.secretaria.plist`

**Interfaces:**
- Consumes: `correo`, `bot`, `reglas`, `clasificador`, `memoria`
- Produces:
  - `class Reloj` con `momentos_pendientes(ahora: datetime, ultimo: datetime) -> list[str]`
  - `class Secretaria` con `.ciclo_de_correo()`, `.ciclo_de_escucha()`, `.arrancar()`
  - `EN_SECO: bool` leído de la variable de entorno `SECRETARIA_EN_SECO`
  - `HORA_INICIO = 8`, `HORA_FIN = 19`
  - `en_horario(momento: datetime) -> bool`

- [ ] **Step 1: Escribir los tests que fallan**

El reloj se prueba con fechas inyectadas, nunca con `sleep`.

```python
# tests/test_secretaria.py
import unittest
from datetime import datetime

import secretaria


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


class Horario(unittest.TestCase):
    def test_un_martes_al_mediodia_esta_en_horario(self):
        self.assertTrue(secretaria.en_horario(datetime(2026, 8, 18, 12, 0)))

    def test_un_martes_a_las_seis_de_la_manana_no(self):
        self.assertFalse(secretaria.en_horario(datetime(2026, 8, 18, 6, 0)))

    def test_un_domingo_no(self):
        self.assertFalse(secretaria.en_horario(datetime(2026, 8, 16, 12, 0)))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr para ver que falla**

Run: `python3 -m unittest tests.test_secretaria -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'secretaria'`

- [ ] **Step 3: Escribir el esqueleto**

```python
#!/usr/bin/env python3
"""La secretaria: el proceso que corre solo y coordina a los demás módulos.

Dos hilos. El que escucha nunca hace nada pesado —recibe el botón o el
audio, lo anota y contesta— porque clasificar tarda hasta tres minutos con
nvidia y con un solo hilo el bot quedaría mudo todo ese rato.

Arranca EN SECO: clasifica y manda los resúmenes, pero no toca la casilla.
Se le saca el freno con SECRETARIA_EN_SECO=false, y de a un paso: primero
que archive ruido, que es reversible, y otro día que marque leído.
"""
import os
import threading
import time
from datetime import datetime

import bot, clasificador, correo, memoria, reglas

EN_SECO = os.environ.get("SECRETARIA_EN_SECO", "true").lower() != "false"
CADA = 180                    # cada cuánto mira la casilla, en segundos
HORA_INICIO, HORA_FIN = 8, 19

MOMENTOS = [("manana", 8, 30), ("tarde", 17, 0), ("ruido", 18, 0)]


def en_horario(momento):
    """De 8 a 19, días hábiles. Fuera de eso lo que entra espera al resumen
    de las 8:30, salvo los clientes importantes."""
    return momento.weekday() < 5 and HORA_INICIO <= momento.hour < HORA_FIN


class Reloj:
    """Decide qué resúmenes corresponden, sin dormir ni mirar la hora sola.

    Recibe el ahora y el último momento en que se lo consultó, así se puede
    probar con fechas inventadas. Si la secretaria estuvo caída doce horas,
    devuelve todo lo que se perdió, en orden.
    """

    def momentos_pendientes(self, ahora, ultimo):
        salida = []
        for nombre, h, m in MOMENTOS:
            corte = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
            if ultimo < corte <= ahora:
                salida.append(nombre)
        return salida
```

Y la clase `Secretaria` con los dos hilos, dejando los cuerpos de los flujos para las tareas que siguen:

```python
class Secretaria:
    def __init__(self, cx=None):
        self.cx = cx or memoria.abrir()
        self.reloj = Reloj()
        self.ultimo_reloj = datetime.now()
        self.offset = 0
        self.parada = threading.Event()
        self.pausada = False

    def ciclo_de_correo(self):
        while not self.parada.is_set():
            try:
                self.revisar_casilla()
                ahora = datetime.now()
                for momento in self.reloj.momentos_pendientes(
                        ahora, self.ultimo_reloj):
                    self.mandar_resumen(momento)
                self.ultimo_reloj = ahora
                memoria.latido(self.cx, ahora.isoformat(timespec="seconds"))
            except Exception as e:
                # Nada que pase acá adentro puede matar el proceso: si se
                # muere, JP no se entera, porque no recibir avisos se parece
                # mucho a un día tranquilo.
                print(f"[correo] {type(e).__name__}: {e}", flush=True)
            self.parada.wait(CADA)

    def ciclo_de_escucha(self):
        while not self.parada.is_set():
            try:
                d = bot.tg("getUpdates", offset=self.offset, timeout=50)
                for u in d.get("result", []):
                    self.offset = u["update_id"] + 1
                    self.atender(u)
            except Exception as e:
                print(f"[escucha] {type(e).__name__}: {e}", flush=True)
                time.sleep(5)

    def arrancar(self):
        hilos = [threading.Thread(target=self.ciclo_de_correo, daemon=True),
                 threading.Thread(target=self.ciclo_de_escucha, daemon=True)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
```

`revisar_casilla`, `mandar_resumen` y `atender` se implementan en las tareas 8 a 11. Por ahora, cuerpos que registran que se los llamó:

```python
    def revisar_casilla(self):
        raise NotImplementedError("tarea 8")

    def mandar_resumen(self, momento):
        raise NotImplementedError("tarea 9")

    def atender(self, update):
        raise NotImplementedError("tarea 11")
```

- [ ] **Step 4: Escribir el `plist` de `launchd`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.fullcontrolgps.secretaria</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd /Users/juanpablosarobe/Documents/secretaria_personal &amp;&amp; set -a &amp;&amp; . ./.env &amp;&amp; set +a &amp;&amp; exec /usr/bin/python3 -u secretaria.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key>
  <string>/Users/juanpablosarobe/Documents/secretaria_personal/datos/secretaria.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/juanpablosarobe/Documents/secretaria_personal/datos/secretaria.log</string>
</dict>
</plist>
```

**No cargarlo todavía.** Se carga en la tarea 12, cuando los flujos existen.

- [ ] **Step 5: Correr todo**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 46 tests

- [ ] **Step 6: Commit**

```bash
git add secretaria.py tests/test_secretaria.py com.fullcontrolgps.secretaria.plist
git commit -m "El esqueleto de la secretaria: dos hilos, un reloj y el freno de mano

El hilo que escucha no hace nada pesado. Clasificar tarda hasta tres
minutos con nvidia, y con un solo hilo JP le escribiría al bot y quedaría
mudo todo ese rato.

El reloj no duerme ni mira la hora por su cuenta: recibe el ahora y la
última vez que se lo consultó, así se prueba con fechas inventadas. Si la
secretaria estuvo caída doce horas devuelve todos los resúmenes que se
perdió, en orden, en vez de tragárselos.

Arranca en seco: clasifica y avisa, pero no toca la casilla. El freno se
saca de a un paso, primero archivar ruido —que es reversible— y otro día
marcar leído, para saber cuál rompió qué si algo rompe.

Ninguna excepción de los ciclos mata el proceso. La falla que más preocupa
es la silenciosa: dejar de mirar el correo se parece mucho a un día
tranquilo."
```

---

### Task 8: Revisar la casilla y archivar el ruido

**Files:**
- Modify: `secretaria.py` (`revisar_casilla`)
- Modify: `correo.py` (agregar `mover_a` y `marcar_leido`)
- Modify: `tests/test_secretaria.py`

**Interfaces:**
- Consumes: `memoria.anotar`, `clasificador.clasificar`, `reglas.es_ruido_conocido`, `reglas.protegido`
- Produces:
  - `correo.mover_a(message_id: str, carpeta: str) -> bool`
  - `correo.marcar_leido(message_id: str) -> bool`
  - `correo.devolver_a_bandeja(message_id: str) -> bool`
  - `correo.traer_nuevos(desde_fecha: datetime.date) -> list[dict]` — cada correo con las claves de `traer_correos` más `ya_leido: bool`
  - `Secretaria.revisar_casilla() -> int` — cuántos correos nuevos procesó
  - `Secretaria.avisar_en_el_momento(c: dict, pred: dict) -> None`
  - `Secretaria.avisar_sin_clasificar(c: dict) -> None`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# agregar a tests/test_secretaria.py
import tempfile
import unittest.mock as mock

import memoria
import secretaria


def correo_falso(mid, asunto, de="promo@ejemplo.com"):
    return {"message_id": mid, "uid": "1", "de": de, "para": "jp@x", "cc": "",
            "asunto": asunto, "fecha": "Mon, 17 Aug 2026 09:00:00 -0300",
            "cuerpo": "cuerpo", "adjuntos": []}


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
```

- [ ] **Step 2: Correr para ver que falla**

Run: `python3 -m unittest tests.test_secretaria -v`
Expected: FAIL con `NotImplementedError: tarea 8`

- [ ] **Step 3: Agregar a `correo.py` las tres operaciones de escritura**

```python
def _uid_de(M, message_id):
    """El UID actual de un mensaje, buscado por Message-ID.

    Nunca por número de secuencia: el número cambia en cuanto se archiva o
    se borra algo, y para entonces apunta a otro correo.
    """
    typ, d = M.uid("SEARCH", None, "HEADER", "Message-ID", f'"{message_id}"')
    uids = d[0].split()
    return uids[-1] if uids else None


def mover_a(message_id, carpeta):
    """Copia el mensaje a `carpeta` y lo borra de INBOX. False si no está.

    COPY + UID EXPUNGE porque el servidor no tiene MOVE pero sí UIDPLUS.
    Nunca EXPUNGE a secas: eso borraría otros mensajes marcados.
    """
    M = abrir_buzon(readonly=False)
    try:
        uid = _uid_de(M, message_id)
        if not uid:
            return False
        M.uid("COPY", uid, carpeta)
        M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        M.uid("EXPUNGE", uid)
        return True
    finally:
        M.logout()


def marcar_leido(message_id):
    M = abrir_buzon(readonly=False)
    try:
        uid = _uid_de(M, message_id)
        if not uid:
            return False
        M.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        return True
    finally:
        M.logout()


def devolver_a_bandeja(message_id):
    """Saca un mensaje de INBOX.Ruido y lo deja en INBOX SIN LEER.

    Sin leer a propósito: si JP dice que no era ruido, tiene que
    encontrarlo como encontraría cualquier correo que no vio.
    """
    M = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"],
                          int(os.environ["IMAP_PORT"]), timeout=40)
    M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    try:
        M.select("INBOX.Ruido", readonly=False)
        uid = _uid_de(M, message_id)
        if not uid:
            return False
        M.uid("COPY", uid, "INBOX")
        M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        M.uid("EXPUNGE", uid)
        M.select("INBOX", readonly=False)
        nuevo = _uid_de(M, message_id)
        if nuevo:
            M.uid("STORE", nuevo, "-FLAGS", "(\\Seen)")
        return True
    finally:
        M.logout()


def traer_nuevos(desde_fecha):
    """Los correos recibidos desde una fecha, con marca de si vienen leídos.

    `ya_leido` importa: si JP lo abrió del celular antes de que la
    secretaria lo mire, no hay que interrumpirlo con algo que ya vio.
    """
    M = abrir_buzon(readonly=True)
    try:
        criterio = (f"{desde_fecha.day:02d}-{MESES_IMAP[desde_fecha.month - 1]}"
                    f"-{desde_fecha.year}")
        typ, d = M.uid("SEARCH", None, "SINCE", criterio)
        salida = []
        for uid in d[0].split():
            typ, dd = M.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
            crudo = dd[0][1]
            banderas = str(dd[0][0])
            msg = email.message_from_bytes(crudo, policy=email.policy.default)
            salida.append({
                "uid": uid.decode(),
                "de": str(msg.get("From", ""))[:200],
                "para": str(msg.get("To", ""))[:300],
                "cc": str(msg.get("Cc", ""))[:300],
                "asunto": str(msg.get("Subject", "(sin asunto)"))[:200],
                "fecha": str(msg.get("Date", "")),
                "message_id": str(msg.get("Message-ID", "")),
                "cuerpo": texto_plano(msg)[:4000],
                "adjuntos": adjuntos(msg),
                "ya_leido": "\\Seen" in banderas,
            })
        return salida
    finally:
        M.logout()
```

- [ ] **Step 4: Implementar `revisar_casilla`**

```python
    def revisar_casilla(self):
        from datetime import date, timedelta
        entrantes = correo.traer_nuevos(date.today() - timedelta(days=1))
        sistema = clasificador.prompt_sistema()
        direcciones, dominios = reglas.remitentes_ruido()
        nuevos = 0
        for c in entrantes:
            if memoria.situacion(self.cx, correo.identidad(c)) is not None:
                continue
            c["message_id"] = correo.identidad(c)
            nuevos += 1
            try:
                pred = clasificador.clasificar(sistema, c)
            except Exception as e:
                # Ningún motor respondió. Se le muestra a JP igual: el
                # silencio no es una categoría.
                memoria.anotar(self.cx, c, "ERROR", f"{type(e).__name__}")
                self.avisar_sin_clasificar(c)
                continue
            memoria.anotar(self.cx, c, pred["categoria"], pred.get("motivo", ""))
            if (pred["categoria"] == "RUIDO" and pred.get("unanime")
                    and not reglas.protegido(c)):
                if not EN_SECO and correo.mover_a(c["message_id"], "INBOX.Ruido"):
                    memoria.cambiar(self.cx, c["message_id"], "archivado")
            elif pred["categoria"] in ("TUYO", "ENZO", "NATALIA"):
                if not c.get("ya_leido"):
                    self.avisar_en_el_momento(c, pred)
        return nuevos
```

- [ ] **Step 5: Implementar los dos avisos que usa `revisar_casilla`**

```python
    def avisar_en_el_momento(self, c, pred):
        """El único aviso que interrumpe a JP. Son unos 4 por día.

        Fuera de horario no interrumpe: el correo queda como clasificado y
        entra en el resumen de las 8:30. La excepción son los clientes
        importantes, que interrumpen siempre — la lista está vacía hoy, así
        que en la práctica todavía no hay excepción.
        """
        import secrets
        from datetime import datetime
        importante = reglas.protegido(c) and pred["categoria"] == "TUYO"
        if not en_horario(datetime.now()) and not importante:
            return
        tanda = secrets.token_hex(2)
        titulo = ("📌 Es tuyo" if pred["categoria"] == "TUYO"
                  else f"➡️ Para derivar a {pred['categoria'].title()}")
        texto = (f"<b>{titulo}</b> · <i>{html.escape(correo.fecha_legible(c['fecha']))}</i>\n"
                 f"<b>De:</b> {html.escape(c['de'][:90])}\n"
                 f"<b>CC:</b> {html.escape((c['cc'] or '(nadie)')[:90])}\n"
                 f"<b>Asunto:</b> {html.escape(c['asunto'][:120])}\n"
                 f"{html.escape(correo.adjuntos_legibles(c.get('adjuntos')))}\n\n"
                 f"<pre>{html.escape((c['cuerpo'] or '')[:600])}</pre>")
        self.enviar(texto, {"inline_keyboard": [[
            {"text": "Listo, lo vi", "callback_data": f"v|{tanda}-1|ok"},
            {"text": "No era mío", "callback_data": f"n|{tanda}-1|0"}]]})
        memoria.cambiar(self.cx, c["message_id"], "avisado")

    def avisar_sin_clasificar(self, c):
        """Ningún motor respondió. Se le muestra igual, con los botones de
        siempre: si el sistema no sabe, decide JP. Nunca se archiva."""
        import secrets
        tanda = secrets.token_hex(2)
        texto = (f"<b>⚠️ No pude clasificarlo</b>\n"
                 f"<b>De:</b> {html.escape(c['de'][:90])}\n"
                 f"<b>Asunto:</b> {html.escape(c['asunto'][:120])}\n\n"
                 f"<pre>{html.escape((c['cuerpo'] or '')[:600])}</pre>\n\n"
                 f"¿Qué correspondía?")
        self.enviar(texto, bot.teclado(1, tanda))

    def enviar(self, texto, teclado=None):
        """Manda a Telegram. Una falla acá no puede matar el proceso."""
        try:
            return bot.tg("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                          text=texto, parse_mode="HTML",
                          reply_markup=teclado) if teclado else \
                   bot.tg("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                          text=texto, parse_mode="HTML")
        except Exception as e:
            print(f"[telegram] no pude enviar: {type(e).__name__}: {e}", flush=True)
            return None
```

Agregar `import html` arriba, y mover `fecha_legible` de `simulacro.py` a `correo.py` (con su test en `tests/test_correo.py`).

- [ ] **Step 6: Correr todo**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 50 tests

- [ ] **Step 7: Commit**

```bash
git add correo.py secretaria.py tests/test_secretaria.py tests/test_correo.py
git commit -m "Revisar la casilla y archivar el ruido, con el freno puesto

Las tres operaciones que escriben en la casilla viven en correo.py y todas
buscan por Message-ID, nunca por posición: el número de secuencia cambia en
cuanto se archiva algo y para entonces apunta a otro correo. mover_a usa
COPY más UID EXPUNGE porque el servidor no tiene MOVE pero sí UIDPLUS;
nunca EXPUNGE a secas, que borraría otros mensajes marcados.

devolver_a_bandeja deja el correo SIN LEER. Si JP dice que algo no era
ruido, tiene que encontrarlo como encontraría cualquier correo que no vio.

Y el test que más importa: un correo que no se pudo clasificar nunca se
archiva. Se le muestra a JP. El silencio no es una categoría."
```

---

### Task 9: Los resúmenes de 8:30 y 17:00

**Files:**
- Modify: `secretaria.py` (`mandar_resumen`, `armar_resumen`)
- Create: `tests/test_resumen.py`

**Interfaces:**
- Consumes: `memoria.del_dia`, `bot.tg`
- Produces:
  - `Secretaria.armar_resumen(correos: list[dict], momento: str) -> tuple[str, dict]` — (texto HTML, teclado)
  - `Secretaria.mandar_resumen(momento: str) -> None`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_resumen.py
import tempfile
import unittest

import memoria
import secretaria


def fila(mid, asunto, categoria, de="alguien@ejemplo.com"):
    return {"message_id": mid, "asunto": asunto, "categoria": categoria,
            "de": de, "motivo": "porque sí", "cc": "", "adjuntos": "[]",
            "fecha": "Mon, 17 Aug 2026 09:00:00 -0300"}


class Resumen(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def test_lo_de_jp_va_primero_y_lo_del_equipo_al_final(self):
        texto, _ = self.s.armar_resumen(
            [fila("<1@x>", "Del equipo", "DELEGADO"),
             fila("<2@x>", "Mío", "TUYO")], "manana")
        self.assertLess(texto.index("Mío"), texto.index("Del equipo"))

    def test_sin_nada_igual_manda_un_mensaje(self):
        """No recibir avisos se parece mucho a un día tranquilo. Que la
        secretaria esté rota tiene que verse."""
        texto, _ = self.s.armar_resumen([], "manana")
        self.assertIn("no entró nada", texto.lower())

    def test_el_boton_de_leer_todo_solo_aparece_si_hay_del_equipo(self):
        _, teclado = self.s.armar_resumen([fila("<1@x>", "Mío", "TUYO")], "manana")
        textos = [b["text"] for f in teclado["inline_keyboard"] for b in f]
        self.assertNotIn("✓ Leí todo", textos)

    def test_cada_resumen_lleva_su_propio_token(self):
        """Dos resúmenes del mismo día no pueden compartir token, si no el
        botón 1 del de la mañana contesta por el de la tarde."""
        _, a = self.s.armar_resumen([fila("<1@x>", "A", "DELEGADO")], "manana")
        _, b = self.s.armar_resumen([fila("<2@x>", "B", "DELEGADO")], "tarde")
        da = a["inline_keyboard"][0][0]["callback_data"]
        db = b["inline_keyboard"][0][0]["callback_data"]
        self.assertNotEqual(da.split("|")[1], db.split("|")[1])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr para ver que falla**

Run: `python3 -m unittest tests.test_resumen -v`
Expected: FAIL con `NotImplementedError: tarea 9`

- [ ] **Step 3: Implementar el armado**

```python
    SALUDO = {"manana": "Buen día.", "tarde": "Cierre del día.",
              "ruido": "Lo que archivé hoy."}

    def armar_resumen(self, correos, momento):
        """El texto y los botones de un resumen.

        Lo de JP y lo derivable van arriba y sin botón de cierre: quedan
        abiertos hasta que haga algo. Lo del equipo se cierra de un toque.
        """
        import secrets
        tanda = secrets.token_hex(2)
        mios = [c for c in correos if c["categoria"] == "TUYO"]
        derivar = [c for c in correos if c["categoria"] in ("ENZO", "NATALIA")]
        equipo = [c for c in correos if c["categoria"] == "DELEGADO"]
        ruido = [c for c in correos if c["categoria"] == "RUIDO"]

        if not correos:
            return (f"{self.SALUDO[momento]} No entró nada nuevo.", 
                    {"inline_keyboard": []})

        lineas = [f"{self.SALUDO[momento]} Entraron {len(correos)} correos."]
        if mios:
            lineas.append(f"\n📌 <b>Tuyo ({len(mios)})</b>")
            lineas += [f"  · {html.escape(c['de'][:34])} — "
                       f"{html.escape(c['asunto'][:48])}" for c in mios]
        if derivar:
            lineas.append(f"\n➡️ <b>Para derivar ({len(derivar)})</b>")
            lineas += [f"  · {html.escape(c['asunto'][:44])} → "
                       f"{c['categoria'].title()}" for c in derivar]
        if equipo:
            lineas.append(f"\n✅ <b>El equipo lo maneja ({len(equipo)})</b>")
            lineas += [f"  {n}. {html.escape(c['de'][:26])} — "
                       f"{html.escape(c['asunto'][:40])}"
                       for n, c in enumerate(equipo, 1)]
        if ruido:
            lineas.append(f"\n🗑 Archivado como ruido: {len(ruido)}")

        filas = []
        if equipo:
            filas.append([{"text": "✓ Leí todo",
                           "callback_data": f"t|{tanda}-0|equipo"},
                          {"text": "⚠ Uno es mío",
                           "callback_data": f"m|{tanda}-0|equipo"}])
        self.abiertos[tanda] = [c["message_id"] for c in equipo]
        return "\n".join(lineas), {"inline_keyboard": filas}
```

En `__init__` agregar `self.abiertos = {}`, que asocia cada token con los correos que ese resumen puede cerrar. Sin eso, `Leí todo` no sabe cuáles marcar.

- [ ] **Step 4: Correr todo**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 54 tests

- [ ] **Step 5: Commit**

```bash
git add secretaria.py tests/test_resumen.py
git commit -m "Los resúmenes de 8:30 y 17:00

Lo de JP y lo derivable van arriba y sin botón de cierre: quedan abiertos
hasta que haga algo. Lo del equipo se cierra de a uno o todo junto.

El resumen sale aunque no haya entrado nada. 'No entró nada nuevo' es
información: si la secretaria se rompe y deja de avisar, el silencio se
parece demasiado a un día tranquilo.

Cada resumen lleva su propio token. Dos resúmenes del mismo día no pueden
compartirlo o el botón 1 del de la mañana contestaría por el de la tarde."
```

---

### Task 10: El resumen de ruido de las 18:00 y el circuito de corrección

**Files:**
- Modify: `secretaria.py`
- Modify: `tests/test_resumen.py`

**Interfaces:**
- Consumes: `correo.devolver_a_bandeja`, `memoria.corregir`, `bot.transcribir`
- Produces:
  - `Secretaria.armar_resumen_de_ruido(correos: list[dict]) -> tuple[str, dict]`
  - `Secretaria.rever_ruido(message_id: str, explicacion: str) -> str` — devuelve la categoría nueva

- [ ] **Step 1: Escribir el test que falla**

```python
# agregar a tests/test_resumen.py
import unittest.mock as mock


class ReverRuido(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))
        memoria.anotar(self.s.cx,
                       {"message_id": "<r@x>", "uid": "1", "de": "sipago@x",
                        "para": "jp@x", "cc": "", "asunto": "Manual",
                        "fecha": "", "cuerpo": "texto", "adjuntos": []},
                       "RUIDO", "parecía promo")

    def test_vuelve_a_la_bandeja_se_reclasifica_y_guarda_el_motivo(self):
        """Sacarlo de Ruido y dejarlo ahí sería devolverle el trabajo a JP."""
        with mock.patch.object(secretaria.correo, "devolver_a_bandeja",
                               return_value=True) as devolver, \
             mock.patch.object(secretaria.clasificador, "clasificar",
                               return_value={"categoria": "NATALIA",
                                             "motivo": "cobros", "unanime": True}):
            nueva = self.s.rever_ruido("<r@x>", "SiPago es mi proveedor de cobros")
        devolver.assert_called_once_with("<r@x>")
        self.assertEqual(nueva, "NATALIA")
        f = self.s.cx.execute("SELECT categoria, categoria_jp, explicacion"
                              " FROM correos WHERE message_id='<r@x>'").fetchone()
        self.assertEqual(f["categoria"], "RUIDO")
        self.assertEqual(f["categoria_jp"], "NATALIA")
        self.assertIn("cobros", f["explicacion"])
```

- [ ] **Step 2: Correr para ver que falla**

Run: `python3 -m unittest tests.test_resumen -v`
Expected: FAIL con `AttributeError: 'Secretaria' object has no attribute 'rever_ruido'`

- [ ] **Step 3: Implementar**

`rever_ruido` hace tres cosas en orden: devuelve el correo a la bandeja sin leer, lo reclasifica pasándole la explicación de JP como contexto adicional, y guarda la corrección. Si la reclasificación falla, el correo igual vuelve a la bandeja — nunca se queda en Ruido después de que JP dijo que no era ruido.

- [ ] **Step 4: Correr todo**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 55 tests

- [ ] **Step 5: Commit**

```bash
git add secretaria.py tests/test_resumen.py
git commit -m "El resumen de ruido de las 18:00 y el circuito de Rever

Rever hace tres cosas y las tres importan: el correo vuelve a la bandeja
sin leer, se reclasifica con lo que JP acaba de explicar, y si ahora es de
Natalia aparece como pendiente de derivar. Sacarlo de Ruido y dejarlo ahí
tirado sería devolverle el trabajo.

Si la reclasificación falla, el correo vuelve igual: nunca se queda en
Ruido después de que JP dijo que no era ruido."
```

---

### Task 11: Escuchar a JP — botones, audios y comandos

**Files:**
- Modify: `secretaria.py` (`atender`)
- Create: `tests/test_escucha.py`

**Interfaces:**
- Consumes: `bot.es_de_esta_tanda`, `bot.transcribir`, `clasificador.elegir_motor`
- Produces:
  - `Secretaria.atender(update: dict) -> None`
  - Comandos: `/pausa`, `/sigo`, `/motor <nombre>`, `/estado`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_escucha.py
import tempfile
import unittest
import unittest.mock as mock

import memoria
import secretaria


class Comandos(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.s = secretaria.Secretaria(cx=memoria.abrir(f.name))

    def _mensaje(self, texto):
        return {"message": {"text": texto, "chat": {"id": 1}}}

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
             mock.patch.object(secretaria.clasificador, "elegir_motor",
                               side_effect=ValueError("motor desconocido: x. Hay: groq, nvidia")):
            self.s.atender(self._mensaje("/motor x"))
        self.assertIn("nvidia", enviar.call_args.kwargs["text"])

    def test_pausada_no_toca_la_casilla(self):
        self.s.pausada = True
        with mock.patch.object(secretaria.correo, "traer_nuevos",
                               return_value=[]) as traer, \
             mock.patch.object(secretaria.correo, "mover_a") as mover:
            self.s.revisar_casilla()
        mover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr para ver que falla**

Run: `python3 -m unittest tests.test_escucha -v`
Expected: FAIL con `NotImplementedError: tarea 11`

- [ ] **Step 3: Implementar `atender` y los comandos**

```python
    def atender(self, update):
        """Todo lo que llega de Telegram entra por acá.

        Tres formas: un botón, un audio, o texto. El texto que empieza con
        barra es un comando; el resto es la respuesta a lo último que se
        preguntó.
        """
        cq = update.get("callback_query")
        if cq:
            return self.atender_boton(cq)
        m = update.get("message") or {}
        if m.get("voice"):
            try:
                texto = bot.transcribir(m["voice"]["file_id"])
            except Exception as e:
                self.enviar(f"No pude escuchar el audio ({type(e).__name__}). "
                            f"¿Me lo escribís?")
                return
        else:
            texto = (m.get("text") or "").strip()
        if not texto:
            return
        if texto.startswith("/"):
            return self.comando(texto)
        return self.responder_pendiente(texto)

    def comando(self, texto):
        from datetime import date
        partes = texto.split()
        cual = partes[0].lower()

        if cual == "/pausa":
            self.pausada = True
            return self.enviar("⏸ Frenada. No toco más la casilla — no "
                               "archivo ni marco leído. Te sigo avisando lo "
                               "que entra. <code>/sigo</code> para soltarla.")
        if cual == "/sigo":
            self.pausada = False
            return self.enviar("▶️ Listo, sigo trabajando.")
        if cual == "/motor":
            if len(partes) < 2:
                return self.enviar("Decime cuál. Por ejemplo: "
                                   "<code>/motor groq</code>")
            try:
                orden = clasificador.elegir_motor(partes[1])
            except ValueError as e:
                return self.enviar(f"⚠️ {html.escape(str(e))}")
            return self.enviar(f"Listo, ahora uso <b>{orden[0]}</b>. "
                               f"Si se queda sin cuota sigo con "
                               f"{', '.join(orden[1:]) or '(nada)'}.")
        if cual == "/estado":
            hoy = date.today().isoformat()
            n = self.cx.execute("SELECT COUNT(*) c FROM correos "
                                "WHERE visto >= ?", (hoy,)).fetchone()["c"]
            latido = memoria.ultimo_latido(self.cx) or "nunca"
            try:
                motor = clasificador.motores()[0][0]
            except Exception:
                motor = "ninguno configurado"
            return self.enviar(
                f"<b>Estado</b>\n"
                f"Correos de hoy: {n}\n"
                f"Último latido: {latido}\n"
                f"Motor: {motor}\n"
                f"En seco: {'sí' if EN_SECO else 'no'}\n"
                f"Pausada: {'sí' if self.pausada else 'no'}")
        return self.enviar("No conozco ese comando. Tengo "
                           "<code>/pausa</code>, <code>/sigo</code>, "
                           "<code>/motor</code> y <code>/estado</code>.")

    def atender_boton(self, cq):
        datos = cq.get("data") or ""
        tanda = datos.split("|")[1].split("-")[0] if "|" in datos else ""
        if tanda not in self.abiertos:
            bot.tg_suave("answerCallbackQuery", callback_query_id=cq["id"],
                         text="Ese botón es de otro correo, ya pasó.")
            return
        bot.tg_suave("answerCallbackQuery", callback_query_id=cq["id"])
        accion = datos.split("|")[0]
        if accion == "t":                      # leí todo
            for mid in self.abiertos.pop(tanda):
                if not EN_SECO and not self.pausada:
                    correo.marcar_leido(mid)
                memoria.cambiar(self.cx, mid, "cerrado")
```

`responder_pendiente` guarda el texto como respuesta a lo último que se preguntó; si no hay nada pendiente, contesta que por ahora solo entiende comandos y botones — las preguntas libres son la Etapa 4.

Y en `revisar_casilla`, al principio: `if self.pausada: return 0`.

- [ ] **Step 4: Correr todo**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 60 tests

- [ ] **Step 5: Commit**

```bash
git add secretaria.py tests/test_escucha.py
git commit -m "Escuchar a JP: botones, audios y cuatro comandos

/pausa es el freno de emergencia: deja de tocar la casilla pero sigue
avisando lo que entra. /motor cambia el modelo sin abrir una terminal,
que es como JP quiere trabajar, y si se equivoca de nombre le contesta
cuáles hay en vez de romperse.

/estado contesta lo que hace falta para saber si está viva: correos de hoy,
último latido, motor en uso, si está en seco y si está pausada."
```

---

### Task 12: Arranque en seco

**Files:**
- Modify: `CLAUDE.md`
- Create: `docs/operacion.md`

- [ ] **Step 1: Correr la suite completa y la regresión**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 60 tests

Run: `set -a; . ./.env; set +a; for f in datos/simulacro-*.json; do python3 -u revisar_reglas.py "$f" 2 --motor nvidia; done | grep -cE "SE ROMPIÓ"`
Expected: no más de 3

- [ ] **Step 2: Arrancar a mano y mirarla media hora**

Run: `set -a; . ./.env; set +a; SECRETARIA_EN_SECO=true python3 -u secretaria.py`

Verificar: que llegue un mensaje de arranque a Telegram, que `/estado` conteste, que no aparezca ningún `mover_a` en el log.

- [ ] **Step 3: Cargar el servicio**

```bash
cp com.fullcontrolgps.secretaria.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fullcontrolgps.secretaria.plist
launchctl list | grep secretaria
```

- [ ] **Step 4: Escribir el manual de operación**

`docs/operacion.md` con: cómo se arranca y se para, dónde está el log, qué significa cada comando de Telegram, cómo se saca el freno en dos pasos, y cómo se vuelve atrás.

- [ ] **Step 5: Anotar en `CLAUDE.md` lo que cambió**

Que ahora hay módulos y no un monolito, que los tests se corren con `python3 -m unittest discover tests`, que la cadena de motores está en `motores.json`, y que `SECRETARIA_EN_SECO=false` es lo que le saca el freno.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/operacion.md
git commit -m "La secretaria arranca en seco, y el manual para operarla

Clasifica, arma los resúmenes y los manda, pero no toca la casilla. Unos
días así con el correo real de JP, comparando contra lo que él haría, antes
de sacarle el freno.

El freno se saca de a un paso y nunca dos el mismo día: primero archivar
ruido, que es reversible porque el correo queda en una carpeta, y otro día
marcar leído. Si algo rompe, así se sabe qué lo rompió.

Antes del segundo paso hay que rotar la contraseña del correo, que se
compartió por chat. En seco no importa porque no escribe nada; en cuanto
archive, sí."
```

---

## Verificación final contra el diseño

| Sección del diseño | Tarea |
|---|---|
| 4.1 Por qué se parte el código | 1, 2, 3, 4 |
| 4.2 Las piezas | 1 (`correo`), 2 (`bot`), 3 (`reglas`), 4 (`clasificador`), 6 (`memoria`), 7 (`secretaria`) |
| 4.3 Los dos hilos | 7 |
| 4.4 Estado en SQLite | 6 |
| 5 Motores en configuración y `/motor` | 4, 11 |
| 6 Situación de un correo | 6, 8 |
| 7.1 Resúmenes 8:30 y 17:00 | 9 |
| 7.2 Aviso en el momento y horario | 7 (`en_horario`), 8 |
| 7.3 Ruido 18:00 y circuito de Rever | 10 |
| 7.4 Qué guarda una corrección | 6, 10 |
| 8 Fallas y `/pausa` | 7, 11 |
| 9 Verificación y arranque en tres pasos | 12 |
| 1 Arreglo de ENZO | 5 |
