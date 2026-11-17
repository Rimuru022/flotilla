"""Línea de comandos.

    uv run python -m flotilla.cli generar [--corte AAAA-MM-DD] [--semilla N]
    uv run python -m flotilla.cli resumen
    uv run python -m flotilla.cli preguntar "¿Qué unidades pierden dinero?"
"""
import argparse
import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from flotilla import analitica, consultas, db, seed


def generar_base(destino=None, corte=None, semilla=2026):
    """Construye la base en un archivo temporal y la instala con la API de
    respaldo de SQLite: quien esté leyendo la base vieja nunca ve una a medias."""
    destino = Path(destino or db.db_path())
    destino.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        nueva = sqlite3.connect(Path(tmp) / "nueva.db")
        nueva.execute("PRAGMA synchronous=OFF")
        info = seed.generar(nueva, corte, semilla)
        viva = sqlite3.connect(destino)
        viva.execute("PRAGMA journal_mode=WAL")
        nueva.backup(viva)
        viva.close()
        nueva.close()
    return info


def main(argv=None):
    p = argparse.ArgumentParser(prog="flotilla")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generar", help="Genera la base de demostración")
    g.add_argument("--corte", type=date.fromisoformat, default=None)
    g.add_argument("--semilla", type=int, default=2026)
    sub.add_parser("resumen", help="Imprime los indicadores generales")
    q = sub.add_parser("preguntar", help="Pregunta al asistente (requiere OPENROUTER_API_KEY)")
    q.add_argument("pregunta")
    a = p.parse_args(argv)

    if a.cmd == "generar":
        info = generar_base(corte=a.corte, semilla=a.semilla)
        print(f"Base generada en {db.db_path()}: {info}")
    elif a.cmd == "resumen":
        print(json.dumps(analitica.resumen(consultas.conexion_lectura()), indent=2, ensure_ascii=False))
    elif a.cmd == "preguntar":
        from flotilla import asistente
        try:
            r = asistente.preguntar(consultas.conexion_lectura(), a.pregunta)
        except asistente.AsistenteNoDisponible as e:
            raise SystemExit(str(e))
        except Exception as e:  # error del proveedor tras los reintentos
            raise SystemExit(f"El modelo no respondió ({type(e).__name__}). Intenta de nuevo en un momento.")
        for paso in r["pasos"]:
            print(f"  > {paso['herramienta']} {json.dumps(paso['argumentos'], ensure_ascii=False)}"
                  + (f"  [error: {paso['error']}]" if paso["error"] else ""))
        print(f"\n{r['respuesta']}\n\n({r['modelo']}, {r['segundos']} s)")


if __name__ == "__main__":
    main()
