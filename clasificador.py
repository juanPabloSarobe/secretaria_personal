#!/usr/bin/env python3
"""
El clasificador: el prompt del sistema y la cadena de motores de LLM.

Antes vivía adentro de simulacro.py. Sale a su propio módulo porque
explorar.py y revisar_reglas.py también lo necesitan, y porque la cadena de
motores dejó de ser algo escrito a mano en Python: ahora vive en motores.json,
que este módulo relee en cada clasificación. Así /motor desde Telegram puede
cambiar el orden sin que nadie reinicie el proceso.
"""
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter

from bot import UA
from correo import TIPOS_ADJUNTO
from reglas import texto_de_conocimiento


def prompt_sistema():
    return f"""Sos la secretaria de Juan Pablo Sarobe (JP), director de Full Control GPS.
Clasificás su correo entrante.

{texto_de_conocimiento()}

PROCEDIMIENTO. Contestá estas preguntas EN ESTE ORDEN y frená en la primera que
dé resultado. El orden no es una sugerencia: una pregunta posterior nunca revierte
lo que decidió una anterior.

PASO 1 — ¿Quién ESCRIBE el correo?
  Si el remitente es del dominio fullcontrolgps.com.ar (Enzo, Natalia, o cualquier
  casilla propia), entonces el equipo YA está actuando sobre este asunto. La
  respuesta es DELEGADO y terminás acá.
  Esto vale aunque el texto sea una despedida, un agradecimiento o algo cariñoso:
  esa cortesía ya la escribió el equipo, no hay nada que JP tenga que agregar.
  ÚNICA EXCEPCIÓN: que le pidan ayuda a JP de forma explícita. Entonces es TUYO.

PASO 2 — ¿Este correo es AJENO a la operación de Full Control GPS?
  RUIDO es lo que no tiene nada que ver con el trabajo con clientes,
  proveedores o el equipo: publicidad, ventas frías, boletines, invitaciones a
  eventos, capacitaciones, avisos de mantenimiento programado de proveedores.
  Terminás acá.

  CUIDADO, dos cosas que NO son ruido:
  - Un correo dentro de un hilo de trabajo con un cliente o con el equipo
    NUNCA es ruido, aunque no quede nada pendiente por hacer. Avisar que algo
    ya se completó es parte de la operación, no publicidad.
  - Un pago rechazado o una factura con problema tampoco: exigen acción.

  Que el texto use vocabulario técnico o mencione dinero no lo hace ruido; lo
  que decide es si el asunto pertenece a la operación o le es ajeno.

PASO 3 — ¿El correo es para JP, o es una escalación?
  Antes de responder que sí, preguntate: ¿qué tendría que HACER JP con esto?
  Si la única respuesta es "leerlo para estar al tanto", entonces NO es suyo:
  es DELEGADO. Estar enterado no es tener una acción pendiente. Un mensaje que
  dos personas se cruzan entre ellas, con JP en copia, es información.
  Conflicto, disconformidad, pedido de reunión de alguien CON relación previa,
  desarrollo nuevo, despedida o agradecimiento dirigido a JP, o un asunto de la
  lista de personales. Si es así: TUYO, sin importar el tema.

PASO 4 — ¿El cliente vuelve a pedir una tarea que sigue sin hacerse?
  ANTES QUE NADA: si este correo le CONTESTA a un mensaje del equipo —el texto
  citado justo arriba lo escribió tecnicos@fullcontrolgps.com.ar o
  administracion@fullcontrolgps.com.ar— entonces el equipo ya actuó y esto es
  DELEGADO. Este paso no se aplica: seguí al paso siguiente.

  Si no, el paso es angosto a propósito. Se aplica a un PEDIDO CONCRETO de esta
  lista corta y de ninguna otra cosa:
    - una programación remota o local de un equipo
    - un cambio de zona o de velocidad de zona
    - los IMEI de unos equipos
    - un certificado de calibración ("bajada de tacógrafo")
    - un alta de usuario o de contraseña en la plataforma
  Y solo cuando el cliente INSISTE: repite un pedido que ya había hecho, refuerza
  el de un colega, o pregunta si lograron avanzar con esa solicitud. En ese caso
  la respuesta es ENZO y terminás acá.

  Es por el seguimiento: estas tareas se cargan desde el teclado, tardan días, y
  si nadie las sigue quedan sin hacer. ESTAR EN COPIA NO CANCELA LA TAREA: que
  tecnicos@fullcontrolgps.com.ar figure en De, en Para o en CC significa que se
  enteró, no que la tarea esté hecha.

  Nada más entra acá. En particular NO entran, y se deciden en el paso siguiente:
    - un equipo que dejó de reportar, que no registra movimientos o que anda mal:
      eso es diagnóstico, no una tarea cargada y pendiente;
    - el mantenimiento físico de una unidad, un turno o la visita de un técnico;
    - presupuestos, cotizaciones, facturación, órdenes de compra, infracciones;
    - un pedido que llega por primera vez.

PASO 5 — ¿De qué se trata, y quién ya está en el hilo?
  Determiná el responsable por el tema (Enzo o Natalia). Después fijate si su
  casilla aparece en De, en Para o en CC.

  Compará por DIRECCIÓN DE CORREO, no por el nombre que se muestra. Un
  destinatario puede figurar como "Tecnicos2 <tecnicos@fullcontrolgps.com.ar>":
  lo que cuenta es tecnicos@fullcontrolgps.com.ar, sin importar cómo lo
  apodaron. Lo mismo con administracion@fullcontrolgps.com.ar.

    - Aparece en alguno de los tres  -> DELEGADO
    - No aparece en ninguno          -> ENZO o NATALIA, según corresponda

PASO 6 — Si no alcanza la información para decidir: DUDA.

Categorías: RUIDO, DELEGADO, ENZO, NATALIA, TUYO, DUDA.

Respondé SOLO un objeto JSON, sin texto alrededor:
{{"categoria":"<una de las seis>","motivo":"<máximo 12 palabras>","confianza":"alta|media|baja"}}"""


# Más allá de esto no es un límite pasajero sino cuota agotada: no tiene sentido
# dormir, conviene cambiar de motor. Groq llegó a mandar Retry-After de 1462s.
# Nemotron razona antes de contestar y no manda nada hasta terminar: con 60s
# se cortaba la conexión a mitad de pensamiento y el correo quedaba sin
# clasificar. Es el motor único mientras ollama esté fuera, así que conviene
# tenerle paciencia.
ESPERA_RESPUESTA = 180
ESPERA_MAXIMA = 90

# Nombre del archivo de configuración de la cadena de motores. Vive en el
# directorio de trabajo, igual que roster.md y reglas.md: no está escrito a
# mano en el código porque JP lo cambia desde Telegram con /motor, y ese
# cambio tiene que sobrevivir sin reiniciar el proceso.
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
    """Motores disponibles, en orden de uso.

    Descarta los que no tengan credenciales cargadas, así el catálogo puede
    listar más proveedores de los que estén configurados en esta máquina.
    """
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


def _pedir(base, key, cuerpo, intentos=4):
    """Una petición a un motor, tolerando límites pasajeros.

    Reintenta los 429 y los 5xx. Respeta Retry-After solo si la espera es
    razonable: si el proveedor pide más que ESPERA_MAXIMA, la cuota está
    agotada y quien llama debería probar otro motor en vez de dormirse.
    """
    req = urllib.request.Request(
        base + "/chat/completions", data=cuerpo,
        headers={**UA, "Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    for intento in range(intentos):
        try:
            with urllib.request.urlopen(req, timeout=ESPERA_RESPUESTA) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise
            pedida = float(e.headers.get("Retry-After") or 0)
            if pedida > ESPERA_MAXIMA:
                raise RuntimeError(
                    f"cuota agotada: el proveedor pide esperar {pedida:.0f}s") from e
            if intento == intentos - 1:
                raise
            espera = pedida or min(2 ** intento, 30)
            print(f"      (HTTP {e.code} — esperando {espera:.0f}s)", flush=True)
            time.sleep(espera + 0.5)
        except OSError as e:
            # Corte de red o de paciencia: la conexión se cayó, o el modelo
            # tardó más de lo que esperamos. No es cuota agotada —cambiar de
            # motor por esto sería tirar el bueno— así que se reintenta acá.
            if intento == intentos - 1:
                raise
            espera = min(2 ** intento, 30)
            print(f"      (red: {e} — reintento en {espera:.0f}s)", flush=True)
            time.sleep(espera)


def clasificar(sistema, correo, preferido=None, pasadas=3):
    """Clasifica con doble pasada y desempate.

    Dos pasadas que coinciden alcanzan. Si difieren, una tercera decide por
    mayoría. Si las tres difieren, la categoría es DUDA y se pregunta.

    El motivo es que el campo `confianza` que devuelve el modelo no sirve como
    señal de duda: dijo "alta" mientras se equivocaba en los tres correos de
    SiPago. La discrepancia entre pasadas sí sirve — marca los casos donde dos
    reglas del archivo compiten. Medido sobre 46 casos: 11 oscilan, y en todos
    la mayoría acierta. Una sola pasada se los juega a cara o cruz.
    """
    votos = []
    for n in range(pasadas):
        votos.append(clasificar_una_vez(sistema, correo, preferido))
        # dos iguales seguidas bastan: no se paga una tercera al pedo
        if n == 1 and votos[0]["categoria"] == votos[1]["categoria"]:
            return {**votos[0], "pasadas": 2, "unanime": True}

    conteo = Counter(v["categoria"] for v in votos)
    categoria, apoyos = conteo.most_common(1)[0]
    emitidas = [v["categoria"] for v in votos]

    if apoyos == 1:                     # ninguna se repitió: ambigüedad real
        return {"categoria": "DUDA",
                "motivo": "sin acuerdo entre pasadas: " + " / ".join(emitidas),
                "confianza": "baja", "pasadas": len(votos), "unanime": False,
                "emitidas": emitidas}

    ganador = next(v for v in votos if v["categoria"] == categoria)
    return {**ganador, "pasadas": len(votos), "unanime": False,
            "emitidas": emitidas}


def _adjuntos_para_modelo(correo):
    """Los adjuntos como texto para el prompt, avisando si el cuerpo está vacío.

    El aviso importa: un remito escaneado llega con el cuerpo en blanco, y sin
    esa aclaración el modelo lee un correo sin contenido y lo trata como ruido.
    """
    lista = correo.get("adjuntos") or []
    if not lista:
        return ""
    linea = "Adjuntos: " + "; ".join(
        f"{a['nombre']} ({TIPOS_ADJUNTO.get(a['tipo'], a['tipo'])}, {a['kb']} KB)"
        for a in lista)
    if len((correo.get("cuerpo") or "").strip()) < 200:
        linea += ("\n(El cuerpo está casi vacío: el contenido real del correo "
                  "está en el adjunto, que no podés leer. Clasificá por el "
                  "asunto, el remitente y el nombre del archivo.)")
    return linea + "\n"


def clasificar_una_vez(sistema, correo, preferido=None):
    disponibles = motores(preferido)
    for n, (nombre, base, key, modelo) in enumerate(disponibles):
        cuerpo = json.dumps({
            "model": modelo,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content":
                 f"Fecha: {correo.get('fecha') or '(desconocida)'}\n"
                 f"De: {correo['de']}\nPara: {correo['para']}\nCC: {correo['cc'] or '(nadie)'}\n"
                 f"Asunto: {correo['asunto']}\n"
                 f"{_adjuntos_para_modelo(correo)}"
                 f"\n{correo['cuerpo'][:3000]}"},
            ],
            # 3000 y no 500: los modelos de razonamiento gastan tokens pensando
            # antes de escribir, y con un presupuesto corto se cortan justo antes
            # del JSON. Los modelos comunes paran solos mucho antes, así que no
            # cuesta nada dejarles margen.
            "temperature": 0, "max_tokens": 3000,
        }).encode()
        try:
            msg = _pedir(base, key, cuerpo)["choices"][0]["message"]
            # algunos modelos dejan content en null y ponen todo en reasoning_content
            txt = msg.get("content") or msg.get("reasoning_content") or ""
            break
        except Exception as e:
            if n == len(disponibles) - 1:
                raise
            print(f"      ({nombre} falló: {e} — paso a {disponibles[n+1][0]})", flush=True)

    i, j = txt.find("{"), txt.rfind("}")
    try:
        return json.loads(txt[i:j + 1])
    except Exception:
        return {"categoria": "DUDA", "motivo": "respuesta ilegible", "confianza": "baja"}
