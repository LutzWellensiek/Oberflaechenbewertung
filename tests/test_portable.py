import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.hardware import check_memory
from app.portable import interpreter, model_configuration, relocate_database, worker_command

ROOT = Path(__file__).resolve().parents[1]


class PortableTests(unittest.TestCase):
    def test_relocation_preserves_labels_and_external_sources(self):
        old, new = ROOT / "old place", ROOT / "new place"
        db = sqlite3.connect(":memory:")
        try:
            db.executescript("""CREATE TABLE images(id INTEGER,source TEXT,stored_path TEXT);
                CREATE TABLE labels(id INTEGER,score INTEGER,crop TEXT,output_path TEXT);
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE model_runs(id TEXT,recipe TEXT);
                CREATE TABLE label_history(id INTEGER,snapshot TEXT);""")
            db.execute("INSERT INTO images VALUES (1,'E:/camera/picture.jpg',?)", (str(old / "data/originals/picture.jpg"),))
            db.execute("INSERT INTO labels VALUES (1,73,'[1,2,3,4]',?)", (str(old / "data/output/picture_73.jpg"),))
            external = str(ROOT / "external export" / "other.jpg")
            db.execute("INSERT INTO labels VALUES (2,25,'[0,0,3,4]',?)", (external,))
            db.execute("INSERT INTO settings VALUES ('output',?)", (json.dumps(str(old / "data/output")),))
            db.execute("INSERT INTO model_runs VALUES ('run',?)", (json.dumps({"samples": [{
                "stored_path": str(old / "data/originals/picture.jpg"), "output_path": external}]}),))
            db.execute("INSERT INTO label_history VALUES (1,?)", (json.dumps({"output_path": str(old / "data/history/previous.jpg")}),))
            relocate_database(db, old, new)
            self.assertEqual(db.execute("SELECT source,stored_path FROM images").fetchone(),
                             ("E:/camera/picture.jpg", str(new / "data/originals/picture.jpg")))
            self.assertEqual(db.execute("SELECT score,crop,output_path FROM labels WHERE id=1").fetchone(),
                             (73, "[1,2,3,4]", str(new / "data/output/picture_73.jpg")))
            self.assertEqual(db.execute("SELECT output_path FROM labels WHERE id=2").fetchone()[0], external)
            self.assertEqual(json.loads(db.execute("SELECT value FROM settings WHERE key='output'").fetchone()[0]), str(new / "data/output"))
            sample = json.loads(db.execute("SELECT recipe FROM model_runs").fetchone()[0])["samples"][0]
            self.assertEqual(sample["stored_path"], str(new / "data/originals/picture.jpg"))
            self.assertEqual(sample["output_path"], external)
            self.assertEqual(json.loads(db.execute("SELECT snapshot FROM label_history").fetchone()[0])["output_path"],
                             str(new / "data/history/previous.jpg"))
        finally:
            db.close()

    def test_portable_interpreter_and_hardware_fallback(self):
        with tempfile.TemporaryDirectory(prefix="portable-test-", dir=ROOT) as temporary:
            root = Path(temporary)
            (root / "portable.json").write_text("{}")
            profile = root / "environments/qwen3vl-2b"
            profile.mkdir(parents=True)
            config = {"device": "cuda", "quantization": "nf4"}
            (profile / "ready.json").write_text(json.dumps(config))
            with patch("app.portable.nvidia_available", return_value=False):
                result = model_configuration(root, "qwen3vl-2b")
            self.assertEqual(result["device"], "cpu")
            self.assertIsNone(result["quantization"])
            self.assertEqual(json.loads((profile / "ready.json").read_text()), config)
            self.assertEqual(interpreter(root, "qwen3vl-2b"), root / "runtime/python.exe")
            command = worker_command(root, "qwen3vl-2b")
            self.assertEqual(command[1:3], ["-I", "-S"])
            self.assertIn(str(root / "portable_bootstrap.py"), command)

    def test_cpu_bfloat16_guard_keeps_memory_margin(self):
        with patch("app.hardware.available_ram_gb", return_value=6.1):
            check_memory("qwen3vl-2b", "cpu", "bfloat16")
            with self.assertRaises(RuntimeError):
                check_memory("qwen3vl-2b", "cpu")
        with patch("app.hardware.available_ram_gb", return_value=5.9):
            with self.assertRaises(RuntimeError):
                check_memory("qwen3vl-2b", "cpu", "bfloat16")
        with patch("app.hardware.available_ram_gb", return_value=2.6):
            check_memory("qwen3vl-2b", "cpu", "vision_float32")
        with patch("app.hardware.available_ram_gb", return_value=2.4):
            with self.assertRaises(RuntimeError):
                check_memory("qwen3vl-2b", "cpu", "vision_float32")


if __name__ == "__main__":
    unittest.main()
