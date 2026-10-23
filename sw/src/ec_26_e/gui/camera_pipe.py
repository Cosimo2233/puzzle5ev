"""菜单与独立摄像头进程之间的帧通信，最多保留一张最新画面。"""
import json
import struct
import subprocess
import sys
import threading
import time

ERROR_PACKET = 0xFFFFFFFF
IDLE_PACKET = 0xFFFFFFFE


def write_preview_error(stream, message, code='preview_error'):
    """Send a bounded error over the same pipe, without publishing it as an image."""
    data = json.dumps(dict(code=code, message=str(message)[:1000]),
                      ensure_ascii=False).encode('utf-8')
    stream.write(struct.pack('!II', ERROR_PACKET, len(data)) + data)
    stream.flush()


class CameraPipe:
    def __init__(self):
        self.process = self.reader = None
        self.latest = None
        self.error = None
        self.error_code = None
        self._closing = False
        self.lock = threading.Lock()
        self.version = 0
        self.label = None
        self.ready = False
        self._idle = threading.Event()
        self.started_at = self.first_frame_seconds = None

    @property
    def active(self):
        return self.process is not None and self.label is not None

    def prime(self):
        """加载软件后等候请求；不打开相机、PL 或任务锁。"""
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        self.label = None
        self.ready = False
        self._idle.clear()
        self.latest = self.error = None
        self.error_code = None
        self._closing = False
        self.process = subprocess.Popen(
            [sys.executable, '-u', '-m', 'ec_26_e.gui.production_preview', '--wait-for-start'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.reader = threading.Thread(target=self._read, name='menu-camera', daemon=True)
        self.reader.start()

    def start(self, label):
        if self.active:
            if self.label == label and self.process.poll() is None:
                return
            self.stop()
        self.prime()
        self.label = label
        self.ready = False
        self._idle.clear()
        self.latest = self.error = self.error_code = None
        self.started_at = time.monotonic()
        self.first_frame_seconds = None
        self.process.stdin.write((json.dumps({'label': label})+'\n').encode('utf-8'))
        self.process.stdin.flush()

    def stop(self):
        """Release preview hardware, retaining the imported worker for next start."""
        if not self.active:
            return
        if self.process.poll() is None and not self._idle.is_set():
            self.process.stdin.write(b'{"action":"stop"}\n')
            self.process.stdin.flush()
            if not self._idle.wait(timeout=15):
                self.close()
                raise RuntimeError('预览释放设备超时')
        self.label = None
        self.latest = None

    def _read(self):
        stream = self.process.stdout
        try:
            while True:
                header = stream.read(4)
                if not header:
                    break
                if len(header) != 4:
                    raise RuntimeError('摄像头管道报头截断')
                length = struct.unpack('!I', header)[0]
                if length in (0, IDLE_PACKET):
                    self.ready = True
                    if length == IDLE_PACKET:
                        self._idle.set()
                    continue
                is_error = length == ERROR_PACKET
                if is_error:
                    header = stream.read(4)
                    if len(header) != 4:
                        raise RuntimeError('预览错误报头截断')
                    length = struct.unpack('!I', header)[0]
                    if not 1 <= length <= 8192:
                        raise RuntimeError('预览错误报文长度无效')
                if not 1 <= length <= 1280 * 720 * 3 + 64:
                    raise RuntimeError('摄像头帧长度无效')
                data = bytearray(length)
                view = memoryview(data)
                received = 0
                while received < length:
                    count = stream.readinto(view[received:])
                    if not count:
                        raise RuntimeError('摄像头管道帧截断')
                    received += count
                if is_error:
                    report = json.loads(data)
                    if not isinstance(report, dict) or not isinstance(report.get('message'), str):
                        raise RuntimeError('预览错误报文无效')
                    with self.lock:
                        self.error = report['message']
                        self.error_code = report.get('code', 'preview_error')
                        self.ready = False
                    continue
                with self.lock:
                    if self.first_frame_seconds is None and self.started_at is not None:
                        self.first_frame_seconds = time.monotonic() - self.started_at
                    self.latest = bytes(data)
                    self.version += 1
            with self.lock:
                if not self._closing and self.error is None:
                    self.error = '预览进程已退出，请重新打开预览或查看日志'
        except Exception as error:
            with self.lock:
                self.error = str(error)

    def close(self):
        process = self.process
        if process is None:
            return
        self._closing = True
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()  # EOF stops the control reader and active session.
        if process.poll() is None:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()  # Camera helper never controls motors.
                process.wait()
        self.reader.join(timeout=2)
        process.stdout.close()
        self.process = None
        self.label = None
        self.ready = False
        self._idle.clear()
