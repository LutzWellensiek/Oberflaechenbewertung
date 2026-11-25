"""Update application code in an extracted portable folder, keeping all user data."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def update(target, port=8765):
    target = Path(target).resolve()
    if target == ROOT or not (target / "portable.json").is_file() or not (target / "runtime/python.exe").is_file():
        raise ValueError("Bitte den vollständig ausgepackten portablen Programmordner angeben.")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as response:
            state = json.load(response)
        if Path(state.get("instance_root", "")).resolve() == target:
            raise ValueError("Die portable Anwendung läuft. Bitte zuerst im Startfenster beenden.")
    except (OSError, json.JSONDecodeError):
        pass
    files = [*ROOT.glob("app/*.py"), *ROOT.glob("web/*.*"),
             *[ROOT / name for name in ("run.py", "model_worker.py", "portable_bootstrap.py")]]
    changed = [p for p in files if not (target / p.relative_to(ROOT)).exists()
               or p.read_bytes() != (target / p.relative_to(ROOT)).read_bytes()]
    if not changed:
        return {"updated": 0, "folder": str(target)}
    backups = target / "data/backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / ("code-before-update-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".zip")
    with zipfile.ZipFile(backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in changed:
            old = target / source.relative_to(ROOT)
            if old.is_file():
                archive.write(old, source.relative_to(ROOT))
    for source in changed:
        destination = target / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    (target / "AKTUALISIERUNG.txt").write_text(
        "Anwendungscode aktualisiert: " + datetime.now().isoformat() +
        "\nMenschliche Bewertung, Bildübersicht, benannte Trainingsstände.\n"
        "Nutzerdaten und Modellgewichte wurden nicht ersetzt.\nCode-Sicherung: " + str(backup) +
        "\nBeim nächsten Start wird die Datenbank mit eigener Sicherung migriert.\n", encoding="utf-8")
    return {"updated": len(changed), "folder": str(target), "backup": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(json.dumps(update(args.folder, args.port), ensure_ascii=True, indent=2))
