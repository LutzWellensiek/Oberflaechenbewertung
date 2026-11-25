import atexit
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import traceback
import uuid

import numpy as np
from .catalog import MODELS
from .diagnostics import record_error
from .hardware import check_memory
from .portable import interpreter, is_portable, model_configuration, worker_command
from .imaging import crop_image, now, opened, texture_features
from .learning import coarse_class, fit_head, head_status, predict_head, predict_parameters, fit_parameters, weighted_error
from .storage import DATA, ROOT, connection, image_row, label_dataset, setting


class Jobs:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="surface")
        self.items = {}
        self.lock = threading.Lock()

    def submit(self, title, fn, unique_title=False):
        job_id = uuid.uuid4().hex
        with self.lock:
            if unique_title:
                for existing in self.items.values():
                    if existing["title"] == title and existing["status"] in ("queued", "running"):
                        return {"job": existing["id"], "already_running": True}
            self.items[job_id] = {"id": job_id, "title": title, "status": "queued", "message": "Wartet …"}
            if len(self.items) > 100:
                finished = [k for k, v in self.items.items() if v["status"] in ("done", "error")]
                if finished:
                    del self.items[finished[0]]

        def progress(message):
            with self.lock:
                self.items[job_id]["message"] = str(message)[-4000:]

        def work():
            with self.lock:
                self.items[job_id]["status"] = "running"
            try:
                result = fn(progress)
                with self.lock:
                    self.items[job_id].update(status="done", result=result, message="Abgeschlossen")
            except Exception as exc:
                error_id = record_error("job:" + title, str(exc), traceback.format_exc())
                with self.lock:
                    self.items[job_id].update(status="error", message=str(exc), error_id=error_id)
        self.executor.submit(work)
        return {"job": job_id}

    def get(self, job_id):
        with self.lock:
            if job_id not in self.items:
                raise ValueError("Auftrag nicht gefunden; möglicherweise wurde die Anwendung neu gestartet.")
            return dict(self.items[job_id])

    def active(self, title=None):
        with self.lock:
            return [{k: item[k] for k in ("id", "title", "status", "message")} for item in self.items.values()
                    if item["status"] in ("queued", "running") and (title is None or item["title"] == title)]


class Engine:
    def __init__(self):
        self.worker = None
        self.worker_model = None
        self.worker_mode = None
        self.worker_log = None
        self.lock = threading.RLock()
        self.jobs = Jobs()
        atexit.register(self.stop)

    @staticmethod
    def profile(model_id):
        if model_id not in MODELS:
            raise ValueError("Unbekanntes Modell.")
        return ROOT / "environments" / model_id

    def feature_key(self, model_id):
        if MODELS[model_id]["kind"] != "embedding" and MODELS[model_id].get("features") != "vision":
            return "texture-v1"
        marker = self.profile(model_id) / "ready.json"
        config = model_configuration(ROOT, model_id)
        revision = config.get("revision", "not-installed")
        if MODELS[model_id].get("features") == "vision":
            return model_id + ":vision-mean-v1:" + revision + ":" + str(config.get("quantization")) + (":cpu-vision-f32" if config.get("cpu_vision_only") else ":" + config["dtype"] if config.get("dtype") else "")
        return model_id + ":" + revision

    def training_signature(self, model_id, dataset):
        return hashlib.sha256((dataset["signature"] + ":quality-v2:" + self.feature_key(model_id)).encode()).hexdigest()

    def status(self, model_id, dataset=None):
        profile = self.profile(model_id)
        python = interpreter(ROOT, model_id)
        marker = profile / "ready.json"
        ready = model_id == "manual" or marker.exists()
        config = model_configuration(ROOT, model_id)
        dataset = dataset if dataset is not None else label_dataset()
        head = head_status(model_id)
        head.update(available=dataset["count"],
                    up_to_date=not dataset["count"] or head["dataset_signature"] == self.training_signature(model_id, dataset))
        blocked_reason = None
        if ready and model_id != "manual" and self.worker_model != model_id:
            try:
                check_memory(model_id, config.get("device", "cpu"), "vision_float32" if config.get("cpu_vision_only") else config.get("dtype"))
            except RuntimeError as exc:
                blocked_reason = str(exc)
        return {"id": model_id, **MODELS[model_id], "environment": python.exists(), "installed": ready,
                "active": self.worker_model == model_id, "head": head,
                "can_infer": ready and not blocked_reason, "blocked_reason": blocked_reason, **config}

    def activate(self, model_id, progress=lambda text: None):
        self.profile(model_id)
        if self.worker_model != model_id:
            self.stop()
        progress("Gespeicherte Ausschnitte und feste Nutzerbewertungen werden übernommen …")
        return self.train(model_id, progress)

    def stop(self):
        with self.lock:
            if self.worker:
                self.worker.terminate()
                try:
                    self.worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.worker.kill()
                    self.worker.wait()
                for pipe in (self.worker.stdin, self.worker.stdout):
                    if pipe:
                        pipe.close()
            self.worker = None
            self.worker_model = None
            self.worker_mode = None
            if self.worker_log:
                self.worker_log.close()
                self.worker_log = None

    def _read(self, timeout=1200):
        result = queue.Queue()
        pipe = self.worker.stdout

        def read():
            try:
                result.put(pipe.readline())
            except Exception:
                result.put("")
        threading.Thread(target=read, daemon=True).start()
        try:
            line = result.get(timeout=timeout)
        except queue.Empty:
            self.stop()
            raise RuntimeError("Zeitlimit für Modell überschritten. Details im Modellprotokoll.")
        if not line:
            code = self.worker.poll()
            if code is None:
                try:
                    code = self.worker.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            self.stop()
            suffix = f" (Exitcode {code}, Windows 0x{code & 0xffffffff:08X})" if code is not None else ""
            raise RuntimeError("Modellprozess wurde beendet" + suffix + ". Details: environments/<Modell>/inference.log")
        data = json.loads(line)
        if data.get("error"):
            raise RuntimeError(data["error"])
        return data

    def infer(self, model_id, request):
        with self.lock:
            profile = self.profile(model_id)
            if not (profile / "ready.json").exists():
                raise ValueError("Dieses Modell bitte zuerst im Bereich Modelle installieren.")
            config = model_configuration(ROOT, model_id)
            vision_only = bool(config.get("cpu_vision_only") and request.get("operation") == "embedding")
            mode = "vision" if vision_only else "full"
            if not self.worker or self.worker_model != model_id or self.worker_mode != mode or self.worker.poll() is not None:
                self.stop()
                config = model_configuration(ROOT, model_id)
                check_memory(model_id, config["device"], "vision_float32" if vision_only else config.get("dtype"))
                self.worker_log = (profile / "inference.log").open("a", encoding="utf-8")
                env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
                self.worker = subprocess.Popen(worker_command(ROOT, model_id, vision_only),
                    cwd=str(ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.worker_log,
                    text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.worker_model = model_id
                self.worker_mode = mode
                self._read()
            self.worker.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.worker.stdin.flush()
            return self._read()

    def install(self, model_id, device, progress):
        if is_portable(ROOT):
            raise ValueError("Die portable Version enthält beide Vision-Modelle bereits. Installationen sind hier deaktiviert.")
        if model_id == "manual" or device not in ("cpu", "cuda"):
            raise ValueError("Ungültige Modellauswahl oder Rechenart.")
        profile = self.profile(model_id)
        python = profile / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.exists():
            raise ValueError("Modellumgebung fehlt. Bitte zuerst Setup.bat ausführen.")
        self.stop()
        progress("Installiere Pakete und Gewichte. Dies kann einige Minuten dauern.")
        with (profile / "install.log").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen([str(python), "-u", str(ROOT / "install_model.py"), model_id, "--device", device],
                cwd=str(ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                proc.wait(timeout=7200)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise RuntimeError("Installationszeitlimit erreicht. Bitte Installationsprotokoll prüfen.")
        if proc.returncode:
            tail = (profile / "install.log").read_text(encoding="utf-8", errors="replace")[-2000:]
            # Never include credentials that a third-party library might include in an exception.
            import re
            tail = re.sub(r"hf_[A-Za-z0-9]+", "[Token entfernt]", tail)
            if "10013" in tail:
                raise RuntimeError("Windows blockiert den Internetzugriff dieser Ausführungsumgebung (WinError 10013). "
                                   "Bitte die Anwendung beenden und Start.bat selbst im Explorer starten. "
                                   "Alternativ Modelle_laden_und_testen.bat starten. Details im Installationsprotokoll.")
            raise RuntimeError("Installation fehlgeschlagen. Protokoll: " + tail)
        progress("Modell installiert. Vorhandene Bewertungen werden für dieses Modell gelernt …")
        self.train(model_id, progress)
        return self.status(model_id)

    def vector(self, model_id, row, crop):
        if MODELS[model_id]["kind"] == "embedding" or MODELS[model_id].get("features") == "vision":
            return np.array(self.infer(model_id, {"path": row["stored_path"], "crop": crop, "operation": "embedding"})["vector"])
        return texture_features(crop_image(row, crop))

    def train(self, model_id, progress=lambda text: None):
        self.profile(model_id)
        if model_id != "manual" and not self.status(model_id)["installed"]:
            return {"count": 0, "waiting_for_install": True,
                    "message": "Alle Bewertungen bleiben gespeichert und werden nach Modellinstallation automatisch übernommen."}
        with connection() as db:
            labels = [dict(r) for r in db.execute('''SELECT l.*,i.stored_path,i.width,i.height,i.origin_folder FROM labels l
                         JOIN images i ON i.id=l.image_id WHERE i.status='rated' AND l.score BETWEEN 10 AND 99
                         AND l.training_enabled=1 AND l.provenance!='ai' ORDER BY l.id''')]
        if not labels:
            return {"count": 0, "message": "Noch keine bestätigten Bewertungen vorhanden."}
        signature = self.training_signature(model_id, label_dataset(labels))
        current = head_status(model_id)
        if current["dataset_signature"] == signature:
            return {"count": current["count"], "cached": True,
                    "message": "Alle gespeicherten Bewertungen sind bereits übernommen."}
        feature_key = self.feature_key(model_id)
        vectors = []
        for index, label in enumerate(labels):
            progress(f"Lerne {index + 1}/{len(labels)} bestätigte Ausschnitte …")
            with connection() as db:
                cached = db.execute("SELECT vector FROM features WHERE label_id=? AND model_id=?", (label["id"], feature_key)).fetchone()
                # Reuse the old per-model texture cache from earlier application versions.
                if not cached and feature_key == "texture-v1":
                    cached = db.execute("SELECT vector FROM features WHERE label_id=? AND model_id=?", (label["id"], model_id)).fetchone()
            if cached:
                vector = np.array(json.loads(cached[0]))
            else:
                vector = self.vector(model_id, label, json.loads(label["crop"]))
                with connection() as db:
                    db.execute("INSERT OR REPLACE INTO features VALUES (?,?,?)", (label["id"], feature_key, json.dumps(vector.tolist())))
            vectors.append(vector)
        return fit_head(model_id, vectors, [l["score"] for l in labels], [l["id"] for l in labels], signature)

    def predict(self, image_id, model_id, crop, criteria, progress=lambda text: None):
        self.profile(model_id)
        row = image_row(image_id)
        if row["status"] == "ignored":
            raise ValueError("Bitte das ignorierte Bild zuerst wieder aufnehmen.")
        from .imaging import checked_crop
        crop = checked_crop(crop, row["width"], row["height"])
        actual = None
        if row["status"] == "rated":
            with connection() as db:
                label = db.execute("SELECT crop,score FROM labels WHERE image_id=?", (image_id,)).fetchone()
            if not label or crop != json.loads(label["crop"]):
                raise ValueError("Für den Vergleich bitte den gespeicherten Bildausschnitt verwenden.")
            actual = label["score"]
        if model_id != "manual" and not self.status(model_id)["installed"]:
            raise ValueError("Dieses Modell ist noch nicht installiert.")
        # Also catch up after a restart or when API clients bypass the model-selection UI.
        self.train(model_id, progress)
        progress("Modell analysiert den gewählten Ausschnitt …")
        vector = self.vector(model_id, row, crop)
        learned = predict_head(model_id, vector)
        if actual is not None:
            # Comparing a confirmed image must not learn its own answer first.
            # Exclude identical original-image copies as well as the selected row.
            from .benchmark import file_hash
            source_hash = file_hash(row["stored_path"])
            with connection() as db:
                other = [dict(r) for r in db.execute('''SELECT l.*,i.stored_path,i.width,i.height,i.origin_folder FROM labels l
                            JOIN images i ON i.id=l.image_id WHERE i.status='rated' AND l.score BETWEEN 10 AND 99
                            AND l.training_enabled=1 AND l.provenance!='ai' AND i.id<>? ORDER BY l.id''', (image_id,))]
            train_vectors, train_scores = [], []
            key = self.feature_key(model_id)
            for label in other:
                if file_hash(label["stored_path"]) == source_hash:
                    continue
                with connection() as db:
                    cached = db.execute("SELECT vector FROM features WHERE label_id=? AND model_id=?", (label["id"], key)).fetchone()
                values = np.array(json.loads(cached[0])) if cached else self.vector(model_id, label, json.loads(label["crop"]))
                train_vectors.append(values)
                train_scores.append(label["score"])
            learned = predict_parameters(fit_parameters(train_vectors, train_scores), vector) if train_vectors else None
        raw = None
        if MODELS[model_id]["kind"] == "vlm" and not model_configuration(ROOT, model_id).get("cpu_vision_only"):
            raw = self.infer(model_id, {"path": row["stored_path"], "crop": crop, "criteria": criteria})
        result = {"score": None, "uncertainty": 1.0, "reason": "Bitte zuerst Beispiele bewerten und bestätigen.",
                  "learned": learned, "raw": raw, "model_id": model_id, "crop": crop, "image_id": image_id}
        if actual is not None:
            result.update(actual_score=actual, comparison=True)
        if learned:
            result.update(score=learned["score"], uncertainty=learned["uncertainty"],
                          reason=f"Angelerntes Bewertungsmodell aus {learned['count']} bestätigten Ausschnitten. Unsicherheit ist eine Heuristik.",
                          score_source="learned_vision" if self.feature_key(model_id) != "texture-v1" else "learned_texture")
        if raw:
            # A learned head on this model's actual visual features is the user's
            # score model. The generic language-model score is an extra diagnostic.
            use_head = learned and (MODELS[model_id].get("features") == "vision" or
                                   learned["count"] >= 20 and learned["uncertainty"] < 0.65)
            result.update(score=learned["score"] if use_head else raw["score"],
                          reason=(f"Angelernt aus {learned['count']} bestätigten Ausschnitten mit eigenen Bildmerkmalen. Bildanalyse: " if use_head and MODELS[model_id].get("features") == "vision" else "") + raw["reason"],
                          uncertainty=learned["uncertainty"] if learned else 1.0,
                          score_source="learned_vision" if use_head and MODELS[model_id].get("features") == "vision" else "learned_texture" if use_head else "raw_vision")
        if actual is not None:
            result["reason"] += f" · Vergleich mit deiner festen Bewertung: {actual} Punkte. Das Testbild war im Lernkopf-Training ausgeschlossen."
        with connection() as db:
            cursor = db.execute('''INSERT INTO predictions (image_id,model_id,crop,criteria,score,uncertainty,result,created_at)
                                VALUES (?,?,?,?,?,?,?,?)''', (image_id, model_id, json.dumps(crop), criteria,
                                 result["score"], result["uncertainty"], json.dumps(result, ensure_ascii=False), now()))
            result["prediction_id"] = cursor.lastrowid
        return result

    def active_queue(self, model_id, progress=lambda text: None):
        self.train(model_id, progress)
        if not head_status(model_id)["count"]:
            raise ValueError("Für Active Learning zuerst einige Bilder bewerten und das Modell trainieren.")
        with connection() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM images WHERE status='pending' ORDER BY id")]
        ranked = []
        for index, row in enumerate(rows):
            progress(f"Unsicherheit {index + 1}/{len(rows)} …")
            draft = json.loads(row["draft"]) if row["draft"] else {}
            crop = draft.get("crop", [0, 0, row["width"], row["height"]])
            vector = self.vector(model_id, row, crop)
            result = predict_head(model_id, vector)
            ranked.append({"id": row["id"], "uncertainty": result["uncertainty"], "score": result["score"]})
        return sorted(ranked, key=lambda r: (-r["uncertainty"], r["id"]))


ENGINE = Engine()
