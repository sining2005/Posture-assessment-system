$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runId = [Guid]::NewGuid().ToString("N")
$testTemp = Join-Path $projectRoot ("tmp\pytest-" + $runId)
$pytestBase = Join-Path $testTemp "runtime"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Project virtual environment was not found: $pythonExe"
}

New-Item -ItemType Directory -Force -Path $testTemp | Out-Null
$previousQt = $env:QT_QPA_PLATFORM
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$previousPasswordIterations = $env:POSTURE_PASSWORD_ITERATIONS
$testExitCode = 1

try {
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:TEMP = $testTemp
    $env:TMP = $testTemp
    # Production keeps 200,000 PBKDF2 rounds. Tests use fewer rounds because
    # every test creates an isolated database and default admin account.
    $env:POSTURE_PASSWORD_ITERATIONS = "2000"
    Set-Location -LiteralPath $projectRoot
    & $pythonExe -m pytest -v --tb=short -p no:cacheprovider --basetemp=$pytestBase
    $testExitCode = $LASTEXITCODE
}
finally {
    if ($null -eq $previousQt) { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue } else { $env:QT_QPA_PLATFORM = $previousQt }
    if ($null -eq $previousTemp) { Remove-Item Env:TEMP -ErrorAction SilentlyContinue } else { $env:TEMP = $previousTemp }
    if ($null -eq $previousTmp) { Remove-Item Env:TMP -ErrorAction SilentlyContinue } else { $env:TMP = $previousTmp }
    if ($null -eq $previousPasswordIterations) { Remove-Item Env:POSTURE_PASSWORD_ITERATIONS -ErrorAction SilentlyContinue } else { $env:POSTURE_PASSWORD_ITERATIONS = $previousPasswordIterations }
    Remove-Item -LiteralPath $testTemp -Recurse -Force -ErrorAction SilentlyContinue
}

exit $testExitCode
