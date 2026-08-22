# Cómo operar la secretaria

Este documento es para JP, que no quiere abrir una terminal si lo puede
evitar, y para quien retome el proyecto más adelante. Cubre lo operativo:
arrancar, parar, mirar el log, entender los comandos de Telegram, sacar el
freno sin arriesgar la casilla real, y qué hacer si algo se ve raro.

Para *qué hace* la secretaria y *por qué* está diseñada así, la fuente de
verdad son los dos documentos de diseño en
`docs/superpowers/specs/`. Esto es el manual de uso, no la explicación del
diseño.

## Antes que nada: hoy no hay motor de respaldo

**Si NVIDIA se cae, la secretaria se queda muda.** El motor de respaldo
(Groq) está roto: el modelo que usaba fue dado de baja, y el modelo nuevo
rechaza el prompt entero por tamaño (unos 6.000 tokens de roster + reglas,
HTTP 413 en el nivel gratuito). Está anotado con la fecha y el detalle en
`motores.json`, en la entrada de `groq`.

Esto no frena nada por sí solo —el proceso sigue vivo, sigue avisando lo
que entra— pero significa que si NVIDIA tiene una caída larga, ningún
correo se clasifica hasta que vuelva. `/estado` dice qué motor está activo
ahora mismo si querés confirmarlo. Se arregla achicando el prompt,
habilitando Ollama de nuevo, o pagando el nivel de Groq — ninguna de las
tres es parte de esta etapa.

## Arrancar y parar

Hoy la secretaria **no está corriendo como servicio**: cada vez que JP la
quiere despierta, alguien la arranca a mano desde la terminal. El `plist`
de `launchd` ya está escrito y probado (`plutil -lint` da `OK`), pero
**no se cargó todavía** — cargarlo es una decisión de JP, porque a partir
de ahí el proceso queda permanente y le va a escribir al Telegram real sin
que nadie lo esté mirando.

### Arrancar a mano (lo de hoy)

```bash
cd ~/Documents/secretaria_personal
set -a; . ./.env; set +a
python3 -u secretaria.py
```

Con eso arranca **en seco** por default (ver la sección de abajo): clasifica,
manda los resúmenes, pero no toca la casilla. El `-u` es importante — sin
eso Python guarda la salida en un buffer y el log no se ve en el momento
que pasan las cosas, sólo cuando el buffer se llena o el proceso termina.

Para dejarla corriendo en segundo plano y no perder la terminal:

```bash
cd ~/Documents/secretaria_personal
set -a; . ./.env; set +a
nohup python3 -u secretaria.py > datos/secretaria.log 2>&1 &
```

Esto anota el PID en pantalla (`[1] 12345`). Guardalo o buscalo después con:

```bash
ps aux | grep secretaria.py
```

### Parar (lo de hoy)

Si está en primer plano: `Ctrl-C`. Si está en segundo plano:

```bash
kill <pid>
```

y confirmar que se fue con `ps aux | grep secretaria.py` de nuevo — no
debería aparecer nada. Los dos hilos son *daemon*, así que matando el
proceso principal se van con él; no hace falta nada más prolijo que eso.

### Arrancar y parar una vez que el servicio esté cargado

Esto es para el día que JP decida dar el paso de `launchd` (no forma parte
de esta etapa; queda documentado para cuando corresponda):

```bash
cp com.fullcontrolgps.secretaria.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fullcontrolgps.secretaria.plist
launchctl list | grep secretaria
```

**Ojo con esto:** el `plist` tiene `KeepAlive` en `true` sin condiciones,
que es justo lo que se quiere para que un cuelgue se recupere solo — pero
también significa que un `kill` a mano sobre el proceso no lo apaga:
`launchd` lo levanta de nuevo enseguida, como si hubiera sido una caída.
Para apagarlo de verdad una vez cargado hay que sacarlo de `launchd`:

```bash
launchctl unload ~/Library/LaunchAgents/com.fullcontrolgps.secretaria.plist
```

Si lo que se quiere es frenar la escritura sin apagar el proceso —por
ejemplo porque algo se ve raro y hay que darse tiempo para mirar sin que
mientras tanto siga archivando— **`/pausa` desde Telegram es más rápido y
no corta los avisos** (ver más abajo). `unload` es para cuando hay que
parar todo, incluida la clasificación.

## Dónde está el log

- **Corriendo a mano en primer plano:** el log es la terminal, no hay
  archivo.
- **Corriendo a mano en segundo plano con `nohup ... > archivo`:** donde
  JP haya redirigido la salida (el ejemplo de arriba usa
  `datos/secretaria.log`).
- **Corriendo como servicio de `launchd`:** siempre
  `datos/secretaria.log` — está fijado así en el `plist`
  (`StandardOutPath`/`StandardErrorPath`), stdout y stderr mezclados en el
  mismo archivo.

El log no tiene formato de niveles (INFO/ERROR): cada línea rara empieza
con un prefijo entre corchetes que dice de qué parte vino —
`[correo]`, `[escucha]`, `[archivar]`, `[rever]`, `[leido]`, `[cerrar]`,
`[confirmar]`, `[listo]`, `[importante]`, `[categoria]`, `[uno_es_mio]`,
`[telegram]`, `[arrancar]`— seguido del tipo de excepción y el mensaje.
Una línea sin corchetes al principio (por ejemplo
`      (HTTP 503 — esperando 1s)`) es un reintento normal, no una falla:
tanto Telegram como los motores de clasificación cortan a veces por
tropiezos pasajeros, y el reintento con espera creciente ya está
incorporado (`bot.tg`, `clasificador._pedir`). Verlo una vez sin que se
repita en bucle es sano, no un síntoma.

## Los comandos de Telegram

Los cuatro comandos que entiende hoy, todos por texto directo al bot (no
son *slash commands* de Telegram con autocompletado, son texto plano que
el bot interpreta):

- **`/pausa`** — Frena la escritura: no archiva ni marca leído. **No frena
  la clasificación ni los avisos** — la secretaria te sigue avisando lo
  que entra, sigue mandando los resúmenes, sigue mostrando lo que no supo
  clasificar. Es el freno de emergencia: si algo empezó a comportarse raro
  y hay que darse tiempo para mirar sin que la casilla se siga moviendo
  mientras tanto, esto es lo primero que hay que tocar.
- **`/sigo`** — Suelta la pausa. Vuelve a escribir (si el freno de fondo,
  `SECRETARIA_EN_SECO`, también lo permite — ver la sección siguiente).
- **`/motor <nombre>`** — Cambia cuál motor de clasificación se usa
  primero, por ejemplo `/motor groq`. El cambio se escribe en
  `motores.json` y se relee en cada clasificación: no hace falta reiniciar
  el proceso para que tome efecto. Si el nombre no existe, contesta cuáles
  hay.
- **`/estado`** — Un resumen rápido: cuántos correos entraron hoy, cuándo
  fue el último latido (que el hilo que mira la casilla sigue vivo y dando
  vueltas), qué motor está activo, si está en seco y si está pausada.
  Es el primer comando para tirar cuando algo se ve raro y no se quiere
  abrir una terminal.

Cualquier otro texto sin nada pendiente de contestar recibe la lista de
estos cuatro comandos — no inventa una respuesta ni dice "no entiendo" a
secas.

## Cómo se saca el freno, en dos pasos, y por qué nunca los dos el mismo día

Hay un solo interruptor real en el código: la variable de entorno
`SECRETARIA_EN_SECO`, que se lee del `.env` una sola vez, al arrancar el
proceso (no es como `motores.json`: cambiarla requiere reiniciar). Por
default (o con `SECRETARIA_EN_SECO=true`) la secretaria clasifica, avisa y
arma los resúmenes, pero **no toca la casilla**: ni `mover_a` (archivar
ruido) ni `marcar_leido` se ejecutan. Con `SECRETARIA_EN_SECO=false` los
dos quedan habilitados a la vez — no hay una variable separada para cada
uno.

Entonces, ¿de dónde sale el "en dos pasos" del que habla el diseño? De que
**archivar y marcar leído no dependen de lo mismo para disparar**:

- **Archivar ruido pasa solo**, cada vez que da la vuelta el ciclo (cada 3
  minutos), sin que JP haga nada. Es la parte reversible: el correo sigue
  existiendo, sólo cambió de carpeta, y se puede arrastrar de vuelta desde
  cualquier cliente de correo.
- **Marcar leído sólo pasa si JP toca el botón "Leí todo"** en un resumen.
  Nunca es automático.

Así que el primer paso real es: **rotar la contraseña del correo** (se
compartió por chat en su momento y nunca se cambió; en seco no importa
porque no se escribe nada, pero apenas se archive sí) y recién después
poner `SECRETARIA_EN_SECO=false` y reiniciar el proceso. Ese mismo día,
**JP no toca "Leí todo"** — sólo mira que el archivado de ruido ande bien
durante unos días. El interruptor técnicamente ya habilita marcar leído
también, pero como esa acción sólo ocurre si alguien aprieta el botón, con
no apretarlo alcanza para separar los dos pasos en la práctica. Recién en
un día aparte, una vez conforme con cómo archivó, JP empieza a tocar "Leí
todo" de verdad.

**Nunca los dos el mismo día**, aunque el código lo permitiría, porque si
algo se rompe se necesita saber cuál de las dos cosas lo rompió. Con los
dos pasos separados en el tiempo, un problema que aparece el día que se
prueba "Leí todo" es casi seguro de marcar leído, no de archivar — ya
estuvo probado por su cuenta.

## Cómo se vuelve atrás si algo sale mal

En orden, de más rápido a más definitivo:

1. **`/pausa` desde Telegram.** Corta toda escritura al instante, sin
   tocar el proceso ni la terminal. Es reversible con `/sigo` apenas se
   entienda qué pasó. Primera reacción casi siempre.
2. **Volver a `SECRETARIA_EN_SECO=true` en el `.env` y reiniciar el
   proceso.** Más lento que `/pausa` porque hace falta la terminal, pero
   es el mismo freno que el arranque original — vuelve a clasificar y
   avisar sin tocar nada.
3. **Devolver un correo archivado por error.** Está en `INBOX.Ruido`, no
   se borró: se arrastra de vuelta a la bandeja desde cualquier cliente de
   correo (Mail, webmail, lo que JP use), y queda como estaba. Si el error
   es sistemático (una regla mal escrita archivando algo que no debía),
   el botón **Rever** del resumen de las 18:00 hace lo mismo pero además
   reclasifica con la corrección de JP — mejor que arrastrarlo a mano
   cuando se puede.
4. **Marcado como leído por error.** No tiene automatismo de vuelta: hay
   que volver a marcarlo como no leído a mano desde el cliente de correo.
   Es la razón de fondo por la que "Leí todo" es el paso que se prueba
   último y por separado: es el único de los dos que no se deshace solo.
5. **Apagar el proceso entero.** `kill <pid>` (o `launchctl unload` si ya
   está cargado como servicio, ver arriba). Es el último recurso: mientras
   está apagado, nadie clasifica ni avisa nada, así que un correo
   importante puede pasar desapercibido hasta que se lo vuelva a prender.
6. **Volver atrás en el código.** Si la sospecha es un bug y no una
   configuración, `git log` y `git revert`/`git checkout` sobre el commit
   sospechoso, con el proceso apagado mientras tanto.

## Qué mirar cuando algo parece roto

En este orden:

1. **`/estado` por Telegram.** Si contesta, el hilo que escucha está vivo
   y responde rápido (no necesita candado largo). Mirá el "último latido":
   si es de hace más de unos minutos con el proceso corriendo, el hilo que
   mira la casilla se colgó o murió sin que `arrancar()` se diera cuenta
   todavía (`POLL_HILOS` es de 1 segundo, así que no debería tardar).
2. **El log**, buscando líneas con corchetes que se repiten seguidas. Una
   vez es un tropiezo de red; la misma línea cada 3 minutos durante horas
   es una falla real (credenciales vencidas, IMAP caído de verdad, motor
   sin cuota en ningún proveedor).
3. **`ps aux | grep secretaria.py`** (o `launchctl list | grep secretaria`
   si ya está cargado). Si no aparece nada y se esperaba que estuviera
   corriendo, se murió — con el `plist` cargado debería haber vuelto solo
   (`KeepAlive`); a mano, hay que arrancarla de nuevo.
4. **La base**, si hace falta más detalle que el log
   (`datos/secretaria.db`, tabla `correos`). Por ejemplo:
   ```bash
   sqlite3 -readonly datos/secretaria.db \
     "SELECT situacion, COUNT(*) FROM correos GROUP BY situacion;"
   ```
   Un número creciente en `pendiente_de_archivar` o `pendiente_de_borrar`
   dice que algo está fallando al escribir y reintentando; después de
   `INTENTOS_MAXIMOS` (5) sin resolverse, JP recibe un aviso por Telegram
   y el correo pasa a `no_se_pudo_archivar` — no se pierde, pero requiere
   mirarlo a mano.
5. **`motores.json`** si la sospecha es de clasificación: qué motor está
   primero, y si tiene alguna nota de "fuera de servicio" como la que
   ya tiene Groq (ver la advertencia al principio de este documento).

Un correo que nunca se clasificó (sin categoría, `situacion` en
`clasificado` pero `categoria` vacía o `ERROR`) **no es un bug que haya
que arreglar corriendo**: es el comportamiento correcto cuando ningún
motor respondió — se le muestra a JP con botones en vez de perderse. Ver
esos avisos de vez en cuando es normal; verlos todo el tiempo significa
que ningún motor está respondiendo.
