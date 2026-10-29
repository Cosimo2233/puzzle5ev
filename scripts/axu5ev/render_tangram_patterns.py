"""从独立求解器公共布局生成图案目录；仅绘图缩放，不产生运动方案。"""
from html import escape
from pathlib import Path

import numpy as np
import typer
from puzzle_solver import PATTERN_NAMES, get_tangram_layout

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(output: Path = typer.Option(..., '--output')):
    colors = ('#45a5bd','#98c755','#edbdbc','#ffd34e','#f2da76','#bb92c5','#e55d78')
    columns, tile_width, tile_height = 4, 300, 280
    rows = (len(PATTERN_NAMES)+columns-1)//columns
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{rows*tile_height}" viewBox="0 0 1200 {rows*tile_height}">',
           '<rect width="100%" height="100%" fill="#fafaf7"/>']
    for index, (name, label) in enumerate(PATTERN_NAMES.items()):
        x, y = index % columns * tile_width, index // columns * tile_height
        pieces = [np.array(p) for p in get_tangram_layout(name).pieces]
        points = np.concatenate(pieces)
        low, high = points.min(axis=0), points.max(axis=0)
        scale = min(250/(high[0]-low[0]), 205/(high[1]-low[1]))
        offset = np.array((x+150,y+115))-(low+high)/2*scale
        svg.append(f'<g><title>{escape(label)} ({name})</title>')
        for p, color in zip(pieces,colors):
            vertices = ' '.join(f'{a:.3f},{b:.3f}' for a,b in p*scale+offset)
            svg.append(f'<polygon points="{vertices}" fill="{color}" stroke="#303c43" stroke-width="1.5" stroke-linejoin="round"/>')
        svg.append(f'<text x="{x+150}" y="{y+245}" text-anchor="middle" font-family="sans-serif" font-size="19">{escape(label)} · {name}</text></g>')
    svg.append('</svg>')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text('\n'.join(svg)+'\n',encoding='utf-8')


if __name__ == '__main__':
    app()
