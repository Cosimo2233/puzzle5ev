#!/bin/sh
# AXU5EVB-P manual p55: PS_LED1 = MIO40 (K18), active high.
# Temporary test for the vendor PetaLinux image; no PL reconfiguration.
# Usage: sh led_blink.sh [cycles], default 30 cycles, 2 seconds per cycle.
set -eu

cycles=${1:-30}
case "$cycles" in
    ''|*[!0-9]*) echo 'cycles must be an integer from 1 to 300' >&2; exit 1 ;;
esac
[ "$cycles" -ge 1 ] && [ "$cycles" -le 300 ] || exit 1

chip=
for candidate in /sys/class/gpio/gpiochip*; do
    [ -f "$candidate/label" ] || continue
    [ "$(cat "$candidate/label")" = zynqmp_gpio ] || continue
    [ -z "$chip" ] || { echo 'Multiple PS GPIO controllers' >&2; exit 1; }
    chip=$candidate
done
[ -n "$chip" ] || { echo 'ZynqMP PS GPIO controller not found' >&2; exit 1; }
base=$(cat "$chip/base")
gpio=$((base + 40))
node=/sys/class/gpio/gpio$gpio
[ ! -e "$node" ] || { echo "GPIO $gpio already exported; refusing to take over" >&2; exit 1; }

# Kernel export also rejects a GPIO already owned by another driver.
echo "$gpio" > /sys/class/gpio/export
original_direction=
original_value=
cleanup() {
    trap - EXIT HUP INT TERM
    if [ "$original_direction" = out ]; then
        if [ "$original_value" = 1 ]; then
            echo high > "$node/direction" || :
        else
            echo low > "$node/direction" || :
        fi
    elif [ "$original_direction" = in ]; then
        echo in > "$node/direction" || :
    fi
    echo "$gpio" > /sys/class/gpio/unexport || :
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
original_direction=$(cat "$node/direction")
original_value=$(cat "$node/value")
echo low > "$node/direction"
echo "PS_LED1 MIO40 GPIO=$gpio: $cycles cycles, 1s on / 1s off"
i=0
while [ "$i" -lt "$cycles" ]; do
    echo 1 > "$node/value"
    [ "$(cat "$node/value")" = 1 ] || { echo 'High readback failed' >&2; exit 1; }
    sleep 1
    echo 0 > "$node/value"
    [ "$(cat "$node/value")" = 0 ] || { echo 'Low readback failed' >&2; exit 1; }
    sleep 1
    i=$((i + 1))
done
echo "LED_TEST_PASS: $cycles cycles; GPIO high/low readback passed"
