"""Exercise the real native launcher, bundled runtime, weights, API and saved labels."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--port", type=int, default=8786)
    parser.add_argument("--models", nargs="+", default=["manual", "dinov2-small", "qwen3vl-2b"])
    args = parser.parse_args()
    root = args.folder.resolve()
    base = "http://127.0.0.1:" + str(args.port)
    token = ""

    def request(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-Surface-Token": token})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    def snapshot():
        with sqlite3.connect(root / "data/surface.sqlite3") as db:
            labels = db.execute("SELECT id,image_id,score,crop,captured_at,date_source,output_path,created_at FROM labels ORDER BY id").fetchall()
            files = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path, in db.execute("SELECT output_path FROM labels")}
        return labels, files

    environment = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME", "SURFACE_DATA_DIR")}
    environment["PATH"] = str(Path(os.environ["WINDIR"]) / "System32")
    subprocess.run([str(root / "Oberflaechenbewertung.exe"), "--headless", "--port", str(args.port)],
                   check=True, timeout=90, env=environment)
    state = request("/api/state")
    token = state["token"]
    assert state["portable"] and Path(state["instance_root"]) == root
    assert [model["id"] for model in state["models"]] == ["manual", "dinov2-small", "qwen3vl-2b"]
    before = snapshot()
    print("Native EXE gestartet; PATH enthält ausschließlich Windows/System32. Feste Bewertungen:", state["counts"]["rated"], flush=True)
    first = request("/api/images?status=rated")[0]
    image = request("/api/image?id=" + str(first["id"]))
    results = []
    try:
        for model in args.models:
            start = time.monotonic()
            task = request("/api/predict", {"image_id": image["id"], "model_id": model, "crop": json.loads(image["label"]["crop"])})
            last_message = None
            while time.monotonic() - start < 1200:
                job = request("/api/jobs/" + task["job"])
                if job["message"] != last_message:
                    print(model, job["status"], job["message"], flush=True)
                    last_message = job["message"]
                if job["status"] == "error":
                    raise RuntimeError(job["message"])
                if job["status"] == "done":
                    result = job["result"]
                    assert result["score_source"] == ("learned_texture" if model == "manual" else "learned_vision")
                    assert result["actual_score"] == image["label"]["score"]
                    assert result["learned"]["count"] < state["counts"]["rated"]
                    results.append({"model": model, "score": result["score"], "actual": result["actual_score"],
                                    "source": result["score_source"], "training_count": result["learned"]["count"],
                                    "seconds": round(time.monotonic() - start, 2)})
                    break
                time.sleep(1)
            else:
                raise TimeoutError(model)
        assert snapshot() == before, "Nutzerbewertungen oder JPEG-Exporte verändert."
        print(json.dumps({"passed": True, "root": str(root), "models": results,
                          "labels_unchanged": True, "exports_unchanged": True}, ensure_ascii=False), flush=True)
    finally:
        request("/api/shutdown", {})


if __name__ == "__main__":
    main()
