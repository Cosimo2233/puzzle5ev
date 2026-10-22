"""Generate udev aliases from attached USB devices, without installing rules."""
from pathlib import Path
import re
import subprocess
import typer

app = typer.Typer()


def properties(device):
    text = subprocess.check_output(['udevadm','info','--query=property','--name',str(device)], text=True)
    return dict(line.split('=',1) for line in text.splitlines() if '=' in line)


def rule(device, alias, subsystem):
    p = properties(device)
    if p.get('ID_BUS') != 'usb': raise ValueError(f'{device} is not a USB device')
    # Physical path also distinguishes cheap serial adapters with cloned serials.
    required = ('ID_VENDOR_ID','ID_MODEL_ID','ID_PATH')
    if any(not p.get(k) for k in required): raise ValueError(f'{device} lacks stable udev identity')
    if any(not re.fullmatch(r'[A-Za-z0-9_.:/+-]+',p[k]) for k in required):
        raise ValueError('Unsupported udev property characters')
    fields = [f'SUBSYSTEM=="{subsystem}"']
    if subsystem == 'video4linux':
        if 'capture' not in p.get('ID_V4L_CAPABILITIES',''): raise ValueError('Camera node is not capture-capable')
        fields.append('ATTR{index}=="0"')
    fields += [f'ENV{{{k}}}=="{p[k]}"' for k in required]
    fields.append(f'SYMLINK+="{alias}"')
    return ', '.join(fields), p['ID_PATH']


@app.command()
def main(camera: Path = typer.Option(...), xyz: Path = typer.Option(...),
         rotation: Path = typer.Option(...), output: Path = typer.Option(...)):
    devices = (camera, xyz, rotation)
    if len({p.resolve() for p in devices}) != 3: raise ValueError('Each role requires a different device')
    if output.exists(): raise ValueError('Choose a new output file; existing rules are not overwritten')
    entries = [rule(camera,'ec26e-camera','video4linux'), rule(xyz,'ec26e-xyz-uart','tty'),
               rule(rotation,'ec26e-rotation-uart','tty')]
    if entries[1][1] == entries[2][1]: raise ValueError('Ambiguous serial identities')
    output.write_text('# AXU5EV-P: aliases follow physical USB sockets; keep the same wiring.\n' +
                      '\n'.join(line for line, _ in entries) + '\n', encoding='utf-8')
    typer.echo(f'Generated for review: {output}; no udev rules installed, no serial ports opened')


if __name__ == '__main__': app()
