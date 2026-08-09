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

**Explícitamente fuera de este diseño:** el sistema **no responde consultas de clientes en nombre de JP**. Lo único que redacta hacia afuera es el aviso de que la consulta fue derivada, y lo hace bajo las restricciones de la sección 7.1: generación acotada, validación mecánica antes de enviar, y plantilla fija de reserva si la validación falla. Contestar la consulta en sí es siempre trabajo de la persona responsable.

## 4. Actores

Definidos en `roster.md`, editable a mano.

| Persona | Rol | Casilla | Se ocupa de |
|---|---|---|---|
| Enzo | Técnico | `tecnicos@fullcontrolgps.com.ar` | Programación de equipos, reprogramaciones remotas, análisis y borrado de infracciones, análisis de funcionamiento y datos de equipos GPS, geocercas de velocidad, todo lo referido a programación de equipos |
| Natalia | Directora administrativa | `administracion@fullcontrolgps.com.ar` | Facturación, coordinación de turnos, coordinación del equipo, remitos y presupuestos, solicitud de OC, facturación de servicios, imputación de pagos, análisis de invoices con problemas, compras, respuestas generales a clientes |
| Juan Pablo | Dirección | `jpsarobe@fullcontrolgps.com.ar` | Grandes problemas y conflictos con clientes, disconformidades, nuevos desarrollos, clientes importantes que piden hablar con él, y todo lo que el resto del equipo no entienda, no pueda resolver, o le pida ayuda |

**Observación estructural:** Enzo y Natalia se definen por *tema*. JP se define por *severidad*. Una consulta de facturación es de Natalia; una disconformidad grave sobre esa misma factura es de JP. Por eso la regla de escalación **pisa** a la regla de tema.

`roster.md` también contiene la **lista de clientes importantes**, cuyos correos habilitan alerta fuera de horario.

**Esa lista se construye desde Telegram, no editando archivos.** Cuando JP está revisando correo y reconoce a un cliente importante, toca el botón ⭐ y el bot le ofrece las direcciones que aparecen en ese correo para que elija cuál. La dirección queda escrita en `roster.md` con la fecha. Las direcciones del propio dominio se excluyen de la oferta: son el equipo, no clientes.

El motivo es práctico: JP suele reconocer al cliente importante en el momento en que ve su correo, y con frecuencia sin tener acceso a la computadora. Pedirle que después edite un archivo garantiza que la lista quede vacía. La lista sigue siendo editable a mano para altas y bajas en bloque.

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
| `classifier` | (correo, roster, reglas) → decisión. Arma el prompt, parsea y valida. **Función pura.** | Qué proveedor de LLM hay abajo; cómo se ejecuta la decisión |
| `llm` | Adaptadores de proveedor: `completar(sistema, usuario, esquema) → texto`. Ver sección 11 | Correos, reglas, roster, el dominio entero |
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
| **Ruido** — newsletters, notificaciones automáticas, spam | Mueve a la carpeta `INBOX.Ruido` (ver 6.3) | No. Solo un conteo en el briefing |
| **Delegado** — tema de Enzo o Natalia, ya están en copia | Ninguna. Abre hilo de seguimiento | No. Solo si vence el plazo sin señal |
| **A derivar** — tema de Enzo o Natalia, no están en copia | Responde al hilo con copia a todos, agregando al responsable. Abre seguimiento | Aparece en el briefing como hecho consumado |
| **Tuyo** — escalación, reunión, pedido personal | Ninguna acción automática | Sí: va al briefing como acción de JP |
| **Duda** — no hay regla que cubra el caso, o la confianza es baja | Ninguna | Sí: pregunta con botones |

Las confirmaciones de pago, pagos rechazados y facturas rechazadas que llegan por ser contacto principal de sistemas de terceros **no son ruido**: son de Natalia, y caen en "A derivar".

### 6.2 Las categorías no siempre son excluyentes

Los tres ejes de la sección 6 asumen que cada correo tiene **un** responsable, y por eso la matriz de acción tiene categorías mutuamente excluyentes. Un caso real mostró que eso es falso.

Un operador de la plataforma que trabaja en un cliente escribió despidiéndose porque lo pasaban a otro sector, con saludos y agradecimientos. Enzo y Natalia estaban en copia, y Natalia ya había respondido. Mecánicamente es `DELEGADO`: el responsable está en copia y el asunto está atendido. Pero JP también debía responder, personalmente, porque el vínculo con esa persona es parte del negocio y el valor de la respuesta está en que sea suya.

O sea: `DELEGADO` y `TUYO` eran **ambas verdaderas**. No es que el clasificador eligiera mal entre dos opciones; es que el modelo de datos no admitía la respuesta correcta.

**Consecuencia para la implementación:** la decisión del clasificador debe permitir una **acción del equipo y una acción de JP simultáneas**, no una sola categoría. En la práctica: un correo puede quedar delegado *y* aparecer en el briefing como acción personal. La matriz de la sección 6.1 se lee entonces como "qué hace el sistema" y "si además va al briefing", que son dos preguntas y no una.

Los botones del simulacro siguen siendo excluyentes por ahora, lo que subestima estos casos. Corregirlo es trabajo pendiente para la primera versión del sistema real.

### 6.3 Convención de carpetas del servidor

Verificado contra el servidor real (`vps-1862452-x.dattaweb.com`, 2026-08-08): las carpetas usan **prefijo `INBOX.` con punto como separador** — `INBOX.Sent`, `INBOX.Trash`, `INBOX.Drafts`, `INBOX.spam`, `INBOX.Promociones`.

La carpeta de ruido debe crearse por lo tanto como **`INBOX.Ruido`**. Asumir el nombre plano `Ruido` hace que el archivado falle, y en algunos servidores falla en silencio. El nombre va en `.env` (`CARPETA_RUIDO`) en lugar de estar escrito en el código, y el sistema la crea si no existe.

## 7. Derivación: responder a todos, no reenviar

Cuando el sistema deriva, **no reenvía**. Responde al hilo original con copia a todos los destinatarios originales, agregando la casilla del responsable en CC.

Esto tiene tres efectos:

1. El responsable queda dentro de la cadena y puede continuarla directamente con el cliente.
2. El cliente ve que su consulta fue derivada y a quién.
3. **JP queda copiado**, así que cuando el responsable conteste con copia a todos, esa respuesta vuelve a la casilla de JP y el sistema puede verla. Esto reduce drásticamente el punto ciego del seguimiento (sección 9).

**Regla global:** toda salida del sistema es respuesta a todos. Nunca responde solo al remitente.

### 7.1 Redacción: generación acotada con validación

Una plantilla idéntica en cada derivación se nota y suena mecánica. Pero el texto fijo era una propiedad de seguridad, no una comodidad: lo que no varía no puede decir algo indebido.

La solución es **generar dentro de una jaula**: el modelo redacta el mensaje, un validador mecánico lo revisa antes de que salga, y si no pasa alguna comprobación se envía la plantilla fija de reserva. La caída es silenciosa y no interrumpe la operación.

**El modelo recibe** el correo original, el nombre y la casilla del responsable, y esta instrucción: informá que se deriva la consulta, no la respondas, no prometas plazos, no inventes datos.

**El validador exige** —todas comprobaciones de código, ninguna de criterio:

| Comprobación | Motivo |
|---|---|
| Menciona al responsable por nombre y por dirección | Es la información que el mensaje existe para transmitir |
| Largo entre 150 y 500 caracteres | Fuera de ese rango el modelo se desvió del encargo |
| Termina con la firma exacta, carácter por carácter | La firma es identidad de JP y no se improvisa |
| No contiene fechas, montos, plazos ni números de comprobante | Son los datos con los que se compromete a la empresa |
| No contiene signos de pregunta dirigidos al cliente | Preguntar abre una conversación que JP no va a seguir |
| No introduce entidades ausentes del correo original | Detecta invención de datos |

**Variación permitida:** mencionar el tema al pasar ("su consulta sobre la unidad 47"), ajustar la formalidad al tono del remitente, y reconocer si es un primer contacto o una insistencia. Eso es lo que evita el efecto robot sin comprometer nada.

**Plantilla de reserva**, en `plantillas.md`, usada cuando la validación falla:

```
Estimado/a:

Derivo su consulta a {responsable} ({casilla}), que queda en copia y le va a dar respuesta.

Saludos cordiales,
Juan Pablo Sarobe
Full Control GPS
```

Repregunta por silencio (esta **no** se genera: es interna, va al equipo, y la variación no aporta nada):

```
{Nombre}, ¿hay novedades sobre este tema?
```

**Métrica a vigilar:** la proporción de mensajes que caen a la plantilla de reserva. Si es alta, el prompt está mal calibrado o el modelo no alcanza para esta tarea, y conviene saberlo por un número y no por una queja de un cliente.

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

### 9.1 Hilos que lleva JP en persona

Las secciones anteriores tratan cada correo como un hecho aislado. Eso alcanza para el correo que entra por primera vez, y falla apenas hay conversación.

**El error que produce:** un cliente escribe, JP le responde personalmente, el cliente contesta. Para un clasificador sin memoria de hilo, esa respuesta es un correo nuevo con tema de facturación, así que lo deriva a Natalia con copia a todos — y le anuncia al cliente que ahora lo atiende otra persona, en medio de una conversación que JP estaba llevando. Es de los errores más caros posibles y el diseño actual lo cometería sin dudar.

**La corrección: estado de hilo.** El sistema registra, por hilo, quién actuó último y si JP lo está llevando. Un hilo pasa a estar "en manos de JP" cuando él manda un mensaje en él.

| Situación | Comportamiento |
|---|---|
| Llega respuesta en un hilo que lleva JP | **No se deriva nunca.** Va al briefing como continuación |
| JP quiere soltarlo | Botón en el briefing: "pasáselo a Natalia" |
| JP respondió y no le contestan | Seguimiento por silencio, igual que con el equipo (sección 9) |

**Consecuencia técnica: hay que leer la carpeta de enviados.** El sistema no puede saber que JP respondió si solo mira la bandeja de entrada. `INBOX.Sent` pasa a ser una fuente de datos del sistema, no solo la bandeja. Esto no estaba contemplado en la sección 5 y cambia el módulo `imap`.

**Y el seguimiento por silencio se amplía:** hoy vigila lo que se derivó al equipo. Con esto vigila también lo que JP respondió y quedó sin contestar, que es un caso que él mencionó espontáneamente y que ninguna versión anterior del diseño cubría.

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

### 10.4 Pedidos por Telegram (evaluado, priorizado)

JP planteó poder pedirle cosas a la secretaria en vez de solo responderle. Son dos capacidades con perfiles de riesgo muy distintos, y conviene no tratarlas como una sola.

**Buscar (solo lectura, entra temprano).** *"¿Qué me mandó Suhr la semana pasada?"*, *"buscá el mail que hablaba de las geocercas"*. IMAP tiene búsqueda nativa, no hay riesgo, y resuelve un problema concreto: consultar el correo desde el teléfono sin abrir el webmail. Barato de implementar y de alto valor; puede entrar antes que la Capa 2.

**Dictar un mensaje (más adelante, y nunca automático).** *"Mandale a Fulano que el equipo 47 ya está reprogramado"*. Contra la intuición, esto es **menos riesgoso que las plantillas de derivación de la sección 7.1**: ahí el modelo redacta, acá JP dicta y el sistema transcribe. Son sus palabras.

El riesgo real es la transcripción, no la redacción: si Whisper escucha "74" donde JP dijo "47", el error sale con su firma. Por eso **esta capacidad no se gradúa nunca** al modo autónomo de la sección 8.4 — siempre muestra el borrador exacto antes de enviar. La graduación tiene sentido para reglas que se repiten; un mensaje dictado es distinto cada vez y no hay patrón que pueda ganarse la confianza.

## 11. Motor de clasificación

El sistema es **agnóstico del proveedor de LLM**. Elegir qué modelo le da vida es una decisión de configuración, no de código, y puede tomarse después de que el sistema esté construido.

### 11.1 Dónde va la frontera

El punto crítico del diseño es la altura de la abstracción. El adaptador de proveedor es **deliberadamente tonto** y expone una sola operación:

```
completar(sistema, usuario, esquema) → texto
```

Todo lo demás —construir el prompt, inyectar `roster.md` y `reglas.md`, parsear la respuesta, validarla contra el esquema, decidir la acción— vive **por encima** de esa línea y es idéntico para todos los proveedores.

La alternativa descartada era que cada proveedor implementara `clasificar(correo)`. Eso duplicaría la lógica de prompt y parseo en cada adaptador, y cambiar de proveedor obligaría a tocar lógica de negocio. Con la frontera donde está, cambiar de proveedor cambia el `.env` y nada más.

### 11.2 Dos adaptadores cubren todo el mercado

Ollama, Groq, NVIDIA NIM, Together, OpenRouter, vLLM y LM Studio hablan todos el mismo protocolo (`/v1/chat/completions`, el de OpenAI). No hacen falta siete adaptadores:

| Adaptador | Cubre | Configuración |
|---|---|---|
| `openai_compat` | Ollama, Groq, NVIDIA NIM, Together, OpenRouter, vLLM, LM Studio, y cualquier otro compatible | `base_url` + `api_key` + `model` |
| `anthropic` | Claude, que usa su propia API | `api_key` + `model` |

```bash
# Local, costo cero
LLM_PROVIDER=openai_compat
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3:30b-a3b
LLM_API_KEY=ollama

# Groq — mismo adaptador, cambian tres valores
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile
LLM_API_KEY=gsk_...
```

Se configuran **dos motores por separado**, porque las dos etapas tienen exigencias distintas: `LLM_RUIDO_*` para el filtro de ruido (tarea fácil, alto volumen, conviene lo más barato y rápido) y `LLM_CLASIFICADOR_*` para la clasificación real. Pueden apuntar al mismo proveedor o a proveedores distintos.

### 11.3 Salida estructurada: lo único que no es uniforme

Cada proveedor pide JSON estructurado de forma distinta —Anthropic con un parámetro, los compatibles con OpenAI con otro, Ollama con un tercero— y algunos modelos chicos no lo soportan de forma confiable. Es donde este tipo de abstracción se suele romper.

Cada adaptador declara qué modos soporta, y existe una **estrategia de reserva universal**: pedir el JSON en el prompt, parsear la respuesta, y ante un fallo reintentar una vez incluyendo el error de parseo. Más lento y menos elegante, pero funciona con cualquier modelo. Ningún proveedor queda excluido por una limitación de formato.

### 11.4 El banco de comparación

Como la interfaz es única, el set de pruebas de la sección 12 deja de ser solo una red de seguridad y pasa a ser un **banco de comparación entre proveedores**. Un comando corre el mismo set contra cada proveedor configurado y devuelve una tabla con aciertos, errores en la categoría crítica, latencia por correo y costo estimado por mes.

La decisión de qué LLM usar se toma leyendo esa tabla.

### 11.5 Candidatos a evaluar

**Local (Ollama en la Mac Mini, M4 Pro 24 GB, reutilizando la instalación de otro proyecto).** Costo de operación cero. Candidatos: **Qwen 3 30B-A3B** (mixture-of-experts, entra cómodo en 24 GB y anda bien en español), **Gemma 3 27B**, **Mistral Small 24B**. Para el filtro de ruido alcanza un modelo de ~4B.

**Remoto.** Groq (muy rápido, tiene nivel gratuito con límites de uso — verificar los vigentes al momento de decidir), NVIDIA NIM, y Claude vía API como referencia de calidad máxima.

**Nota operativa sobre el modo local:** la RAM se comparte con el otro proyecto que usa Ollama en la misma máquina. Si ambos usan modelos distintos, Ollama los descarga y recarga alternadamente, con la demora consiguiente. Conviene que ambos proyectos compartan modelo si es posible.

### 11.6 El set de pruebas decide

Ningún proveedor se adopta por suposición. Se corre el set de pruebas de la sección 12 contra cada candidato y se mide contra un umbral definido de antemano:

**Umbral de aprobación:** ≥ 90 % de aciertos sobre el set completo, **y cero falsos negativos en la categoría "Tuyo"**. Un correo de JP clasificado como ruido o derivado sin avisarle es el único error que el sistema no puede permitirse: los demás se corrigen en el briefing siguiente, ese no se entera nunca. Un error en sentido contrario (mandarle al briefing algo que no era suyo) es tolerable — solo cuesta atención.

Se prefiere, entre los que cumplen el umbral, el de menor costo de operación. El orden esperado de preferencia es local → remoto gratuito → remoto pago, pero lo decide la tabla, no esta lista.

| Resultado contra el umbral | Configuración resultante |
|---|---|
| Un proveedor gratuito lo cumple en las dos etapas | Ese proveedor para todo. USD 0/mes |
| Lo cumple en ruido pero falla en el eje de escalación | Dos motores: el gratuito para ruido, uno pago para clasificación. ~USD 5/mes |
| Ningún gratuito lo cumple | Proveedor pago en ambas etapas. ~USD 20/mes |

Precios de referencia si se llega a necesitar API paga: Claude Haiku 4.5 (USD 1 / 5 por millón de tokens de entrada/salida) para el filtro de ruido, Claude Sonnet 5 (USD 3 / 15) para el clasificador.

**Riesgo identificado:** distinguir "consulta de facturación" de "disconformidad grave sobre facturación" es exactamente donde los modelos chicos flojean. Es la razón por la que el umbral separa la categoría "Tuyo" del promedio general en lugar de mirar solo el porcentaje agregado.

### 11.7 Prueba de humo (2026-08-08)

Se probaron dos proveedores con claves reales (`SecretariaPersonal` en Groq y en NVIDIA) contra tres correos sintéticos difíciles del dominio: uno técnico con el responsable ya en copia (esperado `DELEGADO`), un reclamo furioso sobre facturación (esperado `TUYO` — la escalación pisa al tema), y una notificación automática de comprobante rechazado (esperado `A_DERIVAR` a Natalia, no `RUIDO`).

| Proveedor / modelo | Aciertos | Latencia por correo |
|---|---|---|
| groq / `llama-3.3-70b-versatile` | 3/3 | 0,6 s |
| groq / `openai/gpt-oss-120b` | 3/3 | 1,0 s |
| nvidia / `nvidia/nemotron-3-super-120b-a12b` | 2/3 | 9,6 s |
| groq / `llama-3.1-8b-instant` | 1/3 | 0,4 s |
| nvidia / `meta/llama-3.3-70b-instruct` | 1/3 (timeouts) | 72,7 s |

Todos los modelos serios acertaron el caso de escalación, lo que confirma que el orden de los tres ejes de la sección 6 es aprendible por un modelo.

**Configuración inicial elegida:** Groq para ambas etapas, `llama-3.1-8b-instant` para el filtro de ruido y `llama-3.3-70b-versatile` para el clasificador. NVIDIA queda como respaldo configurado.

Tres hallazgos operativos que condicionan la implementación:

1. **Cloudflare rechaza el User-Agent por defecto de `urllib`** con un `HTTP 403, error code 1010`. No es un error de la API ni de credenciales, pero lo parece. El adaptador `openai_compat` debe enviar un User-Agent propio, y este caso debe estar cubierto por un test.
2. **NVIDIA en nivel gratuito es lento e inestable** (10 a 70 segundos por correo, con timeouts). Sirve como respaldo, no como motor principal.
3. **Los resultados de una sola pasada son ruido.** Nemotron dio 3/3 en una corrida y 2/3 en la siguiente, con `temperature: 0`. El banco de comparación de la sección 11.4 debe correr cada caso varias veces y reportar la dispersión, no un número único.
4. **El nivel gratuito de Groq corta con `HTTP 429` ante llamadas seguidas.** Apareció al reclasificar 10 casos × 3 pasadas sin pausa. El adaptador debe reintentar respetando la cabecera `Retry-After` cuando viene, y con espera creciente cuando no; los `5xx` se tratan igual, y el resto de los errores se propagan porque reintentarlos no arregla nada. Esto no es exclusivo del simulacro: el polleo cada 5 minutos con varios correos por tanda puede alcanzar el mismo límite.

**Alcance de esta prueba:** tres correos sintéticos no son un benchmark. Demuestra que la cadena funciona de punta a punta y da una señal temprana; la decisión definitiva la toma el set real de la sección 12 contra el umbral de la 11.6.

### 11.8 La suscripción de Claude Code no sirve como API

Se evaluó y se descartó. La suscripción de Claude Code paga uso interactivo; la API se factura por token en una cuenta separada. El modo headless (`claude -p`) sí corre contra la suscripción, pero no es base para un sistema desatendido: los límites están pensados para sesiones interactivas, cada llamada arrastra el arranque del CLI, y un ajuste de límites dejaría al sistema sin funcionar sin aviso.

Uso puntual aceptado: la pasada inicial sobre los correos ya acumulados, que es una tarea única.

## 12. Verificación

El clasificador es una función pura, así que se testea de verdad.

- Se arma un set de **correos reales de JP**, con la decisión correcta anotada por él.
- Ese set corre completo cada vez que se agrega o modifica una regla, y cada vez que se cambia de modelo.
- Si un cambio rompe un caso que antes acertaba, salta en el momento, no cuando el sistema le manda algo raro a un cliente.

El set es además el banco de comparación entre proveedores (sección 11.4) y el árbitro de la decisión de modelo (sección 11.6), así que se construye temprano — pero no bloquea la construcción del sistema, porque la elección de proveedor es configuración y no código.

### 12.1 Validación del ciclo de aprendizaje (2026-08-08)

Primera tanda real: 10 correos de la casilla, clasificados y corregidos por JP vía Telegram. Resultado inicial **7/10**. Los tres errores eran el mismo remitente (SiPago, la procesadora de cobros): dos clasificados como `RUIDO` cuando eran de Natalia, y uno escalado a `TUYO` porque el asunto decía "Reclamo" —que era un número de ticket del proveedor, no una queja de cliente.

Escritas esas tres correcciones como reglas y reclasificados los mismos casos: **10/10, estable en 3 de 3 pasadas, sin regresiones**.

**Qué demuestra y qué no.** Demuestra que el mecanismo funciona: una regla escrita cambia el comportamiento del clasificador y no rompe lo que ya acertaba. **No** demuestra que el clasificador haya mejorado en general — las reglas se derivaron de esos mismos correos, así que acertarlos después era casi inevitable. Medir sobre los casos que generaron la regla sobreestima el aprendizaje.

**Consecuencia metodológica:** el progreso se mide sobre **tandas nuevas**, no reevaluando las viejas. La reevaluación sirve para detectar regresiones, que es un propósito distinto y también necesario. El set acumulado cumple las dos funciones, pero solo la primera medición de cada tanda cuenta como señal de calidad.

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

**No hay dependencias bloqueantes.** La independencia del proveedor (sección 11) hace que el sistema pueda construirse y probarse contra cualquier LLM disponible hoy, y que la elección definitiva se tome después, cambiando configuración.

Orden de trabajo:

1. Construir el set de pruebas con correos reales de JP y las decisiones correctas anotadas.
2. Implementar el sistema contra el proveedor que esté disponible en ese momento — sirve cualquiera que cumpla el umbral en una prueba rápida.
3. Cuando esté funcionando, correr el banco de comparación (sección 11.4) contra todos los candidatos y fijar la configuración definitiva.

**Coordinación pendiente, no bloqueante:** hablar con el otro proyecto que está instalando Ollama en la misma Mac Mini para acordar qué modelo se comparte, y así evitar la recarga alternada descrita en la sección 11.5. Si esa conversación se demora, el proyecto avanza igual contra un proveedor remoto.

## 17. Stack

| Pieza | Elección |
|---|---|
| Lenguaje | Python 3.12 |
| Correo | `imap-tools` (lectura) + `smtplib` (envío) |
| Base de datos | SQLite |
| Bot | `python-telegram-bot` |
| Clasificación | Adaptador `openai_compat` (cliente HTTP genérico, cubre Ollama/Groq/NVIDIA/etc.) o adaptador `anthropic` (SDK oficial). Ver sección 11 |
| Configuración | `.env`, `roster.md`, `reglas.md`, `plantillas.md` |

El sistema corre en la Mac Mini de JP. Se diseña portable —toda la configuración en archivos, ninguna ruta absoluta en el código— para que mudarlo a un servidor siempre encendido sea trivial si algún día hace falta.
