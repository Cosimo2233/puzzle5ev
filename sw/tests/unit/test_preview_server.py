"""Reusable preview lifecycle and hardware hand-off, without any devices."""
import io
import json
import queue
import struct
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from ec_26_e.gui import preview_server
from ec_26_e.gui.camera_pipe import CameraPipe, IDLE_PACKET, ERROR_PACKET


def test_worker_reuses_imports_but_releases_hardware_between_requests(monkeypatch):
    incoming, packets = queue.Queue(), queue.Queue()
    source = SimpleNamespace(readline=lambda _: incoming.get(timeout=3))
    output = SimpleNamespace(write=packets.put, flush=lambda: None)
    opened, released, finish_release = threading.Event(), threading.Event(), threading.Event()
    sessions = []

    def session(label, running):
        sessions.append(label)
        opened.set()
        while running():
            threading.Event().wait(.001)
        assert finish_release.wait(3)
        released.set()

    monkeypatch.setattr(preview_server.signal, 'signal', Mock())
    worker = threading.Thread(target=preview_server.serve,
                              args=(session, ['fixed', 'random'], source, output))
    worker.start()
    assert packets.get(timeout=3) == struct.pack('!I', 0)
    assert not opened.is_set()  # Preload never acquires devices.
    for label in ['fixed', 'random']:
        opened.clear(); released.clear(); finish_release.clear()
        incoming.put(json.dumps({'label': label}).encode())
        assert opened.wait(3)
        incoming.put(b'{"action":"stop"}\n')
        assert packets.empty()  # No idle ACK before cleanup finishes.
        finish_release.set()
        assert packets.get(timeout=3) == struct.pack('!I', IDLE_PACKET)
        assert released.is_set()
        assert worker.is_alive()
    incoming.put(b'')
    worker.join(3)
    assert not worker.is_alive() and sessions == ['fixed', 'random']


def test_initial_ready_is_not_a_hardware_release_ack():
    camera = CameraPipe()
    camera.label = 'fixed'
    camera._closing = True
    camera.process = SimpleNamespace(stdout=io.BytesIO(struct.pack('!I', 0)))
    camera._read()
    assert camera.ready and not camera._idle.is_set()


def test_error_packet_still_allows_release_ack_and_next_preview_frame():
    camera = CameraPipe()
    camera._closing = True
    error = json.dumps({'code': 'busy', 'message': 'occupied'}).encode()
    data = (struct.pack('!II', ERROR_PACKET, len(error)) + error
            + struct.pack('!I', IDLE_PACKET) + struct.pack('!I', 3) + b'ppm')
    camera.process = SimpleNamespace(stdout=io.BytesIO(data))
    camera._read()
    assert camera._idle.is_set() and camera.error_code == 'busy'
    assert camera.latest == b'ppm'


def test_stop_keeps_idle_process_for_next_start(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    camera = CameraPipe()
    camera.process = process
    camera.label = 'fixed'
    camera._idle.set()  # Worker has completed cleanup after a session failure.
    camera.stop()
    camera.start('random')
    process.terminate.assert_not_called()
    process.stdin.close.assert_not_called()
    assert camera.process is process and not camera._idle.is_set()
    assert json.loads(process.stdin.write.call_args.args[0]) == {'label': 'random'}
