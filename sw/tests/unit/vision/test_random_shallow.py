"""Synthetic legal five-edge pieces retain shallow seams through temporal review."""
from pathlib import Path
import cv2
import pytest
from ec_26_e.gui.controller import load_profile
from ec_26_e.gui.menu_model import MODES
from ec_26_e.vision import Resize
from ec_26_e.vision.factory import create_production_detector


@pytest.mark.parametrize('case,count', [('random2-18-two_polyline',2),('random3-02-three_y',3)])
def test_supported_shallow_fifth_corner_is_recovered(case,count):
    root=Path(__file__).resolve().parents[4]
    label=next(k for k,v in MODES.items() if v[0]=='random')
    profile=load_profile(root/'deploy/axu5ev/board.env.example',root/'deploy/axu5ev/tasks',label,'cpu')
    image=cv2.imread(str(Path(__file__).parents[2]/'fixtures/vision'/('synthetic-'+case+'.png')))
    rect=Resize(profile.resize).resize(image)
    with create_production_detector(profile.detect1) as detector:
        for _ in range(6): result=detector.detect(rect.image_bgr)
        assert len(result.pieces)==count
        assert any(len(piece.corners_px)==5 for piece in result.pieces)
        assert all(piece.line_residual * 292/761 < .35 for piece in result.pieces)
