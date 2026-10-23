# 摄像头启动与任务首帧显示优化

## 原因和修改

原 `CameraPipe.close()` 结束预览子进程。再次预览或切换模式会重新导入 OpenCV、数值计算和视觉模块。板端三次测量依赖加载均约 3.19 秒，相机打开并传回首张 PPM 约 0.74～0.76 秒，关闭约 0.67～0.92 秒；连起来约 4.6～4.9 秒。菜单空闲预热完成后，原版本首次预览也可低于一秒，问题主要在反复打开和快速切换。

新增 `preview_server.py` 维护预览请求生命周期。关闭预览后，进程保留已导入软件，每次相机、检测器、PL 资源及任务锁仍完整释放；收到独立的释放确认后菜单才启动后续任务。预览错误会显示原始原因，下一次请求可重试。软件预热确认与设备释放确认使用不同报文，避免打开过程中快速取消误判设备已释放。

任务原来只在连续稳定审查通过后发布画面。现在读取第一张新帧就显示，后续最多以 2 FPS 更新采集图，保留 CPU 给识别；相机原始分辨率、检测输入和稳定门槛不变。采集画面不设置稳定快照，不能用于发布预测或执行。菜单用任务令牌及帧序号刷新，避免旧任务图像和重复加载。增加相机启动/关闭与预览首帧计时。

## 板端测量

平台：AXU5EV-P，原 1280×720 MJPEG/30 FPS，相同 PL 和标定，未重启板卡。

| 范围 | 实测 |
| --- | --- |
| 五次固定/随机交替预览首帧 | 0.771、0.595、0.583、0.587、0.603 秒 |
| 五次停止预览、释放设备 | 0.20～0.32 秒 |
| 预览进程 | 五次使用同一 PID，每次停止后可重新获得任务锁 |
| 预览切到只求解，Tk 显示首张采集图 | 1.284 秒，含释放预览设备 |
| 安装后菜单预热完成再打开预览 | 首帧 0.756 秒，5 秒内接收 59 帧 |
| 正式 HDMI 菜单按 P 打开 / 再次打开 | 首帧 0.732 / 0.573 秒，已查看实际预览截图 |
| 菜单启动一次性预热 | 约 3.2～3.4 秒，开机后立即点击仍可能等待此次预热 |

首帧数据是出画面延迟，不能等同完整识别、求解或运动完成时间。当前场景被检测为“轮廓质量不足或疑似粘连”，只求解测试未通过连续稳定审查，验证程序到时取消。采图过程中画面持续更新；本轮没有实际搬运，也不声称此次场景求解通过。

证据：[原版基线](camera-start-20261023/before.json)、[五次复用](camera-start-20261023/after.json)、[任务首图](camera-start-20261023/task-runtime.json)、[安装后测试窗口](camera-start-20261023/installed-runtime.json)、[正式菜单首次预览](camera-start-20261023/production-preview.json)、[正式菜单再次预览](camera-start-20261023/production-reopen.json)、[配置与菜单核验](camera-start-20261023/installed.json)。测试窗口与正式菜单并存时屏幕截图可能仍显示正式菜单，测试窗口的首图时间来自 Tk 更新回调；正式菜单另外通过仅触发 P 键验证，未触发开始运动。

## 软件验证和部署

在 `sw/` 执行：

```powershell
poetry run pytest tests/unit/test_preview_server.py tests/unit/test_production_preview.py tests/unit/test_plan_preview.py tests/unit/test_warm_task.py tests/unit/test_board_menu.py tests/unit/cam/test_camera.py tests/unit/tasks/test_task1_runner.py -q
poetry build -f wheel
```

55 项测试、7 个子测试通过，覆盖释放确认顺序、工作进程复用、故障报文、图像截断、首张采图不能冒充稳定结果、连续帧刷新与旧令牌隔离。额外的相机 CLI 配置测试依赖 Linux 和任务选择环境：Windows 初次运行有平台失败；板端设置测试用 `EC_TASK=task1`、对应测试配置及磁铁 line 12 后，7 项均通过，未访问真实运动接口。初版常驻进程退出时发现 stdin 缓冲锁错误，已改为原始控制流读取和 EOF 正常退出，复测进程退出码为 0。

发布目录 `/opt/ec26e/releases/camera-start-20261023`，清单见 [manifest](camera-start-20261023/manifest.json)。旧软件完整保存在该目录 `production-before/`。安装后比对全部机器配置哈希一致，旋转 25 RPM、并行回程、Z 收尾下降、运动权限、菜单自启与鼠标光标保留。没有修改 PL 或 M0 固件。

基准入口 `deploy/axu5ev/benchmark_camera_start.py`；运行前关闭菜单服务以独占测试设备，测试结束恢复菜单。菜单验证脚本 `validate_menu_runtime.py` 强制只求解和禁用运动，可以在原菜单空闲时临时打开测试窗口。

菜单空闲时，使用板端环境执行软件回滚：

```sh
python /opt/ec26e/releases/camera-start-20261023/install_robust_release.py --release /opt/ec26e/releases/camera-start-20261023 --apply --rollback
```
