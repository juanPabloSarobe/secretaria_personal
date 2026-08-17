# Diseño — La secretaria en producción, Etapa 1: que JP deje de mirar la casilla

**Fecha:** 2026-08-17
**Estado:** aprobado, pendiente de plan de implementación
**Autor:** Juan Pablo Sarobe (con Claude)

**Relación con el diseño anterior:** el documento
`2026-08-08-triage-mail-institucional-design.md` sigue siendo la fuente de verdad
de *qué hace* el sistema — clasificación, derivación con copia a todos,
seguimiento por silencio, validador del texto generado, freno de emergencia. Este
documento define *cómo se pone a correr solo*, y divide el pedido completo en
etapas. Donde los dos hablen del mismo tema, manda este.

---

## 1. Qué cambió desde el diseño original

El diseño de agosto se escribió sin código y sin datos. Hoy hay tres semanas de
entrenamiento con correo real y un número medido, y eso cambia dos supuestos.

**El sistema clasifica mejor de lo que suponíamos, salvo en un lugar.** Sobre 133
casos con criterio de JP verificado uno por uno:

| Categoría | Acierto |
|---|---|
| DELEGADO | 92% |
| NATALIA | 92% |
| TUYO | 94% |
| RUIDO | 96% |
| **ENZO** | **0%** (3 de 3 mal) |
| **Total** | **92%** |

**ENZO falla por una causa conocida y no por falta de reglas.** Las tres veces
respondió DELEGADO. La regla que lo corrige está escrita en `reglas.md`, en
prosa, y el prompt tiene un PROCEDIMIENTO numerado que se ejecuta antes: el paso
que pregunta "¿quién está en el hilo?" contesta DELEGADO y frena ahí. **Una regla
en prosa no revierte un paso numerado.** El arreglo va en el PROCEDIMIENTO, y es
requisito de esta etapa.

**El promedio esconde lo que importa.** El error caro no es equivocarse: es
enterrar algo donde nadie lo va a ver. De los 11 errores que quedan:

- **7 son baratos:** escalan de más, o sea muestran algo que no hacía falta
  mostrar. Cuestan una mirada.
- **4 son caros:** uno entierra correo de JP en DELEGADO, y los otros tres son
  los de ENZO, donde la tarea nunca llega a derivarse y nadie se entera de que
  quedó sin hacer. Los cuatro son invisibles para JP, que es lo que los hace
  caros.

## 2. Las cinco etapas

El pedido completo de JP —producción, aprendizaje autónomo, y un bot que escuche
siempre— son tres proyectos. Se diseñan juntos y se construyen en este orden, una
rama por etapa, cada una entregando algo utilizable el día que termina.

| Etapa | Qué entrega |
|---|---|
| **1. Que dejes de mirar la casilla** | Corre sola, clasifica, archiva ruido, manda los tres resúmenes, avisa lo tuyo. **No escribe correo.** |
| **2. Que aprenda sola** | Redacta la regla, la prueba contra los casos guardados, la escribe si corresponde, te lo informa. |
| **3. Que escriba** | Derivaciones y respuestas dictadas, con aprobación. Seguimiento a los 3 días. |
| **4. Que escuche siempre** | Preguntas sobre el estado del correo (requiere leer Enviados), escribir de cero, enseñar sin correo de por medio. |
| **5. Recordatorios** | La Capa 2 original. |

**Por qué "aprenda sola" antes que "escriba":** ENZO está en 0% y las
derivaciones a Enzo son justo lo que tiene que salir bien. Si la Etapa 3 va
primero, el sistema empieza a mandar correos con una categoría que sabemos rota.

**Por qué la Etapa 1 no escribe:** los primeros días corriendo sola van a
aparecer cosas que hoy no se pueden prever. Conviene que aparezcan cuando lo peor
que puede pasar es que muestre algo de más, no que salga un correo con la firma
de JP a un cliente.

**Lo que la Etapa 1 no resuelve, y hay que decirlo:** JP va a seguir abriendo el
correo para contestar lo suyo y derivar lo que corresponde. Son unos 4 correos
por día sobre 15. La etapa le saca de encima los otros 11.

## 3. Alcance de la Etapa 1

**Dentro:**

- Proceso permanente en la Mac mini, levantado por `launchd`.
- Clasificación de todo el correo entrante, con la cadena de motores actual.
- Archivado de ruido a `INBOX.Ruido`.
- Resúmenes de 8:30, 17:00 y 18:00.
- Aviso en el momento de lo que es de JP y de lo que hay que derivar.
- Marcado como leído de lo que JP cierra desde Telegram.
- Registro de correcciones como casos nuevos.
- Cambio de motor desde Telegram.
- Arreglo del PROCEDIMIENTO que tiene ENZO en 0%.

**Fuera:**

- Enviar correo, de cualquier tipo. Ni derivaciones ni respuestas.
- Escribir reglas sin intervención (Etapa 2).
- Leer la carpeta de enviados (Etapa 4).
- Responder preguntas libres por Telegram (Etapa 4).
- Los ~300 correos atrasados. Se limpian aparte, con `explorar.py`, cuando JP
  quiera.

## 4. Arquitectura

### 4.1 Por qué se parte el código

Hoy `simulacro.py` son 1.211 líneas que hacen IMAP, Telegram, el modelo, las
reglas y la línea de comandos. Le alcanzó a un simulacro; con un reloj, una base
de estado y un proceso permanente encima se vuelve intocable.

Se descartó explícitamente escribir el proceso nuevo de cero dejando
`simulacro.py` quieto. Suena limpio y es la peor opción: quedarían dos
clasificadores, el de producción y el que mide la regresión, y el día que se
separen **los 133 casos dejan de decir nada sobre el sistema que corre de
verdad**. La medición es lo único que permitió pasar de 82% a 92%.

**El clasificador que corre en producción tiene que ser el mismo que mide la
regresión.**

### 4.2 Las piezas

| Pieza | De qué se ocupa | De qué depende |
|---|---|---|
| `correo.py` | IMAP y SMTP: leer sin marcar, mover, marcar leído, enviar (Etapa 3) | red |
| `bot.py` | Telegram: mandar, botones, escuchar, transcribir audio | red |
| `clasificador.py` | El prompt, la cadena de motores, la doble pasada | `reglas.py`, red |
| `reglas.py` | Leer y escribir `reglas.md`, `roster.md`, `codigos.md` | disco |
| `memoria.py` | SQLite: situación de cada correo y pendientes | disco |
| `secretaria.py` | El proceso: reloj, dos hilos, y quién hace qué | todas |

`simulacro.py`, `explorar.py`, `revisar_reglas.py` y `archivar_ruido.py` siguen
funcionando, importando estas mismas piezas.

### 4.3 Los dos hilos

Telegram deja escuchar a un solo proceso a la vez, y clasificar tarda entre 20 y
180 segundos con NVIDIA. Con un solo hilo, JP le escribe al bot y queda mudo tres
minutos.

- **El hilo que escucha** no hace nada pesado: recibe el botón o el audio, lo
  anota en SQLite y contesta.
- **El hilo que trabaja** mira la casilla cada 3 minutos, clasifica, y a las
  8:30, 17:00 y 18:00 arma los resúmenes.

Se comunican por SQLite. Se descartó partirlo en dos procesos con una cola en
disco: es la arquitectura correcta para un equipo, y para una Mac mini con un
usuario es infraestructura que nadie va a atender.

### 4.4 Estado

SQLite, no archivos JSON. Dos hilos tocan el mismo estado; con archivos sueltos
se corrompe el día que coincidan. Viene con Python y no agrega dependencias.

Como el estado está en disco y no en memoria, si el proceso se cae retoma donde
quedó: lo ya clasificado no se reclasifica, y lo que esperaba confirmación la
sigue esperando.

## 5. Motores de clasificación

La cadena está hoy escrita a mano en el código. Pasa a configuración, con la
lista en orden de preferencia: primero el elegido, atrás los respaldos. El
proceso relee el archivo, así que cambiarlo no requiere editar Python ni
reiniciar nada.

**Se cambia desde Telegram.** `/motor groq` cambia la preferencia y contesta con
qué modelo quedó. JP trabaja desde el celular y no quiere abrir una terminal.

**Ollama vuelve cuando JP lo habilite.** Se sacó de la cadena el 2026-08-12
porque necesitaba la Mac para otro proyecto y no puede tener dos modelos
cargados a la vez. Queda en la configuración, comentado y con el motivo. Se
habilita con una línea.

**No tiene que ser el mismo motor para todo.** El prefiltro de ruido puede correr
en un modelo local —es gratis y sobra para distinguir un newsletter— y reservar
el bueno para lo que discrimina de verdad. La configuración lo permite aunque la
Etapa 1 arranque con uno solo para todo.

## 6. La situación de un correo

Cada correo entra una vez, se clasifica una vez, y queda anotado con su
situación. Las transiciones las dispara JP desde Telegram o el reloj.

```
        nuevo
          │
     clasificado
          │
   ┌──────┼──────────┬─────────────┬──────────────┐
   │      │          │             │              │
 RUIDO  DELEGADO  ENZO/NATALIA    TUYO      DUDA / sin clasificar
   │      │          │             │              │
archivado │      avisado        avisado      mostrado
   │      │          │             │              │
listado  en el   pendiente     pendiente    lo decide JP
 18:00  resumen  de derivar    de que JP        │
   │      │      (Etapa 3)     lo resuelva      │
   │      │          │             │              │
   └──────┴──────────┴─────────────┴──────────────┘
                     │
              corregido por JP → vuelve a clasificarse
                     │
                  cerrado
```

**Un correo sin clasificar nunca se archiva ni se esconde.** Si ningún motor
respondió, se le muestra a JP con los botones de siempre. El silencio jamás
significa "era ruido".

## 7. Los flujos

### 7.1 Resumen de 8:30 y de 17:00

Lo de JP y lo derivable van arriba y **sin botón de cerrar**: quedan abiertos
hasta que haga algo. Lo del equipo se cierra de a uno o todo junto.

```
Buen día. Entraron 7 correos desde ayer 19:00.

📌 Tuyo (1)
  · Suhr Ingeniería — "Cierre de etapa"

➡️ Para derivar (1)
  · OPS SRL — IMEI de 12 unidades → Enzo

✅ El equipo lo maneja (4)
  1. administracion@ — RE: IVA JULIO
  2. Daniela Ramos — conductores OPS
  3. tecnicos@ — Perfil R. Betelu
  4. Lahue SRL — turno de mantenimiento

🗑 Archivado como ruido: 1

[ ✓ Leí todo ]  [ Abrir uno ]  [ ⚠ Uno es mío ]
```

`Leí todo` marca como leídos los de la sección del equipo, y solo esos.
`Uno es mío` abre la lista numerada y lo que JP señale se registra como
corrección.

### 7.2 Aviso en el momento

Solo para lo que es de JP y lo que hay que derivar. Son unos 4 por día.

```
📌 Es tuyo · hoy 11:42
De: Miriam Arcuri <miriam.arcuri@caesistemas.com.ar>
CC: administracion@
Asunto: Envío lectores
📎 2026-07-27 17-52.pdf — PDF, 302 KB

Te confirma el envío de los lectores…

[ Listo, lo vi ]  [ No era mío ]  [ ⭐ Cliente importante ]
```

**Horario:** de 8 a 19 en días hábiles avisa en el momento. Fuera de eso queda
para el resumen de las 8:30. La excepción son los clientes importantes, que
interrumpen siempre — la lista está vacía a la fecha, así que hoy la excepción
no existe en la práctica.

**Un correo que ya viene leído se respeta.** Si JP lo abrió desde el celular
antes de que la secretaria lo mire, no se lo muestra de nuevo. Lo clasifica y lo
archiva si es ruido, pero no lo interrumpe con algo que ya leyó.

### 7.3 Resumen de ruido de las 18:00, y el circuito de corrección

```
🗑 Archivé 5 correos como ruido hoy:

1. M2M Dataglobal — promoción 11 años
2. Ruptela — desayuno ESS+ 2026
3. LinkedIn — 4 empleos para vos
4. SiPago — manual de contracargos
5. Zentra Sales — recibimos tu solicitud

[ ✓ Todos eran ruido ]   [ Rever ]
```

`Rever` convierte cada renglón en un botón. JP elige uno, la secretaria pregunta
por qué, y él contesta por texto o por audio. Entonces:

1. El correo **vuelve a la bandeja como no leído**.
2. Se reclasifica con lo que JP acaba de explicar.
3. Si ahora es de Natalia, aparece como pendiente de derivar.

El paso 3 es el que hace que la corrección sirva. Sacarlo de Ruido y dejarlo ahí
sería devolverle el trabajo.

### 7.4 Qué guarda una corrección

El correo entero, lo que dijo el sistema, lo que dijo JP, y su explicación tal
cual — si fue audio, la transcripción y el audio. Se suma a los 133 casos.

**En esta etapa las reglas las sigue escribiendo Claude**, con esos casos y
verificadas contra la regresión antes de entrar. La Etapa 2 es exactamente eso
sin intermediario; por eso va después: la Etapa 1 llena el archivo de casos que
la Etapa 2 necesita para poder probarse sola.

## 8. Cuando algo se rompe

En el simulacro había alguien mirando. Ahora no.

| Falla | Qué hace |
|---|---|
| No responde ningún motor | Muestra el correo sin clasificar. **Nunca lo archiva.** |
| Se cae el IMAP | Reintenta con esperas crecientes; no reprocesa lo ya hecho |
| Se cae Telegram | Los avisos quedan en cola y salen cuando vuelve. Ninguna falla de Telegram mata el proceso |
| Se cae el proceso | `launchd` lo levanta y retoma desde SQLite |

**La falla que más preocupa es la silenciosa:** que deje de mirar el correo y JP
no se entere, porque no recibir avisos se parece mucho a un día tranquilo.

Contra eso, **los resúmenes salen siempre, aunque no haya nada**. "8:30 — no
entró nada nuevo" es información. Y si la secretaria estuvo caída, el resumen lo
dice y desde cuándo.

**El freno de emergencia** del diseño original se mantiene: `/pausa` y deja de
tocar la casilla. No archiva, no marca leído. Sigue avisando lo que entra.

## 9. Verificación

Tres cosas distintas, que se prueban distinto.

**Que el criterio no empeore.** La regresión sobre los 133 casos, antes de
integrar cualquier cambio de regla o de prompt. Si baja, no entra. Ya funciona:
es lo que detectó que la regla de ENZO no arreglaba nada.

**Que la mecánica no se equivoque.** Es lo que hoy no se prueba nunca y lo que
puede hacer daño. Se prueba sin red y sin tocar la casilla, con los casos
guardados como material:

- que un correo sin clasificar nunca termine archivado;
- que se marque leído solo por botón de JP, nunca antes;
- que sacar algo de Ruido lo devuelva **como no leído** y lo reclasifique;
- que los resúmenes salgan aunque no haya nada;
- que un botón de un resumen viejo no conteste por el de hoy;
- que se busque siempre por Message-ID y nunca por posición.

**Que no haga daño en la casilla real.** Puesta en marcha en tres pasos, nunca
dos el mismo día:

1. **En seco.** Corre completa y manda los resúmenes, pero no toca la casilla.
   Los mensajes llevan marca de simulación. Unos días, con correo real y sin
   riesgo.
2. **Archiva ruido.** Reversible: el correo está en una carpeta.
3. **Marca leído.**

Nunca los pasos 2 y 3 juntos, para saber cuál rompió qué si algo rompe.

**Lo que no se puede probar** es si el modelo va a acertar en un correo que nunca
vio. Eso no tiene test, tiene medición. Va a fallar; la pregunta es si JP se
entera cuando falle. Por eso el ruido se archiva en vez de borrarse, por eso
existe el resumen de las 18:00, y por eso lo que no entiende se muestra en vez
de decidirse.

## 10. Seguridad

**La contraseña del correo hay que cambiarla antes de esta etapa.** Se compartió
por chat y sigue sin rotar. En un simulacro que solo lee, aguanta; en un proceso
permanente que además escribe en la casilla, no. Lo mismo para las claves de los
motores.

Se mantiene todo lo del diseño original: `BODY.PEEK[]` siempre, `readonly=True`
al leer, nunca `EXPUNGE` a secas, búsqueda por Message-ID. `datos/` no se
versiona porque tiene correo real de clientes.

## 11. Lo que queda abierto

- **La lista de clientes importantes está vacía.** Hasta que JP marque alguno, la
  excepción de horario no tiene efecto.
- **Orbcomm:** JP planteó que la factura es de Natalia y la relación con un
  proveedor nuevo es de él, al mismo tiempo. Las categorías no son excluyentes
  (sección 6.2 del diseño original) pero Telegram obliga a elegir una. Sin
  resolver; reaparece en la Etapa 3, cuando haya que decidir a quién se le
  escribe.
- **`reglas.md` tiene 342 líneas y entra entera en cada consulta al modelo.** Va
  a seguir creciendo. Podarlo es trabajo de la Etapa 2.
