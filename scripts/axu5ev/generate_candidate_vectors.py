"""生成凹/凸多边形批量距离核的独立 GEOS 参考向量。"""
from itertools import combinations
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
import typer

app = typer.Typer()


@app.command()
def main(output: Path = typer.Option(...), stress: bool = typer.Option(False)):
    rng = np.random.default_rng(22026)
    shapes = [np.array(p, dtype=float) for p in (
        [(0, 0), (40, 0), (20, 15), (40, 30), (0, 30)],
        [(10, 5), (30, 5), (20, 25)],
        [(45, 0), (70, 0), (70, 25), (45, 25)],
        [(0, 50), (40, 50), (40, 80), (25, 65), (0, 80)],
    )]
    lines = ['0']
    cases = 0
    for pieces in (2, 3, 4):
        for count in (1, 63, 64):
            packed = np.zeros(580, dtype=np.float32)
            polygons = []
            for i, shape in enumerate(shapes[:pieces]):
                # Exercise both winding orders without changing the geometry.
                shape = shape[::-1] if count == 63 else shape
                packed[i] = len(shape)
                packed[4+16*i:4+16*i+shape.size] = shape.ravel()
                polygons.append(shape)
            shifts = rng.uniform(-45, 45, (count, 4, 2)).astype(np.float32)
            shifts[0] = 0  # containment, crossing, touching and separated pairs
            shifts[:, pieces:] = 0
            packed[68:68+count*8] = shifts.ravel()
            distances = []
            for shift in shifts:
                moved = [Polygon(p + d) for p, d in zip(polygons, shift)]
                assert all(p.is_valid for p in moved)
                distances.extend(moved[a].distance(moved[b]) if b < pieces else -1
                                 for a, b in combinations(range(4), 2))
            lines.extend((str(count), ' '.join(format(v, '.9g') for v in packed),
                          ' '.join(format(v, '.12g') for v in distances)))
            cases += 1
    if stress:
        for trial in range(60):
            pieces, count = 2+trial % 3, (1, 2, 64)[trial % 3]
            packed = np.zeros(580, dtype=np.float32)
            polygons = []
            for i in range(pieces):
                n = 3+(trial+i) % 6
                angles = np.linspace(0, 2*np.pi, n, endpoint=False)+rng.uniform(-3, 3)
                radius = rng.uniform(8, 35, n)
                shape = np.column_stack((np.cos(angles), np.sin(angles)))*radius[:, None]
                shape += rng.uniform(-450, 450, 2) if trial % 2 else rng.uniform(-20, 20, 2)
                shape = shape.astype(np.float32).astype(float)
                if i % 2: shape = shape[::-1]
                assert Polygon(shape).is_valid
                packed[i] = n
                packed[4+16*i:4+16*i+shape.size] = shape.ravel()
                polygons.append(shape)
            shifts = rng.uniform(-128, 128, (count, 4, 2)).astype(np.float32)
            shifts[:, pieces:] = 0
            packed[68:68+count*8] = shifts.ravel()
            distances = []
            for shift in shifts:
                moved = [Polygon(p+d) for p, d in zip(polygons, shift)]
                distances.extend(moved[a].distance(moved[b]) if b < pieces else -1
                                 for a, b in combinations(range(4), 2))
            lines.extend((str(count), ' '.join(format(v, '.9g') for v in packed),
                          ' '.join(format(v, '.12g') for v in distances)))
            cases += 1
    lines[0] = str(cases)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(lines)+'\n', encoding='ascii')
    typer.echo(str(output))


if __name__ == '__main__':
    app()
