# Secretaria personal

Triage automático del correo institucional de Full Control GPS. El diseño
completo está en `docs/superpowers/specs/2026-08-08-triage-mail-institucional-design.md`
y es la fuente de verdad: si el código y el spec discrepan, se corrige uno de
los dos, no se deja la discrepancia.

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

## Archivos

| Archivo | Qué es |
|---|---|
| `roster.md` | Equipo, áreas de responsabilidad, y lista de clientes importantes |
| `reglas.md` | Reglas de clasificación. Las escribe la secretaria, las edita JP |
| `plantillas.md` | Texto de reserva cuando falla la validación del mensaje generado |
| `simulacro.py` | Triage por Telegram en modo lectura. Genera el set de pruebas |
| `revisar_reglas.py` | Reclasifica un simulacro guardado; detecta aprendizajes y regresiones |
| `datos/` | Simulacros guardados. Contiene correo real de clientes: no se versiona |

## Seguridad

El sistema puede escribir correo a clientes con la firma de JP. Antes de tocar
`actions` o la generación de mensajes, leer las secciones 7.1, 8.4 y 14 del
diseño: la graduación de confianza, el validador y el freno de emergencia están
ahí por razones concretas, no por prolijidad.
