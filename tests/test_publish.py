import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publish_github", ROOT / "scripts/publish_github.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class ReleaseTests(unittest.TestCase):
    def test_generated_working_copies_accept_different_owner_without_global_trust(self):
        before = subprocess.run(["git", "config", "--global", "--get-all", "safe.directory"],
                                capture_output=True).stdout
        with tempfile.TemporaryDirectory(prefix="git-owner-test-", dir=ROOT) as temporary:
            work = Path(temporary)
            directories = [work / name for name in ("repository", "upload-checkout", "unrelated")]
            for directory in directories:
                directory.mkdir()
                subprocess.run(["git", "init", str(directory)], capture_output=True, check=True)
            with patch.object(publisher, "WORK", work), \
                    patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}):
                for directory in directories[:2]:
                    rejected = subprocess.run(["git", "-c", "safe.directory=", "rev-parse", "--show-toplevel"],
                                              cwd=directory, capture_output=True, text=True)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("dubious ownership", rejected.stderr)
                    accepted = publisher.git("rev-parse", "--show-toplevel", cwd=directory, capture=True)
                    self.assertEqual(Path(accepted.stdout.strip()).resolve(), directory.resolve())
                    local = publisher.git("config", "--local", "--get-all", "safe.directory",
                                          cwd=directory, capture=True, check=False)
                    self.assertEqual(local.returncode, 1)
                unrelated = publisher.git("rev-parse", "--show-toplevel", cwd=directories[2], capture=True, check=False)
                self.assertNotEqual(unrelated.returncode, 0)
                self.assertIn("dubious ownership", unrelated.stderr)
        after = subprocess.run(["git", "config", "--global", "--get-all", "safe.directory"],
                               capture_output=True).stdout
        self.assertEqual(before, after)

    def test_preparation_splits_exact_bytes_and_never_stages_private_folders(self):
        with tempfile.TemporaryDirectory(prefix="release-test-", dir=ROOT) as temporary:
            root = Path(temporary)
            (root / "dist").mkdir()
            payload = bytes(range(256)) * 10
            exe = root / "dist/Oberflaechenbewertung-CPU.exe"
            exe.write_bytes(payload)
            expected = hashlib.sha256(payload).hexdigest()
            exe.with_name(exe.name + ".sha256").write_text(expected + "  " + exe.name)
            (root / "dist/BITTE_LESEN.txt").write_text("Anleitung")
            for name in publisher.SOURCES:
                (root / name).write_text("source")
            for folder in ("app", "web", "scripts", "tests"):
                (root / folder).mkdir()
                (root / folder / "test.txt").write_text("source")
            (root / "data").mkdir()
            (root / "data/private.sqlite3").write_text("private")
            with patch.object(publisher, "ROOT", root), patch.object(publisher, "PART_SIZE", 1500), \
                    patch.object(publisher, "RELEASE_FILES", root / "dist/github-release"), \
                    patch.object(publisher, "WORK", root / "build/publication"), patch.object(publisher, "git"):
                manifest, preview = publisher.prepare()
                parts = [root / "dist/github-release" / item["name"] for item in manifest["parts"]]
                self.assertEqual(b"".join(part.read_bytes() for part in parts), payload)
                self.assertEqual(manifest["exe_sha256"], expected)
                for item, path in zip(manifest["parts"], parts):
                    self.assertEqual(item["sha256"], publisher.sha256(path))
                self.assertFalse((preview / "data").exists())
                self.assertFalse((preview / "dist").exists())
                batch = (root / "dist/github-release/Zusammenfuegen.bat").read_text()
                self.assertIn(expected, batch)
                self.assertIn("copy /b", batch)
                self.assertNotIn("python", batch.lower())

    def test_upload_accepts_only_github_upload_host(self):
        client = publisher.GitHub("test-secret")
        with self.assertRaises(RuntimeError):
            client.upload({"upload_url": "https://other.example.com/upload{?name}"}, Path("unused"))

    @unittest.skipUnless(os.name == "nt", "Windows download launcher")
    def test_windows_join_launcher_joins_and_verifies_without_python(self):
        with tempfile.TemporaryDirectory(prefix="join-test-", dir=ROOT) as temporary:
            root = Path(temporary)
            payload = bytes(range(256)) * 40
            name = "test-package.exe"
            (root / (name + ".part01")).write_bytes(payload[:3000])
            (root / (name + ".part02")).write_bytes(payload[3000:])
            batch = root / "Zusammenfuegen.bat"
            batch.write_text(publisher.join_batch(hashlib.sha256(payload).hexdigest(), name), encoding="ascii", newline="")
            environment = dict(os.environ)
            environment["PATH"] = str(Path(os.environ["WINDIR"]) / "System32")
            result = subprocess.run(["cmd.exe", "/d", "/c", str(batch)], cwd=root, input="\n",
                                    capture_output=True, text=True, timeout=30, env=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((root / name).read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
