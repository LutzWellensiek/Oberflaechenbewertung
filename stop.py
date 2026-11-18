import json
import urllib.request

try:
    base = "http://127.0.0.1:8765"
    with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
        state = json.load(response)
    request = urllib.request.Request(base + "/api/shutdown", data=b"{}",
                                     headers={"X-Surface-Token": state["token"], "Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=5).close()
    print("Anwendung wird beendet. Laufende Lern-/Installationsaufträge dürfen noch abschließen.")
except OSError:
    print("Keine laufende Anwendung auf Port 8765 gefunden.")
