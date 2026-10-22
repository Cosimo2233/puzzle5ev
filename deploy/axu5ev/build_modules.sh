#!/bin/sh
# Native build only. Never loads a module or changes the boot partition.
set -eu
tools=/opt/ec26e/build-tools
base=/opt/ec26e/build
archive=/opt/ec26e/install/linux-xlnx-xilinx-v2020.1.tar.gz
kernel=$base/linux-xlnx-xilinx-v2020.1
sources=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PATH="$tools/bin:$PATH"
export ARCH=arm64
export CROSS_COMPILE=aarch64-conda-linux-gnu-
# Linux 5.4 dtc predates GCC 10's -fno-common default (duplicate yylloc).
# Restore the original compiler's common-symbol behavior for host utilities.
export HOSTCC="aarch64-conda-linux-gnu-gcc -fcommon -I$tools/include -L$tools/lib -Wl,-rpath,$tools/lib"
printf '%s  %s\n' 1091b3ce1d97da927fba7c1a3ebc49a38f506cadbafc0deab80b7b4bd2cc9762 "$archive" | sha256sum -c -
mkdir -p "$base"
if [ ! -d "$kernel" ]; then tar -xzf "$archive" -C "$base"; fi
cd "$kernel"
if [ ! -f .config ]; then zcat /proc/config.gz > .config; fi
scripts/config --disable LOCALVERSION_AUTO --set-str LOCALVERSION '-xilinx-v2020.1'
make HOSTCC="$HOSTCC" olddefconfig
make -j2 HOSTCC="$HOSTCC" modules_prepare
release=$(make -s kernelrelease)
test "$release" = "$(uname -r)" || { echo "Kernel release mismatch: $release" >&2; exit 1; }
mkdir -p "$base/modules"
for name in accel hdmi-fb; do
    mkdir -p "$base/modules/$name"
    cp "$sources/$name/Makefile" "$sources/$name/"*.c "$base/modules/$name/"
    make -j2 -C "$kernel" M="$base/modules/$name" modules
done
/opt/ec26e/venv-python311/bin/python "$sources/fix_ch341_baud.py" "$kernel/drivers/usb/serial/ch341.c"
make -j2 -C "$kernel" M=drivers/usb/serial CONFIG_USB_SERIAL_CH341=m CONFIG_USB_SERIAL_FTDI_SIO=m CONFIG_USB_SERIAL_PL2303=m modules
mkdir -p "$base/modules/usb-serial"
for name in ch341 ftdi_sio pl2303; do cp "$kernel/drivers/usb/serial/$name.ko" "$base/modules/usb-serial/"; done
echo 'Modules built only; no hardware loaded. Review modpost and vermagic before deployment.'
