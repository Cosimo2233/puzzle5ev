param([string]$AmdRoot = 'D:/AMD/2026.1')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskHardware = Join-Path $taskRoot 'artifacts/axu5ev'
$taskBit = (Join-Path $taskHardware 'puzzle5ev.bit').Replace('\','/')
$taskBif = Join-Path $taskHardware 'bitstream.bif'
[IO.File]::WriteAllText($taskBif, "all:`n{`n  `"$taskBit`"`n}`n", [Text.UTF8Encoding]::new($false))
Push-Location $taskHardware
try {
    & (Join-Path $AmdRoot 'Vivado/bin/bootgen.bat') -arch zynqmp -image $taskBif -process_bitstream bin -w
    if ($LASTEXITCODE -ne 0) { throw 'Bootgen bitstream conversion failed' }
    & poetry -C (Join-Path $taskRoot 'sw') run python (Join-Path $PSScriptRoot 'axu5ev/package_hardware.py') --root $taskRoot
    if ($LASTEXITCODE -ne 0) { throw 'Hardware packaging failed' }
} finally { Pop-Location }
