"""板端集成测试使用的毫米轮廓样本。"""
from ec_26_e.control import PuzzlePiece

FIXED_PIECES = (
    PuzzlePiece(
        "p0",
        ((0, 0), (20, 0), (36, 12), (0, 20)),
    ),
    PuzzlePiece(
        "p1",
        ((0, 20), (36, 12), (76, 42), (0, 30)),
    ),
    PuzzlePiece(
        "p2",
        ((0, 30), (76, 42), (100, 60), (0, 60)),
    ),
    PuzzlePiece(
        "p3",
        ((20, 0), (100, 0), (100, 60), (76, 42), (36, 12)),
    ),
)
