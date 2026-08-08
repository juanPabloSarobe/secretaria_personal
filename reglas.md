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

- **Generalización a confirmar con JP:** la correspondencia de proveedores de
  servicios financieros, de cobro y de facturación se trata como Natalia por
  defecto, aunque tenga forma de notificación automática. Solo es RUIDO si es
  publicidad pura, sin relación con una cuenta o una operación en curso.
