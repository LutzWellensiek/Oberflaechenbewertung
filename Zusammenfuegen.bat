@echo off
setlocal
cd /d "%~dp0"
echo Oberflaechenbewertung - EXE zusammensetzen
if not exist "Oberflaechenbewertung-CPU.exe.part01" goto missing
if not exist "Oberflaechenbewertung-CPU.exe.part02" goto missing
if exist "Oberflaechenbewertung-CPU.exe" goto verify
echo Bitte warten. Die zwei Dateien werden zusammengefuegt.
copy /b "Oberflaechenbewertung-CPU.exe.part01"+"Oberflaechenbewertung-CPU.exe.part02" "Oberflaechenbewertung-CPU.exe.joining" >nul
if errorlevel 1 goto failed
move /y "Oberflaechenbewertung-CPU.exe.joining" "Oberflaechenbewertung-CPU.exe" >nul
:verify
echo Pruefe SHA256. Dies kann etwas dauern.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$actual=(Get-FileHash -LiteralPath './Oberflaechenbewertung-CPU.exe' -Algorithm SHA256).Hash; if ($actual -ne 'b4b73116eda4fd5625c6d6704c2de0bf7e50a20053b74e4e9324c5fcf8f16792') { Write-Host 'Pruefsumme stimmt nicht. Bitte beide Teile erneut herunterladen.'; exit 1 }"
if errorlevel 1 goto failed
echo Fertig. Starte Oberflaechenbewertung-CPU.exe per Doppelklick.
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
