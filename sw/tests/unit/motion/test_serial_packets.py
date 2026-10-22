"""无设备协议验收；也可通过标准库 unittest 独立运行。"""

import unittest

from ec_26_e.motion.contracts import MotorAddress
from ec_26_e.motion.f32c_controller import decode_feedback_frame
from ec_26_e.motion.protocol import Servo42CProtocol
from ec_26_e.motion.serial_packets import (
    UartRoute, example_packets, rotation_total_target, xyz_move,
)


class SerialPacketTests(unittest.TestCase):
    def test_wire_vectors_and_routes(self):
        packets = example_packets()
        self.assertEqual([p.hex_text for p in packets], [
            "E0 FD 32 00 00 03 E8 FA",
            "7A 02 01 00 0F 76 7B",
            "7A 02 02 00 00 03 84 FD 7B",
            "7A 02 0E 01 77 7B",
        ])
        self.assertEqual(packets[0].route, UartRoute.MOTION)
        self.assertTrue(all(p.route == UartRoute.ROTATION for p in packets[1:]))

    def test_fragmented_servo_reply(self):
        parser = Servo42CProtocol()
        self.assertEqual(parser.feed(bytes.fromhex("E0 01")), ())
        replies = parser.feed(bytes.fromhex("E1 E0 02 E2"))
        self.assertEqual([r.status for r in replies], [1, 2])

    def test_total_angle_feedback(self):
        feedback = decode_feedback_frame(bytes.fromhex("7A 02 01 00 00 03 84 FE 7B"))
        self.assertEqual(feedback.angle_deg, 90.0)

    def test_invalid_motion_parameters(self):
        for speed, pulses in [(0, 1), (128, 1), (50, -1), (50, 2**32)]:
            with self.subTest(speed=speed, pulses=pulses):
                with self.assertRaises(ValueError):
                    xyz_move(MotorAddress.X0, speed, pulses)
        with self.assertRaises(ValueError):
            rotation_total_target(float("nan"))


if __name__ == "__main__":
    unittest.main()
