"""Run with the bundled interpreter using -I -S, without system/user Python packages."""
import json
import os
from pathlib import Path
import runpy
import sys
import traceback

ROOT = Path(__file__).resolve().parent


def main():
    os.chdir(ROOT)
    os.environ.pop("SURFACE_DATA_DIR", None)
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("PYTHONHOME", None)
    os.environ.update(PYTHONNOUSERSITE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", PYTHONDONTWRITEBYTECODE="1")
    args = sys.argv[1:]
    model_id = args[1] if args[:1] == ["--model"] else "dinov2-small"
    if model_id not in ("dinov2-small", "qwen3vl-2b"):
        raise ValueError("Unbekanntes Modell in der portablen Version.")
    sys.path[:0] = [str(ROOT), str(ROOT / "runtime" / "packages")]
    # Native extension dependencies can be found next to the bundled interpreter.
    dll_directory = os.add_dll_directory(str(ROOT / "runtime")) if os.name == "nt" else None
    if args[:1] == ["--model"]:
        sys.argv = [str(ROOT / "model_worker.py"), model_id, *args[2:]]
        runpy.run_path(sys.argv[0], run_name="__main__")
    elif args[:1] == ["--diagnose"]:
        import numpy
        import PIL
        from app.catalog import MODELS
        print(json.dumps({"python": sys.executable, "prefix": sys.prefix, "models": list(MODELS),
                          "numpy": numpy.__file__, "pillow": PIL.__file__, "sys_path": sys.path}))
    else:
        logs = ROOT / "logs"
        logs.mkdir(exist_ok=True)
        with (logs / "server.log").open("a", encoding="utf-8", buffering=1) as out, \
                (logs / "server-error.log").open("a", encoding="utf-8", buffering=1) as err:
            sys.stdout, sys.stderr = out, err
            try:
                sys.argv = [str(ROOT / "run.py"), *args]
                runpy.run_path(sys.argv[0], run_name="__main__")
            except BaseException:
                traceback.print_exc()
                raise
    if dll_directory:
        dll_directory.close()


if __name__ == "__main__":
    main()
