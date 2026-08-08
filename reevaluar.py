#!/usr/bin/env python3
"""
Reevalúa un simulacro guardado con las reglas actuales.

No toca la casilla ni Telegram: lee el JSON del simulacro y vuelve a
clasificar cada caso contra reglas.md tal como está hoy. Sirve para
verificar que una regla nueva arregla lo que debía arreglar sin romper
lo que ya andaba.

Corre cada caso varias veces, porque una sola pasada no es concluyente
ni siquiera con temperature 0.

Uso:  python3 reevaluar.py [archivo.json] [pasadas]
"""
import glob, json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simulacro import clasificar, prompt_sistema  # noqa: E402

ruta = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob("datos/simulacro-*.json"))[-1]
PASADAS = int(sys.argv[2]) if len(sys.argv) > 2 else 3

d = json.load(open(ruta, encoding="utf-8"))
sistema = prompt_sistema()

print(f"archivo : {ruta}")
print(f"modelo  : {os.environ['LLM_CLASIFICADOR_MODEL']}")
print(f"pasadas : {PASADAS} por caso\n")
print(f"{'#':<3} {'JP dijo':<9} {'antes':<9} {'ahora':<9} {'estable':<8} resultado")
print("-" * 72)

antes_ok = ahora_ok = 0
arreglados, rotos, inestables = [], [], []

for i, c in enumerate(d["casos"], 1):
    correo = {k: c[k] for k in ("de", "para", "cc", "asunto", "cuerpo")}
    votos = Counter(clasificar(sistema, correo)["categoria"] for _ in range(PASADAS))
    ahora, n_ahora = votos.most_common(1)[0]
    estable = n_ahora == PASADAS

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
          f"{(str(n_ahora) + '/' + str(PASADAS)):<8} {estado}")

n = len(d["casos"])
print("-" * 72)
print(f"antes: {antes_ok}/{n}    ahora: {ahora_ok}/{n}")
if arreglados:
    print(f"  aprendió en los casos: {arreglados}")
if rotos:
    print(f"  REGRESIONES en los casos: {rotos}  <-- revisar las reglas nuevas")
if inestables:
    print(f"  respuesta inestable entre pasadas: {inestables}")
