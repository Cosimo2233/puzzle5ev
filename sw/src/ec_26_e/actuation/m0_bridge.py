"""通过独立 puzzle_bridge 公共 API 共享两路 UART 和磁铁的会话。

构造不访问硬件；open 只查询状态。执行器验证完整计划后，首次输出才 ARM。
失败锁死本次会话，不重连、不重发 DATA；磁铁拥有整个会话的关闭责任。
"""
from __future__ import annotations

import json
import logging

from puzzle_bridge.client import Bridge

from ec_26_e.motion import FocRotationAxis, Servo42CSerial
from ec_26_e.motion.f32c_controller import F32CMotor

LOGGER = logging.getLogger(__name__)


class M0Session:
    def __init__(self, port: str, *, bridge_factory=Bridge):
        self.bridge = bridge_factory(port)
        self.opened = False
        self.armed = False
        self.closed = False
        self.failed = False

    def call(self, operation, *args, **kwargs):
        if self.closed or self.failed:
            raise RuntimeError("M0 会话已结束或失败，禁止继续输出")
        try:
            return operation(*args, **kwargs)
        except Exception:
            self.failed = True
            self.close()
            raise

    def open(self):
        if self.opened:
            return
        self.call(self.bridge.open)
        self.opened = True
        status = self.call(self.bridge.status)
        if (status["version"] != 1 or status["armed"] or status["magnet"]
                or status["fault"]):
            self.failed = True
            # Query only: retain the first failure, never ARM/DISARM to clear it.
            # Old host libraries/firmware may lack the optional diagnostic API.
            if status["fault"] and not status["armed"] and not status["magnet"]:
                diagnostics = getattr(self.bridge, "diagnostics", None)
                if callable(diagnostics):
                    try:
                        LOGGER.error("M0_UART_DIAGNOSTICS %s", json.dumps(
                            diagnostics(), ensure_ascii=False, sort_keys=True))
                    except Exception as error:
                        LOGGER.warning("M0 故障明细不可用，保留原始 STATUS：%s", error)
            self.close()
            reason = {1: "心跳超时", 2: "UART 接收异常/溢出（故障已锁存）",
                      3: "上游发送队列满"}.get(status["fault"], "状态检查未通过")
            raise RuntimeError(f"M0 初始状态不允许执行：{reason}；{status}")

    def arm(self):
        self.open()
        if not self.armed:
            self.call(self.bridge.arm)
            self.armed = True

    def write(self, channel, data):
        self.arm()
        count = self.call(self.bridge.write, channel, bytes(data))
        if count != len(data):
            self.failed = True
            self.close()
            raise RuntimeError("M0 未完整接受报文；禁止重发")
        return count

    def read(self, channel, size, timeout=0):
        if not self.opened:
            raise RuntimeError("M0 会话未打开")
        return self.call(self.bridge.read, channel, size, timeout=timeout)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.bridge.close()
        except Exception:
            if not self.failed:
                raise
            LOGGER.exception("M0 故障后关闭失败；心跳停止，等待固件租期失效")
        finally:
            self.opened = False
            self.armed = False


class M0Channel:
    """同时满足 Servo42C SerialPort 与 F32C UART 接口，保持字节流语义。"""
    def __init__(self, session: M0Session, channel: int):
        if channel not in (1, 2):
            raise ValueError("M0 通道必须为 1 或 2")
        self.session = session
        self.channel = channel
        self.is_open = False
        self.buffer = bytearray()

    def open(self):
        self.session.open()
        self.is_open = True
        return self

    def _check(self):
        if not self.is_open:
            raise RuntimeError("M0 通道未打开")

    @property
    def in_waiting(self):
        self._check()
        self.buffer.extend(self.session.read(self.channel, 4096))
        return len(self.buffer)

    def read(self, size=1):
        self._check()
        if size <= 0:
            return b""
        if not self.buffer:
            self.buffer.extend(self.session.read(self.channel, max(size, 128)))
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def write(self, data):
        self._check()
        return self.session.write(self.channel, data)

    def write_all(self, data, timeout=1.0):
        self.write(data)

    def read_some(self, timeout):
        self._check()
        if self.buffer:
            return self.read(len(self.buffer))
        return self.session.read(self.channel, 4096, timeout)

    def flush_input(self):
        self._check()
        self.buffer.clear()
        # 有界清理，持续回包不能让心跳或执行线程饿死。
        for _ in range(16):
            if not self.session.read(self.channel, 4096):
                return
        raise RuntimeError("M0 通道持续收到数据，无法清空输入")

    def close(self):
        self.is_open = False
        self.buffer.clear()


class M0Magnet:
    def __init__(self, session):
        self.session = session

    def open(self):
        self.session.open()

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise TypeError("enabled 必须为 bool")
        if enabled:
            self.session.arm()
        if self.session.armed:
            self.session.call(self.session.bridge.set_magnet, enabled)
        elif enabled or not self.session.opened:
            raise RuntimeError("M0 磁铁会话未打开")

    def close(self):
        self.session.close()


class M0Hardware:
    def __init__(self, config, *, bridge_factory=Bridge):
        self.session = M0Session(config.bridge_port, bridge_factory=bridge_factory)

    def magnet(self, config):
        return M0Magnet(self.session)

    def transport(self, config):
        if config.serial.baudrate != 115200:
            raise ValueError("M0 固件的 XYZ 串口固定为 115200")
        channel = M0Channel(self.session, 1)
        return Servo42CSerial(config.serial, serial_factory=lambda _: channel.open())

    def rotation(self, config):
        cfg = config.rotation
        motor = F32CMotor(
            port=cfg.port, address=cfg.address, max_rpm=cfg.max_rpm,
            feedback_timeout=cfg.feedback_timeout_seconds,
            feedback_attempts=cfg.feedback_attempts,
            clockwise_mechanical_sign=cfg.clockwise_mechanical_sign,
            uart=M0Channel(self.session, 2),
        )
        return FocRotationAxis(cfg, motor=motor)
