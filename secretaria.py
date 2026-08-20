#!/usr/bin/env python3
"""La secretaria: el proceso que corre solo y coordina a los demás módulos.

Dos hilos. El que escucha nunca hace nada pesado —recibe el botón o el
audio, lo anota y contesta— porque clasificar tarda hasta tres minutos con
nvidia y con un solo hilo el bot quedaría mudo todo ese rato.

Arranca EN SECO: clasifica y manda los resúmenes, pero no toca la casilla.
Se le saca el freno con SECRETARIA_EN_SECO=false, y de a un paso: primero
que archive ruido, que es reversible, y otro día que marque leído.
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta

import bot, clasificador, correo, memoria, reglas

EN_SECO = os.environ.get("SECRETARIA_EN_SECO", "true").lower() != "false"
CADA = 180                    # cada cuánto mira la casilla, en segundos
POLL_HILOS = 1                 # cada cuánto arrancar() se fija que sigan vivos
HORA_INICIO, HORA_FIN = 8, 19

MOMENTOS = [("manana", 8, 30), ("tarde", 17, 0), ("ruido", 18, 0)]


def en_horario(momento):
    """De 8 a 19, días hábiles (lunes a viernes). Fuera de eso, lo que
    entra espera al resumen de las 8:30 siguiente.

    Acá no hay excepción para clientes importantes: esta función sólo mide
    el reloj de pared. Esa excepción —avisar al toque en vez de esperar al
    resumen— vive del lado de cómo se manda el aviso, no del horario en
    sí; la implementa la tarea 8."""
    return momento.weekday() < 5 and HORA_INICIO <= momento.hour < HORA_FIN


class Reloj:
    """Decide qué resúmenes corresponden, sin dormir ni mirar la hora sola.

    Recibe el ahora y el último momento en que se lo consultó, así se puede
    probar con fechas inventadas. Si la secretaria estuvo caída doce horas
    —o varios días—, devuelve todo lo que se perdió, en orden.
    """

    def momentos_pendientes(self, ahora, ultimo):
        # Recorre cada día entre `ultimo` y `ahora`, inclusive: calcular el
        # corte siempre sobre la fecha de `ahora` (como hacía la primera
        # versión) pierde para siempre los resúmenes de los días
        # intermedios si la caída duró más de una jornada. Como MOMENTOS
        # ya está en orden cronológico dentro del día, y los días se
        # recorren de más viejo a más nuevo, la salida queda ordenada sin
        # necesidad de un sort aparte.
        salida = []
        dia = ultimo.date()
        un_dia = timedelta(days=1)
        while dia <= ahora.date():
            for nombre, h, m in MOMENTOS:
                corte = datetime(dia.year, dia.month, dia.day, h, m)
                if ultimo < corte <= ahora:
                    salida.append(nombre)
            dia += un_dia
        return salida


def _registrar(prefijo, e):
    """Loguea una excepción de los ciclos sin poder tirar nada hacia
    afuera.

    Si esto fallara —el propio `print` con un pipe roto o el disco lleno,
    nada exótico en un proceso que corre meses— la excepción se escapaba
    del `except` que la llama y mataba el hilo en silencio: el otro hilo
    seguía vivo, así que ni `arrancar()` se enteraba. Por eso acá adentro
    no puede quedar nada sin atajar, y por eso además `arrancar()` vigila
    si algún hilo se murió en vez de confiar en que esto alcance."""
    try:
        print(f"[{prefijo}] {type(e).__name__}: {e}", flush=True)
    except Exception:
        pass


class Secretaria:
    def __init__(self, cx=None):
        self.cx = cx or memoria.abrir()
        self.reloj = Reloj()
        self.ultimo_reloj = datetime.now()
        self.offset = 0
        self.parada = threading.Event()
        self.pausada = False

    def ciclo_de_correo(self):
        while not self.parada.is_set():
            try:
                self.revisar_casilla()
                ahora = datetime.now()
                self._disparar_resumenes(ahora)
                memoria.latido(self.cx, ahora.isoformat(timespec="seconds"))
            except Exception as e:
                # Nada que pase acá adentro puede matar el proceso: si se
                # muere, JP no se entera, porque no recibir avisos se parece
                # mucho a un día tranquilo.
                _registrar("correo", e)
            self.parada.wait(CADA)

    def _disparar_resumenes(self, ahora):
        """Manda los resúmenes que correspondan entre `self.ultimo_reloj`
        y `ahora`, y actualiza el reloj interno.

        Separado de `ciclo_de_correo` para poder probarlo con un par de
        llamadas manuales seguidas, sin hilos ni tiempos reales de por
        medio. El reloj sólo avanza hacia adelante: si `ahora` viene antes
        que `self.ultimo_reloj` —el reloj del sistema saltó hacia atrás,
        típico de un NTP que corrige después de un suspend largo— no lo
        piso, porque si no la vuelta siguiente compararía contra un
        `ultimo_reloj` adelantado y podría volver a disparar un resumen
        que ya se mandó.
        """
        for momento in self.reloj.momentos_pendientes(
                ahora, self.ultimo_reloj):
            self.mandar_resumen(momento)
        if ahora > self.ultimo_reloj:
            self.ultimo_reloj = ahora

    def ciclo_de_escucha(self):
        while not self.parada.is_set():
            try:
                d = bot.tg("getUpdates", offset=self.offset, timeout=50)
                for u in d.get("result", []):
                    self.offset = u["update_id"] + 1
                    self.atender(u)
            except Exception as e:
                _registrar("escucha", e)
                time.sleep(5)

    def revisar_casilla(self):
        raise NotImplementedError("tarea 8")

    def mandar_resumen(self, momento):
        raise NotImplementedError("tarea 9")

    def atender(self, update):
        raise NotImplementedError("tarea 11")

    def arrancar(self):
        hilos = [threading.Thread(target=self.ciclo_de_correo, daemon=True,
                                   name="correo"),
                 threading.Thread(target=self.ciclo_de_escucha, daemon=True,
                                   name="escucha")]
        for h in hilos:
            h.start()
        # Nada de join() a ciegas: los cuerpos de los ciclos ya no dejan
        # escapar excepciones (ver _registrar), pero si un hilo se muere
        # igual —por lo que sea— el otro sigue vivo y un join() sobre
        # ambos se queda esperando para siempre. El proceso no se cae, y
        # entonces launchd nunca lo reinicia: queda medio muerto y en
        # silencio, que es la falla que más preocupa. Por eso vigilamos, y
        # apenas se cae alguno terminamos ruidosamente con código
        # distinto de cero para que KeepAlive lo levante de nuevo.
        while not self.parada.is_set():
            muertos = [h for h in hilos if not h.is_alive()]
            if muertos:
                nombres = ", ".join(h.name for h in muertos)
                print(f"[arrancar] se murió el hilo {nombres}: fuerzo la"
                      " salida para que launchd reinicie el proceso",
                      flush=True)
                self.parada.set()
                sys.exit(1)
            self.parada.wait(POLL_HILOS)


if __name__ == "__main__":
    Secretaria().arrancar()
