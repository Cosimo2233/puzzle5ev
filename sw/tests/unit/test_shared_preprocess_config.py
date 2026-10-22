"""共用 PL 时模式参数随当前帧提交，不能继承上次选择的模式。"""
from dataclasses import replace
from types import SimpleNamespace

from ec_26_e.gui.controller import SharedPreprocessor
from ec_26_e.vision.contracts import Detect1Config


def test_mode_parameters_are_bound_to_each_lease():
    calls = []
    backend = SimpleNamespace(config=None)
    backend.process = lambda image, seq: calls.append((seq, backend.config))
    shared = SharedPreprocessor(backend)
    config = Detect1Config(blue_ratio_q8=275)
    fixed = shared.for_config(config)
    random = shared.for_config(replace(config, blue_ratio_q8=300, blue_background_fraction_q8=218))
    for lease in (random, fixed, random):
        lease.process(None, 1)
    assert [(seq, p.blue_ratio_q8, p.blue_background_fraction_q8) for seq, p in calls] == [
        (1,300,218), (2,275,0), (3,300,218)]
