param(
    [string]$SolverRoot = (Join-Path $PSScriptRoot '../../puzzle-solver'),
    [string]$BridgeRoot = (Join-Path $PSScriptRoot '../../mspm0g3507-puzzle-bridge/host'),
    [string]$Python = 'D:/python/python.exe'
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskSolver = (Resolve-Path -LiteralPath $SolverRoot).Path
$taskBridge = (Resolve-Path -LiteralPath $BridgeRoot).Path
$env:PATH = (Split-Path -Parent $Python) + ';' + $env:PATH
$env:PYTHONUTF8 = '1'
$env:POETRY_VIRTUALENVS_IN_PROJECT = 'true'
$taskOutput = Join-Path $taskRoot 'artifacts/software'
New-Item -ItemType Directory -Force $taskOutput | Out-Null
foreach ($taskProject in @($taskSolver, $taskBridge, (Join-Path $taskRoot 'sw'))) {
    Push-Location $taskProject
    try {
    & poetry check
    if ($LASTEXITCODE -ne 0) { throw "Poetry check failed: $taskProject" }
    & poetry build -f wheel -o $taskOutput
    if ($LASTEXITCODE -ne 0) { throw "Wheel build failed: $taskProject" }
    } finally { Pop-Location }
}
& $Python (Join-Path $PSScriptRoot 'software_manifest.py') --root $taskRoot --solver $taskSolver --bridge $taskBridge --output $taskOutput
if ($LASTEXITCODE -ne 0) { throw 'Software manifest failed' }
Write-Output "Software artifacts: $taskOutput"
