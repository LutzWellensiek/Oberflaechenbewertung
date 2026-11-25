"""Build offline from the already installed, pinned CPU environment and weights."""
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("manual", "dinov2-small", "qwen3vl-2b")


def copy_tree(source, target, exclude=()):
    for parent, directories, files in os.walk(source):
        relative = Path(parent).relative_to(source)
        directories[:] = [name for name in directories if name not in {"__pycache__", ".cache", "tests", *exclude}
                          and not (relative == Path("torch") and name == "include")]
        for name in files:
            if Path(name).suffix in (".pyc", ".pyo", ".lib", ".a", ".obj"):
                continue
            old, new = Path(parent) / name, target / relative / name
            new.parent.mkdir(parents=True, exist_ok=True)
            if not new.exists() or (new.stat().st_size, new.stat().st_mtime_ns) != (old.stat().st_size, old.stat().st_mtime_ns):
                shutil.copy2(old, new)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def snapshot(target):
    data = target / "data"
    data.mkdir(exist_ok=True)
    path = data / "surface.sqlite3"
    path.unlink(missing_ok=True)  # Only the explicitly marked build staging is writable here.
    with closing(sqlite3.connect(ROOT / "data" / "surface.sqlite3")) as source, closing(sqlite3.connect(path)) as db:
        source.backup(db)
        db.row_factory = sqlite3.Row
        files = []
        for table, column, folder in (("images", "stored_path", "originals"), ("labels", "output_path", "output")):
            for row in db.execute(f"SELECT id,{column} FROM {table}").fetchall():
                old = Path(row[column])
                if not old.is_absolute():
                    old = ROOT / old
                try:
                    relative = old.relative_to(ROOT / "data")
                    if relative.parts[0] not in ("originals", "output", "history"):
                        raise ValueError("External or unmanaged export")
                    new = data / relative
                except ValueError:
                    new = data / folder / old.name
                if new.exists() and digest(new) != digest(old):
                    new = new.with_name(new.stem + "_" + str(row["id"]) + new.suffix)
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, new)
                checksum = digest(new)
                assert checksum == digest(old)
                db.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (str(new), row["id"]))
                files.append({"table": table, "id": row["id"], "path": str(new.relative_to(target)), "sha256": checksum})
        labels = [dict(row) for row in db.execute("SELECT id,image_id,score,crop,captured_at,date_source,created_at FROM labels ORDER BY id")]
        original_labels = [dict(zip(("id", "image_id", "score", "crop", "captured_at", "date_source", "created_at"), row))
                           for row in source.execute("SELECT id,image_id,score,crop,captured_at,date_source,created_at FROM labels ORDER BY id")]
        assert labels == original_labels, "Nutzerbewertungen wurden beim Verpacken verändert."
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "model_runs" in tables:
            image_paths = dict(db.execute("SELECT id,stored_path FROM images"))
            for run_id, text in db.execute("SELECT id,recipe FROM model_runs").fetchall():
                recipe = json.loads(text)
                for sample in recipe.get("samples", []):
                    if sample["id"] in image_paths:
                        sample["stored_path"] = image_paths[sample["id"]]
                    if sample.get("output_path"):
                        old = Path(sample["output_path"])
                        try:
                            sample["output_path"] = str(data / old.relative_to(ROOT / "data"))
                        except ValueError:
                            pass
                db.execute("UPDATE model_runs SET recipe=? WHERE id=?", (json.dumps(recipe, ensure_ascii=False), run_id))
        if "label_history" in tables:
            for history_id, text in db.execute("SELECT id,snapshot FROM label_history").fetchall():
                previous = json.loads(text)
                if previous.get("output_path"):
                    try:
                        previous["output_path"] = str(data / Path(previous["output_path"]).relative_to(ROOT / "data"))
                    except ValueError:
                        pass
                    db.execute("UPDATE label_history SET snapshot=? WHERE id=?", (json.dumps(previous, ensure_ascii=False), history_id))
        db.execute("INSERT OR REPLACE INTO settings VALUES ('output',?)", (json.dumps(str(data / "output")),))
        db.execute("INSERT OR REPLACE INTO settings VALUES ('portable_root',?)", (json.dumps(str(target)),))
        current = db.execute("SELECT value FROM settings WHERE key='model_id'").fetchone()
        if not current or json.loads(current[0]) not in MODELS:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('model_id','\"manual\"')")
        db.commit()
    for folder in ("profiles", "history"):
        source = ROOT / "data" / folder
        if source.exists():
            copy_tree(source, data / folder)
    if (ROOT / "data" / "benchmarks").exists():
        copy_tree(ROOT / "data" / "benchmarks", data / "benchmarks")
    (data / "portable-snapshot.json").write_text(json.dumps({"created_at": datetime.now().isoformat(), "labels": labels, "files": files}, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(labels)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--archive-only", action="store_true", help="Package the already validated staging without replacing its CPU learning state")
    args = parser.parse_args()
    target = ROOT / "build" / "portable-cpu"
    if target.exists() and not (target / ".build-staging").exists():
        raise SystemExit("Buildziel ist kein markierter Buildordner.")
    if args.archive_only:
        package(target)
        return
    target.mkdir(parents=True, exist_ok=True)
    (target / ".build-staging").touch()
    config = {"build_id": datetime.now().strftime("%Y%m%d-%H%M%S"), "cpu_only": True,
              "models": list(MODELS), "python": sys.version, "installs_allowed": False}
    (target / "portable.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print("Kopiere eigenständige Python-Laufzeit …", flush=True)
    runtime = target / "runtime"
    runtime.mkdir(exist_ok=True)
    base = Path(sys.base_prefix)
    for file in base.iterdir():
        if file.is_file() and (file.suffix in (".exe", ".dll") or file.name == "LICENSE.txt"):
            shutil.copy2(file, runtime / file.name)
    copy_tree(base / "Lib", runtime / "Lib", exclude=("site-packages", "ensurepip", "idlelib", "tkinter", "turtledemo"))
    copy_tree(base / "DLLs", runtime / "DLLs")
    # App-local VC runtime dependencies; no vcredist installer is needed.
    for name in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll", "concrt140.dll"):
        source = Path(os.environ["WINDIR"]) / "System32" / name
        if source.exists():
            shutil.copy2(source, runtime / name)
    print("Kopiere CPU-Bibliotheken (keine CUDA-Pakete) …", flush=True)
    cpu = ROOT / "environments" / "dinov2-small" / "Lib" / "site-packages"
    copy_tree(cpu, runtime / "packages", exclude=("pip", "setuptools", "_distutils_hack", "pkg_resources"))
    for model in MODELS[1:]:
        print("Kopiere Gewichte:", model, flush=True)
        source = ROOT / "environments" / model
        profile = target / "environments" / model
        copy_tree(source / "weights", profile / "weights")
        ready = json.loads((source / "ready.json").read_text(encoding="utf-8"))
        ready.update(device="cpu", quantization=None)
        if model == "qwen3vl-2b":
            ready.update(dtype="bfloat16", cpu_vision_only=True,
                         runtime_note="CPU-Version: Qwen-Bildmerkmale mit deinem angelernten Bewertungsmodell. Die zusätzliche Sprachgenerierung wird im normalen Ablauf ausgelassen; dadurch sinkt der RAM-Bedarf.")
        (profile / "ready.json").write_text(json.dumps(ready, indent=2), encoding="utf-8")
        shutil.copy2(ROOT / "environments" / "dinov2-small" / "requirements-resolved.txt", profile / "requirements-resolved.txt")
    for folder in ("app", "web"):
        copy_tree(ROOT / folder, target / folder)
    for name in ("run.py", "model_worker.py", "portable_bootstrap.py"):
        shutil.copy2(ROOT / name, target / name)
    print("Sichere aktuellen Datensatz …", flush=True)
    count = snapshot(target)
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    exe = target / "Oberflaechenbewertung.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+", "/utf8output",
                    "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", "/r:System.Web.Extensions.dll",
                    "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll",
                    "/win32manifest:" + str(ROOT / "scripts" / "portable.manifest"), "/out:" + str(exe),
                    str(ROOT / "scripts" / "PortableLauncher.cs")], check=True)
    readme = f"""Oberflächenbewertung – portable CPU-Version

Windows 10/11, 64 Bit. Kein installiertes Python, kein Setup und keine Administratorrechte nötig.

START: Oberflaechenbewertung.exe doppelklicken. Die Oberfläche öffnet sich im vorhandenen Browser.
BEENDEN: Im Startfenster auf 'Anwendung beenden' klicken. Der Browser allein beendet den Server nicht.

Enthalten: Basislernmodell, DINOv2 Small, Qwen3-VL 2B. Beide Vision-Modelle sind vollständig lokal enthalten.
Qwen verwendet seinen vortrainierten Bildencoder auf der CPU, keine Grafikkarte. Dafür sollten etwa 2,5 GB RAM frei sein.
Das angelernte Qualitätsmodell wertet dessen eigene Bildmerkmale aus. Die zusätzliche Sprachgenerierung wird ausgelassen.
Die vollständigen Sprachmodellgewichte sind enthalten; optionale Rohbewertungen benötigen etwa 6 GB freien RAM und können deutlich länger dauern.
DINOv2 ist für diesen Rechner der sparsamere Einstieg.
Es sind {count} feste Nutzerbewertungen samt Originalkopien, Ausschnitten und Lernständen enthalten.
Benannte Lernstände und die Bewertungshistorie werden mitkopiert; deine Bewertungen bleiben gleich.

DATEN: 'data' enthält die Datenbank, Originalkopien, Ausschnitte und Lernstände. Diesen Ordner sichern.
KOPIEREN: Den ganzen Programmordner kopieren. Nur die kleine Start-EXE reicht bei der Ordnerfassung nicht.
Der Ordner muss beschreibbar sein. Pfade werden beim Start automatisch an den neuen Ort angepasst.
Modelle müssen weder installiert noch heruntergeladen werden. Installationsknöpfe sind deaktiviert.
Bei Problemen: logs/server-error.log, environments/<Modell>/inference.log und Startfehler.txt ansehen.

Die große Paket-EXE packt diesen Ordner beim ersten Start neben sich aus. Das ist keine Systeminstallation.
Weitere Starts verwenden den ausgepackten Ordner; vorhandene Nutzerdaten werden nicht überschrieben.
Es werden keine Einträge in Registry, Autostart oder System-PATH angelegt.
Das Programm benötigt den in Windows enthaltenen .NET Framework 4.x für das Startfenster.
Falls die Firmenrichtlinie eigene EXE-Dateien sperrt, ist eine Freigabe durch die IT erforderlich.

Die großen Modellgewichte bleiben eingefroren. Eigene Lernköpfe lernen deine Qualitätskriterien.
Bibliothekslizenzen liegen bei den Paketen unter runtime/packages/*.dist-info; Python unter runtime/LICENSE.txt.
Modelle: https://huggingface.co/facebook/dinov2-small und https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct
"""
    (target / "BITTE_LESEN.txt").write_text(readme, encoding="utf-8-sig")
    print("Portable Ordnerfassung bereit:", target, "Bewertungen:", count, flush=True)
    if args.stage_only:
        return
    package(target)


def package(target):
    exe = target / "Oberflaechenbewertung.exe"
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / "Oberflaechenbewertung-CPU.zip"
    print("Komprimiere Programmpaket …", flush=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as zipped:
        for file in sorted(target.rglob("*")):
            if file.is_file() and file.name not in (".build-staging", ".package-complete", ".unpack-lock", "inference.log") and not file.name.endswith(("-wal", "-shm")) and "__pycache__" not in file.parts and "logs" not in file.relative_to(target).parts and "cache" not in file.relative_to(target).parts:
                zipped.write(file, file.relative_to(target).as_posix())
    size = archive.stat().st_size
    print("Archivgröße:", round(size / 1024**3, 3), "GiB", flush=True)
    if size + exe.stat().st_size + 16 < 3.9 * 1024**3:
        single = output / "Oberflaechenbewertung-CPU.exe"
        shutil.copyfile(exe, single)
        with single.open("ab") as stream, archive.open("rb") as source:
            offset = stream.tell()
            shutil.copyfileobj(source, stream, 1024 * 1024)
            stream.write(b"SFLAB01!" + struct.pack("<q", offset))
        checksum = digest(single)
        (output / (single.name + ".sha256")).write_text(checksum + "  " + single.name + "\n", encoding="ascii")
        shutil.copy2(target / "BITTE_LESEN.txt", output / "BITTE_LESEN.txt")
        print("Einzelne Paket-EXE bereit:", single, flush=True)
        archive.unlink()  # The completed EXE contains exactly the same archive.
    else:
        print("Paket ist für eine zuverlässige einzelne Windows-EXE zu groß; ZIP mit Start-EXE verwenden.", flush=True)


if __name__ == "__main__":
    main()
