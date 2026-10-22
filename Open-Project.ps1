param([string]$AmdRoot = 'D:/AMD/2026.1')
$ErrorActionPreference = 'Stop'
$taskProject = Join-Path $PSScriptRoot 'puzzle5ev.xpr'
if (!(Test-Path -LiteralPath $taskProject)) { throw 'Run ./Build.ps1 -Stage project first.' }
Start-Process -FilePath (Join-Path $AmdRoot 'Vivado/bin/vivado.bat') -ArgumentList @('"' + $taskProject + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
