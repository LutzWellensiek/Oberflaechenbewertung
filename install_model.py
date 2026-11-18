"""Run under the chosen profile's Python. Online only during explicit installation."""
import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from app.catalog import MODELS

ROOT = Path(__file__).resolve().parent


def transient_download_error(exc):
    if "10013" in str(exc):
        return False
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if code is not None:
        return code in (408, 429, 500, 502, 503, 504)
    kinds = {"ChunkedEncodingError", "IncompleteRead", "ProtocolError", "ConnectionError", "ConnectionResetError",
             "ConnectionAbortedError", "ReadTimeout", "ConnectTimeout", "ReadError", "RemoteProtocolError",
             "ConnectError", "TimeoutException", "TimeoutError", "URLError"}
    return type(exc).__name__ in kinds or any(cls.__name__ in kinds for cls in type(exc).__mro__)


def retry_download(action, attempts=4, pause=time.sleep):
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:
            if attempt == attempts or not transient_download_error(exc):
                raise
            print(f"Verbindungsabbruch beim Gewichtsdownload: {exc}. "
                  f"Versuch {attempt+1}/{attempts}; vorhandene Teildateien werden fortgesetzt.", flush=True)
            pause(min(5 * attempt, 20))


def torch_installed(device):
    try:
        suffix = "+cu126" if device == "cuda" else "+cpu"
        return (metadata.version("torch") == "2.7.1" + suffix and
                metadata.version("torchvision") == "0.22.1" + suffix)
    except metadata.PackageNotFoundError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=[k for k in MODELS if k != "manual"])
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    model = MODELS[args.model]
    profile = ROOT / "environments" / args.model
    if Path(sys.prefix).resolve() != profile.resolve():
        raise SystemExit("Installation muss mit dem Python dieser Modellumgebung gestartet werden.")
    os.environ.update(HF_HOME=str(profile / "cache"), HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_SYMLINKS_WARNING="1")
    os.environ["PIP_CACHE_DIR"] = str(ROOT / "environments" / "package-cache")
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    marker = profile / "ready.json"

    def pip(*packages, extra=()):
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *packages, *extra], check=True)

    reuse_torch = torch_installed(args.device)
    print(("Verwende vorhandenes PyTorch" if reuse_torch else "Installiere PyTorch") + " (" + args.device + ") …", flush=True)
    index = "https://download.pytorch.org/whl/" + ("cu126" if args.device == "cuda" else "cpu")
    try:
        if not reuse_torch:
            urllib.request.urlopen(index + "/torch/", timeout=12).close()
        urllib.request.urlopen("https://huggingface.co/api/models/" + model["repo"], timeout=12).close()
    except OSError as exc:
        raise SystemExit("Downloadzugriff nicht möglich: " + str(exc) +
                         "\nBei WinError 10013: Anwendung beenden und den lokalen Starter selbst im Explorer starten.")
    marker.unlink(missing_ok=True)
    if not reuse_torch:
        pip("torch==2.7.1", "torchvision==0.22.1", extra=("--index-url", index, "--force-reinstall"))
    pip("transformers==" + model["transformers"], "accelerate>=1.6,<2", "Pillow>=10.4,<13",
        "numpy>=1.26,<3", "einops>=0.8,<1", "safetensors>=0.5", "sentencepiece>=0.2")
    quantization = None
    if args.device == "cuda" and args.model == "qwen3vl-2b":
        print("Richte speichersparende 4-Bit-Ausführung für Qwen3-VL ein …", flush=True)
        pip("bitsandbytes==0.50.2")
        quantization = "nf4"
    if args.device == "cuda":
        subprocess.run([sys.executable, "-c", "import torch; assert torch.cuda.is_available(), 'CUDA ist nicht verfügbar'; print('CUDA:',torch.cuda.get_device_name(0))"], check=True)
    from huggingface_hub import HfApi, snapshot_download
    print("Ermittle feste Modellrevision …", flush=True)
    revision = HfApi().model_info(model["repo"], revision=model.get("revision", "main")).sha
    print("Lade Gewichte und Prozessor lokal: " + model["repo"], flush=True)
    weights = profile / "weights"
    # snapshot_download resumes .incomplete files by default. Some requests errors
    # (notably ChunkedEncodingError) escape the library's own retry loop.
    retry_download(lambda: snapshot_download(model["repo"], revision=revision, local_dir=str(weights),
                      ignore_patterns=["*.bin", "*.h5", "*.msgpack", "*.onnx", "*.tflite", "*.gguf"]))
    # A smoke load of the config catches unsupported architectures without loading GBs into RAM.
    from transformers import AutoConfig, AutoImageProcessor, AutoProcessor
    if model.get("remote_code"):
        from app.local_code import prepare_local_code
        from transformers.utils.hub import HF_MODULES_CACHE
        prepare_local_code(weights, HF_MODULES_CACHE)
    if args.model == "moondream2":
        # The custom model obtains this separate tokenizer in its constructor.
        # Cache it during the authorized online installation for later offline use.
        from huggingface_hub import hf_hub_download
        retry_download(lambda: hf_hub_download("moondream/starmie-v1", "tokenizer.json"))
    AutoConfig.from_pretrained(str(weights), local_files_only=True, trust_remote_code=model.get("remote_code", False))
    if model["kind"] == "embedding":
        AutoImageProcessor.from_pretrained(str(weights), local_files_only=True)
    elif not model.get("remote_code"):
        AutoProcessor.from_pretrained(str(weights), local_files_only=True)
    (profile / "requirements-resolved.txt").write_text(subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True), encoding="utf-8")
    marker.write_text(json.dumps({"repo": model["repo"], "revision": revision, "device": args.device,
                                  "quantization": quantization,
                                  "installed_at": datetime.now().isoformat()}, indent=2), encoding="utf-8")
    print("Installation vollständig. Inferenz läuft ab jetzt ausschließlich offline.", flush=True)


if __name__ == "__main__":
    main()
