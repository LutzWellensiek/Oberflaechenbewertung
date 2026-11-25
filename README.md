# Oberflächenlabor

Lokale Windows-Anwendung zum Sichten, Zuschneiden, Umbenennen und **menschlichen Bewerten** von Oberflächenbildern. Modelltraining und KI-Ergebnisse stehen auf einer eigenen Seite. Originaldateien bleiben erhalten.

## Starten

Im Projektordner `Start.bat` doppelklicken. Die Oberfläche öffnet sich auf `http://127.0.0.1:8765`.

Für die ausgepackte portable Fassung: `dist/Oberflaechenbewertung/Oberflaechenbewertung.exe` starten. Eine bereits laufende Anwendung zuerst beenden und danach neu starten, damit neuer Backend-Code geladen wird.

Die portable Windows-Version steht unter [GitHub Releases](https://github.com/LutzWellensiek/Oberflaechenbewertung/releases/latest). Beide `.part`-Dateien und `Zusammenfuegen.bat` in denselben Ordner herunterladen und die BAT doppelklicken. Sie setzt die große EXE zusammen und prüft ihre SHA256-Prüfsumme. Anschließend die EXE starten; sie entpackt den Programmordner neben sich. Vorübergehend etwa 13 GB freien Speicher vorsehen.

Änderungen im Quellcode aktualisieren bestehende Downloadarchive nicht automatisch. Das lokale ausgepackte Programm lässt sich ohne Austausch von Daten oder Modellgewichten aktualisieren:

```powershell
.\.venv\Scripts\python.exe scripts/update_portable.py dist/Oberflaechenbewertung
```

Die Aktualisierung sichert zuvor den ersetzten Code unter `data/backups`. Zum Beenden `Beenden.bat` oder im portablen Startfenster **Anwendung beenden** verwenden; das Schließen des Browsers beendet den Server nicht.

## Bewerten und zuschneiden

1. Bilderordner importieren; seine Beschriftung und Unterordner bleiben bekannt.
2. Bilder erscheinen standardmäßig in gemischter Reihenfolge. Die Mischung bleibt während der Sitzung stabil; **Neu mischen** erzeugt eine neue Reihenfolge.
3. Direkt ein Rechteck im Original aufziehen: Auch offene Entwürfe und neu geöffnete Bewertungen zeigen sofort das Gesamtbild. Der gewählte Ausschnitt erscheint anschließend groß und lässt sich dort durch erneutes Ziehen direkt verfeinern. Ein Klick auf das kleine Gesamtbild oder **Esc** zeigt wieder alle Bildbereiche.
4. Hauptstufe wählen, optional Einer einstellen und Aufnahmezeitpunkt prüfen.
5. Speichern: JPEG-Ausschnitt und menschliches Urteil werden abgelegt. Dabei wird kein KI-Training gestartet.

**Skala:** 0 = nicht bewertbar. Qualität = ganze Werte von 10 bis 99. 1–9 wählen 10–90; Einer bleiben möglich, z. B. 33 oder 99. Alte gespeicherte Bewertungen werden bei der Migration nicht umgerechnet.

Mehrdeutige Bilder können bis zu drei alternative plausible Werte erhalten. 0 ist keine Qualitätsstufe und wird aus Qualitätstraining ausgeschlossen. Ob ein Bild grundsätzlich zur Aufgabe passt, wird separat mit **passend / unpassend** markiert.

### Tastatur

| Taste | Aktion |
|---|---|
| 1–9 | Hauptstufe 10–90 |
| 0 | Nicht bewertbar |
| A / D | Vorheriges / nächstes Bild |
| W / S | Einen Punkt mehr / weniger |
| < / > oder − / + | Einen Punkt weniger / mehr |
| F | Speichern und weiter |
| C | Ausschnitt per Tastatur beginnen |
| W A S D bei Tastaturauswahl | Ausschnitt bewegen |
| Umschalt + W A S D | Ausschnittgröße ändern |
| F bei Tastaturauswahl | Ausschnitt bestätigen und groß anzeigen; nochmals F speichert |
| Esc | Original zeigen, Eingabefeld verlassen oder Dialog schließen |
| R / I / Entf | Neu bewerten / zurückstellen / entfernen |
| Alt + 1–4 | Arbeitsbereich / Übersicht / Modelle / Einstellungen |
| Tab / Umschalt + Tab | Alle Bedienelemente erreichen |
| F1 | Tastaturhilfe |

In Textfeldern, Zahlenfeldern und Auswahllisten gelten deren normale Tasten. Mit Esc geht der Fokus zurück zum Bild; anschließend speichert F. F funktioniert auch bei fokussiertem Bewertungsregler, Bewertungsknopf und Trainingskästchen. Gedrückthalten von F speichert kein weiteres Bild. Enter bleibt als zusätzliche Bestätigung außerhalb normaler Aktionsknöpfe verfügbar.

## Übersicht, Änderungen und Ausgabe

Die Bildübersicht filtert nach Dateiname, Herkunftsordner, Status, menschlicher Bewertung, Trainingsfreigabe, Eignung und Modellabweichung. Für Abweichungen zuerst einen ausgewerteten Qualitäts-Trainingsstand wählen. Ein Eignungs-Trainingsstand erlaubt den Filter **KI: unpassend**. Geänderte oder veraltete Ergebnisse werden nicht als aktuelle Abweichung verwendet.

Gefilterte Bilder lassen sich gemeinsam auswählen, fürs Training freigeben oder ausschließen, zurückstellen, entfernen und wiederherstellen. **Neu bewerten** öffnet ein bestätigtes Urteil zum Überarbeiten; die nächste Speicherung erhöht seine Version. Frühere Urteile und Exporte bleiben nachvollziehbar erhalten.

Entfernen ist rückgängig machbar. Unter **Entfernt** lassen sich Bilder wiederherstellen. Entfernte und zurückgestellte Bilder gehen nicht ins Training ein. Ihre bisherigen aktiven Exporte werden unter `data/history` archiviert. Importierte Originalkopien bleiben erhalten.

Ausgabe:

```text
Ausgabeordner/Herkunftsordner/Unterordner/YYYY-MM-DD_HH-MM-SS_[Originalname]_33.jpg
Ausgabeordner/Herkunftsordner/Unterordner/YYYY-MM-DD_HH-MM-SS_[Originalname]_50_KI.jpg
```

Namenskonflikte erhalten einen Zusatz. Unter **Einstellungen → Bestehende Exporte nach Herkunftsordnern neu ablegen** können ältere Exporte neu sortiert werden; Bewertungen bleiben dabei gleich.

## Modelle und Ergebnisse

1. In der Übersicht die gewünschten Beispiele auswählen.
2. **Mit Auswahl zum Modellbereich** öffnen.
3. Einen Namen, die Bildmerkmale und die Aufgabe wählen.
4. Trainingsstand anlegen; er dokumentiert genau seine verwendeten Beispiele, Modellrevision und Methode.
5. Ausgewählte, bewertete oder offene Bilder auswerten. Einzelbilder und ähnliche Trainingsbeispiele sind direkt aufrufbar.

Drei getrennte Aufgaben:

- **Qualität:** ordinaler Lernkopf plus ähnliche Beispiele. Alternative menschliche Werte bleiben mehrere plausible Ziele. Optionaler Zeitkontext verwendet Zeitabstände und periodische Merkmale innerhalb des Herkunftsordners. Es gibt keine monotone Regel „später bedeutet bessere/schlechtere Qualität“.
- **Interessanter Ausschnitt:** Bildähnlichkeit überträgt normalisierte Rechtecke bestätigter Beispiele. Das ist ein Vorschlag aus ähnlichen Bildern, kein Objektdetektor. Übernommene Ausschnitte müssen menschlich geprüft und bewertet werden.
- **Passend / unpassend:** lernt aus ausdrücklich markierten positiven und negativen Beispielen. Bilder werden niemals automatisch entfernt.

Farb-/Texturmerkmale funktionieren sofort offline. DINOv2 Small und Qwen3-VL verwenden ihre lokal installierten, eingefrorenen Bildencoder. Nur kleine Lernköpfe beziehungsweise Ähnlichkeitsmodelle werden aus deiner Auswahl gelernt. Modellkarten zeigen, welche Bilder und Kontextinformationen ein Trainingsstand kennt.

Bei Auswertung eines Trainingsbildes werden dieses Original und byteidentische Kopien vom Training ausgeschlossen, einschließlich Skalierung und Zeitreferenz. Das ist eine interne Prüfung, keine unabhängige Produktionsvalidierung. Unsicherheit, Stufengewichte und Ähnlichkeiten sind Heuristiken. Ein zeitlicher Kontext kann helfen, eine bessere Genauigkeit wird damit nicht zugesichert.

KI-Ergebnisse ersetzen kein menschliches Urteil. **Als KI-Datei exportieren** ergänzt `_KI` im Dateinamen und erzeugt kein menschliches Trainingslabel. Änderungen an relevanten Trainingsdaten markieren davon abhängige Stände als veraltet. Ein neuer Stand lässt sich aus derselben Bildauswahl anlegen.

## Daten und Sicherungen

```text
data/surface.sqlite3       Bilder, menschliche Urteile, Historie, Trainingsrezepte, KI-Ergebnisse
data/originals/            Unveränderte Importkopien
data/output/               JPEG-Ausgabe, nach Herkunftsordnern sortiert
data/history/              Frühere Exporte
data/profiles/lab-*/        Benannte Modellstände
data/backups/              Sicherung vor Datenbankmigration / Code-Aktualisierung
environments/<modell>/     Installierte Bildencoder und Modellgewichte
```

Vor der ersten Migration einer älteren Datenbank wird eine vollständige SQLite-Sicherung erstellt. Alte Bewertungen, Ausschnitte und Datumsangaben bleiben unverändert. Regelmäßig den gesamten Datenordner sowie bewusst externe Ausgabeordner sichern; dafür die Anwendung vorher beenden. Beim Umziehen den gesamten portablen Programmordner mitnehmen.

## Entwicklung und Prüfungen

Python 3.12, Pillow und NumPy bilden die lokale Basis. `Setup.bat` richtet die Entwicklungsumgebung ein. Kein externer Dienst ist für den Bewertungsablauf nötig.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workflow tests.test_workspace_v2 tests.test_portable tests.test_publish -v
node --check web/app.js
node tests/test_frontend.cjs
```

Die Frontendprüfung simuliert die echten Ereignisbehandler für Tastatur, Ausschnitt, Navigation, Entwürfe und Speichern ohne zusätzliche Node-Pakete. Sie ersetzt keine visuelle Browserprüfung.

`scripts/build_portable.py` baut die portable Fassung aus lokalen Laufzeiten und Gewichten. Benannte Trainingsstände und Bewertungshistorie werden mitkopiert. `scripts/publish_github.py` veröffentlicht erst bei ausdrücklichem Aufruf; lokale Änderungen sind keine Veröffentlichung.
