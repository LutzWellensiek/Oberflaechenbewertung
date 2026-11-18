"""Application-local runtime configuration; never installs anything on the host."""
import ctypes
from functools import lru_cache
import json
import os
from pathlib import Path


def is_portable(root):
    return (Path(root) / "portable.json").is_file()


def interpreter(root, model_id):
    root = Path(root)
    if is_portable(root):
        return root / "runtime" / "python.exe"
    return root / "environments" / model_id / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@lru_cache(maxsize=1)
def nvidia_available():
    """Probe the installed driver without importing PyTorch into the web server."""
    if os.name != "nt":
        return False
    try:
        cuda = ctypes.WinDLL("nvcuda.dll")
        count = ctypes.c_int()
        return cuda.cuInit(0) == 0 and cuda.cuDeviceGetCount(ctypes.byref(count)) == 0 and count.value > 0
    except (OSError, AttributeError):
        return False


def model_configuration(root, model_id):
    marker = Path(root) / "environments" / model_id / "ready.json"
    config = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    if is_portable(root) and config.get("device") == "cuda" and not nvidia_available():
        config.update(device="cpu", quantization=None,
                      runtime_note="Keine nutzbare NVIDIA-Grafikkarte mit Treiber erkannt. Qwen verwendet die CPU und benötigt etwa 10–14 GB freien RAM.")
    return config


def worker_command(root, model_id, vision_only=False):
    root = Path(root)
    if is_portable(root):
        return [str(interpreter(root, model_id)), "-I", "-S", "-X", "utf8",
                "-u", str(root / "portable_bootstrap.py"), "--model", model_id] + (["--vision-only"] if vision_only else [])
    return [str(interpreter(root, model_id)), "-u", str(root / "model_worker.py"), model_id]


def relocate_database(db, old_root, new_root):
    """Move only application-owned paths. Original source/date/score metadata stays intact."""
    old_root, new_root = Path(old_root), Path(new_root)

    def moved(value):
        path = Path(value)
        if not path.is_absolute():
            return str(new_root / path)
        try:
            return str(new_root / path.relative_to(old_root))
        except ValueError:
            return value  # A deliberately external export directory stays external.

    for table, column in (("images", "stored_path"), ("labels", "output_path")):
        for row_id, value in db.execute(f"SELECT id,{column} FROM {table}").fetchall():
            db.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (moved(value), row_id))
    row = db.execute("SELECT value FROM settings WHERE key='output'").fetchone()
    if row:
        db.execute("UPDATE settings SET value=? WHERE key='output'", (json.dumps(moved(json.loads(row[0])), ensure_ascii=False),))
    db.execute("INSERT OR REPLACE INTO settings VALUES ('portable_root',?)", (json.dumps(str(new_root), ensure_ascii=False),))


def relocate_if_needed(root, db):
    if not is_portable(root):
        return
    row = db.execute("SELECT value FROM settings WHERE key='portable_root'").fetchone()
    old_root = json.loads(row[0]) if row else str(root)
    if str(Path(old_root)) != str(Path(root)) or not row:
        relocate_database(db, old_root, root)
