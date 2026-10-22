"""板端求解适配入口；设备与运动检查由板端执行层负责。"""
import os
import threading
import logging
import json
import time
from puzzle_solver import plan_sheet_moves as _cpu_plan_sheet_moves
from puzzle_solver import PlanningOptions

_backend_lock = threading.Lock()
_backend = None
_clearance_backend = None
_candidate_backend = None


def _selected(name, supplied):
    selected = 'cpu' if supplied is not None else os.environ.get(name, 'cpu').lower()
    if selected not in {'cpu', 'pl'}:
        raise ValueError(f'{name} 必须为 cpu 或 pl')
    return selected


def _timed_plan(pieces, options, kwargs):
    started = time.perf_counter()
    fields = ('request_count', 'batch_count', 'scan_count', 'numerical_cpu_fallbacks',
              'unsupported_count', 'pack_seconds', 'transfer_seconds', 'kernel_wait_seconds')
    backends = {name: value for name, value in kwargs.items()}
    before = {name: {key: getattr(value, key, 0) for key in fields} for name, value in backends.items()}
    try:
        return _cpu_plan_sheet_moves(pieces, options, **kwargs)
    finally:
        stats = {name: {key: getattr(value, key, 0) - before[name][key] for key in fields}
                 for name, value in backends.items()}
        logging.getLogger(__name__).info('PL_PERFORMANCE %s', json.dumps(
            dict(mode=options.mode, elapsed_seconds=time.perf_counter()-started, backends=stats)))


def plan_sheet_moves(pieces, options=PlanningOptions(), *, rigid_backend=None, clearance_backend=None, candidate_backend=None):
    """固定和随机共享配准核；四凸片间隙核仅用于固定模式。"""
    global _backend, _clearance_backend, _candidate_backend
    rigid = _selected('EC_RIGID_BACKEND', rigid_backend)
    clearance = _selected('EC_CLEARANCE_BACKEND', clearance_backend)
    candidate = _selected('EC_CANDIDATE_BACKEND', candidate_backend)
    if options.mode == 'random':
        clearance = 'cpu'
    else:
        candidate = 'cpu'
    kwargs = {}
    if rigid_backend is not None:
        kwargs['rigid_backend'] = rigid_backend
    if clearance_backend is not None:
        kwargs['clearance_backend'] = clearance_backend
    if candidate_backend is not None:
        kwargs['candidate_backend'] = candidate_backend
    if rigid == 'cpu' and clearance == 'cpu' and candidate == 'cpu':
        return _timed_plan(pieces, options, kwargs)
    with _backend_lock:
        bitstream = os.environ.get('EC_PL_BITSTREAM', '')
        if not bitstream:
            raise RuntimeError('PL 求解需要 EC_PL_BITSTREAM 指向已加载的匹配 bit/HWH')
        if rigid == 'pl' and _backend is None:
            from .pl_rigid import PynqRigidFitBackend
            _backend = PynqRigidFitBackend(bitstream)
        if clearance == 'pl' and _clearance_backend is None:
            from .pl_clearance import PynqClearanceScanBackend
            _clearance_backend = PynqClearanceScanBackend(bitstream)
        if candidate == 'pl' and _candidate_backend is None:
            from .pl_candidate import PynqCandidateScanBackend
            _candidate_backend = PynqCandidateScanBackend(bitstream)
        if rigid == 'pl':
            kwargs['rigid_backend'] = _backend
        if clearance == 'pl':
            kwargs['clearance_backend'] = _clearance_backend
        if candidate == 'pl':
            kwargs['candidate_backend'] = _candidate_backend
        return _timed_plan(pieces, options, kwargs)
__all__ = ["plan_sheet_moves"]
