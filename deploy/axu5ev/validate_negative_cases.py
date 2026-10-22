"""Plan-only rejection cases on the board; all inputs are deliberately invalid."""
import argparse
import json
import os
from pathlib import Path

import numpy as np

from puzzle_solver import PuzzlePiece, PlanningOptions, PlanningError
from ec_26_e.control import planner
from ec_26_e.gui.controller import load_profile
from ec_26_e.gui.menu_model import MODES
from ec_26_e.tasks.execution_lock import TaskExecutionLock
from ec_26_e.vision import Resize
from ec_26_e.vision.factory import create_production_detector


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(EC_MOTION_ENABLED='false', EC_RIGID_BACKEND='pl', EC_CLEARANCE_BACKEND='pl')
    corpus = json.loads((args.root/'corpus.json').read_text())
    fixed = [PuzzlePiece(p['id'], tuple(map(tuple, p['vertices_mm']))) for p in corpus[0]['pieces']]
    square = lambda name, x: PuzzlePiece(name, ((x, 0.), (x+20., 0.), (x+20., 20.), (x, 20.)))
    cases = [('missing_fixed_piece', fixed[:3], 'fixed'),
             ('one_random_piece', [square('a', 0)], 'random'),
             ('five_random_pieces', [square(str(i), i*25.) for i in range(5)], 'random'),
             ('no_legal_rectangle', [square('a', 0), square('b', 30)], 'random'),
             ('short_random_edge', [PuzzlePiece('a', ((0., 0.), (10., 0.), (10., 60.), (0., 60.))),
                                    square('b', 40)], 'random')]
    rows = []
    with TaskExecutionLock():
        for name, pieces, mode in cases:
            try:
                planner.plan_sheet_moves(tuple(pieces), PlanningOptions(mode=mode, competition_rules=True))
                rows.append({'case': name, 'rejected': False})
            except (PlanningError, ValueError) as error:
                rows.append({'case': name, 'rejected': True, 'reason': str(error)})
        label = next(key for key, value in MODES.items() if value[0] == 'random')
        profile = load_profile(args.root/'machine.env', args.root/'tasks', label, 'pl')
        with create_production_detector(profile.detect1) as owner:
            blank = np.full((540, 762, 3), (180, 100, 30), dtype=np.uint8)
            boundary = blank.copy()
            boundary[100:220, :120] = 240
            boundary[300:420, 250:370] = 240
            for name, image in [('empty_workspace', blank), ('boundary_occlusion', boundary)]:
                result = owner.detector.detect(image)
                rows.append({'case': name, 'rejected': not result.pieces, 'reason': owner.detector.last_failure})
        try:
            Resize(profile.resize).resize(np.zeros((720, 1280, 3), dtype=np.uint8))
            rows.append({'case': 'covered_a4', 'rejected': False})
        except ValueError as error:
            rows.append({'case': 'covered_a4', 'rejected': True, 'reason': str(error)})
    report = {'motion_executed': False, 'passed': all(r['rejected'] for r in rows), 'cases': rows}
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
