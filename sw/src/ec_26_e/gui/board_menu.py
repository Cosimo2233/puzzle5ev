"""板载任务菜单；预览、只求解与真实搬运使用独立进程。"""
from __future__ import annotations

import json
import importlib.util
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from ec_26_e.gui.camera_pipe import CameraPipe
from ec_26_e.gui.menu_model import MODES, MenuModel, PressEdges
from ec_26_e.gui.board_profile import (
    load_menu_config, physical_buttons_enabled, preview_backend, profile_paths,
    validate_menu_config,
)

logger = logging.getLogger(__name__)
BUTTONS = {"mode": (12, "PS_KEY0"), "confirm": (11, "PS_KEY1"),
           "preview": (54, "PL_KEY0"), "cancel": (55, "PL_KEY1")}


class BoardKeys:
    def __init__(self):
        chips = [p for p in Path('/sys/class/gpio').glob('gpiochip*')
                 if 'zynq' in (p / 'label').read_text().lower()]
        if len(chips) != 1:
            raise RuntimeError('无法唯一定位 Zynq PS GPIO')
        self.base = int((chips[0] / 'base').read_text())
        self.owned = []
        self.paths = {}
        try:
            for action, (offset, _) in BUTTONS.items():
                number = self.base + offset
                path = Path(f'/sys/class/gpio/gpio{number}')
                if path.exists():
                    raise RuntimeError(f'按键 GPIO {number} 已被其他程序占用')
                Path('/sys/class/gpio/export').write_text(str(number))
                self.owned.append(number)
                (path / 'direction').write_text('in')
                self.paths[action] = path / 'value'
        except Exception:
            self.close()
            raise

    def read(self):
        return {name: path.read_text().strip() == '0' for name, path in self.paths.items()}

    def close(self):
        for number in self.owned:
            try:
                Path('/sys/class/gpio/unexport').write_text(str(number))
            except OSError:
                logger.exception('释放按键失败')
        self.owned.clear()


class BoardMenu:
    def __init__(self, root):
        import tkinter as tk
        self.tk = tk
        self.root = root
        self.config = load_menu_config()
        self.warm_task = None
        if self.config.get('warm_tasks') is True:
            from ec_26_e.gui.warm_task import WarmTaskSlot
            self.warm_task = WarmTaskSlot()
            self.warm_task.prime()
        self.model = MenuModel(motion_enabled=self.config.get('motion_enabled') is True,
                               start_task=self.start_task,
                               plan_only=self.config.get('plan_only') is True)
        self.camera = CameraPipe()
        self.camera.prime()
        self.keys = None
        self.key_error = ''
        if physical_buttons_enabled(self.config):
            try:
                self.keys = BoardKeys()
            except Exception as error:
                logger.exception('实体按键初始化失败')
                self.key_error = str(error)
        self.edges = PressEdges(BUTTONS)
        self.key_levels = {}
        self.camera_version = -1
        self.last_status = 0
        self._status_text = self._health_text = None
        self.events = []
        self.closing = False
        self.transition = None
        self.pending_action = None
        self.display_token = None
        self.display_state = None
        self.display_sequence = None
        self.display_directory = Path('/run/ec26e/task-display')
        self.last_display_poll = 0.0
        root.title('几何拼图 · 任务菜单')
        root.geometry('1280x720')
        root.attributes('-fullscreen', True)
        root.configure(bg='#101926', cursor='arrow')
        root.protocol('WM_DELETE_WINDOW', self.close)
        for key, action in [('m', 'mode'), ('Return', 'confirm'), ('p', 'preview'), ('Escape', 'cancel')]:
            root.bind('<' + key + '>', lambda _, value=action: self.action(value, 'keyboard'))
        tk.Label(root, text='几何拼图 · 任务菜单', font=('WenQuanYi Micro Hei', 28, 'bold'),
                 fg='white', bg='#101926').pack(anchor='w', padx=24, pady=(12, 8))
        self.body = tk.Frame(root, bg='#101926')
        self.body.pack(fill='both', expand=True, padx=24)
        self.cards = []
        self.menu = tk.Frame(self.body, bg='#101926', width=292)
        self.menu.pack(side='left', fill='y', padx=(0, 12))
        self.menu.pack_propagate(False)
        for index, label in enumerate(MODES):
            card = tk.Button(self.menu, text=f'{index+1}    {label}', anchor='w', relief='flat',
                             font=('WenQuanYi Micro Hei', 21), padx=16, pady=16, cursor='hand2',
                             activebackground='#6de3bd', activeforeground='#071c16',
                             highlightthickness=2, highlightbackground='#263a51',
                             command=lambda i=index: self.choose(i))
            card.pack(fill='x', pady=7)
            self.cards.append(card)
        self.result_title = tk.Label(self.menu, text='摆放预测', anchor='w', bg='#101926',
            fg='#b3c5d8', font=('WenQuanYi Micro Hei', 15))
        self.result_title.pack(fill='x', pady=(12, 4))
        self.result_image = tk.Label(self.menu, bg='#101926')
        self.result_image.pack()
        self.legend = tk.Label(self.menu, text='点击开始后，识别、求解\n通过才显示本次目标位置。',
            justify='left', anchor='nw', wraplength=282, bg='#101926', fg='#b3c5d8',
            font=('WenQuanYi Micro Hei', 13))
        self.legend.pack(fill='x', pady=6)
        self.video = tk.Label(self.body, bg='#080e16', fg='white', font=('WenQuanYi Micro Hei', 20),
            text='点击“摄像头预览”查看实时画面', cursor='arrow')
        self.video.pack(side='right', fill='both', expand=True)
        bar = tk.Frame(root, bg='#101926')
        bar.pack(fill='x', padx=24, pady=8)
        self.buttons = {}
        for action, caption in [('mode', '切换模式'), ('confirm', '开始求解' if self.model.plan_only else '开始拼图'), ('preview', '摄像头预览'), ('cancel', '取消 / 返回')]:
            key_label = (BUTTONS[action][1] if physical_buttons_enabled(self.config)
                         else {'mode': 'M', 'confirm': 'Enter', 'preview': 'P', 'cancel': 'Esc'}[action])
            button = tk.Button(bar, text=caption + '  [' + key_label + ']',
                      command=lambda a=action: self.action(a, 'mouse'), font=('WenQuanYi Micro Hei', 15),
                      bg='#277a64' if action == 'confirm' else '#263a51', fg='white', relief='flat', pady=12,
                      cursor='hand2', activebackground='#416584', activeforeground='white',
                      disabledforeground='#8393a3', highlightthickness=1, highlightbackground='#42627d')
            button.pack(side='left', expand=True, fill='x', padx=5)
            self.buttons[action] = button
        self.status = tk.Label(root, anchor='w', bg='#101926', fg='#edf5ff',
                               font=('WenQuanYi Micro Hei', 16), wraplength=1200)
        self.status.pack(fill='x', padx=32, pady=(0, 6))
        self.health = tk.Label(root, anchor='w', bg='#101926', fg='#8fa8bf', font=('WenQuanYi Micro Hei', 11))
        self.health.pack(fill='x', padx=32, pady=(0, 14))
        self.refresh()
        root.after(20, self.poll)

    def _spawn_task(self, command, environment):
        token = uuid.uuid4().hex
        self.display_token, self.display_state = token, None
        environment = dict(environment, EC_TASK_DISPLAY_ID=token,
            EC_TASK_DISPLAY_DIR=str(getattr(self, 'display_directory', '/run/ec26e/task-display')))
        warm = getattr(self, 'warm_task', None)
        if warm is not None:
            return warm.dispatch(command, environment)
        return subprocess.Popen(command, env=environment)

    def start_task(self, label):
        launched_at = getattr(self, '_task_requested_at', time.monotonic())
        validate_menu_config(self.config)
        if self.config.get('plan_only') is True:
            env_path, task_dir = profile_paths(self.config)
            mode, _ = MODES[label]
            if not env_path.is_file() or not (task_dir / (mode + '.json')).is_file():
                raise RuntimeError('机器或任务配置不存在')
            backend = preview_backend(self.config)
            environment = dict(os.environ, EC_TASK_LAUNCHED_MONOTONIC=str(launched_at), EC_MOTION_ENABLED='false',
                               EC_PREPROCESS_BACKEND=backend,
                               EC_RIGID_BACKEND=self.config.get('rigid_backend', 'cpu'),
                               EC_CLEARANCE_BACKEND=self.config.get('clearance_backend', 'cpu'))
            return self._spawn_task([
                sys.executable, '-u', '-m', 'ec_26_e.commands.app', 'plan-live',
                '--env', str(env_path), '--config-dir', str(task_dir), '--mode', mode,
                '--backend', backend, '--output', '/run/ec26e/plan-result.json'], environment)
        if self.config.get('board') == 'axu5ev':
            if self.config.get('motion_enabled') is not True:
                raise RuntimeError('运动未启用')
            from ec_26_e.gui.controller import load_profile
            env_path, task_dir = profile_paths(self.config)
            mode, count = MODES[label]
            profile = load_profile(env_path, task_dir, label, preview_backend(self.config))
            if profile.actuation.bridge_port != '/dev/ec26e-m0-bridge':
                raise RuntimeError('AXU5EV 运动必须使用已配置的 M0 桥接')
            if not Path(profile.actuation.bridge_port).exists():
                raise RuntimeError('M0 桥接未连接')
            environment = dict(os.environ, EC_TASK_LAUNCHED_MONOTONIC=str(launched_at), EC_MOTION_ENABLED='true', EC_DRAW_ENABLED='false',
                EC_EXPECTED_PIECES='auto' if count is None else str(count),
                EC_PREPROCESS_BACKEND=preview_backend(self.config),
                EC_RIGID_BACKEND=self.config.get('rigid_backend', 'cpu'),
                EC_CLEARANCE_BACKEND=self.config.get('clearance_backend', 'cpu'))
            logger.info('用户启动%s：重新识别、稳定审查和求解通过后才执行 M0 搬运', label)
            return self._spawn_task([sys.executable, '-u', '-m', 'ec_26_e',
                '--env', str(env_path), '--task', 'task1' if mode == 'fixed' else 'task2',
                '--task-config', str(task_dir / (mode + '.json'))], environment)
        # The menu owns motion authorization; the PL loader only reports
        # loaded hardware and never authorizes or initiates movement.
        pl = json.loads(Path('/run/ec26e/pl-status.json').read_text())
        if pl.get('variant') != 'motion':
            raise RuntimeError('当前为预览硬件，禁止启动电机任务')
        if not (self.config.get('calibration_profile_accepted') is True
                or self.config.get('wiring_and_calibration_verified') is True):
            raise RuntimeError('尚未接受机器接线和标定配置')
        missing = [name for name in ('numpy', 'cv2', 'scipy', 'shapely', 'pynq', 'typer', 'serial', 'gpiod', 'puzzle_solver')
                   if importlib.util.find_spec(name) is None]
        if missing:
            raise RuntimeError('正式任务依赖尚未安装：' + ', '.join(missing))
        mode, count = MODES[label]
        env_path = Path(self.config.get('machine_env', '/opt/ec26e/config/machine.env'))
        task_dir = Path(self.config.get('task_config_dir', '/opt/ec26e/config/tasks'))
        task_path = task_dir / (mode + '.json')
        if not env_path.is_file() or not task_path.is_file():
            raise RuntimeError('机器或任务配置不存在')
        environment = dict(os.environ, EC_EXPECTED_PIECES='auto' if count is None else str(count),
                           EC_DRAW_ENABLED='false')
        logger.info("正在启动%s：加载 Python 运行依赖，随后观测纸片、求解和执行运动", label)
        return subprocess.Popen([sys.executable, '-u', '-m', 'ec_26_e', '--env', str(env_path),
             '--task', 'task1' if mode == 'fixed' else 'task2', '--task-config', str(task_path)],
             env=environment)

    def choose(self, index):
        if self.model.busy or self.transition is not None:
            return
        self.action(('choose', index), 'mouse')

    def action(self, action, source):
        if self.transition is not None:
            if action == 'cancel':
                self.pending_action = ('cancel', source)
            return
        if action == 'confirm':
            self._task_requested_at = time.monotonic()
        logger.info('菜单动作 %s (%s)', action, source)
        self.events.append({'action': action, 'source': source, 'at': time.time()})
        self.events = self.events[-20:]
        if self.camera.active and not self.model.busy:
            self.pending_action = (action, source)
            self.transition_error = None
            def release_camera():
                try:
                    self.camera.stop()
                except Exception as error:
                    self.transition_error = error
            self.transition = threading.Thread(target=release_camera, daemon=True)
            self.transition.start()
            self.model.message = '正在释放摄像头…'
            self.update_controls()
            return
        self._apply_action(action)

    def _apply_action(self, action):
        try:
            if action == 'confirm' and not self.model.busy:
                self.clear_prediction()
            if isinstance(action, tuple) and action[0] == 'choose':
                self.model.index = action[1]
                self.model.message = '已选择：' + self.model.label
            else:
                self.model.action(action)
            if action in ('mode', 'preview', 'cancel') or isinstance(action, tuple):
                self.clear_prediction()
            self.refresh()
        except Exception as error:
            logger.exception('菜单动作失败')
            self.model.message = str(error)

    def clear_prediction(self):
        self.display_token = self.display_state = None
        self.display_sequence = None
        self.result_image.configure(image='')
        self.result_photo = None
        self.legend.configure(text='点击开始后，识别、求解\n通过才显示本次目标位置。')
        self.result_title.configure(text='摆放预测')

    def update_controls(self):
        blocked = self.model.busy or self.transition is not None
        state = (blocked, self.model.preview)
        if state == getattr(self, '_controls_state', None):
            return
        self._controls_state = state
        for action, button in self.buttons.items():
            button.configure(state='disabled' if blocked and action != 'cancel' else 'normal')
        self.buttons['preview'].configure(text=('关闭预览' if self.model.preview else '摄像头预览') + '  [P]')
        for card in self.cards:
            card.configure(state='disabled' if blocked else 'normal')

    def refresh(self):
        if self.model.preview:
            self.video.configure(text='正在打开摄像头…')
            self.camera.start(self.model.label)
        else:
            self.camera_version = -1
            self.video.configure(image='')
            self.photo = None
            self.video.configure(text='正在识别与求解…' if self.model.busy else '点击“摄像头预览”查看实时画面')
        for index, card in enumerate(self.cards):
            selected = index == self.model.index
            card.configure(bg='#47cba2' if selected else '#1b2a3d', fg='#071c16' if selected else 'white')
        self.update_controls()

    def poll_prediction(self):
        if not self.display_token:
            return
        try:
            report = json.loads((self.display_directory / (self.display_token + '.json')).read_text(encoding='utf-8'))
            if report.get('token') != self.display_token:
                return
            if (report.get('state') == self.display_state
                    and (report.get('state') != 'acquiring'
                         or report.get('sequence') == self.display_sequence)):
                return
            state = report['state']
            if state in ('solving', 'ready') or (state == 'acquiring' and 'sequence' in report):
                self.photo = self.tk.PhotoImage(file=str(self.display_directory / (self.display_token + '.ppm')))
                self.video.configure(image=self.photo, text='')
            if state == 'ready':
                self.result_photo = self.tk.PhotoImage(file=str(self.display_directory / (self.display_token + '-result.ppm')))
                self.result_image.configure(image=self.result_photo)
                self.result_title.configure(text=f"预计拼合 {report['width_mm']:.1f} × {report['height_mm']:.1f} mm")
                self.legend.configure(text='\n'.join(f"{m['order']}. 碎片 {m['piece_id']}  顺时针 {m['rotation']:.1f}°" for m in report['legend']) + '\nS：当前位置  T：目标位置\n画面为本次采图快照')
            elif state == 'failed':
                self.video.configure(image='', text='本次未生成有效方案')
                self.result_image.configure(image='')
                self.legend.configure(text=report.get('reason', '求解失败'))
            self.display_state = state
            self.display_sequence = report.get('sequence')
        except (OSError, ValueError, self.tk.TclError):
            pass  # Atomic writer may not have published its first image yet.

    def poll(self):
        if self.closing:
            return
        now = time.monotonic()
        transition = getattr(self, 'transition', None)
        if transition is not None and not transition.is_alive():
            self.transition = None
            action, _source = self.pending_action
            self.pending_action = None
            if self.transition_error is None:
                self._apply_action(action)
            else:
                self.model.preview = False
                self.model.message = '摄像头释放失败：' + str(self.transition_error)
                self.update_controls()
        if self.keys is not None:
            try:
                self.key_levels = self.keys.read()
                for action in self.edges.update(self.key_levels, now):
                    self.action(action, 'physical')
            except OSError as error:
                self.key_error = str(error)
        self.model.poll_task()
        if not self.model.busy and self.transition is None and not self.model.preview:
            self.camera.prime()
        if hasattr(self, 'buttons'):
            self.update_controls()
            if now - self.last_display_poll >= .1:
                self.last_display_poll = now
                self.poll_prediction()
        warm = getattr(self, 'warm_task', None)
        if warm is not None and not self.model.busy:
            warm.prime()
        if self.model.preview:
            with self.camera.lock:
                data, version, error = self.camera.latest, self.camera.version, self.camera.error
            if error:
                self.video.configure(image='', text=error)
            elif data is not None and version != self.camera_version:
                if self.photo is None:
                    self.photo = self.tk.PhotoImage(data=data, format='PPM')
                    self.video.configure(image=self.photo, text='')
                else:
                    self.photo.configure(data=data, format='PPM')
                self.camera_version = version
        if self.model.message != self._status_text:
            self.status.configure(text=self.model.message)
            self._status_text = self.model.message
        health_text = ('Python ' + sys.version.split()[0] +
            ('  |  运动已启用  |  ' if self.model.motion_enabled else '  |  运动未启用  |  ') +
            ('按键异常：' + self.key_error if self.key_error else
             '四键监听中；键盘 M / Enter / P / Esc 也可操作' if self.keys is not None else
             '键鼠操作：M / Enter / P / Esc'))
        if health_text != self._health_text:
            self.health.configure(text=health_text)
            self._health_text = health_text
        if now - self.last_status >= 1:
            self.last_status = now
            if self.model.preview:
                try:
                    report = json.loads(Path('/run/ec26e/preview-status.json').read_text())
                    if time.time() - report.get('updated_at_unix', 0) < 5:
                        detail = report.get('reason') or '单帧轮廓合格；启动任务后仍需稳定性检查'
                        self.model.message = f"正式识别候选 {report.get('candidate_count', 0)} 个：{detail}"
                except (OSError, ValueError):
                    pass
            self.write_status()
        self.root.after(20, self.poll)

    def write_status(self):
        path = Path('/run/ec26e/menu-status.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  'pid': os.getpid(), 'python': sys.version.split()[0], 'mode': self.model.label,
                  'preview': self.model.preview, 'busy': self.model.busy,
                  'motion_enabled': self.model.motion_enabled, 'message': self.model.message,
                  'board': self.config.get('board', 'zynq7020'),
                  'plan_only': self.config.get('plan_only', False),
                  'button_error': self.key_error, 'pressed': self.key_levels,
                  'camera_frames': self.camera.version, 'events': self.events}
        report.update(transition=self.transition is not None, prediction_state=self.display_state,
                      prediction_token=self.display_token, cursor='arrow',
                      preview_worker_ready=self.camera.ready)
        report['preview_first_frame_seconds'] = self.camera.first_frame_seconds
        if self.warm_task is not None:
            report['warm_task_ready'] = self.warm_task.ready
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    def close(self):
        if self.transition is not None:
            self.pending_action = ('cancel', 'shutdown')
            self.root.after(100, self.close)
            return
        if self.model.busy:
            self.action('cancel', 'shutdown')
            self.root.after(100, self.close)
            return
        self.closing = True
        if self.warm_task is not None:
            self.warm_task.close()
        self.camera.close()
        if self.keys is not None:
            self.keys.close()
        self.root.destroy()


def main():
    import tkinter as tk
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    root = tk.Tk()
    # Xorg may reset settings when its last client disconnects. Keep Tk's
    # connection open before disabling display power saving for this session.
    if os.name == 'posix' and os.environ.get('DISPLAY'):
        for options in (('s', 'off'), ('-dpms',), ('s', 'reset')):
            try:
                subprocess.run(['xset', *options], check=True, timeout=3,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except (OSError, subprocess.SubprocessError):
                logging.warning('无法设置屏幕防休眠：xset %s', ' '.join(options))
    menu = BoardMenu(root)
    signal.signal(signal.SIGTERM, lambda *_: root.after(0, menu.close))
    signal.signal(signal.SIGINT, lambda *_: root.after(0, menu.close))
    try:
        root.mainloop()
    finally:
        menu.camera.close()
        if menu.warm_task is not None:
            menu.warm_task.close()
        if menu.keys is not None:
            menu.keys.close()


if __name__ == '__main__':
    main()
