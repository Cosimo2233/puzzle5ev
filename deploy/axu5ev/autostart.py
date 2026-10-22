"""SysV 启动监督：已验证的平台 → HDMI → 菜单；不自动执行任务。"""
import ctypes
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import time

BASE = Path('/opt/ec26e')
RUN = Path('/run/ec26e')
BUNDLE = BASE / 'staging/20261020/axu5ev-reset-bundle'
MARKER = Path('/sys/firmware/devicetree/base/chosen/ec26e,pl-base')
PYTHON = str(BASE / 'venv-python311/bin/python')
LOGS = BASE / 'logs/autostart'
stopping = False


def require_candidate():
    if not MARKER.exists() or MARKER.read_bytes() != b'axu5ev-v1\0':
        raise RuntimeError('未启动工程设备树；禁止在厂家平台加载 PL')


def ps_network_command(net_root=Path('/sys/class/net')):
    """按 PS GEM 控制器身份选网口，不依赖厂家/工程的 eth 编号。"""
    interfaces = [p.name for p in net_root.iterdir()
                  if (p/'device').resolve().name == 'ff0e0000.ethernet']
    if len(interfaces) != 1 or interfaces[0] not in ('eth0', 'eth1'):
        raise RuntimeError(f'无法唯一识别 PS 网口：{interfaces}')
    # Both stanzas already exist in the vendor /etc/network/interfaces.
    return ['ifup', interfaces[0]]


def display_ready():
    x11 = ctypes.CDLL('libX11.so.6')
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(b':0')
    if not display:
        return False
    x11.XCloseDisplay(display)
    return True


def main():
    global stopping
    RUN.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    logging.basicConfig(filename=LOGS/'supervisor.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    state = dict(boot_id=boot_id, pid=os.getpid(), motion_enabled=False,
                 bundle=str(BUNDLE), state='starting')

    def save(**changes):
        state.update(changes)
        temp = RUN/'autostart-status.tmp'
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        temp.replace(RUN/'autostart-status.json')

    def stop(*_):
        global stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # A second start must not touch the active PL or replace its status.
    with (RUN/'autostart.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save()
        children = []
        logs = []
        env = dict(os.environ, DISPLAY=':0', EC_MOTION_ENABLED='false',
                   EC_MENU_CONFIG=str(BASE/'config/axu5ev/menu.json'),
                   PATH=f'{BASE}/venv-python311/bin:/sbin:/usr/sbin:/bin:/usr/bin',
                   OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')

        def run(command, name, background=False):
            output = (LOGS/f'{name}-{boot_id}.log').open('ab')
            logs.append(output)
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, env=env)
            children.append(process)
            if not background:
                code = process.wait(timeout=90)
                if code:
                    raise RuntimeError(f'{name} 退出码 {code}；请检查 {output.name}')
            return process

        try:
            require_candidate()
            # The vendor image calls GEM eth1; disabling factory PL makes it
            # eth0. Bring up the actual PS interface even if vendor rcS tried
            # the obsolete auto eth1 entry. No default route/profile rewrite.
            try:
                run(ps_network_command(), 'network')
            except RuntimeError as error:
                logging.warning('%s', error)
                save(network_warning=str(error))
            config = json.loads((BASE/'config/axu5ev/menu.json').read_text())
            from ec_26_e.gui.board_profile import validate_menu_config
            validate_menu_config(config)
            if config.get('board') != 'axu5ev':
                raise RuntimeError('自启配置必须属于 AXU5EV')
            save(motion_enabled=config.get('motion_enabled') is True)
            status = RUN/'pl-status.json'
            if status.exists():
                pl = json.loads(status.read_text())
                manifest = json.loads((BUNDLE/'manifest.json').read_text())
                if not pl.get('loaded') or pl.get('bit_sha256') != manifest['files']['puzzle5ev.bit']:
                    raise RuntimeError('已有失败或不同的 PL 状态；不自动重载')
            else:
                run([PYTHON, str(BUNDLE/'load_hardware.py'), '--bundle', str(BUNDLE), '--apply'], 'hardware')
            if stopping:
                return
            # Loading the USB driver does not open/arm the bridge. A missing
            # bridge must not prevent the camera/menu from being usable.
            try:
                run(['sh', str(BUNDLE/'setup_m0.sh'), str(BUNDLE)], 'm0-driver')
            except RuntimeError as error:
                logging.warning('%s', error)
                save(peripheral_warning=str(error))
            xserver = run(['sh', str(BASE/'install/start_hdmi_x.sh')], 'xorg', True)
            deadline = time.monotonic()+30
            while not stopping and time.monotonic() < deadline:
                if xserver.poll() is not None:
                    raise RuntimeError('HDMI Xorg 提前退出')
                if display_ready():
                    break
                time.sleep(.25)
            else:
                raise RuntimeError('HDMI Xorg 未在 30 秒内就绪')
            menu = run(['sh', str(BASE/'install/start_menu.sh')], 'menu', True)
            save(state='running', xorg_pid=xserver.pid, menu_pid=menu.pid)
            logging.info('启动完成：boot=%s menu=%s', boot_id, menu.pid)
            while not stopping:
                if xserver.poll() is not None or menu.poll() is not None:
                    raise RuntimeError('菜单或 Xorg 已退出；不重放任务')
                time.sleep(.5)
            save(state='stopped')
        except Exception as error:
            logging.exception('自启失败')
            save(state='failed', error=str(error))
            raise
        finally:
            # Reverse order lets the menu close its preview before X exits.
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        logging.error('进程 %s 未及时退出；保留现场，不强制杀死', child.pid)
            for output in logs:
                output.close()


if __name__ == '__main__':
    main()
