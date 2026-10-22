"""提供适合 1280×720 触摸屏的现场标定文件控制台。"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import typer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ec_26_e.config.calibration import (
    CalibrationPaths,
    active_generation,
    default_calibration_paths,
    initialize_calibration,
    publish_draft,
    reset_draft,
    restore_previous,
    validate_draft,
)


DEFAULT_SOURCE_ENV = PROJECT_ROOT / ".env"
DEFAULT_ENV_TEMPLATE = PROJECT_ROOT / ".env.example"
DEFAULT_TASK1 = PROJECT_ROOT / "configs/tasks/task1.json"
DEFAULT_TASK2 = PROJECT_ROOT / "configs/tasks/task2.json"
VISION_SCRIPT = PROJECT_ROOT / "tests/external/cam/record_detected_video.py"
EDITOR_CANDIDATES = (
    (Path("/usr/bin/kate"), ("--new",)),
    (Path("/usr/bin/kwrite"), ("--new",)),
    (Path("/usr/bin/xdg-open"), ()),
)
EXTERNAL_GUI_ENVIRONMENT_KEYS = (
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QT_QPA_FONTDIR",
    "QT_PLUGIN_PATH",
)
BUTTON_DISPATCHER_SERVICE = "ec26e-button-dispatcher.service"
DEFAULT_BUTTON_STATUS = (
    Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{getattr(os, 'getuid', lambda: 0)()}"))
    / "ec26e/buttons-status.json"
)
DEFAULT_TASK_OUTPUT = DEFAULT_BUTTON_STATUS.with_name("task-output.log")
TASK_OUTPUT_MAX_CHARACTERS = 12_000


class PreviewTask(str, Enum):
    """列出桌面快捷入口允许预览的正式任务。"""

    task1 = "task1"
    task2 = "task2"


def initialize_default_workspace(paths: CalibrationPaths) -> None:
    """使用当前仓库现场值初始化持久化标定工作区。"""

    initialize_calibration(
        paths,
        source_env=DEFAULT_SOURCE_ENV,
        env_template=DEFAULT_ENV_TEMPLATE,
        source_task1=DEFAULT_TASK1,
        source_task2=DEFAULT_TASK2,
    )


def _editor_command(
    path: Path,
    candidates: tuple[tuple[Path, tuple[str, ...]], ...] = EDITOR_CANDIDATES,
) -> list[str]:
    """选择本机可用且会创建独立窗口的图形编辑器命令。"""

    for executable, arguments in candidates:
        if executable.is_file():
            return [str(executable), *arguments, str(path)]
    raise RuntimeError("未找到 Kate、KWrite 或 xdg-open")


def _external_gui_environment(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """移除 OpenCV 注入的 Qt 路径，避免系统 KDE 程序加载冲突插件。"""

    result = dict(os.environ if environ is None else environ)
    for name in EXTERNAL_GUI_ENVIRONMENT_KEYS:
        result.pop(name, None)
    return result


def preview_command(paths: CalibrationPaths, task: PreviewTask) -> list[str]:
    """构造不录像且显式绑定 active 任务 JSON 的预览命令。"""

    return [
        sys.executable,
        str(VISION_SCRIPT),
        "--env",
        str(paths.active_env),
        "--task",
        task.value,
        "--task-config",
        str(paths.active_task(task.value)),
        "--no-record",
        "--preview",
        "--fullscreen",
    ]


def dispatcher_stop_command() -> list[str]:
    """构造只取消当前任务、不停止常驻按键服务的命令。"""

    return [
        "systemctl",
        "--user",
        "kill",
        "--kill-who=main",
        "--signal=SIGUSR1",
        BUTTON_DISPATCHER_SERVICE,
    ]


def dispatcher_start_command() -> list[str]:
    """构造启用开机自启并立刻启动按键监听服务的命令。"""

    return [
        "systemctl",
        "--user",
        "enable",
        "--now",
        BUTTON_DISPATCHER_SERVICE,
    ]


def dispatcher_service_is_active(
    runner: object = subprocess.run,
) -> bool:
    """查询按键监听服务是否已处于活动状态。"""

    if not callable(runner):
        raise TypeError("runner 必须可调用")
    completed = runner(  # type: ignore[operator]
        ["systemctl", "--user", "is-active", BUTTON_DISPATCHER_SERVICE],
        capture_output=True,
        text=True,
        check=False,
        timeout=3.0,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 3:
        return False
    detail = completed.stderr.strip() or completed.stdout.strip()
    raise RuntimeError(
        f"无法查询按键监听服务：{detail or f'退出码 {completed.returncode}'}"
    )


def active_task_from_status(status_path: Path = DEFAULT_BUTTON_STATUS) -> str | None:
    """从原子状态快照读取当前任务；缺失或损坏时返回未知。"""

    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    task_name = payload.get("active_task")
    return task_name if isinstance(task_name, str) and task_name else None


def read_task_output(
    output_path: Path = DEFAULT_TASK_OUTPUT,
    *,
    maximum_characters: int = TASK_OUTPUT_MAX_CHARACTERS,
) -> str:
    """读取运行日志末尾，缺失或暂时不可读时返回说明文字。"""

    if isinstance(maximum_characters, bool) or maximum_characters <= 0:
        raise ValueError("maximum_characters 必须是正整数")
    try:
        content = output_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "等待正式任务输出……"
    except OSError as error:
        return f"无法读取运行日志：{error}"
    if len(content) <= maximum_characters:
        return content or "等待正式任务输出……"
    return "……（仅显示最新日志）\n" + content[-maximum_characters:]


def run_quick_preview(paths: CalibrationPaths, task: PreviewTask) -> int:
    """初始化配置后在前台运行指定任务的快速视觉预览。"""

    initialize_default_workspace(paths)
    completed = subprocess.run(
        preview_command(paths, task),
        cwd=PROJECT_ROOT,
        env=dict(os.environ),
        check=False,
    )
    return completed.returncode


class CalibrationConsole:
    """组织文本编辑、校验发布、恢复、预览和任务停止操作。"""

    def __init__(self, root: tk.Tk, paths: CalibrationPaths) -> None:
        global tk, messagebox
        import tkinter as tk
        from tkinter import messagebox
        """构建大按钮现场标定控制台。"""

        self.root = root
        self.paths = paths
        self.preview_process: subprocess.Popen[bytes] | None = None
        self.editor_process: subprocess.Popen[bytes] | None = None
        self.status = tk.StringVar()
        self.output_path = DEFAULT_TASK_OUTPUT
        self._shown_output = ""
        self._configure_window()
        self._build_layout()
        self.refresh_status("现场标定工作区已就绪。")

    def _configure_window(self) -> None:
        """配置适合当前 1280×720 显示器的全屏窗口。"""

        self.root.title("EC26E 现场标定")
        self.root.configure(background="#16202a")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda _event: self.root.destroy())

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command: object,
        *,
        background: str = "#2f6f9f",
    ) -> tk.Button:
        """创建为日志显示区留出空间的统一触摸按钮。"""

        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Noto Sans CJK SC", 12, "bold"),
            foreground="white",
            background=background,
            activebackground="#438bc0",
            activeforeground="white",
            relief=tk.FLAT,
            borderwidth=0,
            padx=10,
            pady=10,
        )

    def _build_layout(self) -> None:
        """构建紧凑按钮、固定运行日志窗和状态区域。"""

        title = tk.Label(
            self.root,
            text="EC26E 现场标定",
            font=("Noto Sans CJK SC", 28, "bold"),
            foreground="white",
            background="#16202a",
            pady=8,
        )
        title.pack(fill=tk.X)

        body = tk.Frame(self.root, background="#16202a")
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=6)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        controls = tk.Frame(body, background="#16202a")
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        for column in range(2):
            controls.grid_columnconfigure(column, weight=1, uniform="buttons")
        for row in range(6):
            controls.grid_rowconfigure(row, weight=1)

        buttons = (
            ("编辑机器 .env", self.open_environment, "#2f6f9f"),
            ("编辑 Task1 JSON", self.open_task1, "#2f6f9f"),
            ("编辑 Task2 JSON", self.open_task2, "#2f6f9f"),
            ("检查草稿", self.validate, "#526d82"),
            ("检查并应用", self.publish, "#23855b"),
            ("放弃未应用修改", self.discard, "#8b6535"),
            ("预览 Task1（不录像）", self.preview_task1, "#6d4ba5"),
            ("预览 Task2（不录像）", self.preview_task2, "#6d4ba5"),
            ("恢复上一版", self.restore, "#a34b4b"),
        )
        for index, (text, command, color) in enumerate(buttons):
            button = self._button(controls, text, command, background=color)
            button.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=8,
                pady=8,
            )

        self.dispatcher_button = self._button(
            controls,
            "开启按键监听",
            self.ensure_dispatcher_service,
            background="#23855b",
        )
        self.dispatcher_button.grid(
            row=5,
            column=0,
            sticky="nsew",
            padx=8,
            pady=8,
        )
        stop_button = self._button(
            controls,
            "停止当前任务",
            self.stop_current_task,
            background="#c0392b",
        )
        stop_button.grid(
            row=5,
            column=1,
            sticky="nsew",
            padx=8,
            pady=8,
        )

        output_frame = tk.Frame(body, background="#0f171f")
        output_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        tk.Label(
            output_frame,
            text="运行日志",
            anchor="w",
            font=("Noto Sans CJK SC", 16, "bold"),
            foreground="white",
            background="#0f171f",
            padx=12,
            pady=8,
        ).pack(fill=tk.X)
        self.output_display = tk.Text(
            output_frame,
            width=36,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Noto Sans Mono CJK SC", 11),
            foreground="#d6e4ef",
            background="#0a1016",
            insertbackground="white",
            relief=tk.FLAT,
            borderwidth=0,
            padx=10,
            pady=8,
        )
        self.output_display.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        footer = tk.Frame(self.root, background="#0f171f")
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        status_label = tk.Label(
            footer,
            textvariable=self.status,
            justify=tk.LEFT,
            anchor="w",
            font=("Noto Sans CJK SC", 13),
            foreground="#d6e4ef",
            background="#0f171f",
            padx=18,
            pady=12,
        )
        status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        exit_button = self._button(
            footer,
            "退出",
            self.root.destroy,
            background="#5c6570",
        )
        exit_button.configure(font=("Noto Sans CJK SC", 13, "bold"), pady=6)
        exit_button.pack(side=tk.RIGHT, padx=12, pady=8)
        self._refresh_output()
        self._refresh_dispatcher_button()

    def _set_dispatcher_button_active(self, active: bool) -> None:
        """用灰色禁用态明确表示监听服务已经开启。"""

        if active:
            self.dispatcher_button.configure(
                text="按键监听已开启",
                state=tk.DISABLED,
                background="#5c6570",
                disabledforeground="#e4e9ed",
            )
            return
        self.dispatcher_button.configure(
            text="开启按键监听",
            state=tk.NORMAL,
            background="#23855b",
        )

    def _refresh_dispatcher_button(self) -> None:
        """周期检查服务状态，使服务恢复或停止后按钮状态保持准确。"""

        try:
            self._set_dispatcher_button_active(dispatcher_service_is_active())
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            # 查询失败时不锁定按钮，现场人员仍可主动尝试恢复监听服务。
            self._set_dispatcher_button_active(False)
        self.root.after(2_000, self._refresh_dispatcher_button)

    def ensure_dispatcher_service(self) -> None:
        """仅在监听未启动时启用开机自启并启动当前用户服务。"""

        try:
            if dispatcher_service_is_active():
                self._set_dispatcher_button_active(True)
                self.refresh_status("按键监听服务已开启，无需重复启动。")
                return
            completed = subprocess.run(
                dispatcher_start_command(),
                cwd=PROJECT_ROOT,
                env=dict(os.environ),
                capture_output=True,
                text=True,
                check=False,
                timeout=8.0,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise RuntimeError(
                    f"启动按键监听服务失败：{detail or f'退出码 {completed.returncode}'}"
                )
            if not dispatcher_service_is_active():
                raise RuntimeError("按键监听服务未进入运行状态")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self._set_dispatcher_button_active(False)
            self.refresh_status(f"按键监听服务启动失败：{error}")
            messagebox.showerror("启动按键监听失败", str(error))
            return
        self._set_dispatcher_button_active(True)
        self.refresh_status("按键监听服务已开启，并已设为开机自启。")

    def _refresh_output(self) -> None:
        """定时把任务子进程日志显示在固定窗口的末尾。"""

        output = read_task_output(self.output_path)
        if output != self._shown_output:
            self.output_display.configure(state=tk.NORMAL)
            self.output_display.delete("1.0", tk.END)
            self.output_display.insert(tk.END, output)
            self.output_display.see(tk.END)
            self.output_display.configure(state=tk.DISABLED)
            self._shown_output = output
        self.root.after(400, self._refresh_output)

    def refresh_status(self, message: str) -> None:
        """显示操作结果和当前 active 版本。"""

        try:
            generation = active_generation(self.paths).name
        except (OSError, RuntimeError):
            generation = "不可用"
        self.status.set(
            f"{message}\n当前版本：{generation}    草稿：{self.paths.editing_directory}"
        )

    def _open(self, path: Path) -> None:
        """隐藏全屏控制台，并用前台独立窗口打开指定草稿文件。"""

        if self.editor_process is not None and self.editor_process.poll() is None:
            messagebox.showwarning("编辑器已打开", "请先关闭当前编辑窗口。")
            return
        if not path.is_file():
            messagebox.showerror("打开失败", f"草稿文件不存在：\n{path}")
            return
        self.root.withdraw()
        try:
            self.editor_process = subprocess.Popen(
                _editor_command(path),
                cwd=PROJECT_ROOT,
                env=_external_gui_environment(),
            )
        except (OSError, RuntimeError) as error:
            self._restore_console()
            messagebox.showerror("打开失败", str(error))
            return
        self.refresh_status(f"正在编辑：{path}")
        self.root.after(250, self._poll_editor)

    def _poll_editor(self) -> None:
        """轮询独立编辑器，关闭后自动恢复全屏控制台。"""

        if self.editor_process is not None and self.editor_process.poll() is None:
            self.root.after(250, self._poll_editor)
            return
        return_code = (
            self.editor_process.returncode if self.editor_process is not None else None
        )
        self.editor_process = None
        self._restore_console()
        if return_code not in (None, 0):
            messagebox.showerror("编辑器异常退出", f"编辑器退出码：{return_code}")
            return
        self.refresh_status("编辑器已关闭；保存后可检查并应用配置。")

    def _restore_console(self) -> None:
        """恢复全屏控制台并主动获取输入焦点。"""

        self.root.deiconify()
        self.root.attributes("-fullscreen", True)
        self.root.lift()
        self.root.focus_force()

    def open_environment(self) -> None:
        """打开包含全部机器现场参数的环境草稿。"""

        self._open(self.paths.editing_env)

    def open_task1(self) -> None:
        """打开 Task1 完整 JSON 草稿。"""

        self._open(self.paths.editing_task1)

    def open_task2(self) -> None:
        """打开 Task2 完整 JSON 草稿。"""

        self._open(self.paths.editing_task2)

    def validate(self) -> None:
        """校验草稿中的环境和两个任务配置。"""

        try:
            result = validate_draft(self.paths)
        except (OSError, TypeError, ValueError) as error:
            self.refresh_status(f"草稿校验失败：{error}")
            messagebox.showerror("草稿校验失败", str(error))
            return
        self.refresh_status(f"草稿校验通过；当前选择 {result.selected_task}。")
        messagebox.showinfo("校验通过", "machine.env、Task1、Task2 全部有效。")

    def publish(self) -> None:
        """确认后校验并原子发布整套草稿。"""

        if not messagebox.askyesno(
            "应用配置",
            "确认已经在编辑器中保存？\n将校验并原子应用整套配置。",
        ):
            return
        try:
            result = publish_draft(self.paths)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.refresh_status(f"应用失败，原配置保持不变：{error}")
            messagebox.showerror("应用失败", str(error))
            return
        self.refresh_status(f"配置已应用；当前选择 {result.selected_task}。")
        messagebox.showinfo("应用成功", "新配置已生效，旧版本已保留。")

    def discard(self) -> None:
        """确认后丢弃草稿修改并恢复当前生效内容。"""

        if not messagebox.askyesno(
            "放弃修改",
            "将丢弃尚未应用的草稿修改，是否继续？",
        ):
            return
        try:
            reset_draft(self.paths)
        except OSError as error:
            messagebox.showerror("恢复草稿失败", str(error))
            return
        self.refresh_status("草稿已恢复为当前生效配置。")

    def restore(self) -> None:
        """确认后恢复最近一个非当前的已验证版本。"""

        if not messagebox.askyesno(
            "恢复上一版",
            "将恢复上一版已验证配置，并覆盖当前草稿，是否继续？",
        ):
            return
        try:
            target = restore_previous(self.paths)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            messagebox.showerror("恢复失败", str(error))
            return
        self.refresh_status(f"已恢复版本：{target.name}")
        messagebox.showinfo("恢复成功", f"当前版本：{target.name}")

    def _preview(self, task: PreviewTask) -> None:
        """启动一个非录像预览，并拒绝同一控制台重复启动。"""

        if self.preview_process is not None:
            if self.preview_process.poll() is None:
                messagebox.showwarning("预览已运行", "请先在预览窗口按 Esc 退出。")
                return
            self.preview_process = None
        try:
            self.preview_process = subprocess.Popen(
                preview_command(self.paths, task),
                cwd=PROJECT_ROOT,
                env=dict(os.environ),
                start_new_session=True,
            )
        except OSError as error:
            messagebox.showerror("预览启动失败", str(error))
            return
        self.refresh_status(f"已启动 {task.value} 快速预览；Esc 返回。")

    def preview_task1(self) -> None:
        """启动使用 active Task1 配置的快速预览。"""

        self._preview(PreviewTask.task1)

    def preview_task2(self) -> None:
        """启动使用 active Task2 配置的快速预览。"""

        self._preview(PreviewTask.task2)

    def stop_current_task(self) -> None:
        """请求当前预览和正式任务走各自的安全退出路径。"""

        stopped: list[str] = []
        errors: list[str] = []
        preview = self.preview_process
        if preview is not None and preview.poll() is None:
            try:
                os.killpg(preview.pid, signal.SIGINT)
                stopped.append("视觉预览")
            except ProcessLookupError:
                self.preview_process = None
            except OSError as error:
                errors.append(f"视觉预览停止失败：{error}")

        active_task = active_task_from_status()
        try:
            completed = subprocess.run(
                dispatcher_stop_command(),
                cwd=PROJECT_ROOT,
                env=dict(os.environ),
                capture_output=True,
                text=True,
                check=False,
                timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"正式任务停止请求失败：{error}")
        else:
            if completed.returncode == 0:
                if active_task is not None:
                    stopped.append(active_task)
            else:
                detail = completed.stderr.strip() or f"退出码 {completed.returncode}"
                errors.append(f"正式任务停止请求失败：{detail}")

        if errors:
            message = "；".join(errors)
            self.refresh_status(message)
            messagebox.showerror("停止请求存在错误", message)
            return
        if not stopped:
            self.refresh_status("当前没有正在运行的任务。")
            messagebox.showinfo("无需停止", "当前没有正在运行的任务。")
            return
        targets = "、".join(stopped)
        self.refresh_status(
            f"已请求停止 {targets}；正在执行消磁、电机停止和资源清理。"
        )
        messagebox.showinfo(
            "正在安全停止",
            f"已请求停止 {targets}。\n请等待状态恢复为空闲后再开始下一次任务。",
        )


app = typer.Typer(add_completion=False, help="启动 EC26E 现场标定控制台。")


@app.command()
def main(
    data_root: Path = typer.Option(
        default_calibration_paths().root,
        "--data-root",
        help="现场标定草稿、发布版本和 active 链接的持久化目录。",
    ),
    preview_task: PreviewTask | None = typer.Option(
        None,
        "--preview-task",
        help="不打开控制台，直接启动指定任务的不录像视觉预览。",
    ),
) -> None:
    """初始化标定数据，并打开控制台或直接运行快速预览。"""

    paths = CalibrationPaths(data_root)
    try:
        initialize_default_workspace(paths)
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        typer.echo(f"现场标定初始化失败：{error}", err=True)
        raise typer.Exit(code=2) from error
    if preview_task is not None:
        raise typer.Exit(code=run_quick_preview(paths, preview_task))
    import tkinter as tk
    root = tk.Tk()
    CalibrationConsole(root, paths)
    root.mainloop()


if __name__ == "__main__":
    app()
