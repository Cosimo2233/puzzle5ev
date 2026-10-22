"""PL 驱动协议、故障停用和完整固定/随机方案对照（主机，不是上板）。"""
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import numpy as np
import pytest

from puzzle_solver import PuzzlePiece, PlanningOptions, plan_sheet_moves
from puzzle_solver.acceleration import CpuRigidFitBackend, RigidFitRequest
from ec_26_e.control.pl_rigid import PynqRigidFitBackend, ABI, INPUT_BASE, OUTPUT_BASE


class FakeIP:
    def __init__(self, fault=None):
        self.fault, self.registers = fault, {}
        self.mmio = SimpleNamespace(array=np.zeros(0x10000//4, dtype=np.uint32))
        self.started = 0

    def write(self, offset, value):
        self.registers[offset] = value
        if offset != 0:
            return
        self.started += 1
        count = self.registers[0x18]
        packed = self.mmio.array[INPUT_BASE//4:INPUT_BASE//4+count*41].view(np.float32).reshape(count, 41)
        output = np.zeros((count, 6), dtype=np.float32)
        for i, row in enumerate(packed):
            n = int(row[0])
            points = row[1:].reshape(8, 5)[:n]
            # Independent CPU SVD emulates the device, including Float32 transport.
            fit = CpuRigidFitBackend().fit_batch([RigidFitRequest(points[:, :2], points[:, 2:4], points[:, 4])])[0]
            output[i] = [0, fit.angle, *fit.translation, fit.rms, fit.rms]
            if self.fault == 'degenerate': output[i, 0] = 2
            if self.fault == 'invalid': output[i, 0] = 1
            if self.fault == 'nan': output[i, 1] = np.nan
        self.mmio.array[OUTPUT_BASE//4:OUTPUT_BASE//4+count*6] = output.ravel().view(np.uint32)

    def read(self, offset):
        if offset == 0: return 4 if not self.started else (0 if self.fault == 'timeout' else 6)
        if offset == 0x28: return ABI + int(self.fault == 'abi')
        if offset == 0x20: return self.registers[0x10] + int(self.fault == 'id')
        if offset == 0x30: return int(self.fault == 'status')
        return self.registers.get(offset, 0)


def backend(fault=None):
    device = PynqRigidFitBackend.__new__(PynqRigidFitBackend)
    device.ip, device._lock = FakeIP(fault), threading.Lock()
    device._failed = device._closed = False
    device._request_id = device.batch_count = device.numerical_cpu_fallbacks = 0
    device.request_count = 0
    device.pack_seconds = device.transfer_seconds = device.kernel_wait_seconds = 0.0
    device.timeout_seconds = 0.002
    device._lockfile = SimpleNamespace(close=lambda: None)
    return device


def request():
    source = np.array([[12., 7.], [81., 9.], [35., 54.], [3., 47.]])
    from puzzle_solver._engine.transforms import transform_points
    return RigidFitRequest(source, transform_points(source, 179.9, [63., -42.]), np.array([.1, .7, .9, .4]))


def test_chunks_sequences_wrap_and_numeric_results():
    device = backend()
    device._request_id = 0xfffffffe
    fits = device.fit_batch([request()] * 129)
    expected = CpuRigidFitBackend().fit_batch([request()])[0]
    assert device.batch_count == 3 and device._request_id == 2
    assert len(fits) == 129
    for fit in fits:
        assert abs(fit.angle - expected.angle) < 1e-4
        np.testing.assert_allclose(fit.translation, expected.translation, atol=2e-4)
    assert device.fit_batch([]) == []
    device.close()
    with pytest.raises(RuntimeError): device.fit_batch([request()])


@pytest.mark.parametrize('fault', ['timeout', 'abi', 'id', 'status', 'invalid', 'nan'])
def test_fault_disables_backend_and_never_reuses_output(fault):
    device = backend(fault)
    with pytest.raises((RuntimeError, TimeoutError)): device.fit_batch([request()])
    started = device.ip.started
    with pytest.raises(RuntimeError): device.fit_batch([request()])
    assert device.ip.started == started


def test_degenerate_is_explicit_cpu_numerical_fallback():
    device = backend('degenerate')
    result = device.fit_batch([request()])[0]
    expected = CpuRigidFitBackend().fit_batch([request()])[0]
    assert device.numerical_cpu_fallbacks == 1
    assert result.angle == expected.angle
    np.testing.assert_array_equal(result.translation, expected.translation)


@pytest.mark.parametrize('kind', ['nan', 'shape', 'count', 'range', 'weights', 'ratio'])
def test_invalid_input_rejected_before_any_hardware_access(kind):
    device = backend()
    source, target, weights = request().source.copy(), request().target.copy(), request().weights.copy()
    if kind == 'nan': source[0, 0] = np.nan
    if kind == 'shape': target = target[:-1]
    if kind == 'count': source = target = np.zeros((9, 2)); weights = np.ones(9)
    if kind == 'range': source[0, 0] = 4097
    if kind == 'weights': weights[0] = -1
    if kind == 'ratio': weights[0], weights[1] = 1e-6, 10
    with pytest.raises(ValueError): device.fit_batch([request(), RigidFitRequest(source, target, weights)])
    assert device.ip.started == 0


@pytest.mark.parametrize('name', ['fixed4', 'random2', 'random3', 'random4'])
def test_complete_fixed_and_random_plans_use_backend_and_match_cpu(name):
    root = Path(__file__).resolve().parents[4]
    payload = json.loads((root / 'baselines/upstream' / (name + '.json')).read_text())
    pieces = tuple(PuzzlePiece(p['id'], tuple(map(tuple, p['vertices_mm']))) for p in payload['pieces'])
    options = PlanningOptions(mode=payload['mode'], noise_bound_mm=payload.get('noise_bound_mm', 0))
    cpu = plan_sheet_moves(pieces, options)
    device = backend()
    accelerated = plan_sheet_moves(pieces, options, rigid_backend=device)
    assert len(cpu.moves) == len(accelerated.moves)
    assert (cpu.mode, cpu.ambiguous) == (accelerated.mode, accelerated.ambiguous)
    np.testing.assert_allclose((cpu.target_width_mm, cpu.target_height_mm),
        (accelerated.target_width_mm, accelerated.target_height_mm), atol=0.002, rtol=0)
    for a, b in zip(cpu.moves, accelerated.moves):
        assert (a.order, a.piece_id, a.template_id) == (b.order, b.piece_id, b.template_id)
        np.testing.assert_allclose(a.target_vertices_a4_mm, b.target_vertices_a4_mm, atol=0.002)
        np.testing.assert_allclose(a.pick_xy_a4_mm, b.pick_xy_a4_mm, atol=0.002)
        np.testing.assert_allclose(a.place_xy_a4_mm, b.place_xy_a4_mm, atol=0.002)
        assert abs((a.rotation_deg_clockwise-b.rotation_deg_clockwise+180)%360-180) < 0.002
    assert device.batch_count > 0 and device.request_count > 0


@pytest.mark.parametrize('noise', [0.1, 0.7, 2.0])
def test_fixed_candidate_order_and_robust_estimators_with_noise(noise):
    from puzzle_solver._engine.solver.preprocess import preprocess_observations
    from puzzle_solver._engine.solver.fixed_template_solver import solve_fixed
    root = Path(__file__).resolve().parents[4]
    payload = json.loads((root / 'baselines/upstream/fixed4.json').read_text())
    rng = np.random.default_rng(981)
    for piece in payload['pieces']:
        vertices = np.asarray(piece['vertices_mm'], float)
        vertices += rng.normal(0, noise, vertices.shape)
        piece['vertices_mm'] = vertices.tolist()
    pieces = preprocess_observations(payload)
    cpu = solve_fixed(pieces, uncertainty_mm=noise)
    accelerated = solve_fixed(pieces, uncertainty_mm=noise, rigid_backend=backend())
    assert len(cpu) == len(accelerated)
    for a, b in zip(cpu, accelerated):
        assert [p.template_id for p in a.poses] == [p.template_id for p in b.poses]
        assert [p.estimator for p in a.poses] == [p.estimator for p in b.poses]
        assert abs(a.total - b.total) < 0.002
        for p, q in zip(a.poses, b.poses):
            np.testing.assert_allclose(p.target_vertices_local, q.target_vertices_local, atol=0.002)
