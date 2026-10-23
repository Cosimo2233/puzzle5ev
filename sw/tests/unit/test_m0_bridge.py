"""真实桥接客户端 + 模拟线协议，绝不打开实物串口。"""
import time
import struct

import pytest
import serial
from puzzle_bridge import protocol as p

from ec_26_e.actuation import ActuationConfig, ActuationValidationError
from ec_26_e.actuation.m0_bridge import M0Channel, M0Hardware, M0Magnet, M0Session
from ec_26_e.config.task1 import _actuation_from_env
from ec_26_e.motion import MotionSerialConfig
from ec_26_e.tasks.task1.actuation import MovePlanActuator


class WirePeer:
    def __init__(self):
        self.rx = bytearray()
        self.decoder = p.Decoder()
        self.commands = []
        self.armed = False
        self.magnet = False
        self.fault = 0
        self.version = 1
        self.reject = None
        self.short = False
        self.closes = 0
        self.opens = 0

    def open(self, *args, **kwargs):
        self.opens += 1
        assert args[1] == 921600
        return self

    @property
    def in_waiting(self):
        return len(self.rx)

    def read(self, size):
        # Every ACK and RX frame is fragmented across reads.
        size = min(size, 3)
        data = bytes(self.rx[:size])
        del self.rx[:size]
        if not data:
            time.sleep(.001)
        return data

    def write(self, packet):
        for frame in self.decoder.feed(packet):
            self.commands.append(frame)
            if self.short:
                return 1
            reject = self.reject == frame.kind
            if not reject:
                if frame.kind == p.ARM:
                    self.armed = True
                elif frame.kind == p.DISARM:
                    self.armed = self.magnet = False
                elif frame.kind == p.MAGNET:
                    self.magnet = bool(frame.payload[0])
                elif frame.kind == p.DATA:
                    # Interleaved, fragmented downstream streams before ACK.
                    for ch, data in ((2, b"ro"), (1, b"x"), (2, b"t"), (1, b"yz")):
                        self.rx.extend(p.encode(p.Frame(p.RX, ch, 0, data)))
            ack = bytes((frame.kind, 3 if reject else 0, self.armed,
                         self.magnet, self.fault, self.version)) + bytes(4)
            if frame.kind == p.DIAGNOSTICS and not reject:
                ack += struct.pack("<BBI27I", 1, 5, 1000, *([0] * 27))
            self.rx.extend(p.encode(p.Frame(p.ACK, frame.channel, frame.sequence, ack)))
        return len(packet)

    def close(self):
        self.closes += 1


@pytest.fixture
def peer(monkeypatch):
    peer = WirePeer()
    monkeypatch.setattr(serial, "Serial", peer.open)
    return peer


def config():
    return ActuationConfig(serial=MotionSerialConfig(), magnet=None, bridge_port="fake")


def test_construction_and_invalid_plan_never_open(peer):
    actuator = MovePlanActuator(config())
    with pytest.raises(ActuationValidationError):
        actuator.execute(None)
    assert peer.opens == 0 and peer.commands == []


def test_open_and_initial_magnet_off_only_query_status(peer):
    session = M0Session("fake")
    magnet = M0Magnet(session)
    magnet.open()
    magnet.set_enabled(False)
    channel = M0Channel(session, 1).open()
    channel.close()
    magnet.close()
    magnet.close()
    assert [f.kind for f in peer.commands] == [p.STATUS]
    assert peer.opens == peer.closes == 1


def test_two_channels_preserve_streams_single_arm_and_owner_close(peer):
    session = M0Session("fake")
    magnet = M0Magnet(session)
    magnet.open()
    xyz, rotation = M0Channel(session, 1).open(), M0Channel(session, 2).open()
    xyz.write(b"\x7e\x7d")
    assert xyz.in_waiting == 3
    assert xyz.read(1) == b"x"
    assert xyz.read(8) == b"yz"
    assert rotation.read_some(.05) == b"rot"
    rotation.write_all(b"\xfa\x02")
    magnet.set_enabled(True)
    rotation.close()
    xyz.close()
    assert peer.closes == 0
    magnet.set_enabled(False)
    magnet.close()
    kinds = [f.kind for f in peer.commands]
    assert kinds.count(p.ARM) == 1 and kinds[-1] == p.DISARM
    assert [(f.channel, f.payload) for f in peer.commands if f.kind == p.DATA] == [
        (1, b"\x7e\x7d"), (2, b"\xfa\x02")]
    assert not peer.armed and not peer.magnet


@pytest.mark.parametrize("field,value", [
    ("armed", True), ("magnet", True), ("fault", 2), ("version", 2),
])
def test_unsafe_initial_status_never_arms(peer, field, value):
    setattr(peer, field, value)
    with pytest.raises(RuntimeError, match="初始状态"):
        M0Session("fake").open()
    assert [f.kind for f in peer.commands] == (
        [p.STATUS, p.DIAGNOSTICS] if field == "fault" else [p.STATUS])
    assert peer.closes == 1


def test_latched_fault_is_recorded_without_clearing_or_arming(peer, caplog):
    peer.fault = 2
    with pytest.raises(RuntimeError, match="UART 接收异常.*故障已锁存"):
        M0Session("fake").open()
    assert "M0_UART_DIAGNOSTICS" in caplog.text
    assert '"rx_enabled": false' in caplog.text
    assert peer.fault == 2 and not peer.armed and not peer.magnet
    assert [f.kind for f in peer.commands] == [p.STATUS, p.DIAGNOSTICS]


def test_failed_diagnostic_does_not_mask_original_status(peer, caplog):
    peer.fault = 2
    peer.reject = p.DIAGNOSTICS
    with pytest.raises(RuntimeError, match="M0 初始状态.*故障已锁存"):
        M0Session("fake").open()
    assert "保留原始 STATUS" in caplog.text
    assert [f.kind for f in peer.commands] == [p.STATUS, p.DIAGNOSTICS]
    assert peer.closes == 1 and peer.fault == 2


def test_old_client_without_diagnostic_api_keeps_original_rejection(peer):
    peer.fault = 2
    session = M0Session("fake")
    session.bridge.diagnostics = None
    with pytest.raises(RuntimeError, match="M0 初始状态.*故障已锁存"):
        session.open()
    assert [f.kind for f in peer.commands] == [p.STATUS]
    assert peer.closes == 1 and not peer.armed


@pytest.mark.parametrize("short", [False, True])
def test_data_failure_is_terminal_and_never_retried(peer, short):
    session = M0Session("fake")
    channel = M0Channel(session, 1).open()
    session.arm()
    peer.short = short
    peer.reject = p.DATA
    with pytest.raises(Exception):
        channel.write(b"move")
    with pytest.raises(RuntimeError):
        channel.write(b"move")
    assert sum(f.kind == p.DATA for f in peer.commands) == 1
    assert peer.closes == 1
    assert session.bridge._stop.is_set()


def test_heartbeat_failure_propagates_before_next_output(peer):
    session = M0Session("fake")
    channel = M0Channel(session, 1).open()
    session.arm()
    peer.reject = p.HEARTBEAT
    deadline = time.monotonic() + 2
    while not session.bridge._stop.is_set() and time.monotonic() < deadline:
        time.sleep(.01)
    assert session.bridge._stop.is_set()
    with pytest.raises(Exception):
        channel.write(b"move")
    assert not any(f.kind == p.DATA for f in peer.commands)
    assert peer.closes == 1


def test_shared_transport_and_f32c_connection(peer):
    hardware = M0Hardware(config())
    magnet = hardware.magnet(config())
    magnet.open()
    xyz = hardware.transport(config())
    rotation = hardware.rotation(config())
    xyz.open()
    rotation._motor.connect()
    xyz.send_frame(b"\xe0\x01")
    rotation._motor.close(disable=False)
    xyz.close()
    magnet.close()
    assert peer.opens == peer.closes == 1


def test_config_m0_requires_no_gpio_and_rejects_bad_backend():
    cfg = _actuation_from_env({"MOTION_BACKEND": "m0"})
    assert cfg.bridge_port == "/dev/ec26e-m0-bridge" and cfg.magnet is None
    with pytest.raises(ValueError):
        _actuation_from_env({"MOTION_BACKEND": "typo"})
    with pytest.raises(ValueError):
        _actuation_from_env({"MOTION_BACKEND": "m0", "M0_BRIDGE_PORT": ""})
