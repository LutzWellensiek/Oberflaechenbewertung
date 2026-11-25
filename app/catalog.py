"""Each profile owns its interpreter, weights and learned head."""
DEFAULT_CRITERIA = "Bewerte die Gleichmäßigkeit und Unversehrtheit der Oberfläche. Achte auf Kratzer, Risse, Poren, Ausbrüche und Flecken. 0 = nicht bewertbar; 10 = sehr schlecht, 99 = sehr gut. Die individuellen Nutzerbewertungen haben Vorrang. Ähnliche Bilder können mehrere plausible Qualitätsstufen haben. Zeitlicher Verlauf ist keine monotone Qualitätsregel."
MODELS = {
    "manual": {"name": "Farb- & Texturmerkmale", "kind": "baseline", "repo": None,
               "description": "Sofort offline nutzbar. Lernt aus Farb- und Texturmerkmalen; kein vortrainiertes Vision-Modell.",
               "download": "Kein Download", "memory": "Geringer Speicherbedarf"},
    "dinov2-small": {"name": "DINOv2 Small", "kind": "embedding", "repo": "facebook/dinov2-small",
                     "transformers": "4.57.6", "description": "Vortrainierte Bildmerkmale mit eigenem Qualitätsstufen- und Feinbewertungsmodell. Benötigt bestätigte Beispiele.",
                     "download": "ca. 90 MB Gewichte + Python/PyTorch", "memory": "Für die vorhandene 4-GB-GPU am ehesten geeignet"},
    "qwen3vl-2b": {"name": "Qwen3-VL 2B", "kind": "vlm", "repo": "Qwen/Qwen3-VL-2B-Instruct",
                   "features": "vision",
                   "transformers": "4.57.6", "class": "Qwen3VLForConditionalGeneration",
                   "description": "Vortrainierter Qwen-Bildencoder. Eigene Qualitätsköpfe lernen aus ausgewählten Urteilen; deren Begründung nennt ähnliche Beispiele, ohne Sprachgenerierung.",
                   "download": "ca. 4–5 GB Gewichte + Laufzeit", "memory": "CPU: grob 10–14 GB RAM; GPU kann 4 GB überschreiten"},
}
