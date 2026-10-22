"""Read-only Linux inventory. Does not open cameras, UARTs, GPIO or PL registers."""
import gzip
import json
from pathlib import Path
import platform
import time


def read(path):
    try:
        return Path(path).read_text().strip().replace('\x00', ',')
    except (OSError, UnicodeError):
        return None


def collect():
    usb = []
    for path in sorted(Path('/sys/bus/usb/devices').glob('*')):
        if (path / 'idVendor').exists():
            usb.append(dict(path=path.name, **{name: read(path / name) for name in
                       ('idVendor', 'idProduct', 'serial', 'manufacturer', 'product', 'speed')}))
    serial = []
    for pattern in ('ttyUSB*', 'ttyACM*'):
        for path in sorted(Path('/sys/class/tty').glob(pattern)):
            serial.append(dict(device='/dev/' + path.name,
                               sysfs=str((path / 'device').resolve())))
    video = [dict(device='/dev/' + p.name, name=read(p / 'name'),
                  sysfs=str(p.resolve())) for p in sorted(Path('/sys/class/video4linux').glob('*'))]
    display = [dict(name=p.name, status=read(p / 'status'), modes=read(p / 'modes'))
               for p in sorted(Path('/sys/class/drm').glob('card*-*'))]
    drivers = {}
    try:
        config = gzip.decompress(Path('/proc/config.gz').read_bytes()).decode()
        names = ('USB_VIDEO_CLASS', 'USB_SERIAL_CP210X', 'USB_SERIAL_CH341',
                 'USB_SERIAL_FTDI_SIO', 'USB_SERIAL_PL2303', 'USB_ACM',
                 'DRM_I2C_ADV7511', 'UIO_PDRV_GENIRQ', 'DMA_CMA', 'MODVERSIONS')
        for name in names:
            drivers[name] = next((line.split('=', 1)[1] for line in config.splitlines()
                                  if line.startswith('CONFIG_' + name + '=')), 'n')
    except OSError:
        pass
    return dict(timestamp_unix=time.time(), kernel=platform.release(), arch=platform.machine(),
                usb=usb, serial=serial, video=video, display=display, kernel_options=drivers,
                cmdline=read('/proc/cmdline'), framebuffer=read('/proc/fb'),
                fpga_state=read('/sys/class/fpga_manager/fpga0/state'),
                motion_executed=False, hardware_modified=False)


if __name__ == '__main__':
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
