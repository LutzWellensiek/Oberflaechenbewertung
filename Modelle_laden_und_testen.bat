@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Modelle werden einzeln installiert und auf den gespeicherten Bewertungen getestet.
echo Die Downloads benoetigen mehrere GB Speicher. Ergebnisse stehen danach unter Modelle ^& Lernen.
".venv\Scripts\python.exe" stop.py
".venv\Scripts\python.exe" -u model_suite.py --install
if errorlevel 1 (
  echo Installation oder Test abgebrochen. Siehe Meldungen oben.
  pause
  exit /b 1
)
echo Durchlauf beendet. Die Anwendung wird wieder gestartet.
start "" "%~dp0Start.bat"
pause
