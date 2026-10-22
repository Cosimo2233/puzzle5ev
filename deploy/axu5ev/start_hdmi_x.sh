#!/bin/sh
# Run manually after load_hardware.py, with the factory X server stopped.
set -eu
test -f /run/ec26e/pl-status.json
found=
for entry in /sys/class/graphics/fb*/name; do
    if [ -f "$entry" ] && [ "$(cat "$entry")" = ec26e-hdmi ]; then
        test -z "$found" || { echo 'Ambiguous HDMI framebuffer' >&2; exit 1; }
        found=/dev/$(basename "$(dirname "$entry")")
    fi
done
test -n "$found" || { echo 'HDMI framebuffer missing' >&2; exit 1; }
test ! -e /tmp/.X0-lock || { echo 'Stop the factory X server before starting HDMI Xorg' >&2; exit 1; }
ln -sfn "$found" /dev/ec26e-hdmi-fb
# This Xorg fbdev build probes literal /dev/fbN paths; an alias is not accepted.
mkdir -p /run/ec26e
sed "s@/dev/ec26e-hdmi-fb@$found@g" /opt/ec26e/install/xorg-hdmi.conf > /run/ec26e/xorg-hdmi.conf
exec Xorg :0 -config /run/ec26e/xorg-hdmi.conf -nolisten tcp
