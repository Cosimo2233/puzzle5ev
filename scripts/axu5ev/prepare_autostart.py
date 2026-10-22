"""生成带工程启动入口、厂家恢复开关的 U-Boot 脚本，不写入设备。"""
from pathlib import Path
import struct
import time
import zlib
import typer

app = typer.Typer()
HEADER = struct.Struct('>7I4B32s')
PREFIX = '''# AXU5EV-P: remove puzzle5ev-autostart.enable to restore factory boot.
if test -e mmc 1:1 /puzzle5ev-autostart.enable; then
    if fatload mmc 1:1 0x18000000 image-puzzle5ev.ub; then
        setenv ec26e_saved_bootargs "${bootargs}"
        setenv bootargs earlycon console=ttyPS0,115200 clk_ignore_unused root=/dev/mmcblk1p2 rw rootwait x11=false
        bootm 0x18000000
        setenv bootargs "${ec26e_saved_bootargs}"
    fi
    echo "Puzzle boot failed; using original factory boot script"
fi
'''


def unpack_script(blob):
    if len(blob) < 72:
        raise ValueError('Truncated U-Boot script')
    fields = HEADER.unpack(blob[:64])
    magic, header_crc, _, size, _, _, data_crc, _, _, image_type, compression, _ = fields
    header = bytearray(blob[:64]); header[4:8] = b'\0'*4
    data = blob[64:]
    if magic != 0x27051956 or zlib.crc32(header) != header_crc or len(data) != size or zlib.crc32(data) != data_crc:
        raise ValueError('U-Boot script checksum/length mismatch')
    if image_type != 6 or compression != 0:
        raise ValueError('Expected uncompressed U-Boot script')
    text_size, terminator = struct.unpack('>2I', data[:8])
    if terminator or text_size != len(data)-8:
        raise ValueError('Expected one script component')
    return data[8:]


def prepare(blob):
    original = unpack_script(blob)
    if b'puzzle5ev-autostart.enable' in original:
        raise ValueError('Already contains the autostart entry')
    text = PREFIX.encode()+original
    data = struct.pack('>2I', len(text), 0)+text
    fields = [0x27051956, 0, int(time.time()), len(data), 0, 0,
              zlib.crc32(data), 5, 2, 6, 0, b'AXU5EV puzzle + factory fallback']
    fields[1] = zlib.crc32(HEADER.pack(*fields))
    result = HEADER.pack(*fields)+data
    assert unpack_script(result) == text
    return result


@app.command()
def main(source: Path = typer.Option(...), output: Path = typer.Option(...)):
    if source.resolve() == output.resolve() or output.exists():
        raise ValueError('Write a new output; preserve the original boot script')
    output.write_bytes(prepare(source.read_bytes()))


if __name__ == '__main__':
    app()
