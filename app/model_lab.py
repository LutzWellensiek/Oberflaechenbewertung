"""Explicit, reproducible model runs; AI results never become human training labels."""
from datetime import datetime
import json
from pathlib import Path
import uuid

import numpy as np

from .benchmark import file_hash, metrics
from .catalog import MODELS
from .imaging import checked_crop, now
from .learning import fit_parameters, predict_parameters
from .storage import DATA, connection, image_row

TASKS = {"quality": "Qualität", "region": "Ausschnitt", "relevance": "Passend / unpassend"}


def image_input(row, task):
    draft = json.loads(row.get("draft") or "{}") if row["status"] == "pending" else {}
    crop = (draft.get("crop") or (json.loads(row["crop"]) if row.get("crop") else None)) if task == "quality" else None
    return {"crop": crop or [0, 0, row["width"], row["height"]],
            "date": draft.get("date") or row.get("captured_at") or row["chosen_date"]}


def result_is_current(row, result, temporal=False):
    context = image_input(row, result["task"])
    return result.get("input_crop") == context["crop"] and (not temporal or result.get("input_date") == context["date"])


def run_row(run_id):
    with connection() as db:
        row = db.execute("SELECT * FROM model_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise ValueError("Trainingsstand nicht gefunden.")
    result = dict(row)
    result["recipe"] = json.loads(result["recipe"])
    return result


def current_samples():
    with connection() as db:
        return [dict(r) for r in db.execute('''SELECT i.*,l.id label_id,l.score,l.crop,l.captured_at,l.revision,
            l.alternatives,l.provenance,l.training_enabled FROM images i LEFT JOIN labels l ON l.image_id=i.id ORDER BY i.id''')]


def run_is_stale(run_id):
    run = run_row(run_id)
    rows = {r["id"]: r for r in current_samples()}
    for sample in run["recipe"]["samples"]:
        row = rows.get(sample["id"])
        if not row or row["status"] != sample["status"] or row["status"] in ("removed", "ignored"):
            return True
        keys = ["relevance", "training_enabled"] if run["task"] == "relevance" else ["revision", "training_enabled"]
        if run["temporal"]:
            keys.extend(["chosen_date", "origin_folder"])
        if any(row.get(key) != sample.get(key) for key in keys):
            return True
    from .engine import ENGINE
    return ENGINE.feature_key(run["base_model"]) != run["recipe"]["encoder_key"]


def list_runs():
    with connection() as db:
        runs = [dict(r) for r in db.execute("SELECT * FROM model_runs ORDER BY created_at DESC")]
        evaluations = [dict(r) for r in db.execute("SELECT * FROM model_results")]
    current = {r["id"]: r for r in current_samples()}
    for run in runs:
        recipe = json.loads(run.pop("recipe"))
        run.update(count=len(recipe["samples"]), stale=run_is_stale(run["id"]),
                   image_ids=[r["id"] for r in recipe["samples"]], recipe=recipe)
        evaluated = [json.loads(r["result"]) for r in evaluations if r["run_id"] == run["id"]
                     and r["image_id"] in current and current[r["image_id"]]["status"] == "rated"
                     and r["label_revision"] == current[r["image_id"]]["revision"]]
        run["metrics"] = metrics([r for r in evaluated if r.get("actual") not in (None, 0)], "score")
        run["evaluated"] = len(evaluated)
    return runs


def _vector(engine, base_model, row, crop, cache=True):
    key = engine.feature_key(base_model)
    is_label = row.get("label_id") and row.get("crop") and crop == json.loads(row["crop"])
    with connection() as db:
        cached = db.execute("SELECT vector FROM features WHERE label_id=? AND model_id=?", (row.get("label_id"), key)).fetchone() if is_label and cache else None
    if cached:
        return np.asarray(json.loads(cached[0]), dtype=float)
    vector = engine.vector(base_model, row, crop)
    if is_label and cache:
        with connection() as db:
            # Cache keys are attached only to the unchanged crop revision.
            current = db.execute("SELECT revision FROM labels WHERE id=?", (row["label_id"],)).fetchone()
            if current and current[0] == row.get("revision"):
                db.execute("INSERT OR REPLACE INTO features VALUES (?,?,?)", (row["label_id"], key, json.dumps(vector.tolist())))
    return vector


def _date_vector(row, recipe):
    if not recipe.get("temporal"):
        return np.array([], dtype=float)
    date = row.get("captured_at") or row["chosen_date"]
    folder = row["origin_folder"]
    origin = recipe["time_origins"].get(folder)
    if not origin:
        return np.zeros(10)
    days = (datetime.fromisoformat(date) - datetime.fromisoformat(origin)).total_seconds() / 86400
    # Periodic and elapsed-time features allow recurring states, with no monotone constraint.
    return np.array([np.sign(days) * np.log1p(abs(days)) / 10, 1,
                     *[f(2 * np.pi * days / period) for period in (1, 7, 30, 365.25) for f in (np.sin, np.cos)]])


def create_run(engine, body, progress=lambda message: None):
    base = body.get("base_model", "manual")
    task = body.get("task", "quality")
    name = str(body.get("name", "")).strip()
    ids = body.get("image_ids")
    if base not in MODELS or task not in TASKS or not 1 <= len(name) <= 100:
        raise ValueError("Bitte Namen, vorhandenes Basismodell und Aufgabe wählen.")
    if not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids):
        raise ValueError("Bitte Trainingsbilder in der Übersicht auswählen.")
    if not engine.status(base)["installed"]:
        raise ValueError("Das Basismodell ist noch nicht installiert.")
    selected = [r for r in current_samples() if r["id"] in set(ids)]
    samples = []
    for row in selected:
        if row["status"] in ("removed", "ignored"):
            continue
        if task in ("quality", "region") and (row["status"] != "rated" or not row["training_enabled"] or row["provenance"] == "ai" or not row["score"]):
            continue
        if task == "relevance" and (row["relevance"] is None or row["training_enabled"] == 0):
            continue
        samples.append(row)
    if not samples:
        raise ValueError("Keine passenden Trainingsbeispiele. Qualität/Ausschnitt: bestätigte Werte 10–99 mit Trainingsfreigabe. Eignung: ausdrücklich passend/unpassend markieren.")
    if task == "relevance" and len({r["relevance"] for r in samples}) < 2:
        raise ValueError("Für die Eignung werden passende und unpassende Beispiele benötigt.")
    temporal = bool(body.get("temporal")) and task == "quality"
    origins = {}
    for row in samples:
        date = row.get("captured_at") or row["chosen_date"]
        origins[row["origin_folder"]] = min(origins.get(row["origin_folder"], date), date)
    run_id = uuid.uuid4().hex
    recipe = {"version": 2, "encoder_key": engine.feature_key(base), "temporal": temporal,
              "time_origins": origins, "samples": samples, "algorithm": "Ordinaler Lernkopf + ähnliche Beispiele" if task == "quality" else "Ähnlichkeit bestätigter Bildbereiche" if task == "region" else "Bildmerkmale + Eignungsklassifikation",
              "limitations": "Eingefrorener Bildencoder. Nur dieser Lernstand kennt die ausgewählten Beispiele. Unsicherheit und Ähnlichkeit sind Heuristiken; Datum ist optionaler Kontext und keine Qualitätsregel."}
    vectors = []
    for index, row in enumerate(samples):
        progress(f"{name}: Bildmerkmale {index + 1}/{len(samples)}")
        crop = json.loads(row["crop"]) if task == "quality" else [0, 0, row["width"], row["height"]]
        vector = _vector(engine, base, row, crop)
        vectors.append(np.r_[vector, _date_vector(row, recipe)])
        row["hash"] = file_hash(row["stored_path"])
    params = {}
    if task != "region":
        scores = [r["score"] if task == "quality" else 90 if r["relevance"] else 10 for r in samples]
        params = fit_parameters(vectors, scores, alternatives=[json.loads(r["alternatives"] or "[]") if task == "quality" else [] for r in samples])
    directory = DATA / "profiles" / ("lab-" + run_id)
    directory.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(directory / "head.npz", vectors=np.asarray(vectors), **params)
    with connection() as db:
        db.execute("INSERT INTO model_runs VALUES (?,?,?,?,?,?,?)",
                   (run_id, name, base, task, int(temporal), json.dumps(recipe, ensure_ascii=False), now()))
    return {"id": run_id, "name": name, "count": len(samples), "task": task}


def _neighbors(vectors, vector, samples):
    scale = np.maximum(np.std(vectors, axis=0), .03)
    distances = np.mean(np.clip((vectors - vector) / scale, -8, 8) ** 2, axis=1)
    order = np.argsort(distances)[:5]
    weights = np.exp(-(distances[order] - distances[order[0]]) / max(.15, float(np.median(distances[order]))))
    weights /= weights.sum()
    return order, weights, [{"image_id": samples[i]["id"], "file": samples[i]["relative_name"],
                             "score": samples[i]["score"], "distance": round(float(distances[i]), 4),
                             "weight": round(float(w), 4)} for i, w in zip(order, weights)]


def evaluate_run(engine, body, progress=lambda message: None):
    run = run_row(body["run_id"])
    if run_is_stale(run["id"]):
        raise ValueError("Trainingsbilder wurden geändert. Bitte einen neuen Trainingsstand anlegen.")
    ids = body.get("image_ids")
    if not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids):
        raise ValueError("Bitte Bilder zum Auswerten auswählen.")
    targets = [r for r in current_samples() if r["id"] in set(ids) and r["status"] in ("pending", "rated")]
    if not targets:
        raise ValueError("Keine aktiven Testbilder ausgewählt.")
    recipe, task = run["recipe"], run["task"]
    with np.load(DATA / "profiles" / ("lab-" + run["id"]) / "head.npz", allow_pickle=False) as stored:
        vectors = stored["vectors"].copy()
    results = []
    for index, row in enumerate(targets):
        progress(f"{run['name']}: auswerten {index + 1}/{len(targets)}")
        source_hash = file_hash(row["stored_path"])
        train = [i for i, sample in enumerate(recipe["samples"]) if sample["hash"] != source_hash]
        context = image_input(row, task)
        result = {"image_id": row["id"], "actual": row["score"] if row["status"] == "rated" else None,
                  "score": None, "task": task, "source": "KI", "excluded_test_image": True,
                  "train_count": len(train), "temporal": bool(run["temporal"]),
                  "input_crop": context["crop"], "input_date": context["date"]}
        if not train:
            result["reason"] = "Nach Ausschluss dieses Originals und seiner Duplikate bleiben keine Trainingsbilder übrig."
        else:
            training = [recipe["samples"][i] for i in train]
            x = vectors[train]
            crop = context["crop"]
            evaluation_recipe = recipe
            if recipe["temporal"]:
                origins = {}
                for sample in training:
                    date = sample.get("captured_at") or sample["chosen_date"]
                    folder = sample["origin_folder"]
                    origins[folder] = min(origins.get(folder, date), date)
                evaluation_recipe = {**recipe, "time_origins": origins}
                x = np.array([np.r_[v[:-10], _date_vector(sample, evaluation_recipe)] for v, sample in zip(x, training)])
            vector = np.r_[_vector(engine, run["base_model"], row, crop), _date_vector({**row, "captured_at": context["date"]}, evaluation_recipe)]
            order, weights, nearest = _neighbors(x, vector, training)
            result["neighbors"] = nearest
            if task == "region":
                boxes = np.array([[v / (training[i]["width"] if j % 2 == 0 else training[i]["height"])
                                   for j, v in enumerate(json.loads(training[i]["crop"]))] for i in order])
                box = weights @ boxes
                suggestion = [int(round(v * (row["width"] if j % 2 == 0 else row["height"]))) for j, v in enumerate(box)]
                result.update(crop=checked_crop(suggestion, row["width"], row["height"]),
                              reason="Ausschnitt aus ähnlichen bestätigten Bildern übertragen. Bitte prüfen; kein Objektdetektor.")
            else:
                scores = [r["score"] if task == "quality" else 90 if r["relevance"] else 10 for r in training]
                parameters = fit_parameters(x, scores, alternatives=[json.loads(r["alternatives"] or "[]") if task == "quality" else [] for r in training])
                predicted = predict_parameters(parameters, vector)
                if task == "relevance":
                    probability = float(sum(w * training[i]["relevance"] for i, w in zip(order, weights)))
                    result.update(relevant=probability >= .5, probability=round(probability, 4),
                                  reason="Passend/unpassend aus ausdrücklich markierten Beispielen; keine automatische Entfernung.")
                else:
                    # Local examples can represent separated, recurring quality states.
                    local = np.zeros(10)
                    for i, w in zip(order, weights):
                        possible = list(dict.fromkeys([training[i]["score"], *json.loads(training[i]["alternatives"])]))
                        for score in possible:
                            local[min(8, max(0, score // 10 - 1))] += w / len(possible)
                    p = .55 * np.array(predicted["probabilities"]) + .45 * local
                    p[9] = 0
                    p /= p.sum()
                    stages = np.argsort(-p)[:3]
                    selected = int(stages[0])
                    fine = predicted["score"] % 10 if predicted["score"] // 10 - 1 == selected else 0
                    result.update(score=min(99, (selected + 1) * 10 + fine), uncertainty=predicted["uncertainty"],
                                  alternatives=[{"stage": int((i + 1) * 10), "weight": round(float(p[i]), 4)} for i in stages],
                                  reason="55 % Lernkopf, 45 % ähnliche bestätigte Beispiele. Mehrere Stufen können plausibel sein. " +
                                         ("Aufnahmezeit im Herkunftsordner als zusätzlicher, nichtlinearer Kontext." if run["temporal"] else "Nur Bildmerkmale; ohne Zeitkontext."))
        if run_is_stale(run["id"]):
            raise ValueError("Trainingsdaten wurden während der Auswertung geändert. Bitte neu trainieren.")
        with connection() as db:
            db.execute("INSERT OR REPLACE INTO model_results VALUES (?,?,?,?,?)",
                       (run["id"], row["id"], row["revision"] or 0, json.dumps(result, ensure_ascii=False), now()))
        results.append(result)
    return {"run_id": run["id"], "results": results, "metrics": metrics([r for r in results if r["actual"] not in (None, 0)], "score")}


def export_ai(body):
    from .workspace import write_export
    run = run_row(body["run_id"])
    if run["task"] != "quality" or run_is_stale(run["id"]):
        raise ValueError("Nur aktuelle Qualitätsvorhersagen lassen sich als KI exportieren.")
    row = image_row(body["image_id"])
    if row["status"] not in ("pending", "rated"):
        raise ValueError("Bitte das Bild zuerst wiederherstellen.")
    with connection() as db:
        result = db.execute("SELECT * FROM model_results WHERE run_id=? AND image_id=?", (run["id"], row["id"])).fetchone()
        label = db.execute("SELECT revision,crop,captured_at FROM labels WHERE image_id=?", (row["id"],)).fetchone()
    if not result or result["label_revision"] != (label["revision"] if label else 0):
        raise ValueError("Bitte das Bild zuerst mit diesem Trainingsstand auswerten.")
    data = json.loads(result["result"])
    if label:
        row.update(crop=label["crop"], captured_at=label["captured_at"])
    if not result_is_current(row, data, run["temporal"]):
        raise ValueError("Ausschnitt oder Datum wurde verändert. Bitte neu auswerten.")
    if data["score"] is None:
        raise ValueError("Keine exportierbare Vorhersage vorhanden.")
    target = write_export(row, data["input_crop"], data["input_date"], data["score"], ai=True)
    return {"saved": str(target), "provenance": "ai"}
