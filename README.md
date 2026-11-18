# Oberflächenbewertung

Vollständig lokale Windows-Anwendung zum Zuschneiden, Organisieren und Bewerten von Oberflächenbildern. Eigene Qualitätskriterien werden anhand bestätigter Nutzerbewertungen gelernt. Originalbilder bleiben unverändert.

## Windows-Version herunterladen

**[Aktuellen Download unter Releases öffnen](https://github.com/LutzWellensiek/Oberflaechenbewertung/releases/latest)**

Auf dem Zielrechner sind weder Python noch Git oder eine Paketinstallation nötig. Die CPU-Version funktioniert ohne NVIDIA-Grafikkarte und nach dem Download vollständig offline.

Die rund 3,7 GB große EXE wird in zwei Teilen bereitgestellt, da GitHub pro Release-Datei weniger als 2 GiB erlaubt. Diese drei Dateien aus dem Release in **denselben Ordner** herunterladen:

1. [Oberflaechenbewertung-CPU.exe.part01](https://github.com/LutzWellensiek/Oberflaechenbewertung/releases/latest/download/Oberflaechenbewertung-CPU.exe.part01)
2. [Oberflaechenbewertung-CPU.exe.part02](https://github.com/LutzWellensiek/Oberflaechenbewertung/releases/latest/download/Oberflaechenbewertung-CPU.exe.part02)
3. [Zusammenfuegen.bat](https://github.com/LutzWellensiek/Oberflaechenbewertung/releases/latest/download/Zusammenfuegen.bat)

`Zusammenfuegen.bat` doppelklicken. Windows setzt die EXE zusammen und prüft ihre SHA256-Prüfsumme. Danach `Oberflaechenbewertung-CPU.exe` doppelklicken. Die EXE packt beim ersten Start daneben den etwa 4,9 GB großen Ordner `Oberflaechenbewertung` aus. Hier dauerte das Auspacken rund zwei Minuten. Anschließend öffnet sich die lokale Oberfläche im vorhandenen Browser.

Für Downloadteile, zusammengesetzte EXE und ausgepacktes Programm zusammen vorübergehend etwa **13 GB Festplattenplatz** vorsehen. Nach erfolgreichem Zusammenfügen können die beiden Downloadteile entfernt werden. EXE und ausgepackter Programmordner benötigen zusammen etwa 9 GB.

Die von GitHub automatisch angebotenen **Source code**-Archive enthalten den Quellcode. Die ausführbare Windows-Version steht in den oben genannten Release-Dateien. [GitHubs Größenlimit für Release-Dateien](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

## Enthaltene Modelle

| Auswahl | Bewertungsgrundlage |
|---|---|
| Manuell + Basislernmodell | Farb- und Texturmerkmale, sofort ohne großes Vision-Modell nutzbar |
| DINOv2 Small | Eigene vortrainierte Bildmerkmale mit angelerntem Qualitätsmodell |
| Qwen3-VL 2B | Eigener vortrainierter Bildencoder mit angelerntem Qualitätsmodell |

Die portable Fassung enthält die Laufzeit, CPU-Bibliotheken und Modellgewichte bereits. DINOv2 ist die sparsamste Vision-Variante. Qwen lädt im normalen CPU-Ablauf nur seinen Bildencoder; die zusätzliche Sprachgenerierung wird ausgelassen. Dadurch werden keine Grafikkarte und keine CUDA-Installation benötigt. Für den Bildencoder prüft die Anwendung vorsichtig etwa 2,5 GB freien RAM. Die vollständigen Sprachmodellgewichte sind mitgeliefert; optionale unangepasste Rohbewertungen benötigen etwa 6 GB freien RAM im BF16-Betrieb und können deutlich länger dauern. Die Speichergrenzen sind Schätzungen.

Die großen Modellgewichte bleiben eingefroren. Pro Modell lernt ein eigener kleiner Kopf die Hauptqualitätsstufe und Feinbewertung. Hauptstufen werden stärker gewichtet als kleine Punktunterschiede. Mit passenden zusätzlichen Beispielen kann sich die Qualität verbessern; eine Verbesserung ist nicht garantiert.

## Datenstand und Sicherung

**Das erste Paket enthält 36 gespeicherte Nutzerbewertungen samt Originalkopien, zugeschnittenen Bildern und passenden CPU-Lernständen.** Die Kriterien sind individuell angelernt und keine allgemeingültige Qualitätsnorm.

Ausschnitt, Bewertung und Datum gehören zum gemeinsamen Datensatz. Beim Modellwechsel muss kein Bild neu zugeschnitten oder bewertet werden. Die Modelle führen getrennte Lernstände.

Neue Bewertungen werden dauerhaft unter `Oberflaechenbewertung/data` gespeichert. Diesen Ordner regelmäßig sichern. Beim Umziehen den vollständigen ausgepackten Programmordner mitnehmen. Ein erneuter Start derselben Paket-EXE überschreibt vorhandene Nutzerdaten nicht; die EXE selbst bleibt der ursprüngliche Datenstand. Interne Bildpfade werden beim Start an den neuen Programmort angepasst. Bewusst extern gewählte Ausgabeordner bleiben extern und müssen zusätzlich gesichert werden.

Zum Beenden im Startfenster auf **Anwendung beenden** klicken. Das Schließen des Browsers beendet den lokalen Server nicht.

## Arbeitsablauf

Ordner auswählen → Bild anzeigen → Rechteck markieren → Ausschnitt prüfen → KI-Vorschlag anfordern → Hauptbewertung wählen → optional feinjustieren → Datum prüfen → Speichern → nächstes Bild.

- Bewertung von 10 bis 100, mit deutlich anwählbaren Zehnerstufen und optionalen Einer-Schritten.
- EXIF-Aufnahmedatum, ersatzweise Datei- oder Importdatum; manuell korrigierbar. Ursprüngliche Datumsinformationen bleiben erhalten.
- JPEG-Export nach `YYYY-MM-DD_HH-MM-SS_Originalname_Score.jpg`, mit eindeutigen Zusätzen bei Namenskonflikten.
- Originaldateien werden nicht überschrieben. Bearbeitungsentwürfe bleiben gespeichert.
- Ignorierte Bilder gehen nicht ins Training ein. Bestätigte Bewertungen sind schreibgeschützt.
- Unsichere Bilder lassen sich bevorzugt zur manuellen Bewertung vorschlagen.

## Modellvergleich

Unter **Modelle & Lernen → Installierte Modelle testen** werden die festen Nutzerbewertungen für alle drei Modelle verwendet. Das jeweilige Testbild und byteidentische Originalkopien bleiben bei Training und Skalierung ausgeschlossen. Bewertungsgrundlage, mittlere absolute Punktabweichung und richtige Hauptstufe werden getrennt ausgewiesen. Unangepasste Sprachmodell-Rohbewertungen sind eine optionale zusätzliche Auswertung.

Einzelergebnisse und Fortschritt bleiben nach Neuladen sichtbar. Nutzerbewertungen und Bildexporte werden durch einen Test nicht verändert. Mit wenigen Bildern und engem Scorebereich bleiben Vergleiche vorläufig. Punktabweichung und Stufentreffer messen unterschiedliche Eigenschaften.

## Lokale Ablage

```text
data/surface.sqlite3        Bilder, Daten, Entwürfe, Bewertungen, Vorhersagen, Fehler
data/originals/             Unveränderte Importkopien
data/output/                Bewertete JPEG-Ausschnitte
data/profiles/<modell>/     Eigene Lernköpfe
data/benchmarks/             Vergleichsergebnisse als JSON und CSV
environments/<modell>/      Modellgewichte und Konfiguration
runtime/                    Portable Python-Laufzeit und CPU-Bibliotheken
logs/                       Serverprotokolle der portablen Version
```

## Entwicklung und Paketbau

Der Quellcode benötigt Python 3.12 (64 Bit). `Setup.bat` richtet die Entwicklungsumgebung ein, `Start.bat` startet die Anwendung. Für Anwender der portablen Version sind diese Schritte nicht erforderlich.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workflow tests.test_portable tests.test_publish -v
node --check web/app.js
.\.venv\Scripts\python.exe scripts/build_portable.py --stage-only
.\.venv\Scripts\python.exe scripts/smoke_portable.py build/portable-cpu
.\.venv\Scripts\python.exe scripts/build_portable.py --archive-only
```

Der Paketbau verwendet die bereits vorhandenen lokalen Python-, CPU-Paket- und Modellinstallationen. Der native Starter wird mit dem Windows-.NET-Compiler erstellt. Die SQLite-Backup-API erzeugt einen konsistenten Datenbankschnappschuss; Bildkopien werden per SHA256 geprüft. Nach erfolgreichem CPU-Test kann der geprüfte Lernstand paketiert werden.

`GitHub_veroeffentlichen.bat` veröffentlicht README und Quellcode in diesem Repository und die vorbereiteten EXE-Teile als Release. Auf dem Entwicklungsrechner werden dafür Git, Internetzugriff und Schreibrechte auf das Repository benötigt. Die vorhandene GitHub-Anmeldung wird über Git Credential Manager verwendet; Zugangsdaten werden weder in Dateien noch im Repository gespeichert. Ein neuer Release wird erst nach Prüfung aller Uploads veröffentlicht. Nach einem Abbruch können bereits korrekt hochgeladene Teile wiederverwendet werden.

Die großen Laufzeiten, Modellgewichte, lokale Datenbanken und Bildordner werden nicht in die Git-Historie eingecheckt. Der ausdrücklich mitgelieferte Datenschnappschuss befindet sich innerhalb der Release-EXE.

## Prüfungen und Fehler

Die zwölf Workflow- und Portabilitätsprüfungen sowie echte CPU-Modellaufrufe aus dem ausgepackten Programmordner waren erfolgreich. Die Start-EXE wurde mit `Windows/System32` als einzigem PATH geprüft; die Python-Module stammen aus dem Paket. Ein erneuter Paketstart ließ die vorhandene Datenbank unverändert. Diese Prüfungen belegen den technischen Ablauf, keine garantierte Bewertungsgenauigkeit.

Fehler stehen unter **Projekteinstellungen → Lokales Fehlerprotokoll**, in `logs/server-error.log` und `environments/<modell>/inference.log`. Bei Startproblemen der Paket-EXE liegt gegebenenfalls `Startfehler.txt` neben ihr. Der Programmordner muss beschreibbar sein. Windows 10/11 (64 Bit) und der in Windows enthaltene .NET Framework 4.x werden vorausgesetzt. Eine Firmenrichtlinie, die eigene Programme sperrt, wird durch die portable Verpackung nicht umgangen.

## Modelle und Lizenzen

- [DINOv2 Small – offizielle Modellkarte](https://huggingface.co/facebook/dinov2-small)
- [Qwen3-VL 2B – offizielle Modellkarte](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct)
- [Python: anwendungslokale Windows-Laufzeiten](https://docs.python.org/3.12/using/windows.html)

Modellkarten und Bibliothekslizenzen werden mitkopiert. Python-Lizenz: `runtime/LICENSE.txt`; Paketlizenzen: `runtime/packages/*.dist-info`. Genaue Modellrevisionen stehen in den jeweiligen `ready.json`-Dateien, die mitgelieferten CPU-Paketversionen in `requirements-resolved.txt`.
