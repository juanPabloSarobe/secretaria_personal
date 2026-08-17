#!/usr/bin/env python3
"""
El conocimiento: roster del equipo, reglas aprendidas, códigos convenidos y
ruido conocido.

Este es el único módulo que sabe en qué archivo vive el roster, las reglas y
los códigos convenidos (roster.md, reglas.md, codigos.md): el clasificador le
pide texto_de_conocimiento() o protegido() y no abre esos tres por su cuenta.

Ojo, esto NO cubre datos/simulacro-*.json: ese es el historial de tandas, y
simulacro.py y archivar_ruido.py también lo leen directo, cada uno para lo
suyo (saber qué ya se respondió, decidir qué archivar). remitentes_ruido()
de acá abajo lo lee también, pero para derivar el ruido conocido — es una
lectura más entre varias, no una que este módulo tenga en exclusiva.
"""
import email.utils
import glob
import json
from datetime import datetime, timezone


# ------------------------------------------------------------------ Códigos convenidos
def _sin_acentos(s):
    tabla = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return s.translate(tabla).lower()


def codigos_convenidos():
    """Frases que JP le pide a sus contactos para marcar que él pidió el correo."""
    try:
        texto = open("codigos.md", encoding="utf-8").read()
    except FileNotFoundError:
        return []
    frases = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith("- ") and len(linea) > 6:
            frases.append(_sin_acentos(linea[2:].strip()))
    return frases


def tiene_codigo(correo, frases):
    """¿El correo trae alguna frase convenida? Comprobación literal.

    A propósito NO pasa por el modelo. Un correo que continúa una charla de
    JP se lee igual que publicidad no solicitada, y ese es justamente el caso
    que no puede quedar librado a criterio: si la frase está, es de JP y
    punto. Una comparación de texto no tiene pasadas ni desacuerdos.
    """
    donde = _sin_acentos(f"{correo.get('asunto','')} {correo.get('cuerpo','')}")
    for f in frases:
        if f in donde:
            return f
    return None


# ------------------------------------------------------------------ Ruido conocido
# Dominios de correo personal: jamás se pueden dar por ruido en bloque, porque
# el mismo dominio trae publicidad y clientes. En los datos, gmail.com ya
# aparece mezclado entre RUIDO y NATALIA.
DOMINIOS_GENERICOS = {"gmail.com", "hotmail.com", "yahoo.com", "yahoo.com.ar",
                      "outlook.com", "live.com", "icloud.com", "aol.com"}

# Cuántas veces JP tiene que haber marcado lo mismo antes de automatizarlo.
MARCAS_PARA_AUTOMATIZAR = 2

# De cada cuántos correos auto-clasificados se muestra uno igual, para no perder
# de vista si el criterio se degrada. Sin esto, automatizar es dejar de medir.
MUESTREO_CONTROL = 4


def remitentes_ruido():
    """Remitentes y dominios que JP marcó siempre como RUIDO.

    Solo cuenta lo que él decidió a mano, y solo si NUNCA los clasificó de otra
    forma. Un remitente mezclado queda afuera: en los datos, orbcomm.com llegó
    con RUIDO unas veces y TUYO otras, y automatizarlo habría escondido correos
    suyos.
    """
    direcciones, dominios = {}, {}
    for ruta in glob.glob("datos/simulacro-*.json"):
        try:
            d = json.load(open(ruta, encoding="utf-8"))
        except Exception:
            continue
        for c in d.get("casos", []):
            _, dire = email.utils.parseaddr(c.get("de", ""))
            dire = dire.lower().strip()
            if not dire or "@" not in dire:
                continue
            direcciones.setdefault(dire, []).append(c.get("correcto"))
            dom = dire.split("@")[1]
            if dom not in DOMINIOS_GENERICOS:
                dominios.setdefault(dom, []).append(c.get("correcto"))

    def uniformes(mapa):
        return {k for k, v in mapa.items()
                if len(v) >= MARCAS_PARA_AUTOMATIZAR and set(v) == {"RUIDO"}}

    return uniformes(direcciones), uniformes(dominios)


def es_ruido_conocido(correo, direcciones, dominios):
    """¿JP ya dijo, repetidamente, que este remitente es ruido?"""
    _, dire = email.utils.parseaddr(correo.get("de", ""))
    dire = dire.lower().strip()
    if not dire or "@" not in dire:
        return None
    if dire in direcciones:
        return f"remitente {dire}"
    dom = dire.split("@")[1]
    if dom in dominios:
        return f"dominio {dom}"
    return None


def protegido(correo):
    """Remitentes que nunca se archivan solos, por más que parezcan ruido.

    Cubre dos archivos, y el segundo se agregó por un error real: el filtro
    archivó una promoción de SiPago pese a que reglas.md dice explícitamente
    que nada de SiPago es ruido. JP no llegó a verlo nunca.

    El principio es simple: **sobre lo que JP ya se pronunció, el sistema no
    decide solo.** Si su nombre o su dominio figura en el roster o en las
    reglas, el correo se le muestra. Puede que la regla esté de más, o que
    corresponda afinarla — pero eso lo decide él viendo el caso, no un
    clasificador saltándose lo que él escribió.
    """
    _, dire = email.utils.parseaddr(correo.get("de", ""))
    dire = dire.lower().strip()
    if not dire or "@" not in dire:
        return False
    dominio = dire.split("@")[1]
    for archivo in ("roster.md", "reglas.md"):
        try:
            texto = open(archivo, encoding="utf-8").read().lower()
        except FileNotFoundError:
            continue
        if dire in texto or dominio in texto:
            return True
    return False


# ------------------------------------------------------------------ Roster
DOMINIO_PROPIO = "fullcontrolgps.com.ar"


def direcciones_externas(correo):
    """Direcciones del correo que no son del propio equipo, sin repetir."""
    crudas = email.utils.getaddresses(
        [correo["de"], correo["para"], correo.get("cc", "")])
    vistas, salida = set(), []
    for nombre, dire in crudas:
        dire = dire.strip().lower()
        if not dire or "@" not in dire or DOMINIO_PROPIO in dire or dire in vistas:
            continue
        vistas.add(dire)
        salida.append(((nombre.strip() or dire), dire))
    return salida[:8]


def marcar_importante(etiqueta, direccion):
    """Agrega la dirección a roster.md. Devuelve False si ya estaba."""
    texto = open("roster.md", encoding="utf-8").read()
    if direccion in texto:
        return False
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    linea = f"- {direccion}"
    if etiqueta and etiqueta.lower() != direccion:
        linea += f"  <!-- {etiqueta} -->"
    with open("roster.md", "a", encoding="utf-8") as f:
        f.write(f"{linea}  <!-- marcado {hoy} desde Telegram -->\n")
    return True


# ------------------------------------------------------------------ Conocimiento para el prompt
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
