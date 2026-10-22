param(
    [ValidateSet('hls', 'project', 'compile', 'all')][string]$Stage = 'all',
    [string]$AmdRoot = 'D:/AMD/2026.1',
    [switch]$Cosim
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskBuild = Join-Path $taskRoot 'build/axu5ev'
New-Item -ItemType Directory -Force $taskBuild | Out-Null
$taskLock = [IO.File]::Open((Join-Path $taskBuild 'build.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
$taskPreviousCosim = $env:EC_AXU_COSIM
$taskPreviousLocalData = $env:XILINX_LOCAL_USER_DATA
Push-Location $taskBuild
try {
    $env:XILINX_LOCAL_USER_DATA = 'no'
    $env:EC_AXU_COSIM = if ($Cosim) { '1' } else { '0' }
    if ($Stage -in @('hls', 'all')) {
        & (Join-Path $AmdRoot 'Vitis/bin/vitis-run.bat') --mode hls --tcl (Join-Path $PSScriptRoot 'axu5ev/build_hls.tcl')
        if ($LASTEXITCODE -ne 0) { throw 'AXU5EV HLS failed; inspect build/axu5ev.' }
    }
    if ($Stage -ne 'hls') {
        $ErrorActionPreference = 'Continue'
        & (Join-Path $AmdRoot 'Vivado/bin/vivado.bat') -mode batch -log vivado.log -journal vivado.jou -source (Join-Path $PSScriptRoot 'axu5ev/create_project.tcl') -tclargs $Stage
        $ErrorActionPreference = 'Stop'
        if ($LASTEXITCODE -ne 0) { throw 'AXU5EV Vivado failed; inspect build/axu5ev/vivado.log.' }
    }
} finally {
    Pop-Location
    $env:EC_AXU_COSIM = $taskPreviousCosim
    $env:XILINX_LOCAL_USER_DATA = $taskPreviousLocalData
    $taskLock.Dispose()
}
