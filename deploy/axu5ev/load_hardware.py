"""Load a verified AXU5EV bundle once after booting the prepared base DT.

Default is preflight only. Never changes boot files or enables motion.
"""
import ctypes
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
import typer

app = typer.Typer()
DT = Path('/sys/firmware/devicetree/base')
RUN = Path('/run/ec26e')
STATUS = RUN / 'pl-status.json'
log = logging.getLogger(__name__)


def check_base():
    if os.uname().release != '5.4.0-xilinx-v2020.1':
        raise RuntimeError('Bundle requires the validated 5.4.0-xilinx-v2020.1 kernel')
    marker = DT / 'chosen/ec26e,pl-base'
    if not marker.exists() or marker.read_bytes() != b'axu5ev-v1\0':
        raise RuntimeError('Prepared base device tree is not booted; refusing to replace factory PL')
    if (DT / 'amba_pl@0/status').read_bytes() != b'disabled\0':
        raise RuntimeError('Factory PL bus is still enabled')
    old_pl = (DT / 'amba_pl@0').resolve()
    for device in Path('/sys/bus/platform/devices').iterdir():
        if (device / 'driver').exists() and (device / 'of_node').resolve().is_relative_to(old_pl):
            raise RuntimeError(f'Factory PL driver remains bound: {device.name}')
    for comm in Path('/proc').glob('[0-9]*/comm'):
        try: name = comm.read_text().strip()
        except OSError: continue
        if name.startswith('alinx_ui'):
            raise RuntimeError('Stop and disable the factory alinx_ui application before PL replacement')


def verify_bundle(bundle):
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if manifest.get('board') != 'axu5ev-p' or manifest.get('part') != 'xczu5ev-sfvc784-2-i':
        raise RuntimeError('Wrong board/part manifest')
    required = {'puzzle5ev.bit', 'puzzle5ev.bit.bin', 'puzzle5ev.hwh', 'puzzle5ev.dtbo',
                'ec26e_accel.ko', 'ec26e_hdmi_fb.ko'}
    files = manifest['files']
    if not required.issubset(files): raise RuntimeError('Incomplete hardware bundle')
    for name, expected in files.items():
        if Path(name).name != name: raise ValueError('Bundle paths must be simple filenames')
        if hashlib.sha256((bundle / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Bundle checksum mismatch: {name}')
    return files


def read_i2c_register(fd, address, register):
    """Keep the register address and read in one repeated-start transaction."""
    class Message(ctypes.Structure):
        _fields_ = [('addr', ctypes.c_uint16), ('flags', ctypes.c_uint16),
                    ('length', ctypes.c_uint16), ('buffer', ctypes.c_void_p)]
    class Transfer(ctypes.Structure):
        _fields_ = [('messages', ctypes.POINTER(Message)), ('count', ctypes.c_uint32)]
    tx = ctypes.create_string_buffer(bytes([register]))
    rx = ctypes.create_string_buffer(1)
    messages = (Message * 2)(Message(address, 0, 1, ctypes.addressof(tx)),
                            Message(address, 1, 1, ctypes.addressof(rx)))
    fcntl.ioctl(fd, 0x0707, Transfer(messages, 2))  # I2C_RDWR
    return rx.raw[0]


def bind_hls_uio():
    # The factory boot may already have loaded this module for axi-pmon.
    # A later modprobe of_id=... does not change a loaded module's match table.
    driver = Path('/sys/bus/platform/drivers/uio_pdrv_genirq')
    for name in ('a0020000.rigid', 'a0030000.clearance'):
        device = Path('/sys/bus/platform/devices') / name
        if (device / 'of_node/compatible').read_bytes() != b'generic-uio\0':
            raise RuntimeError(f'Unexpected HLS device identity: {name}')
        if (device / 'driver').exists():
            if (device / 'driver').resolve() != driver.resolve():
                raise RuntimeError(f'HLS device already has another driver: {name}')
        else:
            (device / 'driver_override').write_text('uio_pdrv_genirq')
            (driver / 'bind').write_text(name)


def configure_hdmi():
    # Register sequence from ALINX factory lut_adv7511.v (source retained in
    # docs/vendor/lut_adv7511.v with its original copyright and disclaimer).
    sequence = [(0x41,0x00),(0xd6,0xc0),(0x55,0x12),(0x15,0x00),(0xd0,0x3c),
                (0xaf,0x04),(0x4c,0x04),(0x40,0x00),(0x98,0x03),(0x9a,0xe0),
                (0x9c,0x30),(0x9d,0x61),(0xa2,0xa4),(0xa3,0xa4),(0xe0,0xd0),(0xf9,0x00)]
    buses = [p for p in Path('/sys/class/i2c-adapter').glob('i2c-*')
             if 'a0050000' in str(p.resolve())]
    if len(buses) != 1: raise RuntimeError('Cannot uniquely identify HDMI AXI IIC adapter')
    fd = os.open('/dev/' + buses[0].name, os.O_RDWR)
    try:
        fcntl.ioctl(fd, 0x0703, 0x39)  # I2C_SLAVE, 7-bit ADV7511 address
        for reg, value in sequence:
            if os.write(fd, bytes([reg, value])) != 2: raise OSError('Short I2C write')
        for reg, value in ((0x15,0x00),(0x41,0x00),(0x98,0x03)):
            actual = read_i2c_register(fd, 0x39, reg)
            if actual != value:
                raise RuntimeError(f'ADV7511 register {reg:#04x}: expected {value:#04x}, got {actual:#04x}')
    finally: os.close(fd)


def write_status(value):
    temporary = STATUS.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(STATUS)


@app.command()
def main(bundle: Path = typer.Option(...), apply: bool = typer.Option(False, '--apply')):
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    files = verify_bundle(bundle)
    check_base()
    overlay = Path('/sys/kernel/config/device-tree/overlays/ec26e')
    if STATUS.exists() or overlay.exists():
        raise RuntimeError('PL loading has already been attempted; reboot before retrying')
    if not apply:
        log.info('Preflight passed; no hardware changed')
        return
    RUN.mkdir(parents=True, exist_ok=True)
    with (RUN / 'pl-load.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if STATUS.exists() or overlay.exists(): raise RuntimeError('Concurrent/repeated load')
        status = {'board':'axu5ev', 'loaded':False, 'state':'loading',
                  'bit_sha256':files['puzzle5ev.bit'], 'hwh_sha256':files['puzzle5ev.hwh']}
        write_status(status)
        try:
            firmware = Path('/lib/firmware/ec26e')
            firmware.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle / 'puzzle5ev.bit.bin', firmware / 'puzzle5ev.bit.bin')
            manager = Path('/sys/class/fpga_manager/fpga0')
            (manager / 'flags').write_text('0')
            (manager / 'firmware').write_text('ec26e/puzzle5ev.bit.bin')
            if (manager / 'state').read_text().strip() != 'operating':
                raise RuntimeError('FPGA manager did not reach operating state')
            subprocess.run(['modprobe', 'uio_pdrv_genirq', 'of_id=generic-uio'], check=True)
            # PetaLinux may build i2c-dev into the kernel without a modules.builtin
            # index; modprobe then fails although the character interface exists.
            if not Path('/sys/class/i2c-dev').is_dir():
                subprocess.run(['modprobe', 'i2c-dev'], check=True)
            if not Path('/sys/class/i2c-dev').is_dir():
                raise RuntimeError('I2C character interface is unavailable')
            for module in ('ec26e_accel.ko','ec26e_hdmi_fb.ko'):
                subprocess.run(['insmod', str((bundle/module).resolve())], check=True)
            overlay.mkdir()
            (overlay / 'dtbo').write_bytes((bundle / 'puzzle5ev.dtbo').read_bytes())
            for _ in range(50):
                if Path('/dev/ec26e-vision').exists() and any(
                    p.read_text().strip() == 'ec26e-hdmi' for p in Path('/sys/class/graphics').glob('fb*/name')):
                    break
                time.sleep(.1)
            else: raise RuntimeError('Vision or HDMI driver did not bind')
            bind_hls_uio()
            configure_hdmi()
            names = {p.read_text().strip() for p in Path('/sys/class/uio').glob('uio*/name')}
            if not {'puzzle_rigid_fit_0','puzzle_clearance_scan_0'}.issubset(names):
                raise RuntimeError('Missing HLS UIO nodes')
            status.update(loaded=True, state='loaded', hardware_acceptance=False)
            write_status(status)
            log.info('Hardware loaded; camera/display/DMA acceptance remains required')
        except BaseException as error:
            status.update(state='failed', error=str(error)); write_status(status)
            raise


if __name__ == '__main__': app()
