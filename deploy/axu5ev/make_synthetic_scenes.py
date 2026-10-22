"""Generate labelled synthetic camera fixtures, never represent them as live captures."""
import json
from pathlib import Path
import argparse
import cv2
import numpy as np
from shapely.geometry import Polygon


def render(row, seed):
    rng = np.random.default_rng(seed)
    placed = []
    polygons = []
    # Pack separated pieces in the source half using true polygons, not boxes.
    for piece in sorted(row['pieces'],key=lambda p:-Polygon(p['vertices_mm']).area):
        original = np.asarray(piece['vertices_mm'],float)
        for attempt in range(10000):
            angle = rng.uniform(-np.pi,np.pi)
            rotated = original @ np.array([[np.cos(angle),np.sin(angle)],[-np.sin(angle),np.cos(angle)]])
            rotated -= rotated.min(axis=0)
            extent = rotated.max(axis=0)
            if extent[0] > 189 or extent[1] > 126:
                continue
            vertices = rotated+rng.uniform([8,8],[199-extent[0],138-extent[1]])
            shape = Polygon(vertices)
            if all(shape.distance(other)>6 for other in polygons):
                polygons.append(shape); placed.append(vertices);break
        else:
            raise ValueError('packing failed')
    canvas = np.full((540,762,3),(190,150,35),np.uint8)
    for index, vertices in enumerate(placed):
        pixels = np.column_stack(((1-vertices[:,1]/292)*761, vertices[:,0]/207*539))
        mask = np.zeros(canvas.shape[:2],np.uint8)
        cv2.fillPoly(mask,[np.rint(pixels).astype(np.int32)],255)
        canvas[mask>0] = (235,235,235)
        if seed % 2:
            # Interior black/red card-like glyphs; retain a physical white border.
            inner = cv2.erode(mask,np.ones((13,13),np.uint8))
            glyph = np.zeros_like(mask)
            center = np.rint(pixels.mean(axis=0)).astype(int)
            cv2.putText(glyph,'K',tuple(center),cv2.FONT_HERSHEY_SIMPLEX,1.1,255,4)
            cv2.circle(glyph,tuple(center+[-8,17]),9,255,-1)
            canvas[(glyph>0)&(inner>0)] = (5,5,5) if index%2 else (15,15,180)
    corners = np.array([[.202502,.1585535],[.842846,.1376912],[.8491009,.9262865],[.2173573,.9582754]],np.float32)*[1279,719]
    homography = cv2.getPerspectiveTransform(np.float32([[0,0],[761,0],[761,539],[0,539]]),corners.astype(np.float32))
    raw = cv2.warpPerspective(canvas,homography,(1280,720),borderValue=(18,18,18))
    gain = (.8,1.,1.07)[seed%3]
    raw = np.clip(raw.astype(float)*gain,0,255).astype(np.uint8)
    return raw, placed


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--corpus',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    manifest=[]
    for index,row in enumerate(json.loads(args.corpus.read_text())):
        if row['mode']!='random': continue
        for retry in range(30):
            try:
                raw, vertices=render(row,index+6000*retry)
                break
            except ValueError:
                if retry==29: raise
        path=args.output/(row['case']+'.png')
        cv2.imwrite(str(path),raw)
        manifest.append(dict(case=row['case'],file=path.name,count=len(vertices),
            measurement='synthetic_camera',printed=bool(index%2),
            vertices=[v.tolist() for v in vertices]))
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(len(manifest))


if __name__=='__main__': main()
