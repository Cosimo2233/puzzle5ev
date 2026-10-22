#!/bin/sh
# Install the CPU application runtime into an existing standalone Python venv.
# Run on AXU5EV PetaLinux: sh install_runtime_offline.sh /opt/ec26e/wheelhouse
set -eu
wheelhouse=${1:-/opt/ec26e/wheelhouse}
python=/opt/ec26e/venv-python311/bin/python
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$python" -c 'import platform,sys; assert platform.machine()=="aarch64"; assert sys.version_info[:2]==(3,11)'
(
    cd "$wheelhouse"
    sha256sum -c SHA256SUMS
)
"$python" -m pip install --no-index --find-links "$wheelhouse" \
    -r "$script_dir/requirements-runtime-aarch64.txt" \
    ec-26-e==0.1.0 puzzle-solver==0.1.0 puzzle-m0-bridge==0.1.0
"$python" -m pip check
"$python" - <<'PY'
from pathlib import Path
import imageio_ffmpeg
destination = Path('/opt/ec26e/venv-python311/bin/ffmpeg')
source = Path(imageio_ffmpeg.get_ffmpeg_exe())
if destination.is_symlink():
    assert destination.resolve() == source.resolve(), 'Unexpected existing ffmpeg link'
elif destination.exists():
    raise RuntimeError('Refusing to replace existing ffmpeg')
else:
    destination.symlink_to(source)
print('FFmpeg:', source)
PY
"$python" -m pip freeze > /opt/ec26e/logs/requirements-installed.txt
