"""串口协议 V0.1：PS 侧生成带路由的二进制报文，不访问硬件。

路由信息只供 PS 使用，不加到 UART 字节中。实际运动仍由现有控制器
执行完整解检查、坐标标定、使能、超时和到位反馈流程。
"""

from dataclasses import dataclass
from enum import Enum

from ec_26_e.motion.contracts import MotorAddress
from ec_26_e.motion.protocol import Servo42CProtocol
from ec_26_e.motion.f32c_controller import (
    DEFAULT_ADDRESS,
    FEEDBACK_TOTAL_ANGLE,
    FEEDBACK_MECHANICAL_ANGLE,
    FUNC_FEEDBACK_QUERY,
    build_multi_turn_angle_frame,
    build_speed_frame,
    make_frame,
)


class UartRoute(Enum):
    """逻辑串口；设备名通过板端部署配置绑定。"""

    MOTION = "motion_uart"
    ROTATION = "rotation_uart"


@dataclass(frozen=True, slots=True)
class SerialPacket:
    """路由和线上原始字节；payload 不包含额外封装头。"""

    route: UartRoute
    payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.route, UartRoute):
            raise TypeError("route 必须是 UartRoute")
        if not isinstance(self.payload, bytes):
            raise TypeError("payload 必须是 bytes")
        if not self.payload:
            raise ValueError("payload 不能为空")

    @property
    def hex_text(self) -> str:
        """仅用于显示；UART 应发送 payload，而非此字符串。"""
        return self.payload.hex(" ").upper()


def xyz_move(address: MotorAddress, speed: int, pulses: int) -> SerialPacket:
    """相对脉冲移动；speed 符号代表协议方向，不直接代表机械方向。"""
    return SerialPacket(
        UartRoute.MOTION, Servo42CProtocol.build_move(address, speed, pulses)
    )


def xyz_enable(address: MotorAddress, enabled: bool) -> SerialPacket:
    return SerialPacket(
        UartRoute.MOTION, Servo42CProtocol.build_enable(address, enabled)
    )


def xyz_stop(address: MotorAddress) -> SerialPacket:
    return SerialPacket(UartRoute.MOTION, Servo42CProtocol.build_stop(address))


def rotation_speed(rpm: int, address: int = DEFAULT_ADDRESS) -> SerialPacket:
    """协议速度编码；机构速度上限由现有旋转控制器另行检查。"""
    return SerialPacket(UartRoute.ROTATION, build_speed_frame(rpm, address))


def rotation_total_target(
    total_angle_deg: float, address: int = DEFAULT_ADDRESS
) -> SerialPacket:
    """驱动器本次上电坐标下的多圈绝对角度，不能直接传入求解器角度。"""
    return SerialPacket(
        UartRoute.ROTATION,
        build_multi_turn_angle_frame(total_angle_deg, address),
    )


def rotation_query(
    *, mechanical: bool = False, address: int = DEFAULT_ADDRESS
) -> SerialPacket:
    if not isinstance(mechanical, bool):
        raise TypeError("mechanical 必须是 bool")
    kind = FEEDBACK_MECHANICAL_ANGLE if mechanical else FEEDBACK_TOTAL_ANGLE
    return SerialPacket(
        UartRoute.ROTATION, make_frame(address, FUNC_FEEDBACK_QUERY, bytes((kind,)))
    )


def example_packets() -> tuple[SerialPacket, ...]:
    """协议示例，不是可执行的运动序列。"""
    return (
        xyz_move(MotorAddress.X0, 50, 1000),
        rotation_speed(15),
        rotation_total_target(90.0),
        rotation_query(),
    )


if __name__ == "__main__":
    for packet in example_packets():
        print(f"{packet.route.value}: {packet.hex_text}")
