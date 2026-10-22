# 代码结构与维护入口

正式硬件工程为根目录 `puzzle5ev.xpr`，保存的 BD 随 Git 维护。软件仍由三个独立仓库协作：本仓库的板端应用、同级 `puzzle-solver` 几何库、同级 `mspm0g3507-puzzle-bridge` 固件及协议库。部署 wheel，不复制求解器源码到应用。

| 职责 | 维护位置 | 边界 |
| --- | --- | --- |
| HLS / RTL / 引脚与时序 | `src/`、`constraints/axu5ev/` | 修改源文件后通过脚本重建，不手改生成 HDL |
| 构建与导出 | `scripts/`、`Build.ps1` | 生成物进入 `build/`、`artifacts/` |
| 采集与识别 | `sw/src/ec_26_e/cam/`、`vision/` | PS 采图、透视和轮廓；PL 分割、滤波、形态学 |
| 任务编排 | `sw/src/ec_26_e/tasks/` | 固定/随机共用稳定审查、求解和执行检查 |
| 几何求解 | `../puzzle-solver/src/puzzle_solver/` | 公共输入输出 `PuzzlePiece → MovePlan`；批量拟合可注入后端 |
| PL 适配 | `vision/linux_pl.py`、`control/pl_rigid.py`、`control/pl_clearance.py` | PS 统一持锁调度，传输/协议错误阻止任务继续 |
| 随机候选距离 | `src/hls/candidate/`、`control/pl_candidate.py` | 凹/凸多边形批量距离，只做带保护带的候选粗筛；见 [接口说明](pl-candidate-scan.md) |
| 菜单与任务生命周期 | `gui/board_menu.py`、`menu_model.py`、`controller.py` | Tk 主线程处理鼠标/键盘，后台线程释放相机 |
| 预览通信 / 识别 | `gui/camera_pipe.py`、`production_preview.py` | 采集进程只保留最新帧；分析慢时画面继续刷新 |
| 预览顶点显示 | `gui/preview_tracking.py` | 小幅抖动滤波；不影响正式观测或运动坐标 |
| 视觉运行初始化 | `vision/runtime.py` | 空闲预热 Lab 查找表，限制 OpenCV 工作线程 |
| 结果投影 | `gui/plan_preview.py` | 只显示已检查的方案，不修改执行坐标；令牌隔离本次结果 |
| 运动 | `sw/src/ec_26_e/motion/` | 沿用实际机械标定、等待和 M0 桥接策略 |
| 部署和板端验证 | `deploy/axu5ev/` | 安装器、只读核验、无运动基准分开 |

`gui/plan_preview.py` 将稳定采图时的透视矩阵反向用于目标投影，因此预测轮廓与实际方案使用同一坐标转换。实时预览显示最新画面；任务结果使用本次稳定快照，避免把过期识别叠在已移动的纸片上。`/run/ec26e/task-display` 最多保留四次任务，避免内存文件系统持续增长。

## 测试、记录与历史材料

- 主机纯软件/模拟传输测试：`sw/tests/unit/`、`sw/tests/integration/`；求解器测试在自己的仓库。需要 Linux 的文件锁、GPIO/设备路径测试在 Linux 上运行。
- 外部设备/库：`sw/tests/external/`，单独指定运行。
- 板端验证：`deploy/axu5ev/validate_*.py`、`benchmark_*.py`；测试记录注明主机或板端、录制或实时、几何或端到端，不互相代替。
- 可追溯验收证据：`docs/validation/`，只保存必要报告和代表性画面。
- 旧板测试参考：`baselines/zynq7020-tests/`，不参与 AXU5EV 默认测试。
- 临时诊断、完整批量日志、wheel 和工具缓存：`build/` 或 `artifacts/`，不提交 Git。

不要为整理目录移动正在运行的 Vivado 工程依赖。保留当前 XPR/BD 引用和可重建入口；不以删除生成 IP 的方式换取目录整洁。
