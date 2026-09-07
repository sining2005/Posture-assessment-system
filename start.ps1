$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runFile = Join-Path $projectRoot "run.py"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Project virtual environment was not found: $pythonExe"
}

# Single-instance guard: another run.py (from any Python environment) already has the camera open.
$existing = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'run\.py' -and $_.CommandLine -match [regex]::Escape($projectRoot) }
if ($existing) {
    $ids = ($existing | ForEach-Object { $_.ProcessId }) -join ", "
    Write-Warning ("Posture system already running (PID: {0}). Close the old window first." -f $ids)
    Start-Sleep -Seconds 3
    exit 1
}

# Offscreen mode is only for automated tests. It would make the GUI invisible.
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
Set-Location -LiteralPath $projectRoot
& $pythonExe $runFile
