"""生成独立 GEOS 黄金距离，供 HLS 仿真使用。"""
from pathlib import Path
import struct
import numpy as np
import typer
from shapely.geometry import Polygon
from shapely.affinity import translate
from shapely.geometry.polygon import orient

app = typer.Typer()

@app.command()
def generate(output: Path = typer.Option(..., '--output'),
             axi_output: Path | None = typer.Option(None, '--axi-output')):
    rng = np.random.default_rng(20261013)
    cases = []
    for case in range(8):
        polygons = []
        for p in range(4):
            count = (3,4,5,8)[(case+p)%4]
            angles = np.arange(count)*2*np.pi/count + rng.uniform(-3,3)
            vertices = np.column_stack((np.cos(angles),np.sin(angles)))
            vertices *= rng.uniform(8,40,(1,2))
            vertices += rng.uniform(-70,70,(1,2))
            polygons.append(orient(Polygon(vertices), sign=1))
        packed = np.zeros(76,dtype=np.float32)
        for p,poly in enumerate(polygons):
            points=np.asarray(poly.exterior.coords[:-1]);packed[p]=len(points)
            packed[4+2*p:6+2*p]=[poly.centroid.x,poly.centroid.y]
            packed[12+16*p:12+16*p+points.size]=points.ravel()
        expected=[]
        for alpha in np.linspace(0,1,401):
            moved=[translate(poly,alpha*poly.centroid.x,alpha*poly.centroid.y) for poly in polygons]
            expected.append(min(moved[i].distance(moved[j]) for i in range(4) for j in range(i+1,4)))
        cases.append((packed,expected))
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(str(len(cases))+'\n'+'\n'.join(' '.join(format(float(v),'.10g') for v in [*packed,*expected]) for packed,expected in cases)+'\n')
    if axi_output is not None:
        bits = lambda value: f'{struct.unpack("<I", struct.pack("<f", value))[0]:08x}'
        # Put the overlap-to-separated case first: early random cases can
        # have zero minimum distance across the entire scan.
        axi_cases = cases[2:4] + cases[:2] + cases[4:]
        rows = [str(len(axi_cases))]
        for packed, expected in axi_cases:
            rows.extend(bits(float(v)) for v in packed)
            rows.extend(bits(max(0, d-.005)**2)+' '+bits((d+.005)**2) for d in expected)
        axi_output.parent.mkdir(parents=True, exist_ok=True)
        axi_output.write_text('\n'.join(rows)+'\n', encoding='ascii')

if __name__ == '__main__':
    app()
