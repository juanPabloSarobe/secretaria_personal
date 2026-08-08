# Diseño — Secretaria personal, Capa 1: triage del mail institucional

**Fecha:** 2026-08-08
**Estado:** aprobado, pendiente de plan de implementación
**Autor:** Juan Pablo Sarobe (con Claude)

---

## 1. Problema

La casilla institucional (`jpsarobe@fullcontrolgps.com.ar`, IMAP genérico) recibe varias decenas de correos por día — JP estima que llega a revisar unos veinte diarios, y durante una ausencia de pocos días se acumularon ~300. La gran mayoría no requiere acción de JP:

- Notificaciones automáticas y spam.
- Copias de tareas que ejecuta el equipo de trabajo.
- Confirmaciones de pagos, pagos rechazados y facturas rechazadas que llegan a esa casilla por ser el contacto principal de sistemas de terceros.

Lo que sí requiere acción de JP queda enterrado en ese volumen. Además, hay trabajo de coordinación que hoy se hace a mano y de memoria:

- Mails dirigidos solo a JP que corresponden a otra persona del equipo y hay que derivar.
- Seguimiento de si el equipo respondió en tiempo y forma.
- Pedidos de reunión dirigidos a JP personalmente.

Episodio de referencia: durante una mudanza, sin acceso a la computadora, se acumularon ~300 correos sin leer.

## 2. Objetivo

Que JP deje de revisar la casilla institucional. El sistema lee, clasifica, deriva lo que corresponde, hace seguimiento de lo derivado, y entrega dos veces por día un resumen con lo poco que efectivamente requiere su atención.

**Criterio de éxito:** después de un mes de uso, JP no entra a la casilla institucional salvo por curiosidad, y no se le pasa nada importante.

## 3. Alcance

**Dentro:**

- Lectura de la casilla por IMAP.
- Clasificación de cada correo entrante.
- Derivación automática por respuesta al hilo con copia al responsable.
- Seguimiento de hilos derivados y alerta por silencio.
- Aprendizaje de reglas a partir de las decisiones de JP.
- Bot de Telegram: briefings, alertas urgentes, botones de acción, consulta a demanda.

**Fuera (proyectos posteriores, en este orden):**

1. **Capa 2 — Captura de recordatorios.** Mandarle un audio o texto al bot desde cualquier lado y que lo devuelva en el momento correcto. Se apoya sobre la misma infraestructura (bot de Telegram + base de pendientes).
2. **Capa 3 — WhatsApp de la oficina.** Leer y avisar de lo que llega al número de la oficina, sin usar la API oficial. Es donde está el riesgo técnico del proyecto completo.

**Explícitamente fuera de este diseño:** el sistema nunca redacta contenido nuevo hacia afuera. Usa plantillas fijas y editables. No responde consultas de clientes en nombre de JP.

## 4. Actores

Definidos en `roster.md`, editable a mano.

| Persona | Rol | Casilla | Se ocupa de |
|---|---|---|---|
| Enzo | Técnico | `tecnicos@fullcontrolgps.com.ar` | Programación de equipos, reprogramaciones remotas, análisis y borrado de infracciones, análisis de funcionamiento y datos de equipos GPS, geocercas de velocidad, todo lo referido a programación de equipos |
| Natalia | Directora administrativa | `administracion@fullcontrolgps.com.ar` | Facturación, coordinación de turnos, coordinación del equipo, remitos y presupuestos, solicitud de OC, facturación de servicios, imputación de pagos, análisis de invoices con problemas, compras, respuestas generales a clientes |
| Juan Pablo | Dirección | `jpsarobe@fullcontrolgps.com.ar` | Grandes problemas y conflictos con clientes, disconformidades, nuevos desarrollos, clientes importantes que piden hablar con él, y todo lo que el resto del equipo no entienda, no pueda resolver, o le pida ayuda |

**Observación estructural:** Enzo y Natalia se definen por *tema*. JP se define por *severidad*. Una consulta de facturación es de Natalia; una disconformidad grave sobre esa misma factura es de JP. Por eso la regla de escalación **pisa** a la regla de tema.

`roster.md` también contiene la **lista de clientes importantes**, cuyos correos habilitan alerta fuera de horario.

## 5. Arquitectura

```
IMAP institucional ──cada 5 min──▶ Ingesta ──▶ Clasificador ◀── roster.md + reglas.md
                                                    │
              ┌─────────────┬─────────────┬─────────┴──────┬──────────────┐
            RUIDO       DELEGADO      A DERIVAR         TUYO            DUDA
              │        (ya en copia)       │               │               │
          archivar    seguimiento     responder a      va al           pregunta
          (conteo)     silencioso     todos + CC      briefing      en Telegram
                                      responsable          │               │
              └──────────────┴─────────────┴───────────────┘         respuesta
                                     │                                   │
                              Registro de hilos ◀────────────────────────┘
                                     │                          (+ regla nueva)
                          Vigilante de silencios
                                     │
                        Briefing 8:30 / 17:00 ──▶ Telegram
```

### 5.1 Módulos

Siete piezas, cada una con una responsabilidad y testeable por separado.

| Módulo | Responsabilidad | No conoce |
|---|---|---|
| `imap` | Traer correos nuevos, mover a carpetas, enviar por SMTP | Reglas, LLM, Telegram |
| `store` | Persistencia en SQLite | LLM, red |
| `classifier` | (correo, roster, reglas) → decisión. **Función pura.** | Cómo se ejecuta la decisión |
| `rules` | Leer y escribir `roster.md` y `reglas.md`; generar reglas nuevas | Correos |
| `actions` | Responder, archivar, abrir seguimiento. **Único módulo con permiso de escritura hacia afuera.** | Reglas, LLM |
| `bot` | Telegram: briefings, botones, recepción de respuestas | Correos, IMAP |
| `scheduler` | Disparar polleo, briefings, chequeo de silencios | Todo lo demás |

Que `classifier` sea una función pura es lo que hace posible el set de pruebas de la sección 12.

## 6. Clasificación

Para cada correo, el clasificador responde tres preguntas **en este orden**:

1. **¿Buscan a JP, o es una escalación?** Conflicto, disconformidad, nuevo desarrollo, cliente importante que pide hablar con él, o alguien del equipo pidiéndole ayuda. **Si la respuesta es sí, corta acá: es de JP**, sin importar el tema.
2. **¿De qué se trata?** GPS, programación, reprogramación remota, infracciones, geocercas, datos de equipos → **Enzo**. Facturación, remitos, presupuestos, OC, pagos, invoices con problema, turnos, compras, consulta general → **Natalia**.
3. **¿El responsable ya está entre los destinatarios (Para o CC)?** Sí → silencio, solo seguimiento. No → derivar.

### 6.1 Matriz de acción

| Categoría | Acción | ¿Interrumpe a JP? |
|---|---|---|
| **Ruido** — newsletters, notificaciones automáticas, spam | Mueve a la carpeta `Ruido` | No. Solo un conteo en el briefing |
| **Delegado** — tema de Enzo o Natalia, ya están en copia | Ninguna. Abre hilo de seguimiento | No. Solo si vence el plazo sin señal |
| **A derivar** — tema de Enzo o Natalia, no están en copia | Responde al hilo con copia a todos, agregando al responsable. Abre seguimiento | Aparece en el briefing como hecho consumado |
| **Tuyo** — escalación, reunión, pedido personal | Ninguna acción automática | Sí: va al briefing como acción de JP |
| **Duda** — no hay regla que cubra el caso, o la confianza es baja | Ninguna | Sí: pregunta con botones |

Las confirmaciones de pago, pagos rechazados y facturas rechazadas que llegan por ser contacto principal de sistemas de terceros **no son ruido**: son de Natalia, y caen en "A derivar".

## 7. Derivación: responder a todos, no reenviar

Cuando el sistema deriva, **no reenvía**. Responde al hilo original con copia a todos los destinatarios originales, agregando la casilla del responsable en CC.

Esto tiene tres efectos:

1. El responsable queda dentro de la cadena y puede continuarla directamente con el cliente.
2. El cliente ve que su consulta fue derivada y a quién.
3. **JP queda copiado**, así que cuando el responsable conteste con copia a todos, esa respuesta vuelve a la casilla de JP y el sistema puede verla. Esto reduce drásticamente el punto ciego del seguimiento (sección 9).

**Regla global:** toda salida del sistema es respuesta a todos. Nunca responde solo al remitente.

### 7.1 Plantillas

Fijas y editables en `plantillas.md`. El modelo elige qué plantilla usar y a quién copiar; **no redacta texto libre**.

Derivación:

```
Estimado/a:

Derivo su consulta a {responsable} ({casilla}), que queda en copia y le va a dar respuesta.

Saludos cordiales,
Juan Pablo Sarobe
Full Control GPS
```

Repregunta por silencio:

```
{Nombre}, ¿hay novedades sobre este tema?
```

## 8. Aprendizaje

### 8.1 El archivo de reglas

Las reglas viven en `reglas.md`, en lenguaje natural, y se inyectan completas en el contexto del clasificador en cada llamada. Con este volumen entran cientos de reglas sin problema.

```markdown
## Derivación técnica
- Si el pedido es sobre funcionamiento o configuración de equipos → responsable: Enzo (tecnicos@)
  - Si tecnicos@ ya está entre los destinatarios → no derivar, solo seguimiento a 3 días
  - Si no está → responder con copia a tecnicos@ + seguimiento
- EXCEPCIÓN: si el remitente pide hablar o reunirse con JP personalmente → no derivar, va al briefing
  (aprendida 2026-08-08 — Marcela Gómez, "Consulta unidad 47")
```

Cada regla queda firmada con la fecha y el correo del que salió. Eso da trazabilidad: dentro de tres meses, ante un comportamiento raro, se puede rastrear el origen.

Ventaja central de este enfoque sobre una memoria de casos vectorizada: es **auditable y editable**. Cuando el sistema se equivoque —y se va a equivocar— se abre el archivo, se ve la regla culpable y se corrige en treinta segundos.

### 8.2 El ciclo

Cuando no hay regla que cubra el caso, o la confianza es baja, el bot pregunta en Telegram mostrando el correo, su lectura del caso, y botones con las opciones. JP toca uno. El sistema **ejecuta y escribe la regla**.

### 8.3 Reglas contradictorias

Si una regla nueva contradice una existente, el sistema no la agrega en silencio: avisa cuál es la regla en conflicto y su fecha, y pide a JP que elija. La regla vieja se reemplaza, no se acumula.

### 8.4 Graduación de confianza

Como el sistema escribe correos a clientes con la firma de JP, el costo de un error es alto. Por eso no arranca en modo autónomo aunque el modo objetivo sea autónomo:

- **Al inicio:** toda respuesta a un cliente pasa por confirmación, tenga regla o no. El bot muestra el borrador exacto con botones `[Enviar] [Editar] [No mandar]`.
- **Graduación:** cuando una regla acumula **3 confirmaciones consecutivas sin corrección**, pasa a ejecutarse sola. El sistema avisa cuando esto ocurre. JP puede degradarla en cualquier momento.
- **Acciones internas** (archivar ruido, abrir seguimiento) operan solas desde el día uno: son reversibles y no salen hacia afuera.

Así se llega al modo autónomo, pero con reglas que se ganaron el permiso.

## 9. Seguimiento por silencio

- Todo hilo derivado abre un seguimiento con vencimiento a **3 días hábiles** (lunes a viernes; no se maneja calendario de feriados en la primera versión).
- Si llega **cualquier** correo del mismo hilo desde `tecnicos@` o `administracion@`, el seguimiento se cierra solo y JP no se entera.
- Si vence sin señal, aparece en el briefing con un botón `[Repreguntar]` que responde en el mismo hilo, a todos, con la plantilla de repregunta. Otros botones: `[Dar por cerrado]`, `[Dar 3 días más]`.

**Límite conocido:** si el responsable contesta solo al cliente sin copia a JP, el sistema es ciego a esa respuesta y va a alertar por un silencio que no existe. La derivación con copia a todos (sección 7) hace que esto sea el caso raro, no el caso común.

## 10. Telegram

### 10.1 Briefings

Dos por día: **8:30 y 17:00**, hora de Argentina.

```
☀️ Briefing — vie 8 ago, 8:30

🔴 PARA VOS (2)
1. Nueva Cerámica SA — disconformidad, facturación duplicada
   Dice "es la tercera vez". Venía de un hilo con Natalia.
   [Ver completo] [Devolver a Natalia] [Recordar 14h]
2. Logística del Sur — pide reunión martes o miércoles
   [Ver completo] [Que lo coordine Natalia]

✅ RESUELTO SOLO (5)
• 3 a tecnicos@ (GPS, infracciones)  • 2 a administracion@ (facturación)
   [Ver detalle]

⏳ SIN RESPUESTA (1)
• Transporte XYZ → tecnicos@, hace 4 días   [Repreguntar]

🗑️ 34 archivados como ruido   [Ver por las dudas]
```

### 10.2 Alerta fuera de horario

Solo interrumpe fuera de los briefings si se cumple alguna de estas tres condiciones:

1. Piden reunión para el día siguiente.
2. El correo fue clasificado como disconformidad grave.
3. El remitente está en la lista de clientes importantes de `roster.md`.

### 10.3 A demanda

JP escribe `¿qué hay?` y recibe el estado actual al instante, sin esperar al briefing.

## 11. Motor de clasificación

El clasificador tiene **backend intercambiable**, configurado por variable de entorno:

```
LLM_BACKEND=ollama          # ollama | anthropic
OLLAMA_MODEL=qwen3:30b-a3b
```

Misma interfaz, misma función pura, mismos tests.

### 11.1 Arranque: local

Se arranca con **Ollama corriendo en la misma Mac Mini** (M4 Pro, 24 GB), reutilizando la instalación de otro proyecto. Costo de operación: cero.

Candidatos a evaluar, en orden: **Qwen 3 30B-A3B** (mixture-of-experts, entra cómodo en 24 GB y anda bien en español), **Gemma 3 27B**, **Mistral Small 24B**. Para el filtro de ruido alcanza un modelo de ~4B, que lo hace instantáneo.

### 11.2 El set de pruebas decide

No se asume que el modelo local alcanza. Se corre el set de pruebas de la sección 12 contra él y se mide contra un umbral definido de antemano:

**Umbral de aprobación:** ≥ 90 % de aciertos sobre el set completo, **y cero falsos negativos en la categoría "Tuyo"**. Un correo de JP clasificado como ruido o derivado sin avisarle es el único error que el sistema no puede permitirse: los demás se corrigen en el briefing siguiente, ese no se entera nunca. Un error en sentido contrario (mandarle al briefing algo que no era suyo) es tolerable — solo cuesta atención.

| Resultado contra el umbral | Decisión |
|---|---|
| Lo cumple | Se queda en local. USD 0/mes |
| Falla solo en el eje de escalación | Híbrido: local filtra y clasifica, API decide los casos que el local marca como dudosos. ~USD 5/mes |
| Falla de forma generalizada | Backend `anthropic` completo. ~USD 20/mes |

Precios de referencia si se llega a necesitar la API paga: Claude Haiku 4.5 (USD 1 / 5 por millón de tokens de entrada/salida) para el filtro de ruido, Claude Sonnet 5 (USD 3 / 15) para el clasificador.

**Riesgo identificado:** distinguir "consulta de facturación" de "disconformidad grave sobre facturación" es exactamente donde los modelos chicos flojean. Por eso el set de pruebas se construye y se corre **antes** de escribir el resto del sistema.

**Nota operativa:** la RAM se comparte con el otro proyecto que usa Ollama en la misma máquina. Si ambos usan modelos distintos, Ollama los descarga y recarga alternadamente, con la demora consiguiente. Conviene que ambos proyectos compartan modelo si es posible.

### 11.3 La suscripción de Claude Code no sirve como API

Se evaluó y se descartó. La suscripción de Claude Code paga uso interactivo; la API se factura por token en una cuenta separada. El modo headless (`claude -p`) sí corre contra la suscripción, pero no es base para un sistema desatendido: los límites están pensados para sesiones interactivas, cada llamada arrastra el arranque del CLI, y un ajuste de límites dejaría al sistema sin funcionar sin aviso.

Uso puntual aceptado: la pasada inicial sobre los correos ya acumulados, que es una tarea única.

## 12. Verificación

El clasificador es una función pura, así que se testea de verdad.

- Se arma un set de **correos reales de JP**, con la decisión correcta anotada por él.
- Ese set corre completo cada vez que se agrega o modifica una regla, y cada vez que se cambia de modelo.
- Si un cambio rompe un caso que antes acertaba, salta en el momento, no cuando el sistema le manda algo raro a un cliente.

El set es además el árbitro de la decisión de modelo (sección 11.2), así que se construye primero.

## 13. Datos

SQLite, un archivo, respaldable copiándolo.

| Tabla | Contenido |
|---|---|
| `mails` | Todo lo visto, con su decisión, la regla aplicada y la confianza. Clave: `Message-ID` |
| `hilos` | Seguimientos abiertos: responsable, apertura, vencimiento, estado, último mensaje visto |
| `decisiones` | Cada pregunta del bot, la respuesta de JP y la regla que salió. Es la memoria del aprendizaje |
| `enviados` | Log completo y auditable de todo lo que salió con la firma de JP |

**Idempotencia:** un correo se procesa una sola vez, identificado por su `Message-ID`. Si el proceso muere a mitad de camino, al reiniciar no vuelve a actuar sobre lo ya procesado.

## 14. Manejo de fallas

- **IMAP o red caídos:** reintentos con espera creciente. Si a los 30 minutos sigue sin poder leer, avisa por Telegram. Nunca falla en silencio.
- **Motor de clasificación caído** (Ollama apagado o API sin respuesta): los correos quedan en cola y se procesan cuando vuelve.
- **Freno de emergencia:** si el sistema está por enviar **más de 5 correos en una hora**, se detiene y pregunta. Protege contra el escenario grave: un bug o un remitente automático que dispare un bucle de respuestas hacia clientes.
- **Modo simulacro:** el sistema arranca sin permiso de enviar nada. Registra "esto es lo que hubiera hecho" y se lo muestra a JP. Se desactiva manualmente, y se recomienda mantenerlo al menos tres días antes de darle la llave.

## 15. Seguridad

- Credenciales en `.env`, excluido de git desde el primer commit.
- La contraseña de la casilla institucional queda en texto plano en el disco de la Mac. Alternativa más segura, no bloqueante: moverla al Llavero de macOS. Se implementa después de la primera versión funcional.
- Todo lo enviado queda registrado en la tabla `enviados`, con el contenido exacto.

## 16. Dependencias y orden

1. **Bloqueante:** terminar el otro proyecto que está instalando Ollama en la misma Mac Mini, y acordar con ese proyecto qué modelo se comparte.
2. Construir el set de pruebas con correos reales.
3. Evaluar el modelo local contra el set y decidir el backend (sección 11.2).
4. Recién ahí, implementar el resto del sistema.

## 17. Stack

| Pieza | Elección |
|---|---|
| Lenguaje | Python 3.12 |
| Correo | `imap-tools` (lectura) + `smtplib` (envío) |
| Base de datos | SQLite |
| Bot | `python-telegram-bot` |
| Clasificación | Ollama local (HTTP) o SDK `anthropic`, según `LLM_BACKEND` |
| Configuración | `.env`, `roster.md`, `reglas.md`, `plantillas.md` |

El sistema corre en la Mac Mini de JP. Se diseña portable —toda la configuración en archivos, ninguna ruta absoluta en el código— para que mudarlo a un servidor siempre encendido sea trivial si algún día hace falta.
