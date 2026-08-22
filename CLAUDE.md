# Secretaria personal

Triage automático del correo institucional de Full Control GPS. El diseño
completo está en `docs/superpowers/specs/2026-08-08-triage-mail-institucional-design.md`
y es la fuente de verdad de *qué hace* el sistema. Cómo se pone a correr
sola —etapas, arquitectura, freno de emergencia— está en
`docs/superpowers/specs/2026-08-17-secretaria-en-produccion-etapa-1-design.md`,
que manda donde los dos hablen del mismo tema. Si el código y algún spec
discrepan, se corrige uno de los dos, no se deja la discrepancia. Para
operarla día a día (arrancar, parar, leer el log, los comandos de
Telegram) el manual es `docs/operacion.md`, no este archivo.

## No es un monolito

Hasta agosto todo vivía en `simulacro.py`, 1.211 líneas haciendo IMAP,
Telegram, el modelo y las reglas de una. Le alcanzaba a un simulacro; con
un proceso permanente encima (reloj, estado en SQLite, dos hilos) se volvía
intocable. Ahora está partido en piezas, y **el clasificador que corre en
producción es el mismo que mide la regresión** —a propósito, para que los
133 casos guardados sigan diciendo algo sobre el sistema real—:

| Módulo | De qué se ocupa |
|---|---|
| `correo.py` | IMAP y SMTP: leer sin marcar, mover, marcar leído, enviar (Etapa 3) |
| `bot.py` | Telegram: mandar, botones, escuchar, transcribir audio |
| `clasificador.py` | El prompt, la cadena de motores (`motores.json`), la doble pasada |
| `reglas.py` | Leer y escribir `reglas.md`, `roster.md`, `codigos.md` |
| `memoria.py` | SQLite (`datos/secretaria.db`): situación de cada correo y pendientes |
| `secretaria.py` | El proceso: reloj, los dos hilos, y quién hace qué |

`simulacro.py`, `explorar.py`, `revisar_reglas.py` y `archivar_ruido.py`
siguen funcionando, importando estas mismas piezas en vez de tener su
propia copia.

## Convenciones que no son obvias

**Cuidado con las palabras que contienen comandos peligrosos como subcadena.**
Los permisos globales de este equipo deniegan por coincidencia de subcadena:
`Bash(*exec*)`, `Bash(*production*)`, `Bash(*sudo*)`. Un archivo o comando que
contenga esas letras se rechaza entero, con un mensaje de permisos que no
explica el motivo y parece un problema del entorno.

Ya pasó una vez con `Bash(*eval*)` y un archivo llamado `reevaluar.py`: tres
bloqueos seguidos, incluido el comando para diagnosticarlos. Esa regla se afinó
el 2026-08-08 a `Bash(eval)`, `Bash(eval *)`, `Bash(* eval *)` y las variantes
tras `;`, `&&` y `|`, así que "evaluar" y "reevaluar" ya no molestan. Las otras
tres siguen siendo por subcadena.

**`.env` no se puede leer con las herramientas de archivo** (`Read(.env*)` está
denegado, y está bien que lo esté). Para usar sus valores hay que exportarlos en
el shell: `set -a; . ./.env; set +a; python3 …`. Para cambiar algo del `.env`,
pedírselo a JP en lugar de intentar editarlo.

**El puerto SMTP es 465**, o sea TLS implícito: hay que usar `SMTP_SSL`, no
`STARTTLS` sobre 587.

**Las carpetas IMAP llevan prefijo `INBOX.`** con punto como separador. La
carpeta de ruido es `INBOX.Ruido`, no `Ruido`.

**Al leer correo hay que usar `BODY.PEEK[]`, nunca `RFC822`.** Un `FETCH` normal
marca como leídos los mensajes que no lo estaban, y eso ensucia la casilla real
de JP de una forma que no se puede deshacer cómodamente.

**El proveedor de LLM corta con HTTP 429** en el nivel gratuito ante llamadas
seguidas. El reintento con espera ya está en `clasificar()`; cualquier código
nuevo que llame al modelo debería pasar por ahí en vez de armar su propia
llamada.

**Los tests se corren con el `.env` cargado**, aunque no toquen la red:
`set -a; . ./.env; set +a; python3 -m unittest discover tests`. Sin eso,
`Secretaria.enviar()` revienta con `KeyError: 'TELEGRAM_CHAT_ID'` *antes*
de llegar al `bot.tg` que los tests mockean, así que el mock nunca se
llama y el test falla con un `AttributeError: 'NoneType' object has no
attribute 'kwargs'` que no menciona el `.env` para nada — un mensaje que
parece un bug de la suite y es sólo el entorno sin cargar. Con el `.env`
cargado son 279 tests, todos en unos segundos.

**La cadena de motores está en `motores.json`, no en el código.** Se
cambia sola desde Telegram con `/motor <nombre>` y se relee en cada
clasificación (`clasificador._config()`), así que un cambio no necesita
reiniciar el proceso. `SECRETARIA_EN_SECO` es distinto: es una variable de
`.env` que `secretaria.py` lee una sola vez al arrancar, así que cambiarla
sí necesita reiniciar el proceso para que valga.

**`SECRETARIA_EN_SECO=false` es lo que le saca el freno** a la secretaria
—clasifica y avisa siempre; en seco (el default) es lo único que además no
archiva ni marca leído—. Sacarlo es de a un paso y nunca los dos el mismo
día: el detalle de por qué, y cómo se separan en la práctica si sólo hay
un interruptor, está en `docs/operacion.md`. No tocar esto sin leerlo.

## Archivos

| Archivo | Qué es |
|---|---|
| `roster.md` | Equipo, áreas de responsabilidad, y lista de clientes importantes |
| `reglas.md` | Reglas de clasificación. Las escribe la secretaria, las edita JP |
| `codigos.md` | Códigos convenidos con interlocutores para marcar un correo como propio |
| `motores.json` | Cadena de motores de clasificación, en orden de preferencia. Se cambia con `/motor` |
| `plantillas.md` | Texto de reserva cuando falla la validación del mensaje generado. Etapa 3, todavía no existe |
| `simulacro.py` | Triage por Telegram en modo lectura. Genera el set de pruebas |
| `revisar_reglas.py` | Reclasifica un simulacro guardado; detecta aprendizajes y regresiones |
| `archivar_ruido.py` | Archiva a mano lo que JP ya marcó RUIDO en simulacros, para el correo atrasado |
| `com.fullcontrolgps.secretaria.plist` | El servicio de `launchd`. Escrito y probado, no cargado — ver `docs/operacion.md` |
| `docs/operacion.md` | Manual de uso: arrancar, parar, log, comandos de Telegram, freno |
| `datos/` | Estado real (`secretaria.db`) y simulacros guardados. Correo real de clientes: no se versiona |

## Seguridad

El sistema puede escribir correo a clientes con la firma de JP. Antes de tocar
`actions` o la generación de mensajes, leer las secciones 7.1, 8.4 y 14 del
diseño original y la sección 9 del diseño de producción: la graduación de
confianza, el validador y el freno de emergencia están ahí por razones
concretas, no por prolijidad.

La Etapa 1 (la que corre hoy) no manda correo a nadie: sólo clasifica,
archiva ruido y avisa por Telegram. Aun así arranca **en seco**
(`SECRETARIA_EN_SECO=true`, el default) y saca el freno de a un paso — ver
`docs/operacion.md` antes de tocar esa variable o de cargar el `plist` de
`launchd`.
