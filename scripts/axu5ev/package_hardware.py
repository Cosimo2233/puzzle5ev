"""Package a single hardware build with its compiled Linux modules."""
import hashlib
import json
from pathlib import Path
import shutil
from zipfile import ZipFile
import typer

app = typer.Typer()


@app.command()
def main(root: Path = typer.Option(...)):
    root = root.resolve()
    out = root / 'artifacts/axu5ev-bundle'
    out.mkdir(parents=True, exist_ok=True)
    hardware = root / 'artifacts/axu5ev'
    timing = (hardware/'timing.rpt').read_text()
    if 'All user specified timing constraints are met.' not in timing:
        raise RuntimeError('Timing acceptance missing')
    bit = (hardware/'puzzle5ev.bit').read_bytes()
    with ZipFile(hardware/'puzzle5ev.xsa') as archive:
        bits = [n for n in archive.namelist() if n.endswith('.bit')]
        if len(bits) != 1 or archive.read(bits[0]) != bit: raise RuntimeError('XSA/bit mismatch')
        descriptions = [n for n in archive.namelist() if Path(n).name == 'system.hwh']
        if len(descriptions) != 1 or archive.read(descriptions[0]) != (hardware/'puzzle5ev.hwh').read_bytes():
            raise RuntimeError('XSA/HWH mismatch')
    files = {}
    selections = {
        hardware: ['puzzle5ev.bit','puzzle5ev.bit.bin','puzzle5ev.hwh','puzzle5ev.xsa'],
        root/'artifacts/axu5ev-linux': ['puzzle5ev.dtbo','ec26e_accel.ko','ec26e_hdmi_fb.ko','ch341.ko','ftdi_sio.ko','pl2303.ko'],
        root/'deploy/axu5ev': ['load_hardware.py','xorg-hdmi.conf','start_hdmi_x.sh',
                             'setup_x11_fonts.sh','99-ec26e-camera.rules','validate_accelerators.py',
                             '99-ec26e-m0.rules','fix_ch341_baud.py','setup_m0.sh'],
        root/'artifacts/fonts': ['wqy-microhei-ui.ttf', 'copyright'],
    }
    for source, names in selections.items():
        for name in names:
            shutil.copyfile(source/name, out/name)
            files[name] = hashlib.sha256((out/name).read_bytes()).hexdigest()
    source_files = {}
    for folder in ('src','constraints','scripts/axu5ev','deploy/axu5ev'):
        for path in sorted((root/folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                source_files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('puzzle5ev.xpr','puzzle5ev.srcs/sources_1/bd/system/system.bd'):
        source_files[name] = hashlib.sha256((root/name).read_bytes()).hexdigest()
    (out/'manifest.json').write_text(json.dumps({
        'board':'axu5ev-p','part':'xczu5ev-sfvc784-2-i','vivado':'2026.1',
        'kernel':'5.4.0-xilinx-v2020.1','files':files,'source_files':source_files,
        'hardware_acceptance':False,'active_boot_modified':False,
    },indent=2),encoding='utf-8')
    typer.echo(f'Prepared bundle (not loaded): {out}')


if __name__ == '__main__': app()
