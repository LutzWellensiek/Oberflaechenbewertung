"""Real HTTP + SQLite + image export integration checks, no model downloads."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory(prefix="surface-test-", dir=ROOT)
os.environ["SURFACE_DATA_DIR"] = str(Path(TEMP.name) / "data")

import numpy as np
from PIL import Image
from app.server import Handler, ENGINE
from app.storage import DATA, connection, initialize
from app.learning import coarse_class, fit_head, head_path, head_status, predict_head, weighted_error
from app.imaging import date_info, opened
from app.benchmark import cross_validate, metrics
from model_worker import parse_score
from install_model import retry_download


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Installed user models must never be loaded by the synthetic test suite.
        cls.runtime_patch = patch("app.engine.ROOT", Path(TEMP.name))
        cls.runtime_patch.start()
        initialize()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.token = cls.request("/api/state")["token"]

    @classmethod
    def tearDownClass(cls):
        ENGINE.jobs.executor.shutdown(wait=True)
        cls.server.shutdown()
        cls.server.server_close()
        ENGINE.stop()
        cls.runtime_patch.stop()

    @classmethod
    def request(cls, path, body=None, headers=None, raw=False):
        h = {"X-Surface-Token": getattr(cls, "token", ""), "Content-Type": "application/json"}
        h.update(headers or {})
        request = urllib.request.Request(cls.base + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read() if raw else json.load(response)

    def job(self, job_id):
        for _ in range(300):
            result = self.request("/api/jobs/" + job_id)
            if result["status"] == "done":
                return result["result"]
            if result["status"] == "error":
                self.fail(result["message"])
            time.sleep(.02)
        self.fail("Job timeout")

    def test_complete_workflow(self):
        folder = Path(TEMP.name) / "input"
        for sub, color in (("a", (40, 50, 60)), ("b", (190, 200, 210))):
            (folder / sub).mkdir(parents=True)
            exif = Image.Exif()
            exif[36867] = "2026:11:17 09:35:21"
            Image.new("RGB", (160, 100), color).save(folder / sub / "Oberflaeche01.jpg", exif=exif)
        Image.new("RGB", (80, 120), "gray").save(folder / "skip.png")
        (folder / "broken.jpg").write_bytes(b"not an image")
        original = folder / "a" / "Oberflaeche01.jpg"
        digest = hashlib.sha256(original.read_bytes()).digest()
        modified = original.stat().st_mtime_ns
        result = self.job(self.request("/api/import", {"folder": str(folder)})["job"])
        self.assertEqual(result["imported"], 3)
        self.assertEqual(len(result["errors"]), 1)
        duplicate = self.job(self.request("/api/import", {"folder": str(folder)})["job"])
        self.assertEqual(duplicate["duplicates"], 3)
        rows = self.request("/api/images?order=name")
        ids = [r["id"] for r in rows]
        first = self.request("/api/image?id=" + str(ids[0]))
        self.assertEqual(first["chosen_date"], "2026-11-17T09:35:21")
        crop = [10, 20, 130, 90]
        draft = {"crop": crop, "score": 73, "date": first["chosen_date"], "date_source": "exif_original"}
        self.request("/api/draft", {"image_id": ids[0], "draft": draft})
        initialize()  # Same persistence path used when the process is started again.
        self.assertEqual(self.request("/api/image?id=" + str(ids[0]))["draft"], draft)
        cold = self.job(self.request("/api/predict", {"image_id": ids[0], "model_id": "manual", "crop": crop})["job"])
        self.assertIsNone(cold["score"])
        saved = self.request("/api/save", {"image_id": ids[0], "model_id": "manual", **draft, "prediction_id": cold["prediction_id"]})
        output = Path(saved["saved"])
        self.assertEqual(output.name, "2026-11-17_09-35-21_[Oberflaeche01]_73.jpg")
        with Image.open(output) as image:
            self.assertEqual(image.size, (120, 70))
        self.assertEqual(hashlib.sha256(original.read_bytes()).digest(), digest)
        self.assertEqual(original.stat().st_mtime_ns, modified)
        # A browser autosave can finish just after the confirmation request.
        late_draft = self.request("/api/draft", {"image_id": ids[0], "draft": draft})
        self.assertTrue(late_draft["ignored"])
        self.assertIsNone(self.request("/api/image?id=" + str(ids[0]))["draft"])
        comparison = self.job(self.request("/api/predict", {"image_id":ids[0], "model_id":"manual", "crop":crop})["job"])
        self.assertTrue(comparison["comparison"])
        self.assertEqual(comparison["actual_score"], 73)
        self.assertIsNone(comparison["learned"])  # Its own label must be withheld.
        self.assertEqual(self.request("/api/image?id=" + str(ids[0]))["label"]["score"], 73)
        # Identical output name must receive a suffix, never overwrite.
        second = self.request("/api/image?id=" + str(ids[1]))
        predicted = self.job(self.request("/api/predict", {"image_id": ids[1], "model_id": "manual", "crop": crop})["job"])
        self.assertGreaterEqual(predicted["score"], 10)
        self.assertLessEqual(predicted["score"], 100)
        saved2 = self.request("/api/save", {"image_id": ids[1], "model_id": "manual", "crop": crop, "score": 73,
                     "date": second["chosen_date"], "date_source": "exif_original", "prediction_id": predicted["prediction_id"]})
        self.assertTrue(saved2["saved"].endswith("_73.jpg"))
        self.assertNotEqual(Path(saved["saved"]).parent, Path(saved2["saved"]).parent)
        self.request("/api/ignore", {"image_id": ids[2]})
        self.assertEqual(self.request("/api/state")["counts"], {"total": 3, "pending": 0, "rated": 2, "ignored": 1, "removed": 0})
        with connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 2)
        self.assertEqual(len(list((DATA / "output").rglob("*.jpg"))), 2)
        csv = self.request("/api/export", raw=True).decode("utf-8-sig")
        self.assertIn("main_stage", csv)
        self.assertNotIn("skip.png", csv)
        self.request("/api/ignore", {"image_id": ids[2], "restore": True})
        ranking = self.job(self.request("/api/active", {"model_id": "manual"})["job"])
        self.assertEqual(ranking[0]["id"], ids[2])
        for bad_score in (9, 100, 101, 10.5, True):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/save", {"image_id": ids[2], "score": bad_score, "crop": [0, 0, 20, 20], "date": "2026-11-17T00:00:00"})
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/draft", {"image_id": ids[2], "draft": {"crop": [-1, 0, 20, 20]}})
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/save", {"image_id": ids[0], "score": 73})

        # Labels and exported crops are shared human truth; switching models must
        # train a separate head without changing, duplicating or relabeling them.
        with connection() as db:
            saved_labels = [dict(r) for r in db.execute("SELECT * FROM labels ORDER BY id")]
        saved_files = {str(p): hashlib.sha256(p.read_bytes()).digest() for p in (DATA / "output").rglob("*.jpg")}
        waiting = self.job(self.request("/api/settings", {"model_id": "qwen3vl-2b"})["sync_job"])
        self.assertTrue(waiting["waiting_for_install"])
        self.assertEqual(self.request("/api/state")["dataset"]["count"], 2)

        # Isolate the pretrained encoder, but exercise the real feature cache,
        # saved human labels, and independent learned vision head.
        original_status = ENGINE.status
        def installed_profile(model_id, dataset=None):
            status = original_status(model_id, dataset)
            if model_id == "qwen3vl-2b":
                status["installed"] = True
            return status
        def encoded(model_id, request):
            if request.get("operation") == "embedding":
                with Image.open(request["path"]) as im:
                    color = im.convert("RGB").getpixel((0, 0))[0] / 255
                return {"vector": [color, request["crop"][2]/100, request["crop"][3]/100]}
            return {"score": 90, "reason": "Generic raw score"}
        with patch.object(ENGINE, "status", side_effect=installed_profile), patch.object(ENGINE, "infer", side_effect=encoded) as encoder:
            transferred = self.job(self.request("/api/settings", {"model_id": "qwen3vl-2b"})["sync_job"])
            self.assertEqual(transferred["count"], 2)
            self.assertEqual(encoder.call_count, 2)
            self.assertIn(":vision-", ENGINE.feature_key("qwen3vl-2b"))
            self.assertEqual(head_status("qwen3vl-2b")["count"], 2)
            before = head_path("qwen3vl-2b").stat().st_mtime_ns
            again = self.job(self.request("/api/settings", {"model_id": "qwen3vl-2b"})["sync_job"])
            self.assertTrue(again["cached"])
            self.assertEqual(head_path("qwen3vl-2b").stat().st_mtime_ns, before)
            initialize()
            with connection() as db:
                self.assertEqual([dict(r) for r in db.execute("SELECT * FROM labels ORDER BY id")], saved_labels)
            self.assertEqual({str(p): hashlib.sha256(p.read_bytes()).digest() for p in (DATA / "output").rglob("*.jpg")}, saved_files)
            # A new human label makes the other profile stale, and switching back
            # automatically catches it up using the same saved ground truth.
            third = self.request("/api/image?id=" + str(ids[2]))
            saved3 = self.request("/api/save", {"image_id": ids[2], "model_id": "manual", "score": 47,
                       "crop": [0, 0, 40, 40], "date": third["chosen_date"], "date_source": "manual"})
            models = {m["id"]: m for m in self.request("/api/state")["models"]}
            self.assertFalse(models["qwen3vl-2b"]["head"]["up_to_date"])
            self.assertEqual(models["qwen3vl-2b"]["head"]["available"], 3)
            caught_up = self.job(self.request("/api/settings", {"model_id": "qwen3vl-2b"})["sync_job"])
            self.assertEqual(caught_up["count"], 3)
            self.assertTrue(ENGINE.status("qwen3vl-2b")["head"]["up_to_date"])
            comparison = self.job(self.request("/api/predict", {"image_id":ids[0], "model_id":"qwen3vl-2b", "crop":crop})["job"])
            self.assertEqual(comparison["score_source"], "learned_vision")
            self.assertEqual(comparison["score"], comparison["learned"]["score"])
            self.assertEqual(comparison["raw"]["score"], 90)
            self.assertLess(comparison["learned"]["count"], 20)
            self.assertEqual(comparison["actual_score"], 73)

        benchmark = self.job(self.request("/api/benchmark", {"model_ids": ["manual", "qwen3vl-2b"]})["job"])
        self.assertTrue(benchmark["labels_unchanged"])
        self.assertTrue(benchmark["exports_unchanged"])
        self.assertEqual(benchmark["models"][0]["cv"]["n"], 3)
        self.assertEqual(benchmark["models"][1]["status"], "not_installed")
        self.assertEqual(self.request("/api/state")["benchmark"]["id"], benchmark["id"])
        self.assertEqual([m["id"] for m in self.request("/api/state")["models"]],
                         ["manual", "dinov2-small", "qwen3vl-2b"])
        with self.assertRaises(urllib.error.HTTPError) as removed:
            self.request("/api/benchmark", {"model_ids":["moondream2"]})
        self.assertEqual(removed.exception.code, 400)

    def test_double_benchmark_request_reuses_running_job(self):
        release = threading.Event()
        def controlled(*args):
            release.wait(timeout=5)
            return {"models":[]}
        with patch("app.server.run_benchmark", side_effect=controlled) as benchmark:
            first = self.request("/api/benchmark", {"model_ids":["manual"]})
            try:
                second = self.request("/api/benchmark", {"model_ids":["manual"]})
                self.assertEqual(first["job"], second["job"])
                self.assertTrue(second["already_running"])
                snapshot = self.request("/api/benchmark")
                self.assertEqual(snapshot["jobs"][0]["id"], first["job"])
            finally:
                release.set()
            self.job(first["job"])
            self.assertEqual(benchmark.call_count, 1)

    def test_request_security(self):
        for headers in ({"X-Surface-Token": "bad"}, {"Origin": "https://example.com"}, {"Host": "evil.example"}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/settings", {"model_id": "manual"}, headers=headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/../app/storage.py")
        html = self.request("/", raw=True).decode()
        self.assertIn("sourceCanvas", html)

    def test_hierarchical_learning(self):
        self.assertEqual(coarse_class(73), 6)
        self.assertEqual(coarse_class(100), 9)
        self.assertLess(weighted_error(70, 75), weighted_error(40, 80))
        vectors = [[-1, 0], [-.9, .1], [-1.1, -.1], [1, 0], [.9, .1], [1.1, -.1]]
        fit_head("test", vectors, [21, 22, 23, 81, 82, 83], list(range(6)))
        low = predict_head("test", np.array([-1., 0]))
        high = predict_head("test", np.array([1., 0]))
        self.assertEqual(low["coarse"], 20)
        self.assertEqual(high["coarse"], 80)

    def test_score_parser(self):
        self.assertEqual(parse_score('<think>ignore</think>{"score":73,"reason":"fine"}')["score"], 73)
        self.assertEqual(parse_score('{"score":42,"reason":"Markierung {A} ist sichtbar."}')["score"], 42)
        partial = parse_score('```json\n{"score":42,"reason":"Gleichmäßige Oberfläche mit wenigen')
        self.assertEqual(partial["score"], 42)
        self.assertTrue(partial["truncated"])
        self.assertIn("gekürzt", partial["reason"])
        for text in ('{"score":101}', '{"score":true}', 'looks good'):
            with self.assertRaises(ValueError):
                parse_score(text)

    def test_date_fallback_and_orientation(self):
        image = Path(TEMP.name) / "rotated.jpg"
        exif = Image.Exif()
        exif[36867] = "2099:01:01 00:00:00"
        exif[274] = 6
        Image.new("RGB", (40, 20), "red").save(image, exif=exif)
        dates, source, warning = date_info(image)
        self.assertEqual(dates["exif_original_raw"], "2099:01:01 00:00:00")
        self.assertNotEqual(source, "exif_original")
        self.assertIn("unplausibel", warning)
        self.assertEqual(opened(image).size, (20, 40))

    def test_validation_excludes_original_image_duplicates(self):
        from app.learning import fit_parameters
        vectors = [np.array([1.,0]), np.array([1.,.1]), np.array([2.,0]), np.array([3.,0])]
        with patch("app.benchmark.fit_parameters", wraps=fit_parameters) as fit:
            result = cross_validate(vectors, [10, 70, 30, 40], ["same", "same", "other", "third"])
        self.assertEqual(result[0]["train_count"], 2)
        self.assertEqual(result[1]["train_count"], 2)
        self.assertEqual(fit.call_args_list[0].args[1], [30, 40])
        self.assertEqual(fit.call_args_list[1].args[1], [30, 40])
        metric = metrics([{"actual":30,"raw":35}, {"actual":10,"raw":20}, {"actual":70}], "raw")
        self.assertEqual(metric["n"], 2)
        self.assertEqual(metric["mae"], 7.5)
        self.assertEqual(metric["stage_accuracy"], .5)

    def test_error_history_persists_and_redacts_tokens(self):
        error = self.request("/api/client-error", {"message": "Example hf_exampleSecret123 token", "details": "test"})
        initialize()
        records = self.request("/api/errors")
        saved = next(r for r in records if r["id"] == error["error_id"])
        self.assertNotIn("hf_exampleSecret123", saved["message"])
        self.assertIn("Token entfernt", saved["message"])

    def test_interrupted_download_is_retried_without_removing_partial_file(self):
        from http.client import IncompleteRead
        partial = Path(TEMP.name) / "resume.incomplete"
        partial.write_bytes(b"downloaded-")
        attempts = []
        def download():
            attempts.append(partial.read_bytes())
            if len(attempts) == 1:
                raise IncompleteRead(b"data", 10)
            with partial.open("ab") as stream:
                stream.write(b"remaining")
            return "complete"
        with patch("builtins.print"):
            self.assertEqual(retry_download(download, pause=lambda delay: None), "complete")
        self.assertEqual(attempts, [b"downloaded-", b"downloaded-"])
        self.assertEqual(partial.read_bytes(), b"downloaded-remaining")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        TEMP.cleanup()
