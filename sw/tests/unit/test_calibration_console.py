"""
验证现场标定控制台的编辑器环境、任务状态读取和安全停止命令。

该文件不依赖相机、显示器或其他外部环境，可通过 pytest 独立运行。
"""

from pathlib import Path
import subprocess

import pytest

from tools.calibration.console import (
    _editor_command,
    _external_gui_environment,
    active_task_from_status,
    dispatcher_service_is_active,
    dispatcher_start_command,
    dispatcher_stop_command,
    read_task_output,
)


def test_editor_command_forces_independent_kate_window(tmp_path: Path) -> None:
    """验证 Kate 使用独立窗口参数，避免启动请求静默退出。"""

    executable = tmp_path / "kate"
    executable.touch()
    target = tmp_path / "machine.env"

    command = _editor_command(target, ((executable, ("--new",)),))

    assert command == [str(executable), "--new", str(target)]


def test_editor_command_falls_back_to_next_available_editor(tmp_path: Path) -> None:
    """验证首选编辑器不存在时会使用后备编辑器。"""

    missing = tmp_path / "missing-kate"
    fallback = tmp_path / "xdg-open"
    fallback.touch()
    target = tmp_path / "task1.json"

    command = _editor_command(
        target,
        ((missing, ("--new",)), (fallback, ())),
    )

    assert command == [str(fallback), str(target)]


def test_editor_command_rejects_missing_editors(tmp_path: Path) -> None:
    """验证没有可用图形编辑器时返回明确错误。"""

    with pytest.raises(RuntimeError, match="未找到 Kate"):
        _editor_command(tmp_path / "machine.env", ())


def test_external_gui_environment_removes_opencv_qt_paths() -> None:
    """验证系统编辑器不会继承 OpenCV 私有 Qt 插件目录。"""

    source = {
        "DISPLAY": ":0",
        "QT_QPA_PLATFORM_PLUGIN_PATH": "/venv/site-packages/cv2/qt/plugins",
        "QT_QPA_FONTDIR": "/venv/site-packages/cv2/qt/fonts",
        "QT_PLUGIN_PATH": "/venv/site-packages/cv2/qt/plugins",
        "EC_TASK": "task1",
    }

    result = _external_gui_environment(source)

    assert result == {"DISPLAY": ":0", "EC_TASK": "task1"}
    assert source["QT_QPA_PLATFORM_PLUGIN_PATH"].endswith("cv2/qt/plugins")


def test_dispatcher_stop_command_only_signals_service_main_process() -> None:
    """验证停止按钮不会关闭常驻服务或直接强杀任务子进程。"""

    assert dispatcher_stop_command() == [
        "systemctl",
        "--user",
        "kill",
        "--kill-who=main",
        "--signal=SIGUSR1",
        "ec26e-button-dispatcher.service",
    ]


def test_dispatcher_start_command_enables_and_starts_service() -> None:
    """验证监听按钮使用用户级开机自启并立即启动服务。"""

    assert dispatcher_start_command() == [
        "systemctl",
        "--user",
        "enable",
        "--now",
        "ec26e-button-dispatcher.service",
    ]


def test_dispatcher_service_is_active_interprets_systemctl_status() -> None:
    """验证监听按钮只在服务确实运行时进入灰色禁用态。"""

    commands: list[list[str]] = []

    def active_runner(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """模拟 systemctl 报告服务正在运行。"""

        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="active\n")

    def inactive_runner(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """模拟 systemctl 报告服务尚未运行。"""

        return subprocess.CompletedProcess(command, 3, stdout="inactive\n")

    assert dispatcher_service_is_active(active_runner)
    assert not dispatcher_service_is_active(inactive_runner)
    assert commands == [
        ["systemctl", "--user", "is-active", "ec26e-button-dispatcher.service"]
    ]


def test_active_task_from_status_handles_valid_and_broken_files(
    tmp_path: Path,
) -> None:
    """验证界面只把有效状态快照中的任务显示为停止目标。"""

    status_path = tmp_path / "status.json"
    status_path.write_text('{"active_task": "task2"}\n', encoding="utf-8")
    assert active_task_from_status(status_path) == "task2"

    status_path.write_text("not-json\n", encoding="utf-8")
    assert active_task_from_status(status_path) is None


def test_read_task_output_returns_latest_content_and_handles_missing_file(
    tmp_path: Path,
) -> None:
    """验证运行日志窗口只保留可读的最新内容。"""

    output_path = tmp_path / "task-output.log"
    assert read_task_output(output_path) == "等待正式任务输出……"

    output_path.write_text("第一行\n第二行\n", encoding="utf-8")
    assert read_task_output(output_path) == "第一行\n第二行\n"
    assert read_task_output(output_path, maximum_characters=3).endswith("二行\n")
