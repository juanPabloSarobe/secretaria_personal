#!/usr/bin/env python3
"""Rompe el código a propósito, uno por uno, y mira si la suite se entera.

Un test que pasa no dice nada por sí solo: dice algo cuando FALLA al
romper lo que dice cuidar. Las tres fallas del despacho final vivían
justo donde no había ningún test que pudiera fallar -y dos de ellas
tenían la mutación exacta anotada en la revisión: "token fijo" y
"readonly a False" sobrevivían la suite entera.

Cada mutación de acá abajo es una de esas: se aplica al archivo, se corre
la suite, y se restaura el archivo pase lo que pase. Lo que se espera de
cada una es que la suite se caiga.

    set -a; . ./.env; set +a; python3 mutaciones.py
"""
import io
import subprocess
import sys

# (archivo, qué se rompe, texto original, texto mutado)
MUTACIONES = [
    ("correo.py", "abrir_buzon abre en escritura por defecto",
     "def abrir_buzon(readonly=True):",
     "def abrir_buzon(readonly=False):"),

    ("secretaria.py", "el token de tanda vuelve a ser corto",
     "            tanda = secrets.token_hex(4)",
     "            tanda = secrets.token_hex(2)"),

    ("secretaria.py", "el token de tanda se asigna sin chequear colisión",
     "            if tanda not in self.abiertos:\n                return tanda",
     "            if True:\n                return tanda"),

    ("secretaria.py", "CRÍTICO 1: el resumen general consume lo archivado",
     '        consumibles = {c["message_id"] for c in clasificados}',
     '        consumibles = {c["message_id"] for c in correos}'),

    ("secretaria.py", "CRÍTICO 1b: el resumen de ruido se colapsa contra el general",
     '            if "ruido" in pendientes:\n                self.mandar_resumen("ruido")',
     '            if "ruido" in pendientes and False:\n                self.mandar_resumen("ruido")'),

    ("secretaria.py", "CRÍTICO 2: el aviso se da por entregado sin mirar",
     '            memoria.cambiar(self.cx, c["message_id"], "pendiente_de_avisar")\n'
     '            return False\n'
     '        memoria.cambiar(self.cx, c["message_id"], "avisado")',
     '            memoria.cambiar(self.cx, c["message_id"], "avisado")\n'
     '            return False\n'
     '        memoria.cambiar(self.cx, c["message_id"], "avisado")'),

    ("secretaria.py", "CRÍTICO 3: el reloj arranca en ahora y no en el latido",
     "        self.ultimo_reloj = self._retomar_el_reloj()",
     "        self.ultimo_reloj = datetime.now()"),

    ("secretaria.py", "el resumen no dice que estuvo caída",
     "        if not self.caida_desde:\n            return \"\"",
     "        if True:\n            return \"\""),

    ("secretaria.py", "SystemExit vuelve a escaparse de revisar_casilla",
     "            except (Exception, SystemExit) as e:\n"
     "                # Ningún motor respondió.",
     "            except Exception as e:\n"
     "                # Ningún motor respondió."),

    ("secretaria.py", "SystemExit vuelve a escaparse del ciclo de correo",
     '            except (Exception, SystemExit) as e:\n'
     '                # Nada que pase acá adentro',
     '            except Exception as e:\n'
     '                # Nada que pase acá adentro'),

    ("secretaria.py", "el aviso de CorreoPerdido vuelve a decir que está duplicado",
     '        if str(falla).startswith("CorreoPerdido"):',
     "        if False:"),
]


def correr():
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                       capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip().splitlines()[-1]


def main():
    print("Sin mutar:", end=" ", flush=True)
    ok, resumen = correr()
    print(resumen)
    if not ok:
        print("La suite ya falla sin mutar. No tiene sentido seguir.")
        return 1

    sobrevivieron = []
    for archivo, que, viejo, nuevo in MUTACIONES:
        original = io.open(archivo, encoding="utf-8").read()
        if original.count(viejo) != 1:
            print(f"  ⚠️  {que}: el texto a mutar no está una sola vez"
                  f" ({original.count(viejo)}). Mutación sin aplicar.")
            sobrevivieron.append(que)
            continue
        try:
            io.open(archivo, "w", encoding="utf-8").write(
                original.replace(viejo, nuevo, 1))
            ok, resumen = correr()
        finally:
            io.open(archivo, "w", encoding="utf-8").write(original)
        marca = "SOBREVIVIÓ" if ok else "la caza"
        print(f"  [{marca:>10}] {que} — {resumen}")
        if ok:
            sobrevivieron.append(que)

    print()
    if sobrevivieron:
        print(f"{len(sobrevivieron)} mutaciones sin red:")
        for q in sobrevivieron:
            print(f"  · {q}")
        return 1
    print(f"Las {len(MUTACIONES)} mutaciones se cazan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
