#!/bin/sh
# Run after each candidate Linux boot. Does not open the UART or arm the M0.
set -eu
bundle=${1:?Usage: sh setup_m0.sh /path/to/verified-bundle}
/opt/ec26e/venv-python311/bin/python - "$bundle" <<'PY'
import hashlib, json, sys
from pathlib import Path
p = Path(sys.argv[1])
manifest = json.loads((p / 'manifest.json').read_text())
for name in ('ch341.ko', '99-ec26e-m0.rules'):
    assert hashlib.sha256((p / name).read_bytes()).hexdigest() == manifest['files'][name], name
PY
if [ ! -d /sys/module/ch341 ]; then
    insmod "$bundle/ch341.ko"
else
    echo 'CH341 is already loaded; kept the active module. Use a fresh boot to ensure bundle identity.'
fi
mkdir -p /etc/udev/rules.d
cp "$bundle/99-ec26e-m0.rules" /etc/udev/rules.d/99-ec26e-m0.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty
udevadm settle
ls -l /dev/ec26e-m0-bridge
