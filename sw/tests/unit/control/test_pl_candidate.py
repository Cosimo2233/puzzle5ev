"""候选核协议、批次边界、凹多边形和失步后的禁止复用。"""
from itertools import combinations
from types import SimpleNamespace
import threading

import numpy as np
import pytest
from shapely.geometry import Polygon
from ec_26_e.control.pl_candidate import ABI, INPUT_BASE, OUTPUT_BASE, INPUT_WORDS, PAIRS, PynqCandidateScanBackend, pack_geometry


class FakeIP:
    def __init__(self, fault=None):
        self.fault, self.registers, self.started = fault, {}, 0
        self.counts = []
        self.mmio = SimpleNamespace(array=np.zeros(0x10000//4, dtype=np.uint32))

    def write(self, offset, value):
        self.registers[offset] = value
        if offset != 0:
            return
        self.started += 1
        count = self.registers[0x18]
        self.counts.append(count)
        row = self.mmio.array[INPUT_BASE//4:INPUT_BASE//4+INPUT_WORDS].view(np.float32)
        vertices = [row[4+16*i:4+16*i+2*int(row[i])].reshape(-1, 2).astype(float)
                    for i in range(4) if row[i] > 0]
        output = []
        for shifts in row[68:68+8*count].reshape(count, 4, 2):
            polygons = [Polygon(v+d) for v, d in zip(vertices, shifts)]
            output.extend(polygons[a].distance(polygons[b])**2 if b < len(polygons) else -1
                          for a, b in PAIRS)
        output = np.asarray(output, dtype=np.float32)
        if self.fault == 'nan': output[0] = np.nan
        if self.fault == 'negative': output[0] = -1
        if self.fault == 'padding': output[1] = 0
        self.mmio.array[OUTPUT_BASE//4:OUTPUT_BASE//4+len(output)] = output.view(np.uint32)

    def read(self, offset):
        if offset == 0: return 4 if not self.started else (0 if self.fault == 'timeout' else 6)
        if offset == 0x28: return ABI + int(self.fault == 'abi')
        if offset == 0x20: return self.registers[0x10] + int(self.fault == 'id')
        if offset == 0x30: return int(self.fault == 'status')
        return self.registers.get(offset, 0)


def backend(fault=None):
    device = PynqCandidateScanBackend.__new__(PynqCandidateScanBackend)
    device.ip, device._lock = FakeIP(fault), threading.Lock()
    device._failed = device._closed = False
    device._request_id = device.request_count = device.batch_count = device.unsupported_count = 0
    device.pack_seconds = device.transfer_seconds = device.kernel_wait_seconds = 0.0
    device.timeout_seconds = .002
    device._lockfile = SimpleNamespace(close=lambda: None)
    return device


def geometry(n=2):
    concave = np.array([[0., 0.], [40, 0], [20, 15], [40, 30], [0, 30]])
    return [(concave + [i*20, i*12])[::(-1 if i % 2 else 1)] for i in range(n)]


@pytest.mark.parametrize('count', [0, 1, 63, 64, 65, 190])
@pytest.mark.parametrize('n', [2, 3, 4])
def test_batch_mapping_and_concave_reference(count, n):
    vertices = geometry(n)
    deltas = np.random.default_rng(27).uniform(-40, 40, (count, n, 2))
    device = backend()
    device._request_id = 0xffffffff
    values = device.pair_distances(vertices, deltas)
    assert values.shape == (count, n*(n-1)//2)
    for i, shifts in enumerate(deltas):
        polygons = [Polygon(v+d) for v, d in zip(vertices, shifts)]
        np.testing.assert_allclose(values[i], [a.distance(b) for a, b in combinations(polygons, 2)], atol=1e-4)
    assert device.request_count == count
    assert device.ip.counts == [min(64, count-start) for start in range(0, count, 64)]
    device.close()
    with pytest.raises(RuntimeError): device.pair_distances(vertices, deltas)


@pytest.mark.parametrize('fault', ['timeout', 'abi', 'id', 'status', 'nan', 'negative', 'padding'])
def test_error_poisoning(fault):
    device = backend(fault)
    args = geometry(), np.zeros((1, 2, 2))
    with pytest.raises((RuntimeError, TimeoutError)): device.pair_distances(*args)
    started = device.ip.started
    with pytest.raises(RuntimeError): device.pair_distances(*args)
    assert device.ip.started == started


def test_invalid_and_unsupported_input():
    vertices = geometry()
    with pytest.raises(ValueError): pack_geometry(vertices, np.full((1, 2, 2), np.nan))
    assert pack_geometry(vertices, np.full((1, 2, 2), 129)) is None
    vertices[0] += 1000
    assert pack_geometry(vertices, np.zeros((1, 2, 2))) is None
    vertices[0] = np.array([[0, 0], [20, 20], [0, 20], [20, 0]])
    with pytest.raises(ValueError): pack_geometry(vertices, np.zeros((1, 2, 2)))


@pytest.mark.parametrize('mode,used', [('fixed', False), ('random', True)])
def test_board_selects_candidate_core_only_for_random(monkeypatch, mode, used):
    from ec_26_e.control import planner
    from puzzle_solver import PlanningOptions
    from unittest.mock import Mock
    sentinel = object()
    call = Mock(return_value='complete-plan')
    monkeypatch.setattr(planner, '_candidate_backend', sentinel)
    monkeypatch.setattr(planner, '_cpu_plan_sheet_moves', call)
    monkeypatch.setenv('EC_RIGID_BACKEND', 'cpu')
    monkeypatch.setenv('EC_CLEARANCE_BACKEND', 'cpu')
    monkeypatch.setenv('EC_CANDIDATE_BACKEND', 'pl')
    monkeypatch.setenv('EC_PL_BITSTREAM', '/verified/puzzle5ev.bit')
    assert planner.plan_sheet_moves([], PlanningOptions(mode=mode)) == 'complete-plan'
    assert (call.call_args.kwargs.get('candidate_backend') is sentinel) is used
