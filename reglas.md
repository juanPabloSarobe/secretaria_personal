# Reglas aprendidas

Este archivo lo escribe la secretaria a partir de las decisiones de JP, y lo
puede editar JP a mano en cualquier momento. Se inyecta completo en el contexto
del clasificador en cada llamada.

Cada regla queda firmada con la fecha y el correo del que salió, para poder
rastrear su origen más adelante.

---

## Base

Estas son las únicas reglas escritas a mano, derivadas del diseño. Todo lo
demás se aprende.

- El orden de decisión es: (1) ¿es escalación o buscan a JP?, (2) ¿de qué tema
  es?, (3) ¿el responsable ya está entre los destinatarios? La respuesta
  afirmativa a (1) corta la cadena: es de JP sin importar el tema.

- Las notificaciones automáticas de pagos rechazados, facturas rechazadas y
  comprobantes con error **no son ruido**. Llegan a la casilla de JP porque es
  el contacto principal de esos sistemas, pero corresponden a Natalia.

- RUIDO es solo lo que no tiene ninguna consecuencia operativa: newsletters,
  publicidad, y notificaciones informativas que nadie necesita accionar.

---

## Aprendidas

### Proveedores de servicios financieros y de cobro

- **SiPago** (`sipago.coop`, `sipago.com.ar`, `envios.sipago.coop`) es la procesadora
  de cobros de Full Control GPS. **Nada de SiPago es RUIDO.** Todo lo que llegue de
  ese dominio corresponde a **Natalia**, incluso cuando parezca una notificación
  informativa sin acción: manuales, instructivos, avisos de consulta resuelta,
  novedades de la plataforma.
  *Motivo:* toca la operación de cobranzas, y el que sigue esos temas es
  administración, no JP.
  (aprendida 2026-08-08 — "Manual de contracargos" y "Tu consulta fue resuelta")

- La palabra **"Reclamo"** en el asunto de un **proveedor** es un número de ticket
  de ese proveedor, **no** una queja de un cliente. No dispara la regla de
  escalación. Va al responsable del tema, que en el caso de proveedores de cobro
  es Natalia.
  *Contraste:* la regla de escalación se aplica cuando quien se queja es un
  **cliente** de Full Control GPS, no cuando un proveedor nos numera un ticket.
  (aprendida 2026-08-08 — consultas@sipago.coop, "sipago Reclamo 42467")

- **NO generalizar a otros proveedores.** JP descartó extender esta regla a
  "proveedores financieros y de cobro" en general: SiPago es un caso concreto,
  no la instancia de una categoría. Cada proveedor nuevo se aprende por
  separado, preguntando.
  (decidido 2026-08-08 por JP, al revisar la generalización propuesta)

### Correo personal en la casilla de trabajo

- Si el remitente o el asunto figura en la sección **"Asuntos personales de JP"**
  del roster, la categoría es **TUYO**. No se deriva a nadie y no es ruido,
  aunque tenga forma de trámite corporativo.

  *Por qué:* la casilla institucional también recibe asuntos privados de JP.
  Nada en el texto permite distinguirlos —una notificación municipal sobre
  obra pública se lee igual sea personal o de la empresa— así que **esto no se
  deduce, se aprende marcándolo**. Ante un remitente parecido que no esté en la
  lista, preguntar en vez de suponer.

  (aprendida 2026-08-08 — explicada por JP: los avisos de la Municipalidad de
  Neuquén sobre pavimentación son suyos como vecino, no de Full Control GPS)

### Mensajes de cortesía y de vínculo personal

- Si el correo es una **despedida, un agradecimiento, una felicitación, un
  pésame o el aviso de un cambio personal** de alguien que trabaja en un
  cliente —típicamente un operador de la plataforma—, entonces **corresponde
  también una respuesta personal de JP**, aunque el tema esté delegado y aunque
  un integrante del equipo ya haya respondido.

  *Por qué:* estos mensajes no se "resuelven", se corresponden. El valor está
  justamente en que la respuesta sea personal, así que el hecho de que Natalia
  o Enzo ya hayan contestado **no lo da por cerrado**. Son personas con las que
  Full Control GPS construye relación a lo largo de los años, y esa relación es
  parte del negocio.

  *Cómo se distingue de una escalación:* no hay conflicto ni problema. La regla
  de escalación se dispara por severidad; esta se dispara por vínculo.

  *Consecuencia estructural:* este es el primer caso donde **dos categorías son
  verdaderas a la vez** (DELEGADO y TUYO). Ver la sección 6.2 del diseño.

  (aprendida 2026-08-08 — explicada por JP: un operador de un cliente se
  despedía porque lo pasaban a otro sector, mandaba saludos y buenos augurios;
  Natalia y Enzo estaban en copia y Natalia ya había respondido)

- **La despedida gana aunque el correo además pida algo.** Un mensaje puede
  traer un pedido concreto *y* una despedida en el mismo texto. El pedido se
  deriva a quien corresponda, pero el saludo sigue siendo de JP.

  (aprendida 2026-08-08 — Carlos Bernardo Suhr, "Sulicitud": pedía algo y a la
  vez se despedía. JP lo marcó como suyo por lo segundo)

- **LÍMITE IMPORTANTE: el agradecimiento tiene que estar dirigido a JP.** Si un
  cliente agradece porque **un integrante del equipo le terminó un trabajo**, el
  agradecimiento es para esa persona, no para JP. Eso es DELEGADO, no TUYO.

  *Cómo distinguirlos:* preguntarse a quién le está hablando. Una despedida
  general o un saludo a la empresa incluye a JP. Un "gracias" que responde a un
  mensaje concreto de Natalia o de Enzo es de ellos.

  (aprendida 2026-08-08 — María Fabián, "RE: CONTRATOS Y USUARIOS PARA BORRAR":
  el clasificador aplicó la regla de cortesía de arriba y JP lo corrigió —
  "no me respondió las gracias a mí sino a Natalia, se ve claro que Natalia
  terminó un trabajo y se lo está informando")

- **LÍMITE: la regla de cortesía solo aplica a lo que ENTRA.** Si el remitente
  es del propio dominio `fullcontrolgps.com.ar`, la despedida o el agradecimiento
  ya lo escribió el equipo. Eso es DELEGADO, nunca TUYO, por cariñoso que sea el
  texto.

  (aprendida 2026-08-08 — Natalia escribiéndole a un cliente que se iba: "te
  vamos a extrañar un montonnnn, te queremosss". El clasificador leyó la
  despedida y la marcó como de JP. La despedida que importa es la que llega,
  no la que sale)

### Reuniones: quién la pide importa más que el pedido

- Un **pedido de reunión dispara la regla de escalación solo si viene de un
  cliente, un proveedor con relación, o alguien conocido**. Una venta fría que
  ofrece un producto y pide "una breve reunión informativa para comentarles
  nuestros beneficios" es **RUIDO**, no una reunión.

  *Cómo distinguirlos:* preguntarse si hay una relación previa. Un cliente pide
  reunión sobre algo que ya existe entre las partes. Un vendedor pide reunión
  para crear una relación que todavía no hay.

  (aprendida 2026-08-08 — Medife ofreciendo cobertura médica corporativa; el
  clasificador lo escaló a JP por el pedido de reunión, JP lo marcó como ruido)

### Notificaciones de proveedores de servicios

- Los **avisos de mantenimiento programado, cortes anunciados y cambios de
  infraestructura** de proveedores (telefonía, conectividad, plataformas) son
  **RUIDO**, aunque estén llenos de vocabulario técnico. No hay nada que hacer:
  son informativos.

  *Contraste con la regla de SiPago:* una factura rechazada exige una acción y
  por eso no es ruido. Un aviso de mantenimiento no exige ninguna. La pregunta
  no es si el tema es técnico, sino si alguien tiene que hacer algo.

  (aprendida 2026-08-08 — Telefónica, "NOTIFICACION CRQ000000843789": tareas de
  mantenimiento programado. El clasificador lo derivó a Enzo por el vocabulario
  técnico, JP lo marcó como ruido)

### Excepciones a "un hilo de trabajo nunca es ruido"

La regla de que un correo dentro de un hilo de trabajo no es ruido tiene dos
excepciones, y las dos son mecánicas:

- **Acuses de lectura y de entrega son siempre RUIDO.** Asuntos que empiezan
  con `Read:`, `Leído:`, `Lido:`, `Delivered:`, `Non-Delivery Report`, o cuerpos
  que solo dicen que un mensaje fue leído o entregado. Los genera el programa de
  correo, no una persona, y no hay nada que hacer con ellos.

  (aprendida 2026-08-12 — "Read: RE: Algoritmo MCI LASC": el clasificador lo
  dejó fuera de ruido por estar dentro de un hilo de trabajo real)

- **Avisos de rutina de que una factura está disponible** —de telefonía,
  servicios públicos, plataformas— son **RUIDO**. Es distinto de una factura
  con problema o de un pago rechazado, que sí exigen acción y son de Natalia.
  La diferencia está en si algo salió mal o simplemente hay un resumen nuevo.

  (aprendida 2026-08-12 — Claro, "Ya podés acceder a tu factura")

### Contratos y acuerdos comerciales

- **Un contrato firmado, un acuerdo o un enlace de pago enviado directamente a
  JP, sin nadie más en Para ni en CC, es TUYO.** No importa que mencione
  facturas: no es un tema de facturación sino una relación comercial que él
  lleva. Nadie del equipo está en ese hilo para hacerse cargo.

  (aprendida 2026-08-12 — Mauro Micheletti de Orbcomm, "Contrato, facturas de
  SC1000 y link de pago", dirigido solo a JP)

### Currículums y envíos masivos

- Un **CV espontáneo**, sin búsqueda abierta de por medio, es **RUIDO**. No es
  de Natalia aunque ella coordine al equipo: nadie tiene que hacer nada con él.

- **`undisclosed-recipients` en el campo Para es señal fuerte de envío masivo.**
  Quien manda algo a una lista oculta no le está escribiendo a Full Control GPS
  en particular. Salvo que el contenido pida una acción concreta, es RUIDO.

  (aprendida 2026-08-11 — "CV - FANELLO FERNANDO" a undisclosed-recipients; el
  clasificador lo mandó a Natalia razonando que ella lleva las consultas
  generales)

### Correos que continúan algo que JP arrancó fuera del mail

- Si el cuerpo **hace referencia a un contacto previo**, el correo es **TUYO**,
  aunque el remitente sea desconocido y aunque el contenido parezca publicidad.
  Frases que lo delatan:

  > "de acuerdo a lo conversado", "según lo hablado", "como te comenté",
  > "tal cual charlamos", "conforme a nuestra charla", "te adjunto lo que
  > te prometí", "siguiendo nuestra conversación telefónica"

  *Por qué:* nadie escribe "de acuerdo a lo conversado" a un desconocido. Esa
  frase es evidencia de que JP inició el contacto por otra vía —teléfono,
  WhatsApp, una reunión— y el correo es la continuación.

  *El peligro que evita:* un proveedor nuevo mandando listas de precios se lee
  exactamente igual que publicidad no solicitada. La diferencia no está en el
  formato sino en si JP lo pidió, y esa frase es la única huella que queda.

  (aprendida 2026-08-11 — Matías Sanguinetti, dreinet.com, "Contacto": mandó
  listas de precios de Queclink tras una charla por WhatsApp de ese mismo día.
  El clasificador lo leyó como publicidad. JP: "parece spam y no tenías forma
  de saberlo")

### Conversaciones que ya empezó JP

- **Hay que leer el correo ENTERO, incluido el texto citado abajo.** Las
  respuestas arrastran el hilo anterior, y ahí está la prueba de quién venía
  hablando. Un `De: Lic.Sarobe Juan Pablo` en la parte citada significa que JP
  escribió el mensaje anterior.

- **Si el mensaje responde DIRECTAMENTE a algo que escribió JP, es TUYO.** No se
  deriva a nadie, aunque el tema sea de Enzo o de Natalia y aunque ellos figuren
  en copia. JP está llevando esa conversación: meter a otra persona en el medio
  le anuncia al cliente que cambió de interlocutor.

- **LÍMITE: haber participado en el hilo no alcanza.** Lo que decide es a quién
  le contesta ESTE mensaje. Si JP escribió hace varios mensajes pero los últimos
  intercambios son entre otras dos personas y él quedó en copia, la respuesta es
  **DELEGADO**, no TUYO.

  *La distinción de fondo, en palabras de JP:* «está bien que yo me entere, pero
  en este caso yo no tengo que hacer nada». **Estar al tanto no es lo mismo que
  tener una acción pendiente**, y esa diferencia es exactamente lo que separa
  DELEGADO de TUYO. Ante la duda, preguntarse qué tendría que hacer JP con ese
  correo: si la respuesta es "leerlo", es DELEGADO.

  (aprendida 2026-08-12 — Daniela Ramos, "Re: Situación conductores OPS WM":
  JP había indicado qué hacer varios mensajes atrás, Alejo lo confirmó, y este
  correo es Daniela avisándole a Alejo que ya está hecho. El clasificador vio
  "JP inició la conversación" y lo escaló. JP: "es una respuesta entre 2
  personas que estaban en copia... yo no tengo que hacer nada")

  *Cómo reconocerlo sin la carpeta de enviados:* buscar en el cuerpo citado una
  línea `De:` seguida del nombre o la casilla de JP.

  (aprendida 2026-08-11 — Enrique Rodríguez, "RE: Pedido de Cotización". JP:
  "le pregunté yo y me respondió a mí. Tener que leer absolutamente todo el
  mail, el cuerpo también, no solamente lo nuevo")

### Integraciones con plataformas de terceros

- **Todo lo referido a integrar la plataforma con sistemas de otras empresas es
  TUYO**, sin importar quién esté en copia. No es tema de Enzo aunque suene
  técnico, ni de Natalia aunque haya coordinación de por medio.

- **IMSEG** (`imseg.com`): los correos **dirigidos a JP** son suyos, por la
  misma razón.

  **El dominio por sí solo no alcanza.** Antes de aplicar esta regla hay que
  pasar el filtro de la sección "Quién está en el hilo": si el mensaje es un
  intercambio entre otras dos personas y JP solo figura en copia, es DELEGADO,
  venga de IMSEG o de donde venga. Un dominio no convierte en acción algo que
  es información.

  (aprendida 2026-08-11 — IMSEG, "Situación conductores OPS WM / PLUSPETROL":
  el clasificador vio a Natalia en copia y dijo DELEGADO. JP: "todo lo de
  integraciones con plataforma de terceros es mío. Todos los de IMSEG es mío")

### Quién está en el hilo

- **El responsable puede estar en el campo De, no solo en Para o CC.** Si el
  correo lo manda `administracion@` o `tecnicos@`, esa persona ya está llevando
  el asunto: es DELEGADO, y no hay nada que derivarle a quien ya está
  escribiendo. Hay que mirar los tres campos, no dos.

- **JP en copia de una conversación entre el equipo y el cliente no es JP
  involucrado.** Es visibilidad, no un pedido. Categoría DELEGADO.

  (aprendida 2026-08-08 — dos correos enviados por Natalia con JP en copia; el
  clasificador dijo "administracion@ no está en Para ni en CC" sin advertir que
  era la remitente. JP: "yo solo estoy en copia. Este es un ejemplo de cuando
  estoy en copia de un pedido pero no es para mí")

### Qué es de Enzo y qué no

La frontera no es "suena técnico". Enzo hace lo que se resuelve **desde el
teclado**; lo que necesita que alguien viaje hasta la unidad no es de él.

- **De Enzo:** programación remota y local de equipos, cambios de zona y de
  velocidad de zona, obtención de IMEIs, y certificados de calibración técnica
  —lo que varios clientes llaman "bajada de tacógrafo"—.

- **De Natalia:** el mantenimiento físico de una unidad. Lo pide el cliente
  como "asistencia" o "mantenimiento", y lo que sigue es coordinar un turno
  para que vaya un técnico de campo. Hoy son tres —Lorenzo, Joaquín y
  Marcelo— y **no tienen correo**, así que nunca van a aparecer en copia: que
  no figure ningún técnico no significa que nadie se esté ocupando.

  (aprendida 2026-08-13 — Andrea Cabezas, "Solicitud de asistencia". El
  clasificador dijo ENZO por ser mantenimiento técnico. JP: "Enzo no es el
  responsable de los mantenimientos técnicos físicos, sino de la programación
  remota de equipos, y programación local... se está pidiendo que un técnico
  viaje a realizar un mantenimiento de una unidad. Eso lo coordina Natalia")

### Un pedido nuevo no es DELEGADO por tener al responsable en copia

**DELEGADO describe un hilo donde el equipo ya actuó** — contestó, confirmó,
o avisó que está hecho. Si este mensaje **es el pedido en sí**, la categoría
es la del responsable del tema, aunque ya esté en copia y aunque nadie tenga
que reenviarle nada.

La diferencia no es cosmética: de ahí sale el seguimiento. En palabras de JP,
sobre los cambios de zona y de velocidad: «son lentos y Enzo suele tardar días
en hacerlos y cargarlos. Estos son ejemplos de casos que hay que darles
seguimiento y asegurarnos que la tarea se cumpla».

  (aprendida 2026-08-13 — Maria Fabian, "RV: Verificar FULLCONTROL AG 959 OV",
  y Alejandro Garcetti, "RE: Solicitud de IMEI de equipos FULL CONTROL". En los
  dos el clasificador dijo DELEGADO con el motivo "Enzo ya en copia". En los
  dos JP dijo ENZO)

### Pedidos de IMEI

- **Un pedido de IMEIs es de Enzo**, porque los IMEI están en la memoria del
  equipo y no en el sistema: para obtenerlos hay que mandar una programación
  remota. Aunque el pedido venga por una integración, la tarea es de Enzo.

- **A JP también le tiene que llegar.** Es de Enzo por la tarea y de JP por la
  integración; no se elige una y se descarta la otra.

  (aprendida 2026-08-13 — Alejandro Garcetti, "RE: Solicitud de IMEI de equipos
  FULL CONTROL – Flota OPS y Subcontratistas", en el marco de la integración con
  IMSEG. JP: "hay que enviar una programación remota para obtener los imei s, y
  Enzo se encarga de las programaciones remotas. Igualmente está bien que yo
  esté informado, por ende también me tiene que llegar este correo")
