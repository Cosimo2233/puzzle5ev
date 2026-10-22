param(
    [ValidateSet('hls','project','compile','all')][string]$Stage = 'all',
    [string]$AmdRoot = 'D:/AMD/2026.1',
    [switch]$Cosim
)
& (Join-Path $PSScriptRoot 'scripts/Build-Axu5ev.ps1') -Stage $Stage -AmdRoot $AmdRoot -Cosim:$Cosim
