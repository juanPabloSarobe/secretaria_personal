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
import threading
import time
from datetime import datetime

import bot, clasificador, correo, memoria, reglas

EN_SECO = os.environ.get("SECRETARIA_EN_SECO", "true").lower() != "false"
CADA = 180                    # cada cuánto mira la casilla, en segundos
HORA_INICIO, HORA_FIN = 8, 19

MOMENTOS = [("manana", 8, 30), ("tarde", 17, 0), ("ruido", 18, 0)]


def en_horario(momento):
    """De 8 a 19, días hábiles. Fuera de eso lo que entra espera al resumen
    de las 8:30, salvo los clientes importantes."""
    return momento.weekday() < 5 and HORA_INICIO <= momento.hour < HORA_FIN


class Reloj:
    """Decide qué resúmenes corresponden, sin dormir ni mirar la hora sola.

    Recibe el ahora y el último momento en que se lo consultó, así se puede
    probar con fechas inventadas. Si la secretaria estuvo caída doce horas,
    devuelve todo lo que se perdió, en orden.
    """

    def momentos_pendientes(self, ahora, ultimo):
        salida = []
        for nombre, h, m in MOMENTOS:
            corte = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
            if ultimo < corte <= ahora:
                salida.append(nombre)
        return salida


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
                for momento in self.reloj.momentos_pendientes(
                        ahora, self.ultimo_reloj):
                    self.mandar_resumen(momento)
                self.ultimo_reloj = ahora
                memoria.latido(self.cx, ahora.isoformat(timespec="seconds"))
            except Exception as e:
                # Nada que pase acá adentro puede matar el proceso: si se
                # muere, JP no se entera, porque no recibir avisos se parece
                # mucho a un día tranquilo.
                print(f"[correo] {type(e).__name__}: {e}", flush=True)
            self.parada.wait(CADA)

    def ciclo_de_escucha(self):
        while not self.parada.is_set():
            try:
                d = bot.tg("getUpdates", offset=self.offset, timeout=50)
                for u in d.get("result", []):
                    self.offset = u["update_id"] + 1
                    self.atender(u)
            except Exception as e:
                print(f"[escucha] {type(e).__name__}: {e}", flush=True)
                time.sleep(5)

    def revisar_casilla(self):
        raise NotImplementedError("tarea 8")

    def mandar_resumen(self, momento):
        raise NotImplementedError("tarea 9")

    def atender(self, update):
        raise NotImplementedError("tarea 11")

    def arrancar(self):
        hilos = [threading.Thread(target=self.ciclo_de_correo, daemon=True),
                 threading.Thread(target=self.ciclo_de_escucha, daemon=True)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()


if __name__ == "__main__":
    Secretaria().arrancar()
