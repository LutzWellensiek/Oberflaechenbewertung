@echo off
setlocal
cd /d "%~dp0"
echo Veroeffentliche Oberflaechenbewertung auf GitHub.
echo README und Quellcode kommen ins Repository; die EXE-Teile in einen Release.
echo Das Paket enthaelt auch deine 36 gespeicherten Bildbewertungen.
echo Falls GitHub eine Anmeldung verlangt, bitte im Browser selbst anmelden.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -I -S -X utf8 "scripts\publish_github.py"
) else if exist "dist\Oberflaechenbewertung\runtime\python.exe" (
  "dist\Oberflaechenbewertung\runtime\python.exe" -I -S -X utf8 "scripts\publish_github.py"
) else (
  echo Die lokale Python-Laufzeit wurde nicht gefunden. Bitte den Starter im Projektordner ausfuehren.
  pause
  exit /b 1
)
if errorlevel 1 (
  echo Upload nicht abgeschlossen. Die konkreten Meldungen stehen oben.
  echo Erfolgreiche Teildateien bleiben im Release-Entwurf und werden beim naechsten Start wiederverwendet.
  pause
  exit /b 1
)
echo Fertig. README und Download sind auf GitHub verfuegbar.
pause
