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
