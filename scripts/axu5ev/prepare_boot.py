"""Prepare a separate FIT with stock PL drivers disabled; preserve kernel/rootfs."""
import hashlib
import json
from pathlib import Path
import struct
import zlib
import typer
from fdt import Fdt

app = typer.Typer()


def digest(algorithm, data):
    if algorithm == 'crc32': return struct.pack('>I', zlib.crc32(data))
    if algorithm not in ('sha1', 'sha256', 'sha384', 'sha512'):
        raise ValueError(f'Unsupported FIT hash {algorithm}')
    return hashlib.new(algorithm, data).digest()


def prepare(blob, *, usb_phy=False):
    fit = Fdt(blob)
    for node in fit.root.walk():
        if node.name.startswith('signature') or any(k in node.props for k in ('data-offset', 'data-position')):
            raise ValueError('Signed or external-data FIT requires vendor tools')
    images = fit.root.at('/images')
    changed = []
    for image in images.children:
        payload = image.props.get('data')
        if payload is None: raise ValueError('FIT image missing inline data')
        hashes = [n for n in image.children if n.name.startswith('hash')]
        if not hashes: raise ValueError('Unhashed FIT image')
        for h in hashes:
            algorithm = h.props['algo'].rstrip(b'\0').decode()
            if digest(algorithm, payload) != h.props['value']: raise ValueError('Source FIT hash mismatch')
        if image.props.get('type') != b'flat_dt\0': continue
        if image.props.get('compression', b'none\0') != b'none\0':
            raise ValueError('Compressed device tree unsupported')
        dt = Fdt(payload)
        pl = dt.root.at('/amba_pl@0')
        pl.props['status'] = b'disabled\0'
        for child in pl.children: child.props['status'] = b'disabled\0'
        dt.root.at('/amba/i2c@ff020000').props['status'] = b'disabled\0'
        if usb_phy:
            # AXU5EV vendor PS preset: USB0 on GTR lane1, Ref Clk1 26 MHz.
            # Xilinx 2020.1 PHY binding uses four cells after the lane phandle.
            lane = dt.root.at('/amba/zynqmp_phy@fd400000/lane1')
            usb = dt.root.at('/amba/usb0@ff9d0000/dwc3@fe200000')
            usb.props['phys'] = lane.props['phandle'] + struct.pack('>4I', 4, 0, 1, 26000000)
            usb.props['phy-names'] = b'usb3-phy\0'
        dt.root.at('/chosen').props['ec26e,pl-base'] = b'axu5ev-v1\0'
        image.props['data'] = dt.pack()
        for h in hashes:
            h.props['value'] = digest(h.props['algo'].rstrip(b'\0').decode(), image.props['data'])
        changed.append(image.name)
    if not changed: raise ValueError('FIT contains no supported DT image')
    result = fit.pack()
    reread = Fdt(result)
    records = []
    before = Fdt(blob).root.at('/images')
    for image in reread.root.at('/images').children:
        payload = image.props['data']
        if image.name not in changed and payload != before.child(image.name).props['data']:
            raise ValueError('Non-DT image changed')
        for h in image.children:
            if h.name.startswith('hash'):
                assert digest(h.props['algo'].rstrip(b'\0').decode(), payload) == h.props['value']
        records.append({'name': image.name, 'changed': image.name in changed,
                        'sha256': hashlib.sha256(payload).hexdigest()})
    return result, records


@app.command()
def main(source: Path = typer.Option(...), output: Path = typer.Option(...),
         usb_phy: bool = typer.Option(False, '--usb-phy')):
    if source.resolve() == output.resolve() or output.exists():
        raise ValueError('Use a new staging output; never overwrite an existing boot image')
    data = source.read_bytes()
    result, records = prepare(data, usb_phy=usb_phy)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    output.with_suffix('.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256(data).hexdigest(),
        'output_sha256': hashlib.sha256(result).hexdigest(), 'images': records,
        'boot_tested': False, 'active_boot_modified': False, 'usb_phy': usb_phy,
    }, indent=2), encoding='utf-8')
    typer.echo(f'Staged only: {output}')


if __name__ == '__main__': app()
