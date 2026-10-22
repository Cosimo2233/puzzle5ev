"""Clearance transport faults and independent GEOS end-to-end equivalence."""
import json
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
import threading

import numpy as np
import pytest
from shapely.geometry import Polygon

from ec_26_e.control.pl_clearance import ABI, INPUT_BASE, OUTPUT_BASE, PynqClearanceScanBackend, pack_scan
from puzzle_solver import PuzzlePiece, PlanningOptions, plan_sheet_moves


class FakeIP:
    def __init__(self, fault=None):
        self.fault, self.registers, self.started = fault, {}, 0
        self.mmio = SimpleNamespace(array=np.zeros(0x10000//4, dtype=np.uint32))

    def write(self, offset, value):
        self.registers[offset] = value
        if offset != 0:
            return
        self.started += 1
        row = self.mmio.array[INPUT_BASE//4:INPUT_BASE//4+76].view(np.float32)
        vertices = [row[12+16*i:12+16*i+2*int(row[i])].reshape(-1, 2).astype(float) for i in range(4)]
        centers = row[4:12].reshape(4, 2).astype(float)
        output = []
        for alpha in np.linspace(0, 1, 401):
            polygons = [Polygon(p + alpha*c) for p, c in zip(vertices, centers)]
            output.append(min(a.distance(b) for a, b in combinations(polygons, 2))**2)
        output = np.asarray(output, dtype=np.float32)
        if self.fault == 'nan': output[0] = np.nan
        if self.fault == 'negative': output[0] = -1
        self.mmio.array[OUTPUT_BASE//4:OUTPUT_BASE//4+401] = output.view(np.uint32)

    def read(self, offset):
        if offset == 0: return 4 if not self.started else (0 if self.fault == 'timeout' else 6)
        if offset == 0x28: return ABI + int(self.fault == 'abi')
        if offset == 0x20: return self.registers[0x10] + int(self.fault == 'id')
        if offset == 0x30: return int(self.fault == 'status')
        return self.registers.get(offset, 0)


def backend(fault=None):
    device = PynqClearanceScanBackend.__new__(PynqClearanceScanBackend)
    device.ip, device._lock = FakeIP(fault), threading.Lock()
    device._failed = device._closed = False
    device._request_id = device.scan_count = device.unsupported_count = 0
    device.request_count = 0
    device.pack_seconds = device.transfer_seconds = device.kernel_wait_seconds = 0.0
    device.timeout_seconds = .002
    device._lockfile = SimpleNamespace(close=lambda: None)
    return device


def geometry():
    square = np.array([[0., 0.], [20., 0.], [20., 20.], [0., 20.]])
    vertices = [square + offset for offset in ([0, 0], [20, 0], [0, 20], [20, 20])]
    return vertices, np.array([v.mean(axis=0) for v in vertices])


def test_packing_winding_and_scan_sequence():
    vertices, centers = geometry()
    reversed_vertices = [v[::-1] for v in vertices]
    assert pack_scan(reversed_vertices, centers) is not None
    device = backend()
    device._request_id = 0xffffffff
    values = device.minimum_distances(vertices, centers)
    np.testing.assert_allclose(values, np.linspace(0, 20, 401), atol=1e-5)
    assert device._request_id == 1 and device.scan_count == 1
    device.close()
    with pytest.raises(RuntimeError): device.minimum_distances(vertices, centers)


@pytest.mark.parametrize('fault', ['timeout', 'abi', 'id', 'status', 'nan', 'negative'])
def test_fault_poisoning(fault):
    device = backend(fault)
    with pytest.raises((RuntimeError, TimeoutError)): device.minimum_distances(*geometry())
    started = device.ip.started
    with pytest.raises(RuntimeError): device.minimum_distances(*geometry())
    assert device.ip.started == started


def test_unsupported_and_invalid_geometry_never_touches_hardware():
    device = backend()
    vertices, centers = geometry()
    assert device.minimum_distances(vertices[:3], centers[:3]) is None
    vertices[0] = np.array([[0, 0], [20, 0], [10, 5], [20, 20], [0, 20]])
    assert device.minimum_distances(vertices, centers) is None
    vertices[0][0, 0] = 1000
    assert device.minimum_distances(vertices, centers) is None
    centers[0, 0] = np.nan
    with pytest.raises(ValueError): device.minimum_distances(vertices, centers)
    assert device.ip.started == 0


@pytest.mark.parametrize('name', ['fixed4', 'live', 'random2', 'random3', 'random4'])
def test_public_complete_plan_equality(name):
    root = Path(__file__).resolve().parents[4]
    path = (root/'sw/tests/fixtures/vision/fixed4-live-input.json' if name == 'live'
            else root/'baselines/upstream'/f'{name}.json')
    payload = json.loads(path.read_text())
    pieces = tuple(PuzzlePiece(p['id'], tuple(map(tuple, p['vertices_mm']))) for p in payload['pieces'])
    options = PlanningOptions(mode=payload['mode'], noise_bound_mm=payload.get('noise_bound_mm', 0))
    cpu = plan_sheet_moves(pieces, options)
    device = backend()
    accelerated = plan_sheet_moves(pieces, options, clearance_backend=device)
    assert accelerated.to_dict() == cpu.to_dict()
    assert (device.scan_count > 0) == (name in {'fixed4', 'live'})
