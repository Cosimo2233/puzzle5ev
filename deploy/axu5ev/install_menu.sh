#!/bin/sh
# Run with the directory containing this script, tasks/, and the application wheel.
set -eu
test "$(uname -m)" = aarch64 || { echo 'AXU5EV requires AArch64' >&2; exit 1; }
package=${1:?Usage: install_menu.sh /path/to/ec_26_e.whl}
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=/opt/ec26e/venv-python311/bin/python
test -x "$python"
test -f "$package"
"$python" -m pip install --no-index --no-deps --force-reinstall "$package"
"$python" -m pip check
mkdir -p /opt/ec26e/config/axu5ev/tasks /run/ec26e /opt/ec26e/logs
for name in fixed random; do
    target=/opt/ec26e/config/axu5ev/tasks/$name.json
    test -e "$target" || cp "$source_dir/tasks/$name.json" "$target"
done
test -e /opt/ec26e/config/axu5ev/machine.env || cp "$source_dir/board.env.example" /opt/ec26e/config/axu5ev/machine.env
test -e /opt/ec26e/config/axu5ev/menu.json || cp "$source_dir/menu-config.example.json" /opt/ec26e/config/axu5ev/menu.json
cp "$source_dir/start_menu.sh" /opt/ec26e/install/start_menu.sh
chmod 755 /opt/ec26e/install/start_menu.sh
echo 'Application installed. HDMI and USB require separate validation; autostart is not enabled.'
