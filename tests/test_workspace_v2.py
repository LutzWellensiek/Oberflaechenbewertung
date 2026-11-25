"""Regression tests for human labels, migration, model recipes and data leakage."""
import json
from contextlib import closing
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import urllib.request

import numpy as np
from PIL import Image

from app import benchmark, diagnostics, engine, imaging, learning, model_lab, server, storage, workspace

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workspace-v2-test-", dir=ROOT)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.patches = [patch.object(module, "DATA", self.data) for module in
                        (storage, imaging, learning, workspace, model_lab, benchmark, engine, server)]
        for p in self.patches:
            p.start()
        self.model = engine.Engine()
        self.engine_patch = patch.object(engine, "ENGINE", self.model)
        self.server_patch = patch.object(server, "ENGINE", self.model)
        self.engine_patch.start()
        self.server_patch.start()
        storage.initialize()

    def tearDown(self):
        self.model.jobs.executor.shutdown(wait=True)
        self.model.stop()
        self.engine_patch.stop()
        self.server_patch.stop()
        for p in reversed(self.patches):
            p.stop()
        self.temporary.cleanup()

    def images(self, count=5, duplicate=False):
        folder = self.root / "Charge Ä" / "Serie 1"
        folder.mkdir(parents=True)
        for i in range(count):
            im = Image.new("RGB", (100, 80), (20 + i * 35, 30 + i * 20, 80))
            exif = Image.Exif()
            exif[36867] = f"2026:10:{i + 1:02d} 09:00:00"
            im.save(folder / f"surface-{i}.jpg", exif=exif)
        if duplicate:
            (folder / "surface-copy.jpg").write_bytes((folder / "surface-0.jpg").read_bytes())
        imaging.import_folder(self.root / "Charge Ä")
        return workspace.list_images({"status": ["pending"], "order": ["name"]})

    def save(self, row, score, **extra):
        image = storage.image_row(row["id"])
        return workspace.save_human_label({"image_id": row["id"], "score": score,
                 "crop": [10, 10, 90, 70], "date": image["chosen_date"],
                 "date_source": image["date_source"], **extra})

    def train(self, rows, task="quality", **extra):
        return model_lab.create_run(self.model, {"name": "Mein Versuch", "base_model": "manual", "task": task,
                                    "image_ids": [r["id"] for r in rows], **extra})

    def test_revision_zero_grouped_export_and_reversible_removal(self):
        rows = self.images(2)
        result = self.save(rows[0], 33, alternatives=[20, 50])
        output = Path(result["saved"])
        self.assertEqual(output.parent.relative_to(self.data / "output"), Path("Charge Ä/Serie 1"))
        source = Path(storage.image_row(rows[0]["id"])["stored_path"])
        original = source.read_bytes()
        self.assertEqual(result["revision"], 1)
        workspace.change_image({"image_id": rows[0]["id"], "action": "reopen"})
        self.assertFalse(output.exists())
        self.assertEqual(len(list((self.data / "history").glob("*.jpg"))), 1)
        self.assertTrue(source.exists())
        revised = self.save(rows[0], 99, expected_revision=1)
        self.assertEqual(revised["revision"], 2)
        with self.assertRaises(ValueError):
            self.save(rows[0], 30)
        workspace.change_image({"image_id": rows[0]["id"], "action": "remove"})
        self.assertEqual(storage.label_dataset()["count"], 0)
        self.assertFalse(Path(revised["saved"]).exists())
        workspace.change_image({"image_id": rows[0]["id"], "action": "restore"})
        self.assertEqual(storage.image_row(rows[0]["id"])["status"], "rated")
        self.assertEqual(storage.label_dataset()["count"], 1)
        self.assertEqual(source.read_bytes(), original)
        self.save(rows[1], 0)
        self.assertEqual(storage.label_dataset()["count"], 1)
        with storage.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM label_history WHERE image_id=?", (rows[0]["id"],)).fetchone()[0], 5)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        for bad in (-1, 1, 9, 100, 10.5, True):
            with self.assertRaises(ValueError):
                workspace.validate_score(bad)

    def test_training_selection_and_no_automatic_training_on_save(self):
        rows = self.images(5)
        for row, score in zip(rows, [20, 50, 80, 0, 33]):
            self.save(row, score)
        self.assertEqual(self.model.jobs.active(), [])
        workspace.change_image({"image_id": rows[4]["id"], "action": "training", "enabled": False})
        run = self.train(rows)
        recipe = model_lab.run_row(run["id"])["recipe"]
        self.assertEqual([r["id"] for r in recipe["samples"]], [r["id"] for r in rows[:3]])
        self.assertFalse(model_lab.run_is_stale(run["id"]))
        workspace.change_image({"image_id": rows[0]["id"], "action": "remove"})
        self.assertTrue(model_lab.run_is_stale(run["id"]))
        with self.assertRaises(ValueError):
            model_lab.evaluate_run(self.model, {"run_id": run["id"], "image_ids": [rows[1]["id"]]})

    def test_group_exclusion_temporal_context_gap_filter_and_ai_provenance(self):
        rows = self.images(4, duplicate=True)
        for row, score in zip(rows, [20, 50, 80, 33, 20]):
            self.save(row, score, alternatives=[50] if score == 20 else [])
        run = self.train(rows, temporal=True)
        with storage.connection() as db:
            labels_before = [tuple(r) for r in db.execute("SELECT * FROM labels ORDER BY id")]
        report = model_lab.evaluate_run(self.model, {"run_id": run["id"], "image_ids": [rows[0]["id"]]})
        result = report["results"][0]
        self.assertEqual(result["train_count"], 3)  # Both original and identical copy withheld.
        self.assertTrue(result["excluded_test_image"])
        self.assertTrue(result["temporal"])
        self.assertNotIn(rows[0]["id"], [r["image_id"] for r in result["neighbors"]])
        self.assertNotIn(rows[-1]["id"], [r["image_id"] for r in result["neighbors"]])
        self.assertTrue(10 <= result["score"] <= 99)
        self.assertEqual(len(result["alternatives"]), 3)
        visible = workspace.list_images({"status": ["rated"], "run_id": [run["id"]], "min_gap": ["0"]})
        self.assertEqual([r["id"] for r in visible], [rows[0]["id"]])
        exported = model_lab.export_ai({"run_id": run["id"], "image_id": rows[0]["id"]})
        self.assertIn("_KI", Path(exported["saved"]).name)
        self.assertIn("[surface-0]", Path(exported["saved"]).name)
        with storage.connection() as db:
            self.assertEqual(labels_before, [tuple(r) for r in db.execute("SELECT * FROM labels ORDER BY id")])
        workspace.change_image({"image_id": rows[0]["id"], "action": "reopen"})
        self.save(rows[0], 10, expected_revision=1)
        self.assertEqual(workspace.list_images({"status": ["rated"], "run_id": [run["id"]], "min_gap": ["0"]}), [])

    def test_region_and_relevance_are_separate_learned_tasks(self):
        rows = self.images(5)
        for row, score in zip(rows[:4], [20, 50, 80, 33]):
            self.save(row, score)
        roi = self.train(rows[:4], task="region")
        region = model_lab.evaluate_run(self.model, {"run_id": roi["id"], "image_ids": [rows[4]["id"]]})["results"][0]
        self.assertEqual(region["crop"], [10, 10, 90, 70])
        for index, row in enumerate(rows[:4]):
            workspace.change_image({"image_id": row["id"], "action": "relevance", "value": index % 2})
        relevant = self.train(rows[:4], task="relevance")
        result = model_lab.evaluate_run(self.model, {"run_id": relevant["id"], "image_ids": [rows[4]["id"]]})["results"][0]
        self.assertIsInstance(result["relevant"], bool)
        self.assertIsNone(result["score"])
        filtered = workspace.list_images({"status": ["all"], "run_id": [relevant["id"]], "ai_relevance": [str(int(result["relevant"]))]})
        self.assertEqual([r["id"] for r in filtered], [rows[4]["id"]])

    def test_random_order_is_stable_and_folder_filters_are_exact(self):
        rows = self.images(5)
        one = workspace.list_images({"status": ["pending"], "seed": ["one"]})
        repeat = workspace.list_images({"status": ["pending"], "seed": ["one"]})
        two = workspace.list_images({"status": ["pending"], "seed": ["two"]})
        self.assertEqual([r["id"] for r in one], [r["id"] for r in repeat])
        self.assertNotEqual([r["id"] for r in one], [r["id"] for r in two])
        self.assertEqual(workspace.list_images({"status": ["pending"], "folder": ["missing"]}), [])
        self.assertEqual(len(workspace.list_images({"status": ["pending"], "folder": [rows[0]["origin_folder"]]})), 5)

    def test_http_new_routes_and_stale_autosave_do_not_replace_revised_label(self):
        rows = self.images(2)
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{http.server_port}"
        def request(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json", "X-Surface-Token": server.TOKEN})
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.load(response)
        try:
            self.save(rows[0], 33)
            request("/api/image-state", {"image_id": rows[0]["id"], "action": "reopen"})
            self.save(rows[0], 44, expected_revision=1)
            request("/api/image-state", {"image_id": rows[0]["id"], "action": "reopen"})
            late = request("/api/draft", {"image_id": rows[0]["id"], "expected_revision": 1,
                    "draft": {"crop": [0, 0, 20, 20], "score": 99}})
            self.assertTrue(late["ignored"])
            self.assertIsNone(storage.image_row(rows[0]["id"])["draft"])
            self.assertEqual(request("/api/model-runs"), [])
            state = request("/api/state")
            self.assertIn("removed", state["counts"])
            self.assertEqual(state["folders"], [str(Path("Charge Ä/Serie 1"))])
        finally:
            http.shutdown()
            http.server_close()
            thread.join()

    def test_changed_draft_invalidates_ai_export_and_uses_exact_evaluated_crop(self):
        rows = self.images(3)
        self.save(rows[0], 20)
        self.save(rows[1], 80)
        run = self.train(rows[:2])
        with storage.connection() as db:
            db.execute("UPDATE images SET draft=? WHERE id=?", (json.dumps({"crop": [20, 20, 60, 50]}), rows[2]["id"]))
        report = model_lab.evaluate_run(self.model, {"run_id": run["id"], "image_ids": [rows[2]["id"]]})
        self.assertEqual(report["results"][0]["input_crop"], [20, 20, 60, 50])
        exported = model_lab.export_ai({"run_id": run["id"], "image_id": rows[2]["id"]})
        with Image.open(exported["saved"]) as image:
            self.assertEqual(image.size, (40, 30))
        with storage.connection() as db:
            db.execute("UPDATE images SET draft=? WHERE id=?", (json.dumps({"crop": [0, 0, 90, 70]}), rows[2]["id"]))
        with self.assertRaises(ValueError):
            model_lab.export_ai({"run_id": run["id"], "image_id": rows[2]["id"]})
        row = next(r for r in workspace.list_images({"status": ["pending"], "run_id": [run["id"]]}) if r["id"] == rows[2]["id"])
        self.assertTrue(row["result_stale"])

    def test_reexport_preserves_human_label_and_nested_restore_state(self):
        rows = self.images(2)
        saved = self.save(rows[0], 33)
        old = Path(saved["saved"])
        flat = self.data / "output" / old.name
        old.replace(flat)
        with storage.connection() as db:
            db.execute("UPDATE labels SET output_path=? WHERE image_id=?", (str(flat), rows[0]["id"]))
        signature = storage.label_dataset()
        self.assertEqual(workspace.rebuild_exports()["updated"], 1)
        self.assertEqual(storage.label_dataset(), signature)
        self.assertFalse(flat.exists())
        with storage.connection() as db:
            current = Path(db.execute("SELECT output_path FROM labels WHERE image_id=?", (rows[0]["id"],)).fetchone()[0])
            legacy = current.with_name(current.name.replace("[", "").replace("]", ""))
            current.replace(legacy)
            db.execute("UPDATE labels SET output_path=? WHERE image_id=?", (str(legacy), rows[0]["id"]))
        self.assertEqual(workspace.rebuild_exports()["updated"], 1)
        self.assertFalse(legacy.exists())
        self.assertEqual(storage.label_dataset(), signature)
        self.assertEqual(workspace.rebuild_exports()["updated"], 0)
        workspace.change_image({"image_id": rows[0]["id"], "action": "ignore"})
        workspace.change_image({"image_id": rows[0]["id"], "action": "remove"})
        workspace.change_image({"image_id": rows[0]["id"], "action": "restore"})
        self.assertEqual(storage.image_row(rows[0]["id"])["status"], "ignored")
        workspace.change_image({"image_id": rows[0]["id"], "action": "restore"})
        self.assertEqual(storage.image_row(rows[0]["id"])["status"], "rated")

    def test_portable_snapshot_includes_named_profiles_grouped_exports_and_history(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("builder", ROOT / "scripts/build_portable.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        rows = self.images(3)
        self.save(rows[0], 20)
        self.save(rows[1], 80)
        run = self.train(rows[:2])
        workspace.change_image({"image_id": rows[0]["id"], "action": "reopen"})
        target = self.root / "portable-copy"
        target.mkdir()
        with patch.object(builder, "ROOT", self.root):
            self.assertEqual(builder.snapshot(target), 2)
        self.assertTrue((target / "data/profiles" / ("lab-" + run["id"]) / "head.npz").is_file())
        self.assertEqual(len(list((target / "data/history").glob("*.jpg"))), 1)
        with closing(sqlite3.connect(target / "data/surface.sqlite3")) as db:
            for path, in db.execute("SELECT stored_path FROM images UNION ALL SELECT output_path FROM labels"):
                self.assertTrue(Path(path).is_relative_to(target))
                self.assertTrue(Path(path).is_file())
            recipe = json.loads(db.execute("SELECT recipe FROM model_runs WHERE id=?", (run["id"],)).fetchone()[0])
            self.assertTrue(all(Path(r["stored_path"]).is_relative_to(target) for r in recipe["samples"]))


class MigrationTests(unittest.TestCase):
    def test_legacy_database_backup_preserves_labels_features_and_references(self):
        with tempfile.TemporaryDirectory(prefix="migration-v2-test-", dir=ROOT) as temp:
            data = Path(temp)
            with closing(sqlite3.connect(data / "surface.sqlite3")) as db:
                db.executescript('''CREATE TABLE images(id INTEGER PRIMARY KEY,source TEXT NOT NULL,relative_name TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,stored_path TEXT NOT NULL,width INTEGER,height INTEGER,dates TEXT,
                    chosen_date TEXT,date_source TEXT,warning TEXT,status TEXT,draft TEXT,imported_at TEXT);
                    CREATE TABLE labels(id INTEGER PRIMARY KEY,image_id INTEGER UNIQUE REFERENCES images(id),score INTEGER CHECK(score BETWEEN 10 AND 100),
                    coarse INTEGER,crop TEXT,captured_at TEXT,date_source TEXT,output_path TEXT,model_id TEXT,prediction_id INTEGER,created_at TEXT);
                    CREATE TABLE features(label_id INTEGER REFERENCES labels(id),model_id TEXT,vector TEXT,PRIMARY KEY(label_id,model_id));
                    INSERT INTO images VALUES(7,'C:/source/Charge/a.jpg','a.jpg','abc','C:/data/a.jpg',100,80,'{}','2026-10-01T00:00:00','manual','','rated',NULL,'2026-10-01');
                    INSERT INTO labels VALUES(9,7,91,90,'[0,0,90,70]','2026-10-01T00:00:00','manual','C:/output/a.jpg','manual',NULL,'2026-10-01');
                    INSERT INTO features VALUES(9,'texture-v1','[1,2,3]');''')
                db.commit()
                before = db.execute("SELECT * FROM labels").fetchall()
            with patch.object(storage, "DATA", data):
                storage.initialize()
                storage.initialize()
                with storage.connection() as db:
                    self.assertEqual([tuple(r)[:11] for r in db.execute("SELECT * FROM labels")], before)
                    self.assertEqual(db.execute("SELECT vector FROM features").fetchone()[0], "[1,2,3]")
                    self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                    db.execute("UPDATE labels SET score=0 WHERE id=9")
            backups = list((data / "backups").glob("*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as db:
                self.assertEqual(db.execute("SELECT * FROM labels").fetchall(), before)


if __name__ == "__main__":
    unittest.main()
