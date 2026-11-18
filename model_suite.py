"""Normal local launcher: install selected profiles and test the fixed saved dataset."""
import argparse
import json
import os
from pathlib import Path
import shutil
import urllib.request
from datetime import datetime
from app.catalog import MODELS
from app.engine import ENGINE
from app.benchmark import run_benchmark
from app.diagnostics import record_error
from app.storage import DATA, initialize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--models", nargs="*", choices=list(MODELS), default=list(MODELS))
    args = parser.parse_args()
    initialize()
    output = DATA / "benchmarks"
    output.mkdir(parents=True, exist_ok=True)
    attempts = []
    network_error = None
    if args.install:
        try:
            urllib.request.urlopen("https://download.pytorch.org/whl/cpu/torch/", timeout=12).close()
            urllib.request.urlopen("https://huggingface.co/api/models/facebook/dinov2-small", timeout=12).close()
        except OSError as exc:
            network_error = str(exc)
            print("Downloadzugriff blockiert:", network_error, flush=True)
        order = [m for m in ("dinov2-small", "qwen3vl-2b") if m in args.models]
        for model_id in order:
            if ENGINE.status(model_id)["installed"]:
                attempts.append({"model_id": model_id, "status": "already_installed"})
                continue
            if network_error:
                attempts.append({"model_id": model_id, "status": "blocked_network", "message": network_error})
                continue
            required = {"dinov2-small": 2, "qwen3vl-2b": 7}[model_id]
            if shutil.disk_usage(DATA).free / 1024**3 < required:
                attempts.append({"model_id": model_id, "status": "disk_space", "message": f"Mindestens {required} GB freier Speicher für Installation benötigt."})
                continue
            try:
                print("\nInstalliere", MODELS[model_id]["name"], flush=True)
                ENGINE.install(model_id, "cpu", lambda message: print(message, flush=True))
                attempts.append({"model_id": model_id, "status": "installed"})
            except Exception as exc:
                print(str(exc), flush=True)
                record_error("suite-install:" + model_id, str(exc))
                attempts.append({"model_id": model_id, "status": "error", "message": str(exc)})
        (output / "installation-results.json").write_text(json.dumps({"created_at": datetime.now().isoformat(), "models": attempts}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = run_benchmark(ENGINE, args.models, lambda message: print(message, flush=True))
    print("\nErgebnisse:", output, flush=True)
    for model in report["models"]:
        print(model["name"], model["status"], "CV:", model.get("cv"), "Rohmodell:", model.get("raw"), flush=True)
        if model.get("message"):
            print("  " + model["message"], flush=True)
    if any(model["status"] != "done" for model in report["models"]):
        print("Nicht alle Modelle konnten getestet werden. Gründe und Teilresultate bleiben im Vergleich gespeichert.", flush=True)
    print("Nutzerbewertungen unverändert:", report["labels_unchanged"], "Bildexporte unverändert:", report["exports_unchanged"], flush=True)
    ENGINE.stop()


if __name__ == "__main__":
    main()
