"""Subset the licensed WQY font for this application's X11 core-font UI."""
import argparse
import hashlib
import json
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from puzzle_solver import PATTERN_NAMES

parser = argparse.ArgumentParser()
parser.add_argument('--font', type=Path, required=True)
parser.add_argument('--license', type=Path, required=True)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
sources = sorted((args.root / 'sw/src').rglob('*.py'))
text = ''.join(p.read_text(encoding='utf-8') for p in sources)
text += ''.join(PATTERN_NAMES.values())
text += ''.join(chr(value) for value in range(32, 127))
font = TTFont(args.font, fontNumber=0)
processor = subset.Subsetter(subset.Options())
processor.populate(text=text)
processor.subset(font)
args.output.mkdir(parents=True, exist_ok=True)
destination = args.output / 'wqy-microhei-ui.ttf'
font.save(destination)
(args.output / 'copyright').write_bytes(args.license.read_bytes())
(args.output / 'manifest.json').write_text(json.dumps({
    'upstream_package': 'Debian fonts-wqy-microhei 0.2.0-beta-3.1',
    'upstream_font_sha256': hashlib.sha256(args.font.read_bytes()).hexdigest(),
    'font_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
    'requested_codepoints': len(set(text)),
    'scope': 'ASCII, sw/src Python codepoints and public puzzle_solver pattern labels; regenerate after adding UI text',
}, indent=2), encoding='utf-8')
