$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$candidates = @()
if (Test-Path -LiteralPath '.venv\Scripts\python.exe') { $candidates += (Resolve-Path -LiteralPath '.venv\Scripts\python.exe').Path }
foreach ($version in @('3.12','3.11','3.13','3.10')) {
    try {
        $found = & py "-$version" -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { $candidates += $found }
    } catch { }
}
$cachedPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $cachedPython) { $candidates += $cachedPython }
if (-not $candidates.Count) { throw 'Bitte Python 3.12 (64 Bit) von python.org installieren und Setup.bat erneut starten.' }
$selectedPython = $candidates[0]
$hasLocalPackages = $false
try { & $selectedPython -c 'import PIL,numpy' 2>$null; $hasLocalPackages = $LASTEXITCODE -eq 0 } catch { }
if ($hasLocalPackages) { & $selectedPython setup_project.py --use-local-packages } else { & $selectedPython setup_project.py }
if ($LASTEXITCODE -ne 0) { throw 'Einrichtung fehlgeschlagen. Siehe Meldung oben.' }
