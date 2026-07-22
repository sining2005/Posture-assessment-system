$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runFile = Join-Path $projectRoot "run.py"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Project virtual environment was not found: $pythonExe"
}

# Offscreen mode is only for automated tests. It would make the GUI invisible.
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
Set-Location -LiteralPath $projectRoot
& $pythonExe $runFile

