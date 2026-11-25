"""Local model comparisons on fixed human labels, without training/test leakage."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import time
from datetime import datetime

import numpy as np
from .catalog import DEFAULT_CRITERIA, MODELS
from .diagnostics import record_error
from .learning import coarse_class, fit_parameters, predict_parameters, weighted_error
from .storage import DATA, connection, label_dataset, setting


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metrics(rows, key):
    pairs = [(r["actual"], r[key]) for r in rows if r.get(key) is not None]
    if not pairs:
        return {"n": 0, "mae": None, "stage_accuracy": None, "weighted_error": None}
    return {"n": len(pairs), "mae": round(float(np.mean([abs(a-p) for a,p in pairs])), 3),
            "stage_accuracy": round(float(np.mean([coarse_class(a)==coarse_class(p) for a,p in pairs])), 4),
            "weighted_error": round(float(np.mean([weighted_error(a,p) for a,p in pairs])), 4)}


def cross_validate(vectors, scores, groups):
    """Leave one original-image group out; test label never enters scaling or training."""
    results = []
    for index, group in enumerate(groups):
        train = [j for j, other in enumerate(groups) if other != group]
        if not train:
            results.append({"cv_score": None, "median_score": None, "train_count": 0})
            continue
        parameters = fit_parameters([vectors[j] for j in train], [scores[j] for j in train])
        predicted = predict_parameters(parameters, vectors[index])
        results.append({"cv_score": predicted["score"], "train_count": len(train),
                        "median_score": int(round(float(np.median([scores[j] for j in train]))))})
    return results


def latest_benchmark():
    path = DATA / "benchmarks" / "latest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def run_benchmark(engine, model_ids, progress=lambda text: None, include_raw=False):
    for model_id in model_ids:
        engine.profile(model_id)
    with connection() as db:
        labels = [dict(r) for r in db.execute('''SELECT l.*,i.stored_path,i.width,i.height,i.origin_folder,i.relative_name FROM labels l
                    JOIN images i ON i.id=l.image_id WHERE i.status='rated' AND l.score BETWEEN 10 AND 99
                    AND l.training_enabled=1 AND l.provenance!='ai' ORDER BY l.id''')]
    if not labels:
        raise ValueError("Noch keine bestätigten Ausschnitte zum Testen vorhanden.")
    snapshot = label_dataset(labels)
    exported_hashes = {r["output_path"]: file_hash(r["output_path"]) for r in labels}
    groups = [file_hash(r["stored_path"]) for r in labels]
    criteria = setting("criteria", DEFAULT_CRITERIA)
    output = DATA / "benchmarks"
    output.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report = {"schema_version": 2, "id": run_id, "created_at": datetime.now().isoformat(), "dataset": snapshot,
              "include_raw": bool(include_raw),
              "score_range": [min(r["score"] for r in labels), max(r["score"] for r in labels)],
              "criteria": criteria, "models": [], "complete": False,
              "method": "Rohvorschläge der Vision-Sprachmodelle ohne Nutzerlabels im Prompt. Lernköpfe mit Leave-one-original-image-out; Testlabel und Bildduplikate von Training und Skalierung ausgeschlossen. Keine unabhängige Produktionsvalidierung.",
              "note": "Angelernte Bewertungen werden mit jeweils zurückgehaltenem Testbild geprüft. Unangepasste Rohbewertungen haben deine individuelle Skala nicht gelernt und sind separat ausgewiesen. Mit wenigen Bildern und Qualitätsstufen ist der Vergleich vorläufig."}

    def persist():
        text = json.dumps(report, ensure_ascii=False, indent=2)
        for name in (run_id + ".json", "latest.json"):
            temporary = output / (name + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, output / name)

    persist()
    try:
        for model_id in model_ids:
            started = time.monotonic()
            result = {"model_id": model_id, "name": MODELS[model_id]["name"], "status": "running", "rows": []}
            learned_vision = MODELS[model_id]["kind"] == "embedding" or MODELS[model_id].get("features") == "vision"
            learned_primary = learned_vision or MODELS[model_id]["kind"] == "baseline"
            result.update(score_mode="learned_vision" if learned_vision else "learned_texture" if learned_primary else "raw_vision",
                          primary=metrics([], "cv_score"), expected=len(labels))
            report["models"].append(result)
            try:
                engine.stop()
                status = engine.status(model_id)
                if not status["installed"]:
                    result.update(status="not_installed", message="Pakete und Gewichte noch nicht installiert.")
                    continue
                if not status.get("can_infer", True) and not learned_primary:
                    result.update(status="blocked", message=status.get("blocked_reason", "Modell kann derzeit nicht ausgeführt werden."))
                    continue
                feature_key = engine.feature_key(model_id)
                result["revision"] = status.get("revision")
                vectors = []
                for index, label in enumerate(labels):
                    progress(f"{result['name']}: Bildmerkmale {index+1}/{len(labels)}")
                    with connection() as db:
                        cached = db.execute("SELECT vector FROM features WHERE label_id=? AND model_id=?", (label["id"], feature_key)).fetchone()
                    vector = np.array(json.loads(cached[0])) if cached else engine.vector(model_id, label, json.loads(label["crop"]))
                    if not cached:
                        with connection() as db:
                            db.execute("INSERT OR REPLACE INTO features VALUES (?,?,?)", (label["id"], feature_key, json.dumps(vector.tolist())))
                    vectors.append(vector)
                progress(result["name"] + ": Lernkopf mit zurückgehaltenen Bildern prüfen …")
                validation = cross_validate(vectors, [r["score"] for r in labels], groups)
                result["rows"] = [{"label_id": r["id"], "image_id": r["image_id"], "file": r["relative_name"],
                                   "crop": json.loads(r["crop"]), "actual": r["score"], **validation[i]} for i,r in enumerate(labels)]
                result["cv"] = metrics(result["rows"], "cv_score")
                result["median_baseline"] = metrics(result["rows"], "median_score")
                if learned_primary:
                    result["primary"] = result["cv"]
                persist()
                if MODELS[model_id]["kind"] == "vlm" and (include_raw or not learned_primary):
                    consecutive_errors = 0
                    for index, (row, label) in enumerate(zip(result["rows"], labels)):
                        progress(f"{result['name']}: Vortrainiertes Modell {index+1}/{len(labels)}")
                        try:
                            raw = engine.infer(model_id, {"path": label["stored_path"], "crop": row["crop"], "criteria": criteria})
                            row.update(raw_score=raw["score"], reason=raw["reason"])
                            consecutive_errors = 0
                        except Exception as exc:
                            row["error"] = str(exc)
                            record_error("benchmark:" + model_id, str(exc))
                            consecutive_errors += 1
                            if consecutive_errors >= 3 or "Arbeitsspeicher" in str(exc):
                                result["message"] = str(exc)
                                break
                        finally:
                            result["raw"] = metrics(result["rows"], "raw_score")
                            if not learned_primary:
                                result["primary"] = result["raw"]
                            persist()
                    result["status"] = "done" if result["raw"]["n"] == len(labels) else "partial"
                else:
                    result["status"] = "done" if result["primary"]["n"] == len(labels) else "insufficient_data"
                engine.train(model_id, progress)
            except Exception as exc:
                result.update(status="error", message=str(exc))
                record_error("benchmark:" + model_id, str(exc))
            finally:
                engine.stop()
                result["seconds"] = round(time.monotonic()-started, 2)
                persist()
    finally:
        report["labels_unchanged"] = label_dataset() == snapshot
        report["exports_unchanged"] = all(Path(p).is_file() and file_hash(p)==digest for p,digest in exported_hashes.items())
        report["complete"] = True
        report["overall_status"] = "done" if all(m["status"] == "done" for m in report["models"]) else "partial"
        persist()
        columns = ["model_id", "label_id", "image_id", "file", "crop", "actual", "cv_score", "median_score", "train_count", "raw_score", "reason", "error"]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for model in report["models"]:
            for row in model["rows"]:
                writer.writerow({"model_id": model["model_id"], **row})
        (output / (run_id + ".csv")).write_text(stream.getvalue(), encoding="utf-8-sig")
    return report
