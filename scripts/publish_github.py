"""Publish the prepared portable build using Git + Git Credential Manager, no gh/pip needed.

Run normally in Explorer via GitHub_veroeffentlichen.bat. Credentials are used only
in memory. Large files go to Release assets, never to the Git history.
"""
import argparse
from datetime import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OWNER, REPO = "LutzWellensiek", "Oberflaechenbewertung"
REMOTE = f"https://github.com/{OWNER}/{REPO}.git"
RELEASE_FILES = ROOT / "dist" / "github-release"
WORK = ROOT / "build" / "github-publication"
PART_SIZE = 1900 * 1024 * 1024  # Safely below GitHub's 2 GiB asset limit.
SOURCES = [".gitignore", "README.md", "requirements.txt", "run.py", "stop.py", "setup_project.py",
           "install_model.py", "model_suite.py", "model_worker.py", "portable_bootstrap.py",
           "Start.bat", "Setup.bat", "Beenden.bat", "Modelle_laden_und_testen.bat", "GitHub_veroeffentlichen.bat"]


def sha256(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def git(*args, cwd=None, capture=False, input=None, check=True):
    command = ["git"]
    if cwd is not None:
        directory = Path(cwd).resolve()
        generated = {(WORK / name).resolve() for name in ("repository", "upload-checkout")}
        if directory in generated:
            # These copies are created by the publisher, sometimes under the
            # sandbox account and later reused by the interactive Windows user.
            # Trust only this exact directory for this invocation; never write a
            # global exception or disable ownership checks for other repositories.
            command += ["-c", "safe.directory=", "-c", "safe.directory=" + directory.as_posix()]
    return subprocess.run([*command, *args], cwd=cwd, input=input, text=True, encoding="utf-8",
                          errors="replace", stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, check=check)


def join_batch(expected, exe_name):
    # The download user needs Windows only, not Python, Git, 7-Zip, or gh.
    return f'''@echo off
setlocal
cd /d "%~dp0"
echo Oberflaechenbewertung - EXE zusammensetzen
if not exist "{exe_name}.part01" goto missing
if not exist "{exe_name}.part02" goto missing
if exist "{exe_name}" goto verify
echo Bitte warten. Die zwei Dateien werden zusammengefuegt.
copy /b "{exe_name}.part01"+"{exe_name}.part02" "{exe_name}.joining" >nul
if errorlevel 1 goto failed
move /y "{exe_name}.joining" "{exe_name}" >nul
:verify
echo Pruefe SHA256. Dies kann etwas dauern.
"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -Command "$actual=(Get-FileHash -LiteralPath './{exe_name}' -Algorithm SHA256).Hash; if ($actual -ne '{expected}') {{ Write-Host 'Pruefsumme stimmt nicht. Bitte beide Teile erneut herunterladen.'; exit 1 }}"
if errorlevel 1 goto failed
echo Fertig. Starte {exe_name} per Doppelklick.
echo Die EXE entpackt beim ersten Start ihren Programmordner neben sich.
pause
exit /b 0
:missing
echo Bitte zuerst BEIDE .part-Dateien in denselben Ordner herunterladen.
pause
exit /b 1
:failed
echo Die EXE konnte nicht korrekt erstellt oder geprueft werden.
echo Eine fehlerhafte EXE bitte entfernen und das Zusammenfuegen erneut starten.
pause
exit /b 1
'''.replace("\n", "\r\n")


def prepare():
    RELEASE_FILES.mkdir(parents=True, exist_ok=True)
    exe = ROOT / "dist" / "Oberflaechenbewertung-CPU.exe"
    expected = (exe.with_name(exe.name + ".sha256")).read_text().split()[0]
    manifest_path = RELEASE_FILES / "release-manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if old.get("exe_sha256") == expected and all((RELEASE_FILES / item["name"]).is_file()
            and (RELEASE_FILES / item["name"]).stat().st_size == item["bytes"] for item in old.get("parts", [])) and len(old.get("parts", [])) == 2:
        # Still verify the actual file hashes before publication below.
        manifest = old
    else:
        print("Teile die EXE fuer GitHub-Releases auf …", flush=True)
        complete, parts = hashlib.sha256(), []
        with exe.open("rb") as source:
            index = 1
            while source.tell() < exe.stat().st_size:
                name = exe.name + f".part{index:02}"
                path = RELEASE_FILES / name
                part_hash, count = hashlib.sha256(), 0
                with path.open("wb") as target:
                    while count < PART_SIZE:
                        chunk = source.read(min(4 * 1024 * 1024, PART_SIZE - count))
                        if not chunk:
                            break
                        target.write(chunk); part_hash.update(chunk); complete.update(chunk); count += len(chunk)
                parts.append({"name": name, "bytes": count, "sha256": part_hash.hexdigest()})
                print(name, count, "Bytes", flush=True)
                index += 1
        if complete.hexdigest() != expected:
            raise RuntimeError("Die EXE stimmt nicht mit ihrer gespeicherten Pruefsumme ueberein.")
        if len(parts) != 2:
            raise RuntimeError("Dieses Paket erwartet genau zwei Downloadteile.")
        manifest = {"exe": exe.name, "exe_bytes": exe.stat().st_size, "exe_sha256": expected,
                    "created_at": datetime.now().isoformat(), "parts": parts}
    (RELEASE_FILES / "Zusammenfuegen.bat").write_text(join_batch(expected, exe.name), encoding="ascii", newline="")
    shutil.copy2(exe.with_name(exe.name + ".sha256"), RELEASE_FILES)
    shutil.copy2(ROOT / "dist/BITTE_LESEN.txt", RELEASE_FILES)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    preview = WORK / "repository"
    preview.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        shutil.copy2(ROOT / name, preview / name)
    for folder in ("app", "web", "scripts", "tests"):
        shutil.copytree(ROOT / folder, preview / folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(RELEASE_FILES / "Zusammenfuegen.bat", preview / "Zusammenfuegen.bat")
    shutil.copy2(manifest_path, preview / manifest_path.name)
    if not (preview / ".git").exists():
        git("init", "-b", "publication-preview", cwd=preview)
    git("add", "--all", cwd=preview)
    if git("diff", "--cached", "--quiet", cwd=preview, check=False).returncode:
        git("commit", "-m", "Portable CPU-Version und GitHub-Downloadanleitung vorbereiten", cwd=preview)
    print("Vorbereitet:", RELEASE_FILES, flush=True)
    return manifest, preview


def credential():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    result = git("credential", "fill", capture=True, input="protocol=https\nhost=github.com\n\n", check=False)
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or not fields.get("password"):
        raise RuntimeError("GitHub-Anmeldung fehlt. Bitte im von Git geoeffneten Browser anmelden und den Starter erneut ausfuehren.")
    return fields["password"]


class GitHub:
    def __init__(self, token):
        self.token = token

    def api(self, method, path, data=None, missing=False):
        payload = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request("https://api.github.com" + path, data=payload, method=method,
                  headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                           "Content-Type": "application/json", "User-Agent": "SurfaceLab-Publisher",
                           "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if missing and exc.code == 404:
                return None
            raise RuntimeError(f"GitHub-API antwortet mit HTTP {exc.code}. Bitte Repository-Zugriff und Berechtigungen pruefen.") from None

    def upload(self, release, file):
        endpoint = urllib.parse.urlsplit(release["upload_url"].split("{")[0])
        if endpoint.scheme != "https" or endpoint.hostname != "uploads.github.com":
            raise RuntimeError("Unerwartetes GitHub-Uploadziel.")
        size = file.stat().st_size
        connection = http.client.HTTPSConnection(endpoint.hostname, timeout=180)
        try:
            connection.putrequest("POST", endpoint.path + "?" + urllib.parse.urlencode({"name": file.name}))
            connection.putheader("Authorization", "Bearer " + self.token)
            connection.putheader("User-Agent", "SurfaceLab-Publisher")
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Content-Length", str(size))
            connection.endheaders()
            sent, last = 0, 0
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    connection.send(chunk); sent += len(chunk)
                    if time.monotonic() - last >= 5:
                        print(f"{file.name}: {sent / size:.0%}", flush=True); last = time.monotonic()
            response = connection.getresponse()
            body = response.read()
            if response.status != 201:
                raise RuntimeError(f"Upload fehlgeschlagen: HTTP {response.status}. Der Release bleibt als Entwurf erhalten.")
            return json.loads(body)
        finally:
            connection.close()


def publish(manifest, preview):
    # Fail before credential prompts or changes when run inside the network sandbox.
    try:
        urllib.request.urlopen("https://github.com", timeout=12).close()
    except OSError:
        raise RuntimeError("Kein GitHub-Internetzugriff. Bitte GitHub_veroeffentlichen.bat selbst normal im Explorer starten.") from None
    github = GitHub(credential())
    prefix = f"/repos/{OWNER}/{REPO}"
    repository = github.api("GET", prefix)
    if not repository.get("permissions", {}).get("push"):
        raise RuntimeError("Das angemeldete GitHub-Konto hat keinen Schreibzugriff auf dieses Repository.")
    # Verify every locally prepared asset before any remote changes.
    assets = [RELEASE_FILES / item["name"] for item in manifest["parts"]]
    assets += [RELEASE_FILES / name for name in ("Zusammenfuegen.bat", "BITTE_LESEN.txt", manifest["exe"] + ".sha256", "release-manifest.json")]
    hashes = {file.name: sha256(file) for file in assets}
    for item in manifest["parts"]:
        if hashes[item["name"]] != item["sha256"]:
            raise RuntimeError("Ein vorbereiteter Downloadteil wurde veraendert. Bitte neu vorbereiten.")
    checkout = WORK / "upload-checkout"
    if not (checkout / ".git").exists():
        git("clone", REMOTE, str(checkout))
    if git("remote", "get-url", "origin", cwd=checkout, capture=True).stdout.strip() != REMOTE:
        raise RuntimeError("Unerwartetes Git-Remote im Uploadordner.")
    branch = repository["default_branch"]
    if git("rev-parse", "--verify", "HEAD", cwd=checkout, capture=True, check=False).returncode:
        git("checkout", "-B", branch, cwd=checkout)
    else:
        git("checkout", branch, cwd=checkout)
        git("pull", "--ff-only", "origin", branch, cwd=checkout)
    files = git("ls-files", cwd=preview, capture=True).stdout.splitlines()
    for name in files:
        destination = checkout / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(preview / name, destination)
    git("add", "--all", "--", *files, cwd=checkout)
    if git("diff", "--cached", "--quiet", cwd=checkout, check=False).returncode:
        git("commit", "-m", "Portable Windows-Version mit README und Downloadanleitung", cwd=checkout)
    git("push", "--set-upstream", "origin", branch, cwd=checkout)
    commit = git("rev-parse", "HEAD", cwd=checkout, capture=True).stdout.strip()
    marker = "Paket-SHA256: " + manifest["exe_sha256"]
    tag, number = "v1.0.0-cpu", 1
    pending_path = WORK / "pending-release.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else {}
    resumed = github.api("GET", prefix + f"/releases/{pending['id']}", missing=True) if pending.get("sha256") == manifest["exe_sha256"] else None
    while True:
        release = resumed or github.api("GET", prefix + "/releases/tags/" + tag, missing=True)
        if release is None or marker in (release.get("body") or ""):
            break
        resumed = None
        number += 1; tag = "v1.0.0-cpu-" + str(number)
    notes = ("Portable CPU-Version für Windows 10/11 (64 Bit), ohne Python-Installation.\n\n"
             "Enthält Basislernmodell, DINOv2 Small, Qwen3-VL 2B und den Schnappschuss der gespeicherten Nutzerbewertungen.\n\n"
             "**Download:** Beide `.part`-Dateien und `Zusammenfuegen.bat` in denselben Ordner laden. "
             "Die BAT doppelklicken; sie erstellt und prüft die vollständige EXE. Anschließend die EXE starten. "
             "Beim ersten Start wird der Programmordner daneben ausgepackt. Zusammenfügen und Auspacken brauchen vorübergehend etwa 13 GB Platz einschließlich Downloads. "
             "Vorhandene Bewertungen im ausgepackten Datenordner werden nicht überschrieben.\n\n"
             "Qwen nutzt im normalen CPU-Ablauf seinen Bildencoder mit dem angelernten Bewertungskopf.\n\n" + marker)
    if release is None:
        release = github.api("POST", prefix + "/releases", {"tag_name": tag, "target_commitish": commit,
                     "name": "Portable Windows-Version (CPU)", "body": notes, "draft": True, "prerelease": False})
    tag = release["tag_name"]
    pending_path.write_text(json.dumps({"id": release["id"], "tag": tag, "sha256": manifest["exe_sha256"]}, indent=2), encoding="utf-8")
    for file in assets:
        existing = github.api("GET", prefix + f"/releases/{release['id']}/assets?per_page=100")
        match = next((asset for asset in existing if asset["name"] == file.name), None)
        if match:
            if match.get("state") == "uploaded" and match["size"] == file.stat().st_size and match.get("digest") == "sha256:" + hashes[file.name]:
                print("Bereits korrekt hochgeladen:", file.name, flush=True)
                continue
            if release["draft"] and match.get("state") != "uploaded":
                github.api("DELETE", prefix + f"/releases/assets/{match['id']}")
            else:
                raise RuntimeError("Vorhandene Release-Datei weicht ab; sie wird nicht ueberschrieben: " + file.name)
        uploaded = github.upload(release, file)
        if uploaded["size"] != file.stat().st_size or uploaded.get("digest") != "sha256:" + hashes[file.name]:
            raise RuntimeError("GitHub bestaetigt keine passende Upload-Pruefsumme. Release bleibt unveroeffentlicht.")
    release = github.api("PATCH", prefix + f"/releases/{release['id']}", {"draft": False, "make_latest": "true"})
    (WORK / "published.json").write_text(json.dumps({"url": release["html_url"], "commit": commit,
                  "tag": tag, "assets_verified": True}, indent=2), encoding="utf-8")
    print("\nVeroeffentlicht:", release["html_url"], flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    manifest, preview = prepare()
    if not args.prepare_only:
        publish(manifest, preview)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("\nNicht veroeffentlicht:", str(error), file=sys.stderr)
        raise SystemExit(1)
