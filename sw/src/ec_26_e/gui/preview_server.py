"""Keep imports warm between previews; acknowledge stop only after hardware closes."""
import json
import queue
import signal
import struct
import threading
import traceback

from ec_26_e.gui.camera_pipe import write_preview_error, IDLE_PACKET
from ec_26_e.tasks.execution_lock import TaskBusyError


def serve(session, labels, source, output):
    requests = queue.Queue()
    mutex = threading.RLock()
    current = None

    def shutdown(*_):
        with mutex:
            if current is not None:
                current.set()
        requests.put(None)

    def read_requests():
        nonlocal current
        try:
            while True:
                line = source.readline(4096)
                if not line:
                    break
                request = json.loads(line)
                with mutex:
                    if request.get('action') == 'stop':
                        if current is not None:
                            current.set()
                    else:
                        label = request['label']
                        if label not in labels or current is not None:
                            raise ValueError('Preview start requires an idle worker and a valid mode')
                        current = threading.Event()
                        requests.put((label, current))
        except Exception as error:
            requests.put(error)
        finally:
            shutdown()

    def ready(packet=0):
        output.write(struct.pack('!I', packet))
        output.flush()

    signal.signal(signal.SIGTERM, shutdown)
    threading.Thread(target=read_requests, name='preview-requests', daemon=True).start()
    ready()  # No camera, PL or execution lock is held while waiting.
    while True:
        request = requests.get()
        if request is None:
            return
        if isinstance(request, Exception):
            raise request
        label, stopped = request
        try:
            if not stopped.is_set():
                session(label, lambda: not stopped.is_set())
        except BrokenPipeError:
            return
        except TaskBusyError:
            write_preview_error(output, '设备正由其他任务或维护占用，请稍后重新打开预览', 'busy')
        except Exception as error:
            traceback.print_exc()
            write_preview_error(output, f'预览失败：{error}')
        finally:
            with mutex:
                current = None
        ready(IDLE_PACKET)  # Hardware has been released before this ACK.
