param([string]$VivadoBin = 'D:/AMD/2026.1/Vivado/bin')
$ErrorActionPreference = 'Stop'
$taskSoftware = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskSim = Join-Path $taskSoftware 'build/hdmi-sim'
New-Item -ItemType Directory -Force -Path $taskSim | Out-Null
Push-Location $taskSim
try {
    & (Join-Path $VivadoBin 'xvlog.bat') --sv (Join-Path $taskSoftware 'src/rtl/hdmi_scanout.v') (Join-Path $taskSoftware 'src/rtl/test_hdmi_scanout.sv')
    if ($LASTEXITCODE -ne 0) { throw 'RTL compilation failed' }
    & (Join-Path $VivadoBin 'xelab.bat') test_hdmi_scanout -s hdmi_test
    if ($LASTEXITCODE -ne 0) { throw 'RTL elaboration failed' }
    & (Join-Path $VivadoBin 'xsim.bat') hdmi_test -runall
    if ($LASTEXITCODE -ne 0) { throw 'RTL simulation failed' }
    & (Join-Path $VivadoBin 'xvlog.bat') --sv (Join-Path $taskSoftware 'src/rtl/axu5ev_scanout.v') (Join-Path $taskSoftware 'src/rtl/test_axu5ev_scanout.sv')
    if ($LASTEXITCODE -ne 0) { throw 'AXU5EV RTL compilation failed' }
    & (Join-Path $VivadoBin 'xvlog.bat') (Join-Path $VivadoBin '../data/verilog/src/glbl.v')
    if ($LASTEXITCODE -ne 0) { throw 'Global simulation compilation failed' }
    & (Join-Path $VivadoBin 'xelab.bat') -L unisims_ver test_axu5ev_scanout glbl -s axu5ev_hdmi_test
    if ($LASTEXITCODE -ne 0) { throw 'AXU5EV RTL elaboration failed' }
    & (Join-Path $VivadoBin 'xsim.bat') axu5ev_hdmi_test -runall
    if ($LASTEXITCODE -ne 0) { throw 'AXU5EV RTL simulation failed' }
} finally { Pop-Location }
