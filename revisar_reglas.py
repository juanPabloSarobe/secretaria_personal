#!/usr/bin/env python3
"""
Reevalúa un simulacro guardado con las reglas actuales.

No toca la casilla ni Telegram: lee el JSON del simulacro y vuelve a
clasificar cada caso contra reglas.md tal como está hoy. Sirve para
verificar que una regla nueva arregla lo que debía arreglar sin romper
lo que ya andaba.

Corre cada caso varias veces, porque una sola pasada no es concluyente
ni siquiera con temperature 0.

Uso:  python3 revisar_reglas.py [archivo.json] [pasadas] [--motor groq|nvidia|ollama]

Conviene correrlo contra un motor que NO sea el de producción: revisar N casos
por M pasadas consume mucha más cuota que la operación normal de un día entero,
y agotar el motor principal deja a la secretaria muda.
"""
import glob, json, sys
from collections import Counter
import os.path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clasificador import clasificar_una_vez, motores, prompt_sistema  # noqa: E402


def main():
    args = sys.argv[1:]
    motor = None
    if "--motor" in args:
        i = args.index("--motor")
        motor = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]

    ruta = args[0] if args else sorted(glob.glob("datos/simulacro-*.json"))[-1]
    pasadas = int(args[1]) if len(args) > 1 else 3

    d = json.load(open(ruta, encoding="utf-8"))
    sistema = prompt_sistema()
    cadena = motores(motor)

    print(f"archivo : {ruta}")
    print("motores : " + " → ".join(f"{n} ({m})" for n, _, _, m in cadena))
    print(f"pasadas : {pasadas} por caso\n")
    print(f"{'#':<3} {'JP dijo':<9} {'antes':<9} {'ahora':<9} {'estable':<8} resultado")
    print("-" * 72)

    antes_ok = ahora_ok = 0
    arreglados, rotos, inestables = [], [], []

    for i, c in enumerate(d["casos"], 1):
        # fecha y adjuntos incluidos: si el prompt de producción los ve y este no,
        # la regresión mide un sistema que no es el que corre
        correo = {k: c.get(k) for k in
                  ("de", "para", "cc", "asunto", "cuerpo", "fecha", "adjuntos")}
        votos = Counter()
        for _ in range(pasadas):
            try:
                votos[clasificar_una_vez(sistema, correo, motor)["categoria"]] += 1
            except Exception as e:                  # un fallo aislado no tira la corrida
                print(f"{i:<3} error en una pasada: {type(e).__name__}")
        if not votos:
            print(f"{i:<3} {c['correcto']:<9} {'—':<9} {'SIN DATO':<9} {'0/' + str(pasadas):<8} ⚠️  todas las pasadas fallaron")
            continue
        ahora, n_ahora = votos.most_common(1)[0]
        estable = n_ahora == pasadas

        esperado = c["correcto"]
        antes = c["prediccion"]["categoria"]
        ok_antes, ok_ahora = antes == esperado, ahora == esperado
        antes_ok += ok_antes
        ahora_ok += ok_ahora

        if not ok_antes and ok_ahora:
            estado, arreglados = "✅ APRENDIÓ", arreglados + [i]
        elif ok_antes and not ok_ahora:
            estado, rotos = "🔴 SE ROMPIÓ", rotos + [i]
        elif ok_ahora:
            estado = "   ok"
        else:
            estado = "   sigue mal"
        if not estable:
            inestables.append(i)

        print(f"{i:<3} {esperado:<9} {antes:<9} {ahora:<9} "
              f"{(str(n_ahora) + '/' + str(pasadas)):<8} {estado}")

    n = len(d["casos"])
    print("-" * 72)
    print(f"antes: {antes_ok}/{n}    ahora: {ahora_ok}/{n}")
    if arreglados:
        print(f"  aprendió en los casos: {arreglados}")
    if rotos:
        print(f"  REGRESIONES en los casos: {rotos}  <-- revisar las reglas nuevas")
    if inestables:
        print(f"  respuesta inestable entre pasadas: {inestables}")


if __name__ == "__main__":
    main()
