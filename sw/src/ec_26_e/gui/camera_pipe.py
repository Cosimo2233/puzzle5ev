"""菜单与独立摄像头进程之间的帧通信，最多保留一张最新画面。"""
import json
import struct
import subprocess
import sys
import threading

ERROR_PACKET = 0xFFFFFFFF


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
            self.close()
        self.prime()
        self.label = label
        self.process.stdin.write((json.dumps({'label': label})+'\n').encode('utf-8'))
        self.process.stdin.flush()
        self.process.stdin.close()

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
                if length == 0:
                    self.ready = True
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
                    return
                with self.lock:
                    self.latest = bytes(data)
                    self.version += 1
            with self.lock:
                if not self._closing:
                    self.error = '预览进程已退出，请重新打开预览或查看日志'
        except Exception as error:
            with self.lock:
                self.error = str(error)

    def close(self):
        process = self.process
        if process is None:
            return
        self._closing = True
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()  # Camera helper never controls motors.
                process.wait()
        self.reader.join(timeout=2)
        process.stdout.close()
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        self.process = None
        self.label = None
        self.ready = False
