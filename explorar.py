#!/usr/bin/env python3
"""
Barre un período largo y arma una lista corta de casos que valen tu tiempo.

El problema que resuelve: en la bandeja, el ruido es mayoría. Revisar correos
en orden cronológico hace que JP confirme "ruido" una y otra vez sin enseñar
nada — el clasificador ya va 32 de 32 en esa categoría. Lo que falta aprender
está en las otras, que son minoría.

Este barrido usa el modelo LOCAL a propósito. No decide nada: solo separa lo
evidente de lo dudoso, y para eso un modelo chico alcanza. Es gratis, no tiene
cuota diaria y no compite con la operación. El modelo bueno se reserva para los
correos que efectivamente lleguen a Telegram.

Entra en la lista corta todo lo que NO sea ruido evidente:
  - lo que el modelo local clasifica en cualquier categoría que no sea RUIDO
  - lo que clasifica distinto en cada pasada (ambigüedad real)

Uso:  python3 explorar.py [meses] [--motor ollama|nvidia|groq]
"""
import email.utils, glob, json, os, sys, time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clasificador import clasificar_una_vez, prompt_sistema  # noqa: E402
from simulacro import identidad, ids_respondidos, traer_correos  # noqa: E402

_args = sys.argv[1:]
MOTOR = "ollama"
if "--motor" in _args:
    i = _args.index("--motor")
    MOTOR = _args[i + 1]
    del _args[i:i + 2]
SALTEAR, PREVIO = 0, None
if "--retomar" in _args:
    i = _args.index("--retomar")
    PREVIO = _args[i + 1]
    del _args[i:i + 2]
MESES = int(_args[0]) if _args else 2


def main():
    desde = date.today() - timedelta(days=30 * MESES)
    print(f"Barriendo desde {desde} con el motor {MOTOR}…")

    correos = traer_correos(0, desde)
    ya = ids_respondidos()
    pendientes = [c for c in correos if identidad(c) not in ya]
    print(f"  {len(correos)} en el período, {len(pendientes)} sin revisar\n")
    if not pendientes:
        return

    sistema = prompt_sistema()
    sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ruta = f"datos/candidatos-{sello}.json"
    os.makedirs("datos", exist_ok=True)

    candidatos, saltear = [], 0
    if PREVIO:
        # Un barrido de una hora se corta por cualquier cosa. Retomar donde
        # quedó vale más que empezar de nuevo: el orden de los correos es
        # estable, así que basta con saltear los ya tanteados.
        d = json.load(open(PREVIO, encoding="utf-8"))
        candidatos = d["candidatos"]
        saltear = d["revisados"]
        print(f"  retomando desde {PREVIO}: {saltear} ya tanteados, "
              f"{len(candidatos)} candidatos\n")

    conteo, t0 = Counter(), time.time()
    for n, c in enumerate(pendientes, 1):
        if n <= saltear:
            continue
        try:
            a = clasificar_una_vez(sistema, c, MOTOR)["categoria"]
            b = clasificar_una_vez(sistema, c, MOTOR)["categoria"]
        except Exception as e:
            a = b = "ERROR"
            print(f"  {n}: falló ({type(e).__name__})", flush=True)

        interesa = not (a == b == "RUIDO")
        conteo[f"{a}/{b}" if a != b else a] += 1
        if interesa:
            candidatos.append({**c, "tanteo": [a, b]})

        # guardado incremental: un barrido de una hora no puede perderse entero
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump({"fecha_utc": sello, "motor": MOTOR, "meses": MESES,
                       "revisados": n, "total": len(pendientes),
                       "candidatos": candidatos}, f, ensure_ascii=False, indent=2)

        if n % 10 == 0 or n == len(pendientes):
            transcurrido = time.time() - t0
            hechos = n - saltear
            faltan = (transcurrido / hechos) * (len(pendientes) - n)
            print(f"  {n}/{len(pendientes)}  candidatos: {len(candidatos)}  "
                  f"({transcurrido/60:.0f} min, faltan ~{faltan/60:.0f})", flush=True)

    print(f"\n=== qué encontró el tanteo ===")
    for k, v in conteo.most_common():
        print(f"  {k:<22} {v}")
    print(f"\n  lista corta: {len(candidatos)} de {len(pendientes)}")
    print(f"  guardada en: {ruta}")


if __name__ == "__main__":
    main()
