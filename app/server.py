import csv
import io
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .catalog import DEFAULT_CRITERIA, MODELS
from .benchmark import latest_benchmark, run_benchmark
from .diagnostics import recent_errors, record_error
from .engine import ENGINE
from .imaging import checked_crop, crop_image, filename, import_folder, jpeg_bytes, normalize_date, now, opened
from .learning import coarse_class, weighted_error
from .storage import DATA, ROOT, connection, image_row, initialize, label_dataset, set_setting, setting
from .portable import is_portable

TOKEN = secrets.token_urlsafe(32)
SAVE_LOCK = threading.Lock()


def save_label(body):
    with SAVE_LOCK:
        row = image_row(body["image_id"])
        if row["status"] != "pending":
            raise ValueError("Dieses Bild ist bereits abgeschlossen. Es wird nicht erneut exportiert.")
        score = body.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 10 <= score <= 100:
            raise ValueError("Bitte eine ganze Bewertung zwischen 10 und 100 auswählen.")
        crop = checked_crop(body["crop"], row["width"], row["height"])
        captured = normalize_date(body["date"])
        source = str(body.get("date_source", "manual"))
        model_id = body.get("model_id", "manual")
        if model_id not in MODELS:
            raise ValueError("Ungültiges Modell.")
        date_options = json.loads(row["dates"])
        if source != "manual" and date_options.get(source) != captured:
            raise ValueError("Datumsquelle passt nicht zum Datum. Für eine Korrektur 'Manuell' wählen.")
        output = Path(setting("output", str(DATA / "output"))).resolve()
        if output == DATA / "originals" or (DATA / "originals") in output.parents:
            raise ValueError("Der Originalordner ist kein Ausgabeordner.")
        output.mkdir(parents=True, exist_ok=True)
        name = filename(captured, row["relative_name"], score)
        payload = jpeg_bytes(crop_image(row, crop))
        target, suffix = output / name, 1
        while True:
            try:
                with target.open("xb") as stream:
                    stream.write(payload)
                break
            except FileExistsError:
                target = output / (Path(name).stem + f"_{suffix}.jpg")
                suffix += 1
        try:
            with connection() as db:
                prediction_id = body.get("prediction_id")
                if prediction_id:
                    p = db.execute("SELECT * FROM predictions WHERE id=?", (prediction_id,)).fetchone()
                    if not p or p["image_id"] != row["id"] or p["model_id"] != model_id or json.loads(p["crop"]) != crop or p["criteria"] != setting("criteria", DEFAULT_CRITERIA):
                        raise ValueError("Vorhersage gehört zu einem anderen Ausschnitt oder Modell. Bitte neu bewerten.")
                db.execute('''INSERT INTO labels (image_id,score,coarse,crop,captured_at,date_source,output_path,
                              model_id,prediction_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                           (row["id"], score, (coarse_class(score) + 1) * 10, json.dumps(crop), captured, source,
                            str(target), model_id, prediction_id, now()))
                db.execute("UPDATE images SET status='rated', chosen_date=?,date_source=?,draft=NULL WHERE id=?", (captured, source, row["id"]))
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        training = ENGINE.jobs.submit("Lernmodell aktualisieren", lambda progress: ENGINE.train(model_id, progress))
        return {"saved": str(target), **training}


def browse_folder():
    if os.name != "nt":
        raise ValueError("Bitte den Ordnerpfad in das Textfeld eintragen.")
    script = '''Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Bilderordner oder Ausgabeordner auswaehlen'
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        [Console]::Write($dialog.SelectedPath)
    }'''
    result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                            capture_output=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
    return {"path": result.stdout.decode("utf-8-sig", errors="replace").strip()}


def state():
    with connection() as db:
        counts = {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) n FROM images GROUP BY status")}
        total = db.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        metrics = []
        for model_id in MODELS:
            records = db.execute('''SELECT l.score actual,p.score predicted FROM labels l
                         JOIN predictions p ON p.id=l.prediction_id WHERE p.model_id=? AND p.score IS NOT NULL''', (model_id,)).fetchall()
            metrics.append({"model_id": model_id, "n": len(records),
                            "mae": round(sum(abs(r["actual"]-r["predicted"]) for r in records)/len(records), 2) if records else None,
                            "stage_accuracy": round(sum(coarse_class(r["actual"]) == coarse_class(r["predicted"]) for r in records)/len(records), 3) if records else None})
    dataset = label_dataset()
    return {"token": TOKEN, "dataset": dataset, "counts": {"total": total, "pending": counts.get("pending", 0), "rated": counts.get("rated", 0), "ignored": counts.get("ignored", 0)},
            "instance_root": str(ROOT), "process_id": os.getpid(), "portable": is_portable(ROOT),
            "model_id": setting("model_id", "manual") if setting("model_id", "manual") in MODELS else "manual", "criteria": setting("criteria", DEFAULT_CRITERIA),
            "output": setting("output", str(DATA / "output")), "models": [ENGINE.status(m, dataset) for m in MODELS],
            "metrics": metrics, "benchmark": latest_benchmark(), "errors": recent_errors(10)}


class Handler(BaseHTTPRequestHandler):
    server_version = "SurfaceLab/1.0"

    def log_message(self, format, *args):
        pass

    def send(self, data, content_type="application/json; charset=utf-8", status=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8") if isinstance(data, (dict, list)) else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def validate_host(self):
        if self.headers.get("Host") not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
            raise PermissionError("Zugriff nur über localhost erlaubt.")

    def fail(self, exc, status):
        error_id = record_error("http:" + self.path.split("?")[0], str(exc))
        self.send({"error": str(exc), "error_id": error_id}, status=status)

    def do_GET(self):
        try:
            self.validate_host()
            self.get_route()
        except PermissionError as exc:
            self.fail(exc, 403)
        except (ValueError, KeyError, TypeError) as exc:
            self.fail(exc, 400)
        except Exception as exc:
            self.fail(exc, 500)

    def get_route(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/state":
            return self.send(state())
        if url.path == "/api/errors":
            return self.send(recent_errors(50))
        if url.path == "/api/benchmark":
            return self.send({"report": latest_benchmark(), "jobs": ENGINE.jobs.active("Gespeicherte Bilder testen")})
        if url.path == "/api/images":
            status = query.get("status", ["pending"])[0]
            if status not in ("pending", "rated", "ignored"):
                raise ValueError("Ungültiger Bildstatus.")
            with connection() as db:
                rows = [dict(r) for r in db.execute('''SELECT i.id,i.relative_name,i.width,i.height,i.status,l.score
                         FROM images i LEFT JOIN labels l ON l.image_id=i.id WHERE i.status=? ORDER BY i.id''', (status,))]
            return self.send(rows)
        if url.path == "/api/image":
            row = image_row(query["id"][0])
            row["dates"] = json.loads(row["dates"])
            row["draft"] = json.loads(row["draft"]) if row["draft"] else None
            with connection() as db:
                row["label"] = dict(r) if (r := db.execute("SELECT * FROM labels WHERE image_id=?", (row["id"],)).fetchone()) else None
                row["predictions"] = [dict(r) for r in db.execute("SELECT id,model_id,crop,score,uncertainty,result FROM predictions WHERE image_id=? ORDER BY id DESC LIMIT 30", (row["id"],))]
            return self.send(row)
        if url.path == "/api/photo":
            row = image_row(query["id"][0])
            crop = json.loads(query["crop"][0]) if "crop" in query else None
            img = crop_image(row, crop) if crop else opened(row["stored_path"])
            size = max(64, min(3000, int(query.get("size", [2000])[0])))
            return self.send(jpeg_bytes(img, size), "image/jpeg")
        if url.path.startswith("/api/jobs/"):
            return self.send(ENGINE.jobs.get(url.path.rsplit("/", 1)[-1]))
        if url.path == "/api/export":
            stream = io.StringIO(newline="")
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["image_id", "original", "score", "main_stage", "crop_xyxy", "date", "date_source", "output", "model", "all_original_dates"])
            with connection() as db:
                for row in db.execute("SELECT l.*,i.source,i.dates FROM labels l JOIN images i ON i.id=l.image_id WHERE i.status='rated' ORDER BY l.id"):
                    writer.writerow([row["image_id"], row["source"], row["score"], row["coarse"], row["crop"], row["captured_at"], row["date_source"], row["output_path"], row["model_id"], row["dates"]])
            return self.send(stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")
        if url.path == "/favicon.ico":
            return self.send(b"", "image/x-icon", 204)
        static = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/extra.css": "extra.css"}
        if url.path in static:
            path = ROOT / "web" / static[url.path]
            return self.send(path.read_bytes(), {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}[path.suffix])
        self.send({"error": "Nicht gefunden"}, status=404)

    def do_POST(self):
        try:
            # Consume bounded request bodies before rejecting a request, so Windows does
            # not reset the connection while the client is still sending its JSON.
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= 1_000_000:
                raise ValueError("Anfrage zu groß.")
            payload = self.rfile.read(length)
            self.validate_host()
            expected = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
            if self.headers.get("Origin") and self.headers["Origin"] not in expected:
                raise PermissionError("Ungültiger Ursprung.")
            if not secrets.compare_digest(self.headers.get("X-Surface-Token", ""), TOKEN):
                raise PermissionError("Sitzung ungültig. Bitte Seite neu laden.")
            body = json.loads(payload or b"{}")
            self.post_route(urlparse(self.path).path, body)
        except PermissionError as exc:
            self.fail(exc, 403)
        except (ValueError, KeyError, TypeError) as exc:
            self.fail(exc, 400)
        except Exception as exc:
            self.fail(exc, 500)

    def post_route(self, path, body):
        if path == "/api/shutdown":
            self.send({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path == "/api/browse":
            return self.send(browse_folder())
        if path == "/api/import":
            folder = str(body["folder"])
            return self.send(ENGINE.jobs.submit("Bilder importieren", lambda progress: import_folder(folder, bool(body.get("recursive", True)), progress)))
        if path == "/api/settings":
            result = {"ok": True}
            if "model_id" in body:
                if body["model_id"] not in MODELS:
                    raise ValueError("Unbekanntes Modell.")
                set_setting("model_id", body["model_id"])
                model_id = body["model_id"]
                result["sync_job"] = ENGINE.jobs.submit("Gespeicherte Bewertungen übernehmen", lambda progress: ENGINE.activate(model_id, progress))["job"]
            if "criteria" in body:
                criteria = str(body["criteria"]).strip()
                if not 5 <= len(criteria) <= 6000:
                    raise ValueError("Bewertungskriterien müssen 5–6000 Zeichen enthalten.")
                set_setting("criteria", criteria)
            if "output" in body:
                output = Path(body["output"]).expanduser().resolve()
                if output == DATA or (DATA / "originals") == output or (DATA / "originals") in output.parents:
                    raise ValueError("Bitte einen separaten Ausgabeordner wählen.")
                set_setting("output", str(output))
            return self.send(result)
        if path == "/api/draft":
            # A delayed autosave may arrive after Save/Ignore. It must not overwrite
            # a confirmed record or produce a spurious error popup.
            with SAVE_LOCK:
                row = image_row(body["image_id"])
                if row["status"] != "pending":
                    return self.send({"ok": True, "ignored": True})
                draft = body["draft"]
                draft["crop"] = checked_crop(draft["crop"], row["width"], row["height"])
                with connection() as db:
                    db.execute("UPDATE images SET draft=? WHERE id=? AND status='pending'", (json.dumps(draft), row["id"]))
            return self.send({"ok": True})
        if path == "/api/client-error":
            error_id = record_error("browser", str(body.get("message", ""))[:6000], str(body.get("details", ""))[:20000])
            return self.send({"ok": True, "error_id": error_id})
        if path == "/api/benchmark":
            existing = ENGINE.jobs.active("Gespeicherte Bilder testen")
            if existing:
                return self.send({"job": existing[0]["id"], "already_running": True})
            model_ids = body.get("model_ids", [m for m in MODELS if ENGINE.status(m)["installed"]])
            if not isinstance(model_ids, list) or not model_ids:
                raise ValueError("Bitte mindestens ein Modell auswählen.")
            for model_id in model_ids:
                ENGINE.profile(model_id)
            return self.send(ENGINE.jobs.submit("Gespeicherte Bilder testen", lambda progress: run_benchmark(ENGINE, model_ids, progress, bool(body.get("include_raw", False))), unique_title=True))
        if path == "/api/save":
            return self.send(save_label(body))
        if path == "/api/ignore":
            with SAVE_LOCK, connection() as db:
                row = image_row(body["image_id"])
                if row["status"] == "rated":
                    raise ValueError("Ein bereits bewertetes Bild kann nicht stillschweigend ignoriert werden.")
                db.execute("UPDATE images SET status=?,draft=NULL WHERE id=?", ("pending" if body.get("restore") else "ignored", row["id"]))
            return self.send({"ok": True})
        if path == "/api/install":
            model_id = body["model_id"]
            ENGINE.profile(model_id)
            return self.send(ENGINE.jobs.submit("Modell installieren", lambda progress: ENGINE.install(model_id, body.get("device", "cpu"), progress)))
        if path == "/api/predict":
            model_id = body["model_id"]
            ENGINE.profile(model_id)
            criteria = setting("criteria", DEFAULT_CRITERIA)
            return self.send(ENGINE.jobs.submit("KI-Bewertung", lambda progress: ENGINE.predict(body["image_id"], model_id, body["crop"], criteria, progress)))
        if path == "/api/train":
            model_id = body["model_id"]
            ENGINE.profile(model_id)
            return self.send(ENGINE.jobs.submit("Lernmodell aktualisieren", lambda progress: ENGINE.train(model_id, progress)))
        if path == "/api/active":
            model_id = body["model_id"]
            ENGINE.profile(model_id)
            return self.send(ENGINE.jobs.submit("Unsichere Bilder suchen", lambda progress: ENGINE.active_queue(model_id, progress)))
        self.send({"error": "Nicht gefunden"}, status=404)


def serve(port=8765):
    initialize()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Oberflächenlabor läuft lokal: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        ENGINE.stop()
