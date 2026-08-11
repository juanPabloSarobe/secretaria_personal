#!/usr/bin/env python3
"""
Archiva en INBOX.Ruido los correos que JP marcó como RUIDO en los simulacros.

Solo mueve lo que JP clasificó a mano. Las predicciones del clasificador no
cuentan: si él no lo miró, se queda donde está.

Nada se borra. Los correos se mueven a una carpeta y se pueden devolver
arrastrándolos desde cualquier cliente de correo.

Uso:  python3 archivar_ruido.py [--hacerlo]
      Sin --hacerlo solo muestra qué haría.
"""
import glob, imaplib, json, os, sys

CARPETA = os.environ.get("CARPETA_RUIDO", "INBOX.Ruido")
EJECUTAR = "--hacerlo" in sys.argv


def a_archivar():
    """(message_id, asunto, de) de todo lo que JP marcó como RUIDO."""
    vistos, salida = set(), []
    for ruta in sorted(glob.glob("datos/simulacro-*.json")):
        d = json.load(open(ruta, encoding="utf-8"))
        for c in d.get("casos", []):
            mid = (c.get("message_id") or "").strip()
            if c.get("correcto") == "RUIDO" and mid and mid not in vistos:
                vistos.add(mid)
                salida.append((mid, c["asunto"], c["de"]))
    return salida


def main():
    objetivos = a_archivar()
    print(f"Marcados como RUIDO por JP: {len(objetivos)}\n")
    for _, asunto, de in objetivos:
        print(f"  {de[:42]:<42} | {asunto[:52]}")
    if not objetivos:
        return

    M = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.environ["IMAP_PORT"]), timeout=40)
    M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])

    capacidades = M.capabilities
    tiene_move = "MOVE" in capacidades
    tiene_uidplus = "UIDPLUS" in capacidades
    print(f"\nServidor: MOVE={'sí' if tiene_move else 'no'}  "
          f"UIDPLUS={'sí' if tiene_uidplus else 'no'}")

    typ, data = M.list()
    carpetas = [l.decode(errors="replace").split(' "')[-1].strip('"') for l in data]
    if CARPETA not in carpetas:
        print(f"La carpeta {CARPETA} no existe.")
        if EJECUTAR:
            typ, r = M.create(CARPETA)
            print(f"  creada: {typ} {r}")
            M.subscribe(CARPETA)
        else:
            print("  (se crearía)")

    if not EJECUTAR:
        print(f"\nSIMULACRO: no se movió nada. Agregá --hacerlo para ejecutar.")
        M.logout()
        return

    M.select("INBOX")                                  # lectura y escritura
    movidos, no_encontrados, fallidos = 0, [], []
    for mid, asunto, _ in objetivos:
        # buscar por Message-ID: la posición no es un identificador estable
        typ, d = M.uid("SEARCH", None, 'HEADER', 'Message-ID', f'"{mid}"')
        uids = d[0].split() if d and d[0] else []
        if not uids:
            no_encontrados.append(asunto)
            continue
        uid = uids[0]
        if tiene_move:
            typ, r = M.uid("MOVE", uid, CARPETA)
        else:
            typ, r = M.uid("COPY", uid, CARPETA)
            if typ == "OK":
                M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                # UID EXPUNGE borra SOLO este mensaje; EXPUNGE a secas borraría
                # cualquier otro que ya estuviera marcado en la casilla
                if tiene_uidplus:
                    M.uid("EXPUNGE", uid)
        if typ == "OK":
            movidos += 1
        else:
            fallidos.append((asunto, r))

    if not tiene_move and not tiene_uidplus:
        print("\n  ATENCIÓN: sin UIDPLUS no se ejecutó EXPUNGE. Los correos están "
              "copiados en la carpeta y marcados como borrados en INBOX; "
              "desaparecen de la bandeja al compactarla desde tu cliente.")

    M.logout()
    print(f"\n  movidos a {CARPETA}: {movidos}")
    if no_encontrados:
        print(f"  no encontrados en INBOX ({len(no_encontrados)}): "
              + "; ".join(a[:40] for a in no_encontrados))
    if fallidos:
        print(f"  fallaron ({len(fallidos)}): {fallidos}")


if __name__ == "__main__":
    main()
