import argparse
import threading
import webbrowser
import json
import urllib.request
from app.server import serve

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    # Reusing an existing local instance makes repeated Start.bat clicks harmless.
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/state", timeout=1) as response:
            existing = json.load(response)
        if "models" in existing and "token" in existing and "counts" in existing:
            print(f"Anwendung läuft bereits: http://127.0.0.1:{args.port}")
            if not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{args.port}")
            raise SystemExit(0)
    except (OSError, ValueError):
        pass
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    try:
        serve(args.port)
    except KeyboardInterrupt:
        print("Anwendung beendet.")
