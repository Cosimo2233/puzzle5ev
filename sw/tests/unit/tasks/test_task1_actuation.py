"""验证 MovePlan 无回包执行、时序、FINISH 和安全清理。

该文件只使用假串口、假 GPIO 和假时钟，不访问任何真实设备。可通过
``python -m unittest tests.unit.tasks.test_task1_actuation`` 独立运行。
"""

from __future__ import annotations

from dataclasses import replace
import unittest

from ec_26_e.actuation import (
    ActuationConfig,
    ActuationExecutionError,
    ActuationShutdownError,
    ActuationValidationError,
    MagnetConfig,
    MotionLifecycleState,
    compile_move_plan,
)
from ec_26_e.control import MovePlan, PlannedMove
from ec_26_e.motion import (
    FocRotationConfig,
    MksXYController,
    MotionSerialConfig,
    XYState,
)
from ec_26_e.motion.f32c_controller import (
    F32CProtocolError,
    F32CTimeoutError,
)
from ec_26_e.tasks.task1.actuation import MovePlanActuator


class FakeClock:
    """提供由 sleep 精确推进的单调时钟。"""

    def __init__(self) -> None:
        """从零秒开始。"""

        self.now = 0.0

    def monotonic(self) -> float:
        """返回当前假时间。"""

        return self.now

    def sleep(self, seconds: float) -> None:
        """直接推进假时间而不阻塞测试。"""

        self.now += seconds


class FakeTransport:
    """记录无回包串口生命周期和全部报文。"""

    def __init__(self, *, fail_disable: bool = False) -> None:
        """配置可选失能失败并创建空事件。"""

        self.fail_disable = fail_disable
        self.frames: list[bytes] = []
        self.frame_times: list[float] = []
        self.events: list[str] = []
        self.monotonic = lambda: 0.0
        self.opened = False
        self.closed = False

    def open(self) -> None:
        """记录打开。"""

        self.opened = True
        self.events.append("open")

    def send_frame(self, frame: bytes) -> None:
        """记录报文，并可在失能报文处模拟写入失败。"""

        if (
            self.fail_disable
            and len(frame) >= 3
            and frame[1] == 0xF3
            and frame[2] == 0
        ):
            raise RuntimeError("模拟失能写入失败")
        self.frames.append(frame)
        self.frame_times.append(self.monotonic())

    def discard_replies(self) -> None:
        """记录丢弃但不提供任何回包。"""

        self.events.append("discard")

    def take_reply(self) -> None:
        """无回包测试传输始终立即返回 None。"""

        return None

    def close(self) -> None:
        """记录幂等关闭。"""

        self.closed = True
        self.events.append("close")


class FakeMagnet:
    """记录磁铁电平并支持模拟上磁失败。"""

    def __init__(self, *, fail_on_enable: bool = False) -> None:
        """保存失败开关并初始化事件。"""

        self.fail_on_enable = fail_on_enable
        self.values: list[bool] = []
        self.value_times: list[float] = []
        self.monotonic = lambda: 0.0
        self.opened = False
        self.closed = False

    def open(self) -> None:
        """记录 GPIO 申请。"""

        self.opened = True

    def set_enabled(self, enabled: bool) -> None:
        """记录电平，并可在高电平处模拟故障。"""

        if enabled and self.fail_on_enable:
            raise RuntimeError("模拟上磁失败")
        self.values.append(enabled)
        self.value_times.append(self.monotonic())

    def close(self) -> None:
        """记录释放。"""

        self.closed = True


class FakeCompletionLed:
    """记录完成提示灯电平和生命周期。"""

    def __init__(self) -> None:
        """创建空事件记录。"""

        self.values: list[bool] = []
        self.value_times: list[float] = []
        self.monotonic = lambda: 0.0
        self.opened = False
        self.closed = False

    def open(self) -> None:
        """记录 pin33 已申请。"""

        self.opened = True

    def set_enabled(self, enabled: bool) -> None:
        """记录 pin33 目标电平。"""

        self.values.append(enabled)
        self.value_times.append(self.monotonic())

    def close(self) -> None:
        """记录 pin33 已释放。"""

        self.closed = True


class FakeRotation:
    """记录定向旋转、反馈和安全关闭，不访问真实 UART。"""

    def __init__(
        self,
        *,
        fail_move: bool = False,
        open_error: Exception | None = None,
        move_error: Exception | None = None,
        return_error: Exception | None = None,
        transport: FakeTransport | None = None,
    ) -> None:
        """从机械零点开始，并配置可选运动失败。"""

        self.fail_move = fail_move
        self.open_error = open_error
        self.move_error = move_error
        self.return_error = return_error
        self.transport = transport
        self.angle = 0.0
        self.calls: list[object] = []
        self.frames_before_clockwise: tuple[bytes, ...] = ()
        self.frames_before_counterclockwise: list[tuple[bytes, ...]] = []
        self.opened = False
        self.closed = False

    def open(self) -> None:
        """记录旋转轴使能。"""

        self.opened = True
        self.calls.append("open")
        if self.open_error is not None:
            raise self.open_error

    def current_angle_deg(self) -> float:
        """返回当前模拟机械角。"""

        self.calls.append("angle")
        return self.angle

    def move_clockwise_from_zero(
        self,
        target_degrees: float,
        *,
        wait: bool = True,
    ) -> float:
        """记录顺时针目标并更新模拟机械角。"""

        self.calls.append(("clockwise", target_degrees, wait))
        if self.transport is not None:
            self.frames_before_clockwise = tuple(self.transport.frames)
        if self.fail_move:
            raise RuntimeError("模拟旋转失败")
        if self.move_error is not None:
            raise self.move_error
        self.angle = target_degrees % 360.0
        return -target_degrees

    def return_counterclockwise_to_zero(
        self,
        target_degrees: float,
        *,
        wait: bool = True,
    ) -> float:
        """记录逆时针原路回零并提交零位反馈。"""

        self.calls.append(("counterclockwise", target_degrees, wait))
        if self.transport is not None:
            self.frames_before_counterclockwise.append(
                tuple(self.transport.frames)
            )
        if self.return_error is not None:
            raise self.return_error
        self.angle = 0.0
        return 0.0

    def close(self) -> None:
        """记录失能和关闭。"""

        self.calls.append("close")
        self.closed = True


def make_config(**overrides: object) -> ActuationConfig:
    """构造快速、合法且不关联真实设备的运动配置。"""

    values: dict[str, object] = {
        "serial": MotionSerialConfig(),
        "magnet": MagnetConfig(line_offset=12),
        "rotation": FocRotationConfig(),
        "xy_speed_mm_per_second": 1000.0,
        "xy_fixed_wait_seconds": 0.0,
        "z_boot_degrees": 0.0,
        "z_up_degrees": 180.0,
        "z_down_degrees": 0.0,
        "z_enable_wait_seconds": 0.5,
        "z_move_wait_seconds": 0.001,
        "magnet_attach_wait_seconds": 0.001,
        "magnet_release_wait_seconds": 0.001,
        "rotation_zero_wait_seconds": 0.001,
    }
    values.update(overrides)
    return ActuationConfig(**values)  # type: ignore[arg-type]


def make_plan(
    *,
    pick: tuple[float, float] = (10.0, 20.0),
    place: tuple[float, float] = (30.0, 40.0),
    rotation: float = 123.0,
) -> MovePlan:
    """构造一个包含非零旋转但可平移执行的单周期计划。"""

    vertices = ((1.0, 1.0), (2.0, 1.0), (1.0, 2.0))
    move = PlannedMove(
        order=1,
        piece_id="piece-1",
        pick_xy_a4_mm=pick,
        pick_clearance_mm=1.0,
        rotation_deg_clockwise=rotation,
        place_xy_a4_mm=place,
        translation_a4_mm=(20.0, 20.0),
        observed_vertices_a4_mm=vertices,
        target_vertices_a4_mm=vertices,
    )
    return MovePlan(
        mode="fixed",
        confidence=1.0,
        ambiguous=False,
        target_width_mm=1.0,
        target_height_mm=1.0,
        target_center_a4_mm=(30.0, 40.0),
        moves=(move,),
    )


def make_two_piece_plan() -> MovePlan:
    """构造两个位置不同的周期以验证回零与下一片的执行顺序。"""

    plan = make_plan()
    second = replace(
        plan.moves[0],
        order=2,
        piece_id="piece-2",
        pick_xy_a4_mm=(50.0, 60.0),
        place_xy_a4_mm=(70.0, 80.0),
        rotation_deg_clockwise=45.0,
    )
    return replace(plan, moves=(*plan.moves, second))


class MovePlanActuatorTest(unittest.TestCase):
    """验证执行器正常、拒绝、故障和 FINISH 后退出路径。"""

    def test_speed_change_preserves_commands_coordinates_and_z_speed(self):
        streams = []
        for level in (64, 80):
            transport = FakeTransport()
            actuator, _, _ = self._actuator(transport, FakeMagnet(),
                                            make_config(xy_speed_level=level))
            actuator.execute(make_plan())
            streams.append(transport.frames)
        self.assertEqual(len(streams[0]), len(streams[1]))
        changed = 0
        for old, new in zip(*streams):
            if old == new:
                continue
            changed += 1
            self.assertIn(new[0], (0xE0, 0xE1, 0xE2))
            self.assertEqual(old[:2], new[:2])
            self.assertEqual(old[2] & 0x7f, 64)
            self.assertEqual(new[2] & 0x7f, 80)
            self.assertEqual(old[2] & 0x80, new[2] & 0x80)
            self.assertEqual(old[3:-1], new[3:-1])
        self.assertGreater(changed, 0)

    def _actuator(
        self,
        transport: FakeTransport,
        magnet: FakeMagnet,
        config: ActuationConfig | None = None,
        rotation: FakeRotation | None = None,
    ) -> tuple[MovePlanActuator, FakeClock, FakeCompletionLed]:
        """构造注入全部假外部依赖的执行器。"""

        clock = FakeClock()
        transport.monotonic = clock.monotonic
        magnet.monotonic = clock.monotonic
        led = FakeCompletionLed()
        led.monotonic = clock.monotonic
        actuator = MovePlanActuator(
            config or make_config(),
            transport_factory=lambda unused: transport,
            magnet_factory=lambda unused: magnet,
            completion_indicator_factory=lambda: led,
            rotation_factory=lambda unused: rotation or FakeRotation(),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        return actuator, clock, led

    def test_default_calibration_converts_a4_coordinates_to_motor_space(
        self,
    ) -> None:
        """默认标定应把 A4 原点映射为电机的 14.5、6.5 mm 位置。"""

        cycles = compile_move_plan(
            make_plan(pick=(0.0, 0.0), place=(10.125, 20.125)),
            make_config(),
        )

        self.assertEqual(
            (
                cycles[0].pick_x_0p01mm,
                cycles[0].pick_y_0p01mm,
                cycles[0].place_x_0p01mm,
                cycles[0].place_y_0p01mm,
            ),
            (1450, 650, 2463, 2663),
        )

    def test_calibration_override_is_applied_before_limit_validation(
        self,
    ) -> None:
        """环境装配后的标定覆盖应参与坐标换算和越界预检。"""

        config = make_config(a4_origin_x_mm=230.0, a4_origin_y_mm=6.5)
        with self.assertRaisesRegex(ActuationValidationError, "拾取 X"):
            compile_move_plan(make_plan(pick=(0.01, 0.0)), config)

    def test_full_turn_rotation_is_preserved_without_normalization(
        self,
    ) -> None:
        """360° 必须作为真实整圈目标保留，不能归一化为零。"""

        cycle = compile_move_plan(
            make_plan(rotation=360.0),
            make_config(),
        )[0]

        self.assertEqual(cycle.rotation_degrees_clockwise, 360.0)

    def test_normal_path_finishes_before_disabling_and_exits(self) -> None:
        """完整周期应先 FINISH，再失能四个电机并 EXITED。"""

        transport = FakeTransport()
        magnet = FakeMagnet()
        rotation = FakeRotation(transport=transport)
        actuator, _, _ = self._actuator(
            transport,
            magnet,
            rotation=rotation,
        )

        result = actuator.execute(make_plan())

        self.assertEqual(result.state, MotionLifecycleState.EXITED)
        self.assertEqual(result.completed_cycles, 1)
        finish_index = result.state_history.index(
            MotionLifecycleState.FINISH
        )
        shutdown_index = result.state_history.index(
            MotionLifecycleState.SHUTTING_DOWN
        )
        self.assertLess(finish_index, shutdown_index)
        self.assertLess(
            result.state_history.index(MotionLifecycleState.RETURNING_TO_START),
            finish_index,
        )
        self.assertEqual(result.state_history[-1], MotionLifecycleState.EXITED)
        self.assertEqual(
            [(frame[0], frame[1], frame[2]) for frame in transport.frames[-4:]],
            [
                (0xE0, 0xF3, 0),
                (0xE1, 0xF3, 0),
                (0xE2, 0xF3, 0),
                (0xE3, 0xF3, 0),
            ],
        )
        self.assertFalse(magnet.values[-1])
        self.assertEqual(
            magnet.values,
            [False, True, False, False, False],
        )
        self.assertTrue(magnet.closed)
        self.assertTrue(transport.closed)
        self.assertTrue(rotation.closed)
        self.assertIn(("clockwise", 123.0, True), rotation.calls)
        self.assertEqual(
            [frame[1] for frame in rotation.frames_before_clockwise[-3:]],
            [0xFD, 0xFD, 0xFD],
        )
        self.assertEqual(
            [
                call
                for call in rotation.calls
                if isinstance(call, tuple)
            ],
            [
                ("counterclockwise", 0.0, True),
                ("clockwise", 123.0, True),
                ("counterclockwise", 123.0, True),
            ],
        )

    def test_large_z_transition_is_split_without_reply_waits(self) -> None:
        """180°状态差应拆成 90°报文且不要求 transport 提供回包方法。"""

        transport = FakeTransport()
        actuator, _, _ = self._actuator(transport, FakeMagnet())

        actuator.execute(make_plan())

        z_moves = [
            frame
            for frame in transport.frames
            if frame[0] == 0xE3 and frame[1] == 0xFD
        ]
        self.assertGreaterEqual(len(z_moves), 6)
        self.assertTrue(
            all(int.from_bytes(frame[3:7], "big") <= 3200 for frame in z_moves)
        )

    def test_place_descent_disconnects_magnet_without_extra_wait(self) -> None:
        """放置下降只等待 Z 完成，随后应在同一时刻断开电磁铁。"""

        config = make_config(
            z_up_degrees=90.0,
            z_move_wait_seconds=0.25,
        )
        transport = FakeTransport()
        magnet = FakeMagnet()
        actuator, _, _ = self._actuator(transport, magnet, config)

        actuator.execute(make_plan())

        z_move_times = [
            timestamp
            for frame, timestamp in zip(
                transport.frames,
                transport.frame_times,
                strict=True,
            )
            if frame[0] == 0xE3 and frame[1] == 0xFD
        ]
        self.assertEqual(len(z_move_times), 5)
        placement_descent_started = z_move_times[-2]
        placement_magnet_disabled = magnet.value_times[2]
        self.assertAlmostEqual(
            placement_magnet_disabled - placement_descent_started,
            config.z_move_wait_seconds,
            places=9,
        )

    def test_completion_signal_keeps_z_raised_and_lights_pin33_one_second(
        self,
    ) -> None:
        """末片放下后应回 Z 抬升位，并只点亮 pin33 恰好一秒。"""

        config = make_config(
            z_up_degrees=90.0,
            z_move_wait_seconds=0.25,
            rotation_zero_wait_seconds=0.001,
        )
        transport = FakeTransport()
        magnet = FakeMagnet()
        actuator, _, led = self._actuator(transport, magnet, config)

        actuator.execute(make_plan())

        z_move_times = [
            timestamp
            for frame, timestamp in zip(
                transport.frames,
                transport.frame_times,
                strict=True,
            )
            if frame[0] == 0xE3 and frame[1] == 0xFD
        ]
        self.assertEqual(len(z_move_times), 5)
        completion_enabled = led.value_times[0]
        completion_disabled = led.value_times[1]
        self.assertEqual(led.values, [True, False])
        self.assertTrue(led.opened)
        self.assertTrue(led.closed)
        self.assertEqual(magnet.values, [False, True, False, False, False])
        self.assertGreaterEqual(
            completion_enabled - z_move_times[-1],
            config.z_move_wait_seconds + config.rotation_zero_wait_seconds,
        )
        self.assertAlmostEqual(
            completion_disabled - completion_enabled,
            1.0,
            places=9,
        )

    def test_returns_to_start_after_release_before_completion_and_disable(self) -> None:
        """固定/随机、多片和负 Y 坐标均应空载返回启动零点后才提示完成。"""

        for mode, plan in (
            ("fixed", make_plan()),
            ("random", make_two_piece_plan()),
        ):
            with self.subTest(mode=mode):
                config = make_config(
                    a4_origin_x_mm=18.5,
                    a4_origin_y_mm=-8.0,
                    xy_speed_mm_per_second=10.0,
                    xy_fixed_wait_seconds=0.5,
                )
                plan = replace(plan, mode=mode)
                plan = replace(plan, moves=tuple(
                    replace(move, pick_xy_a4_mm=(move.pick_xy_a4_mm[0], 2.0),
                            place_xy_a4_mm=(move.place_xy_a4_mm[0], 4.0))
                    for move in plan.moves
                ))
                transport = FakeTransport()
                magnet = FakeMagnet()
                rotation = FakeRotation(transport=transport)
                actuator, _, led = self._actuator(
                    transport, magnet, config, rotation=rotation,
                )

                result = actuator.execute(plan)

                # 每个 XY 电机的有符号总脉冲为零，确实返回启动位置。
                for address in (0xE0, 0xE1, 0xE2):
                    moves = [frame for frame in transport.frames
                             if frame[0] == address and frame[1] == 0xFD]
                    self.assertEqual(sum(
                        (-1 if frame[2] & 0x80 else 1)
                        * int.from_bytes(frame[3:7], "big") for frame in moves
                    ), 0)
                return_start = len(rotation.frames_before_counterclockwise[-1])
                return_frames = transport.frames[return_start:-4]
                self.assertEqual([frame[0] for frame in return_frames], [0xE0, 0xE1, 0xE2])
                self.assertTrue(all(frame[1] == 0xFD for frame in return_frames))
                returned_at = led.value_times[0]
                last_cycle = compile_move_plan(plan, config)[-1]
                distance_mm = max(abs(last_cycle.place_x_0p01mm),
                                  abs(last_cycle.place_y_0p01mm)) / 100.0
                return_started_at = transport.frame_times[return_start]
                self.assertGreaterEqual(
                    returned_at - return_started_at,
                    distance_mm / config.xy_speed_mm_per_second
                    + config.xy_fixed_wait_seconds - 1e-9,
                )
                self.assertLessEqual(magnet.value_times[2 * len(plan.moves)], return_started_at)
                self.assertFalse(magnet.values[2 * len(plan.moves)])
                self.assertFalse(any(enabled for enabled, timestamp in
                                     zip(magnet.values, magnet.value_times)
                                     if return_started_at <= timestamp < returned_at))
                self.assertGreaterEqual(transport.frame_times[-4], led.value_times[-1])
                self.assertEqual(result.completed_cycles, len(plan.moves))

    def test_return_handles_zero_distance_and_single_axis(self) -> None:
        """末片已位于起点或只偏离一个轴时，不应发出多余轴移动。"""

        for place, expected_addresses in (
            ((0.0, 0.0), []),
            ((10.0, 0.0), [0xE0, 0xE1]),
            ((0.0, 10.0), [0xE2]),
        ):
            with self.subTest(place=place):
                transport = FakeTransport()
                rotation = FakeRotation(transport=transport)
                actuator, _, led = self._actuator(
                    transport, FakeMagnet(),
                    make_config(a4_origin_x_mm=0.0, a4_origin_y_mm=0.0),
                    rotation=rotation,
                )

                result = actuator.execute(make_plan(place=place))

                return_start = len(rotation.frames_before_counterclockwise[-1])
                self.assertEqual(
                    [frame[0] for frame in transport.frames[return_start:-4]],
                    expected_addresses,
                )
                self.assertEqual(result.state, MotionLifecycleState.EXITED)
                self.assertEqual(led.values, [True, False])

    def test_return_failure_or_cancel_stops_without_signalling_completion(self) -> None:
        """返回指令失败或被取消时应停止清理，不提示完成或重试移动。"""

        class FailedReturnTransport(FakeTransport):
            def send_frame(self, frame):
                if frame[0] == 0xE0 and frame[1] == 0xFD and frame[2] & 0x80:
                    self.frames_before_failure = len(self.frames)
                    raise failure
                super().send_frame(frame)

        for failure, expected_error, expected_state in (
            (RuntimeError("模拟返回起点失败"), ActuationExecutionError, MotionLifecycleState.FAILED),
            (KeyboardInterrupt(), KeyboardInterrupt, MotionLifecycleState.ABORTED),
        ):
            with self.subTest(failure=type(failure).__name__):
                transport = FailedReturnTransport()
                magnet = FakeMagnet()
                rotation = FakeRotation()
                actuator, _, led = self._actuator(transport, magnet, rotation=rotation)

                with self.assertRaises(expected_error):
                    actuator.execute(make_plan())

                self.assertIn(MotionLifecycleState.RETURNING_TO_START, actuator.state_history)
                self.assertNotIn(MotionLifecycleState.FINISH, actuator.state_history)
                self.assertEqual(actuator.state, expected_state)
                cleanup = transport.frames[transport.frames_before_failure:]
                self.assertTrue(all(frame[1] in (0xF7, 0xF3) for frame in cleanup))
                self.assertTrue(any(frame[1] == 0xF7 for frame in cleanup))
                self.assertTrue(transport.closed)
                self.assertTrue(rotation.closed)
                self.assertTrue(magnet.closed)
                self.assertFalse(magnet.values[-1])
                self.assertFalse(led.opened)

    def test_start_outside_software_limits_is_rejected_before_hardware(self) -> None:
        """返回起点超出配置限位时，应在访问硬件前拒绝整份方案。"""

        for limits in ({"x_min_mm": 1.0}, {"y_min_mm": 1.0}, {"y_max_mm": -1.0}):
            with self.subTest(limits=limits):
                calls = []
                actuator = MovePlanActuator(
                    make_config(**limits),
                    transport_factory=lambda unused: calls.append("serial"),
                    magnet_factory=lambda unused: calls.append("gpio"),
                    rotation_factory=lambda unused: calls.append("rotation"),
                )
                with self.assertRaisesRegex(ActuationValidationError, "返回起点"):
                    actuator.execute(make_plan())
                self.assertEqual(calls, [])

    def test_next_pick_xy_overlaps_return_and_pickup_waits_for_both(self) -> None:
        """移向下一片与回零并行，吸取必须等待旋转和 XY 都完成。"""

        return_completed_times = []

        class SlowReturn(FakeRotation):
            def return_counterclockwise_to_zero(self, target_degrees, *, wait=True):
                result = super().return_counterclockwise_to_zero(target_degrees, wait=wait)
                clock.sleep(0.4)
                return_completed_times.append(clock.monotonic())
                return result

        for xy_fixed_wait in (0.0, 0.8):
            with self.subTest(xy_fixed_wait=xy_fixed_wait):
                return_completed_times.clear()
                transport = FakeTransport()
                magnet = FakeMagnet()
                rotation = SlowReturn(transport=transport)
                config = make_config(xy_fixed_wait_seconds=xy_fixed_wait)
                actuator, clock, _ = self._actuator(
                    transport, magnet, config, rotation=rotation,
                )

                result = actuator.execute(make_two_piece_plan())

                self.assertEqual(result.completed_cycles, 2)
                self.assertEqual(len(rotation.frames_before_counterclockwise), 3)
                frames_at_first_cycle_return = rotation.frames_before_counterclockwise[1]
                self.assertEqual(
                    [frame[0] for frame in frames_at_first_cycle_return[-5:]],
                    [0xE3, 0xE3, 0xE0, 0xE1, 0xE2],
                )
                self.assertTrue(all(frame[1] == 0xFD
                                    for frame in frames_at_first_cycle_return[-5:]))
                first_next_pick = len(frames_at_first_cycle_return) - 3
                move_started = transport.frame_times[first_next_pick]
                self.assertLess(move_started, return_completed_times[1])
                pickup_times = [t for enabled, t in zip(magnet.values, magnet.value_times)
                                if enabled]
                self.assertGreaterEqual(pickup_times[1], return_completed_times[1])
                cycles = compile_move_plan(make_two_piece_plan(), config)
                next_distance_mm = max(
                    abs(cycles[1].pick_x_0p01mm - cycles[0].place_x_0p01mm),
                    abs(cycles[1].pick_y_0p01mm - cycles[0].place_y_0p01mm),
                ) / 100.0
                self.assertGreaterEqual(
                    pickup_times[1],
                    move_started + next_distance_mm / config.xy_speed_mm_per_second
                    + xy_fixed_wait,
                )
                self.assertTrue(all(call[2] is True for call in rotation.calls
                                    if isinstance(call, tuple)))


    def test_startup_returns_counterclockwise_from_current_angle_before_pick(self) -> None:
        """启动角度非零时，先逆时针回零，再顺时针搬运并逆时针回零。"""

        transport = FakeTransport()
        rotation = FakeRotation(transport=transport)
        rotation.angle = 80.0
        actuator, _, _ = self._actuator(transport, FakeMagnet(), rotation=rotation)
        actuator.execute(make_plan(rotation=123.0))
        self.assertEqual(
            [call for call in rotation.calls if isinstance(call, tuple)],
            [("counterclockwise", 80.0, True),
             ("clockwise", 123.0, True),
             ("counterclockwise", 123.0, True)],
        )
        self.assertFalse(any(frame[1] == 0xFD and frame[0] != 0xE3
                             for frame in rotation.frames_before_counterclockwise[0]))

    def test_xy_wait_uses_longest_axis_plus_fixed_value(self) -> None:
        """XY 外层截止时间应使用两轴距离最大值而不是相加。"""

        config = make_config(
            xy_speed_mm_per_second=10.0,
            xy_fixed_wait_seconds=0.5,
        )
        transport = FakeTransport()
        actuator, clock, _ = self._actuator(
            transport,
            FakeMagnet(),
            config,
        )
        xy = MksXYController(
            transport,
            monotonic=clock.monotonic,
        )
        xy.initialize()
        clock.sleep(0.5)
        xy.process()
        self.assertEqual(xy.state, XYState.READY)
        started = clock.now

        actuator._move_xy_to(1000, 2000, xy)

        self.assertAlmostEqual(clock.now - started, 2.5, places=6)

    def test_preflight_failure_never_constructs_hardware(self) -> None:
        """坐标越界时应在工厂调用之前失败。"""

        factory_calls: list[str] = []
        clock = FakeClock()
        actuator = MovePlanActuator(
            make_config(),
            transport_factory=lambda unused: factory_calls.append("serial"),
            magnet_factory=lambda unused: factory_calls.append("gpio"),
            rotation_factory=lambda unused: factory_calls.append("rotation"),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        with self.assertRaises(ActuationValidationError):
            actuator.execute(make_plan(pick=(231.0, 20.0)))

        self.assertEqual(factory_calls, [])
        self.assertEqual(actuator.state, MotionLifecycleState.FAILED)

    def test_execution_failure_demagnetizes_stops_and_disables(self) -> None:
        """FINISH 前异常应先消磁，再尽力停止和失能。"""

        transport = FakeTransport()
        magnet = FakeMagnet(fail_on_enable=True)
        actuator, _, _ = self._actuator(transport, magnet)

        with self.assertRaisesRegex(
            ActuationExecutionError,
            "电机运动失败.*模拟上磁失败",
        ):
            actuator.execute(make_plan())

        self.assertEqual(actuator.state, MotionLifecycleState.FAILED)
        self.assertFalse(magnet.values[-1])
        self.assertTrue(magnet.closed)
        functions = [frame[1] for frame in transport.frames]
        self.assertIn(0xF7, functions)
        self.assertTrue(
            any(frame[1] == 0xF3 and frame[2] == 0 for frame in transport.frames)
        )

    def test_invalid_rotation_is_rejected_before_hardware(self) -> None:
        """负角和超过 360° 的目标必须在构造硬件前失败。"""

        for target in (-0.1, 360.1, float("inf")):
            with self.subTest(target=target):
                factory_calls: list[str] = []
                actuator = MovePlanActuator(
                    make_config(),
                    transport_factory=lambda unused: factory_calls.append(
                        "serial"
                    ),
                    magnet_factory=lambda unused: factory_calls.append("gpio"),
                    rotation_factory=lambda unused: factory_calls.append(
                        "rotation"
                    ),
                )
                with self.assertRaises(ActuationValidationError):
                    actuator.execute(make_plan(rotation=target))
                self.assertEqual(factory_calls, [])

    def test_rotation_failure_uses_emergency_cleanup(self) -> None:
        """旋转异常时不得进入 FINISH，且旋转轴必须失能关闭。"""

        transport = FakeTransport()
        magnet = FakeMagnet()
        rotation = FakeRotation(fail_move=True)
        actuator, _, _ = self._actuator(
            transport,
            magnet,
            rotation=rotation,
        )

        with self.assertRaisesRegex(
            ActuationExecutionError,
            "电机运动失败.*模拟旋转失败",
        ):
            actuator.execute(make_plan(rotation=270.0))

        self.assertEqual(actuator.state, MotionLifecycleState.FAILED)
        self.assertTrue(rotation.closed)
        self.assertNotIn(
            MotionLifecycleState.FINISH,
            actuator.state_history,
        )

    def test_rotation_protocol_feedback_error_interrupts_plan(
        self,
    ) -> None:
        """机械角回包或容差异常必须中断运动并消磁、失能。"""

        transport = FakeTransport()
        magnet = FakeMagnet()
        rotation = FakeRotation(
            move_error=F32CProtocolError("机械角度复核失败"),
        )
        actuator, _, _ = self._actuator(
            transport,
            magnet,
            rotation=rotation,
        )

        with self.assertRaisesRegex(ActuationExecutionError, "机械角度复核失败"):
            actuator.execute(make_plan(rotation=1.5))
        self.assertEqual(actuator.state, MotionLifecycleState.FAILED)
        self.assertTrue(rotation.closed)
        self.assertFalse(magnet.values[-1])
        self.assertNotIn(MotionLifecycleState.FINISH, actuator.state_history)

    def test_rotation_feedback_timeout_interrupts_plan(self) -> None:
        """等待旋转反馈超时后必须中断当前方案，不能继续下一片。"""

        rotation = FakeRotation(
            move_error=F32CTimeoutError("连续5次未返回合法机械角度反馈"),
        )
        actuator, _, _ = self._actuator(
            FakeTransport(),
            FakeMagnet(),
            rotation=rotation,
        )

        with self.assertRaisesRegex(ActuationExecutionError, "未返回合法机械角度反馈"):
            actuator.execute(make_two_piece_plan())
        self.assertEqual(actuator.state, MotionLifecycleState.FAILED)
        self.assertTrue(rotation.closed)
        self.assertEqual(len([call for call in rotation.calls
                              if isinstance(call, tuple) and call[0] == 'clockwise']), 1)

    def test_rotation_initial_feedback_timeout_prevents_xyz_motion(
        self,
    ) -> None:
        """旋转轴初始化无回包时直接失败，不能使能 XYZ 或吸取纸片。"""

        rotation = FakeRotation(
            open_error=F32CTimeoutError("连续5次未返回合法总角度反馈"),
        )
        transport = FakeTransport()
        magnet = FakeMagnet()
        actuator, _, _ = self._actuator(
            transport,
            magnet,
            rotation=rotation,
        )

        with self.assertRaisesRegex(ActuationExecutionError, "旋转轴初始化失败"):
            actuator.execute(make_plan(rotation=90.0))
        self.assertEqual(actuator.state, MotionLifecycleState.FAILED)
        self.assertNotIn(("clockwise", 90.0, True), rotation.calls)
        self.assertTrue(rotation.closed)
        self.assertEqual(transport.frames, [])
        self.assertTrue(magnet.closed)
        self.assertNotIn(True, magnet.values)

    def test_return_timeout_prevents_next_pickup_after_preposition(self) -> None:
        """允许 XY 提前移动，回零失败后不能下降吸取下一片。"""

        class ReturnTimeout(FakeRotation):
            def return_counterclockwise_to_zero(self, target_degrees, *, wait=True):
                if target_degrees != 0.0:
                    self.return_error = F32CTimeoutError('模拟回零到位超时')
                return super().return_counterclockwise_to_zero(target_degrees, wait=wait)

        transport = FakeTransport()
        magnet = FakeMagnet()
        rotation = ReturnTimeout(transport=transport)
        actuator, _, _ = self._actuator(transport, magnet, rotation=rotation)
        with self.assertRaisesRegex(ActuationExecutionError, '回零到位超时'):
            actuator.execute(make_two_piece_plan())
        after_return = transport.frames[len(rotation.frames_before_counterclockwise[-1]):]
        self.assertTrue(all(frame[1] in (0xF7, 0xF3) for frame in after_return))
        self.assertEqual(magnet.values.count(True), 1)
        self.assertEqual(
            [frame[0] for frame in rotation.frames_before_counterclockwise[-1][-3:]],
            [0xE0, 0xE1, 0xE2],
        )

    def test_invalid_zero_feedback_prevents_first_pick(self) -> None:
        """回零方法返回但机械角仍非零或非有限值时，不能开始吸取。"""

        class IncorrectReturn(FakeRotation):
            def return_counterclockwise_to_zero(self, target_degrees, *, wait=True):
                super().return_counterclockwise_to_zero(target_degrees, wait=wait)
                self.angle = bad_angle
                return 0.0

        for bad_angle in (3.0, float('nan')):
            with self.subTest(angle=bad_angle):
                transport = FakeTransport()
                magnet = FakeMagnet()
                rotation = IncorrectReturn(transport=transport)
                actuator, _, _ = self._actuator(transport, magnet, rotation=rotation)
                with self.assertRaisesRegex(ActuationExecutionError, '回零到位检查失败'):
                    actuator.execute(make_plan())
                self.assertNotIn(True, magnet.values)
                self.assertEqual(actuator.state, MotionLifecycleState.FAILED)

    def test_incorrect_clockwise_target_feedback_prevents_placement(self) -> None:
        """顺时针接口返回但角度未到位时，不得下降放置或报告完成。"""

        class IncorrectTarget(FakeRotation):
            def move_clockwise_from_zero(self, target_degrees, *, wait=True):
                result = super().move_clockwise_from_zero(target_degrees, wait=wait)
                self.angle -= 5.0
                return result

        transport = FakeTransport()
        magnet = FakeMagnet()
        rotation = IncorrectTarget(transport=transport)
        actuator, _, led = self._actuator(transport, magnet, rotation=rotation)
        with self.assertRaisesRegex(ActuationExecutionError, '顺时针旋转到位检查失败'):
            actuator.execute(make_plan())
        self.assertEqual(actuator.state, MotionLifecycleState.FAILED)
        self.assertTrue(rotation.closed)
        self.assertFalse(magnet.values[-1])
        self.assertFalse(led.opened)
        z_moves = [frame for frame in transport.frames if frame[0] == 0xE3 and frame[1] == 0xFD]
        before_rotation = [frame for frame in rotation.frames_before_clockwise
                           if frame[0] == 0xE3 and frame[1] == 0xFD]
        self.assertEqual(z_moves, before_rotation)  # 失败后未执行放置下降。

    def test_shutdown_failure_preserves_finish_state(self) -> None:
        """FINISH 后失能失败应保留 FINISH 并报告中文安全退出错误。"""

        transport = FakeTransport(fail_disable=True)
        magnet = FakeMagnet()
        actuator, _, _ = self._actuator(transport, magnet)

        with self.assertRaisesRegex(
            ActuationShutdownError,
            "MovePlan 已执行完成，但安全退出失败",
        ):
            actuator.execute(make_plan())

        self.assertEqual(actuator.state, MotionLifecycleState.FINISH)
        self.assertIn(
            MotionLifecycleState.FINISH,
            actuator.state_history,
        )
        self.assertTrue(magnet.closed)


if __name__ == "__main__":
    unittest.main()
