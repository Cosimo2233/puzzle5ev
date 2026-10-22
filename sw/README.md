# AXU5EV-P 板端应用

保留 USB 采集、传统轮廓检测、PL 驱动、HDMI 操作界面和运动执行。几何求解通过独立 `puzzle-solver` 包调用，`ec_26_e.control` 为兼容适配层。

开发：相邻目录准备 `puzzle-solver` 与 `mspm0g3507-puzzle-bridge/host`，在本目录执行 `poetry install`。从本目录运行 `poetry run ec-7020 --help`。
配置模板在 `../deploy/axu5ev/`，默认 CPU、仅规划、禁用运动和实体按键。菜单由板端 `/opt/ec26e/config/axu5ev/menu.json` 选择新板配置；`ec-7020` 保留为兼容命令名称。

新板工作区参考位置已按 2026-10-21 现场图像更新；定位回退仍须验证真实四边，不能用旧参考框裁切后继续执行。相机或蓝色工作区位置变化时应重新核对 profile 的 `resize.reference_corners_relative`。本轮真实摄像头稳定识别及四片只求解已通过，见 [验证记录](../docs/validation/axu5ev-20261021/README.md)。
具体参数以 `poetry run ec-7020 gui --help` 为准。默认不驱动电机。

实际板端保留用户授权的运动权限，原机械标定下的固定四片搬运已由用户确认成功。
`MOTION_BACKEND=m0` 使用共享桥接会话驱动 XYZ、旋转轴和磁铁；XYZ 固件仅发送，旋转轴保留接收，详情见 [M0 接入记录](../docs/m0-bridge.md)。自动验证只求解。

## 模块与验证

- `gui/board_menu.py`：Tk 布局、鼠标/键盘、任务生命周期。
- `gui/camera_pipe.py`、`gui/production_preview.py`：独立采集进程、最新帧传输、异步轮廓分析。
- `gui/plan_preview.py`：正式 `MovePlan` 的快照投影、结果缩略图；不参与运动决策。
- `tasks/task1/runner.py`：固定/随机共享的采图、稳定审查、求解和执行编排。
- `vision/`、`control/`：视觉和 PL 适配；几何算法始终维护在独立求解器中。

`poetry run pytest` 默认收集 `tests/unit`、`tests/integration`；需要真实外部库或设备的 `tests/external` 必须显式指定。GPIO、执行锁、标定发布等 Linux 测试应在 Linux 环境运行，Windows 主机用于算法及模拟传输测试。旧板专用参考测试在 `../baselines/zynq7020-tests`，不参与新板测试。

部署使用固定版本 wheel，不依赖开发机的相邻源码目录。硬件与 Linux 配置见主工程 `deploy/` 和 `docs/`；构建入口在主工程根目录。
