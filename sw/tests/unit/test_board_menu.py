"""验证摄像头管道分块、截断和菜单文字状态更新。"""
import io
import struct
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from ec_26_e.gui.board_menu import BoardMenu, CameraPipe


class ShortStream(io.BytesIO):
    def readinto(self, target):
        return super().readinto(target[:3])


def test_camera_pipe_preserves_complete_ppm_across_short_reads():
    frame = b'P6\n2 1\n255\n' + bytes(range(6))
    pipe = CameraPipe()
    pipe.process = SimpleNamespace(stdout=ShortStream(struct.pack('!I', len(frame)) + frame))
    pipe._read()
    assert pipe.latest == frame
    assert pipe.version == 1


def test_camera_pipe_does_not_publish_truncated_frame():
    pipe = CameraPipe()
    pipe.process = SimpleNamespace(stdout=ShortStream(struct.pack('!I', 12) + b'abcd'))
    pipe._read()
    assert pipe.latest is None
    assert pipe.version == 0
    assert '截断' in pipe.error


def test_menu_updates_changed_text_and_keeps_button_polling():
    menu = BoardMenu.__new__(BoardMenu)
    menu.closing = False
    menu.root = Mock()
    menu.keys = Mock(read=Mock(return_value={'mode': False}))
    menu.edges = Mock(update=Mock(return_value=[]))
    menu.model = SimpleNamespace(preview=False, message='就绪', motion_enabled=False, poll_task=Mock())
    menu.status = Mock()
    menu.health = Mock()
    menu.key_error = ''
    menu._status_text = menu._health_text = None
    menu.last_status = float('inf')
    for _ in range(5):
        menu.poll()
    assert menu.status.configure.call_count == menu.health.configure.call_count == 1
    assert menu.keys.read.call_count == 5
    menu.model.message = '采集完成'
    menu.key_error = 'GPIO 故障'
    menu.poll()
    menu.status.configure.assert_called_with(text='采集完成')
    assert 'GPIO 故障' in menu.health.configure.call_args.kwargs['text']
    assert menu.root.after.call_count == 6
