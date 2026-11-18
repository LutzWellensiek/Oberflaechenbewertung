"""Bootstrap without administrator rights; all writes stay in this project."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def python_in(directory):
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, cwd=str(ROOT), **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-local-packages", action="store_true", help="Use existing Pillow/NumPy without network")
    args = parser.parse_args()
    if sys.version_info < (3, 10) or sys.version_info >= (3, 14):
        raise SystemExit("Bitte Python 3.10–3.13 (64 Bit), empfohlen 3.12, installieren.")
    target = ROOT / ".venv"
    if not python_in(target).exists():
        venv.EnvBuilder(with_pip=True, system_site_packages=args.use_local_packages).create(target)
    python = python_in(target)
    if args.use_local_packages:
        run([python, "-c", "import PIL,numpy; print('Lokale Bild-/Lernbibliotheken verfügbar')"])
    else:
        run([python, "-m", "pip", "install", "-r", ROOT / "requirements.txt"])
    from app.catalog import MODELS
    for model_id in MODELS:
        if model_id == "manual":
            continue
        directory = ROOT / "environments" / model_id
        if not python_in(directory).exists():
            print("Erstelle isolierte Umgebung:", model_id, flush=True)
            venv.EnvBuilder(with_pip=True).create(directory)
    # Launchers use this local path, never change PATH or the user's Python installation.
    (ROOT / ".venv" / "bootstrap.json").write_text(json.dumps({"python": sys.executable}), encoding="utf-8")
    print("Bereit. Starten mit Start.bat. Modellpakete/Gewichte werden in der Anwendung einzeln installiert.")


if __name__ == "__main__":
    main()
