#!/bin/sh
# The board's Tk uses X11 core fonts. Fontconfig alone does not configure them.
# Install the UI subset produced from Debian fonts-wqy-microhei and its license.
set -eu
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fontdir=/usr/share/fonts/ttf/ec26e
if [ -f "$source_dir/wqy-microhei-ui.ttf" ]; then
    test -f "$source_dir/copyright"
    mkdir -p "$fontdir" /usr/share/doc/ec26e-ui-font
    cp "$source_dir/wqy-microhei-ui.ttf" "$fontdir/"
    cp "$source_dir/copyright" /usr/share/doc/ec26e-ui-font/copyright
fi
test -f "$fontdir/wqy-microhei-ui.ttf"
cat > "$fontdir/fonts.dir" <<'EOF'
1
wqy-microhei-ui.ttf -wenquanyi-wenquanyi micro hei-medium-r-normal--0-0-0-0-p-0-iso10646-1
EOF
cp "$fontdir/fonts.dir" "$fontdir/fonts.scale"
