#!/bin/sh
# Foreground entry: use only after the HDMI X server is verified.
set -eu
. /opt/ec26e/env.sh
export EC_PL_ACCESS=linux
export EC_PL_BITSTREAM=${EC_PL_BITSTREAM:-/opt/ec26e/staging/20261020/axu5ev-reset-bundle/puzzle5ev.bit}
export EC_MENU_CONFIG=/opt/ec26e/config/axu5ev/menu.json
export EC_MOTION_ENABLED=false
export EC_VISION_THREADS=${EC_VISION_THREADS:-2}
export DISPLAY=${DISPLAY:-:0}
# Dedicated machine display: never blank while the camera/solver is running.
xset s off
xset -dpms
xset s reset
found_hdmi=false
for name in /sys/class/graphics/fb*/name; do
    if [ -f "$name" ] && [ "$(cat "$name")" = ec26e-hdmi ]; then found_hdmi=true; fi
done
"$found_hdmi" || { echo 'HDMI framebuffer is not ready; refusing to start on PS DisplayPort.' >&2; exit 1; }
mkdir -p /run/ec26e
exec python -u -m ec_26_e.gui.board_menu
