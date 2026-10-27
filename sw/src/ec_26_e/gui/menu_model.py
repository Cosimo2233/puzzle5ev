"""板载菜单状态与任务进程生命周期；不依赖数值或摄像头库。"""
from __future__ import annotations

import signal

MODES = {"固定四片": ("fixed", 4), "随机拼图": ("random", None)}
MODES["七巧板"] = ("tangram", 7)


class MenuModel:
    def __init__(self, *, motion_enabled=False, start_task=None, plan_only=False):
        if motion_enabled and plan_only:
            raise ValueError('只求解模式不能同时启用运动')
        self.motion_enabled = motion_enabled
        self.plan_only = plan_only
        self.task_plan_only = plan_only
        self.start_task = start_task
        self.index = 0
        self.preview = False
        self.process = None
        self.cancel_requested = False
        self.message = "选择任务模式，按确认开始；按预览查看摄像头"

    @property
    def label(self):
        return tuple(MODES)[self.index]

    @property
    def busy(self):
        return self.process is not None

    def action(self, action):
        if action == "cancel":
            self.preview = False
            if self.busy:
                if not self.cancel_requested:
                    try:
                        self.process.send_signal(signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    self.cancel_requested = True
                self.message = "正在取消任务并等待硬件清理…"
            else:
                self.message = "已返回任务菜单"
            return
        if self.busy:
            self.message = "任务运行中；取消键可中止当前任务"
            return
        if action == "mode":
            self.index = (self.index + 1) % len(MODES)
            self.message = "已选择：" + self.label
        elif action == "preview":
            self.preview = not self.preview
            self.message = "摄像头实时预览" if self.preview else "任务菜单"
        elif action == "confirm":
            self.preview = False
            if not self.motion_enabled and not self.plan_only:
                self.message = self.label + "：运动未启用，请先完成电机接线和标定"
                return
            if self.start_task is None:
                raise RuntimeError("没有配置正式任务入口")
            self.process = self.start_task(self.label)
            self.cancel_requested = False
            self.message = ("正在识别与求解（不运动）：" if self.task_plan_only else "正在执行：") + self.label
        else:
            raise ValueError("未知菜单动作：" + action)

    def poll_task(self):
        if not self.busy:
            return
        code = self.process.poll()
        if code is None:
            return
        self.message = ("当前任务已取消" if self.cancel_requested else
                        ("求解完成（未执行运动）" if self.task_plan_only else "任务完成")
                        if code == 0 else f"任务失败（{code}），请检查日志")
        self.process = None
        self.cancel_requested = False


class PressEdges:
    """低电平按键消抖；开机按住不触发，长按只触发一次。"""
    def __init__(self, actions, debounce=0.04):
        self.debounce = debounce
        self.state = {name: [None, 0.0, None, False] for name in actions}

    def update(self, samples, now):
        events = []
        for name, pressed in samples.items():
            state = self.state[name]
            if state[0] != pressed:
                state[0], state[1] = pressed, now
            if now - state[1] < self.debounce or state[2] == pressed:
                continue
            state[2] = pressed
            if not pressed:
                state[3] = True
            elif state[3]:
                state[3] = False
                events.append(name)
        # Two keys pressed together never start a task; cancel has priority.
        if "cancel" in events:
            return ["cancel"]
        return events if len(events) <= 1 else []
