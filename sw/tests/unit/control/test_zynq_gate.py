"""验证板端适配层传播算法失败，且共享相同公共类型。"""
import puzzle_solver
import pytest
from ec_26_e import control

def test_public_adapter_identity():
    assert control.PuzzlePiece is puzzle_solver.PuzzlePiece
    assert control.PlanningError is puzzle_solver.PlanningError
    from ec_26_e.control import planner
    assert planner._cpu_plan_sheet_moves is puzzle_solver.plan_sheet_moves


def test_default_adapter_preserves_call_and_result(monkeypatch):
    from ec_26_e.control import planner
    monkeypatch.delenv('EC_RIGID_BACKEND', raising=False)
    monkeypatch.delenv('EC_CLEARANCE_BACKEND', raising=False)
    result = object()
    calls = []
    monkeypatch.setattr(planner, '_cpu_plan_sheet_moves', lambda *args: calls.append(args) or result)
    pieces, options = [], puzzle_solver.PlanningOptions()
    assert planner.plan_sheet_moves(pieces, options) is result
    assert calls == [(pieces, options)]


def test_clearance_backend_is_lazy_cached_and_independent(monkeypatch):
    from ec_26_e.control import planner, pl_clearance
    monkeypatch.setenv('EC_RIGID_BACKEND', 'cpu')
    monkeypatch.setenv('EC_CLEARANCE_BACKEND', 'pl')
    monkeypatch.setenv('EC_PL_BITSTREAM', '/test/design.bit')
    monkeypatch.setattr(planner, '_clearance_backend', None)
    created, calls = [], []
    backend = object()
    monkeypatch.setattr(pl_clearance, 'PynqClearanceScanBackend',
                        lambda path: created.append(path) or backend)
    monkeypatch.setattr(planner, '_cpu_plan_sheet_moves',
                        lambda *args, **kwargs: calls.append(kwargs))
    options = puzzle_solver.PlanningOptions(mode='fixed')
    planner.plan_sheet_moves([], options)
    planner.plan_sheet_moves([], options)
    assert created == ['/test/design.bit']
    assert calls == [{'clearance_backend': backend}]*2


def test_random_uses_rigid_pl_but_never_four_convex_clearance(monkeypatch):
    from ec_26_e.control import planner, pl_rigid, pl_clearance
    monkeypatch.setenv('EC_RIGID_BACKEND', 'pl')
    monkeypatch.setenv('EC_CLEARANCE_BACKEND', 'pl')
    monkeypatch.setenv('EC_PL_BITSTREAM', '/test/design.bit')
    monkeypatch.setattr(planner, '_backend', None)
    backend = object()
    monkeypatch.setattr(pl_rigid, 'PynqRigidFitBackend', lambda path: backend)
    monkeypatch.setattr(pl_clearance, 'PynqClearanceScanBackend',
                        lambda path: pytest.fail('random must not open four-convex clearance core'))
    calls = []
    monkeypatch.setattr(planner, '_cpu_plan_sheet_moves',
                        lambda *args, **kwargs: calls.append(kwargs))
    planner.plan_sheet_moves([], puzzle_solver.PlanningOptions(mode='random'))
    assert calls == [{'rigid_backend': backend}]


def test_random_pl_requires_matching_hardware_configuration(monkeypatch):
    from ec_26_e.control import planner
    monkeypatch.setenv('EC_RIGID_BACKEND', 'pl')
    monkeypatch.delenv('EC_PL_BITSTREAM', raising=False)
    with pytest.raises(RuntimeError, match='EC_PL_BITSTREAM'):
        planner.plan_sheet_moves([], puzzle_solver.PlanningOptions(mode='random'))


def test_explicit_clearance_backend_overrides_environment(monkeypatch):
    from ec_26_e.control import planner
    monkeypatch.setenv('EC_RIGID_BACKEND', 'cpu')
    monkeypatch.setenv('EC_CLEARANCE_BACKEND', 'invalid')
    backend, calls = object(), []
    monkeypatch.setattr(planner, '_cpu_plan_sheet_moves',
                        lambda *args, **kwargs: calls.append(kwargs))
    planner.plan_sheet_moves([], puzzle_solver.PlanningOptions(mode='fixed'), clearance_backend=backend)
    assert calls == [{'clearance_backend': backend}]
