# 七巧板图案扩充与单帧视觉鲁棒性（2026-10-29）

后续逐片对照参考图发现七个布局与参考图不一致。本文记录的是当时的几何、视觉和规划验证，不能证明图案外观与参考相符；最新模板、参考图回归和更正记录以 [名称与拼法审查](../tangram-layout-audit-20261029/README.md) 为准。

图案从六个增加到十二个，新增狗、天鹅、鸟、企鹅、海豹和椅子，按用户提供参考图重建标准七片几何。见 [全部图案](patterns.svg)。整套仍为 100×100 mm 标准七巧板；所有模板面积为 10000 mm²，使用两大、两小、中三角、方形和平行四边形，无内部面积重叠。模板及实测轮廓仅旋转和平移，单片不翻面、不缩放。

## 算法调整

- 新增 `vision/tangram.py`：归一化蓝色度、占多数的蓝底颜色簇、局部背景补偿和白片核心支持，增强低亮度、阴影、冷暖色偏、浅反光及噪声条件下的分割。预览、旧 GUI 控制器和单张采图任务使用同一分割器。
- 轮廓在同一张图上尝试多个分边尺度，仍须通过凸性、实测直边、边长、交并比和原有最大残差检查，不使用凸包或理想模板补全轮廓。缺片、接触、遮挡、额外目标区异物仍拒绝运动。
- 装配间隙直接通过分离轴与工作区约束生成最小平移，避免统一拉开质心使细长图案超限。常规 45° 朝向全部无解后再细化到 15°，兼顾窄工作区和板端 CPU 耗时。
- 每个任务仍只读一张新照片，关闭相机后计算。验证工具新增 `--replay` 复用保存轮廓，不再采图。下拉列表显示八行、可滚动至十二项；字体生成器检查所有名称的中文字形。

## 软件验证

通过 Poetry 执行：

```powershell
# D:/FPGA/puzzle-solver
poetry run pytest tests/test_tangram.py -q
# D:/FPGA/puzzle5ev/sw
poetry run pytest tests/unit/test_tangram.py tests/unit/test_gui_controller.py tests/unit/test_shared_preprocess_config.py tests/unit/vision/test_traditional.py tests/unit/vision/test_adaptive_preprocess.py tests/unit/test_board_menu.py tests/unit/test_axu5ev_menu.py tests/unit/test_menu_model.py tests/unit/tasks/test_task1_actuation.py tests/integration/tasks/test_task2_application.py -q
```

求解器 **63 项通过**；应用相关回归 **176 项、29 个子测试通过**。两个项目均成功 `poetry build`。脚本编译、字体名称覆盖和 Git 空白检查通过。

视觉测试包含一张 2026-10-27 保存实拍和一张合成白片蓝底图，各自覆盖原图、30% 亮度、平滑阴影、暖偏色、冷偏色、褪色、固定种子噪声、浅反光及模糊/JPEG 压缩，共十八组。每组识别全部七片并通过标准片型检查；相对各自原图的质心变化小于 0.8 mm、顶点变化小于 1 mm。另覆盖青蓝/深蓝底色和六种失败场景，失败时相机关闭且运动执行器不被构造。实拍及其扰动不是十八次现场采集，轮廓相对变化也不代表绝对标定或机械精度。

额外运行旧 `test_task1_pipeline.py` 的两个用例仍失败：一个要求 3 ms 超时后继续使用两片局部结果，一个要求历史固定模板宽度 100 mm（实际约 111.831 mm）。从改动前 `ce32261` 导出应用源码与测试后复现相同失败；未修改它们或运动策略来迎合旧断言。因此不宣称完整未筛选测试套件全部通过。

## 板端验证

A7A 与 FPGA 已在线，通过已核验的 SSH 跳板部署。正式发布为 `/opt/ec26e/releases/tangram-robust-20261029-r3`。两个 wheel 与字体经哈希校验；安装使用 `--preserve-profile`，保留全部生产配置，包括机械标定和 `motion_verified=true` 手动试机权限。[部署记录](deployed.json) 与 [字体覆盖清单](font-manifest.json) 已保存。Xorg/菜单已重启，无硬件工程、PL、设备树或启动镜像修改。

首次板端验证只拍一张照片供十二个图案共用，全部得到完整七步方案并通过机械软件限位，见 [新照片规划](live-results.json)。菜单测试使用额外独立任务的一张新照片，选择新增椅子，得到 ready 状态且 `motion_executed=false`，见 [菜单结果](gui-result.json)、[首屏选项](gui-picker-first.png)、[滚动末屏](gui-picker-last.png) 和 [椅子预览](gui-prediction.png)。所有中文字形和列表边界已目视核验。

最终版本复用同一份保存观测再次计算十二图案，全部通过，未再拍照或运动，见 [最终重算记录](replay-results.json)。以下为 AXU5EV PS CPU 上的几何规划及机械软件限位检查时间，不含相机启动、图像识别或界面显示：

| 图案 | 秒 |
| --- | ---: |
| 正方形 | 5.775 |
| 猫 | 2.514 |
| 兔 | 2.520 |
| 鱼 | 5.646 |
| 船 | 2.869 |
| 房子 | 5.782 |
| 狗 | 2.525 |
| 天鹅 | 3.608 |
| 鸟 | 2.528 |
| 企鹅 | 2.523 |
| 海豹 | 2.576 |
| 椅子 | 3.686 |

本次没有发送运动指令。保留原机械流程和运动前角度检查，实物拼装及 2 mm / 3° 精度仍由现场测试确认。

## 恢复

每次发布目录均有不可覆盖的 `production-before`。回退最近一次软件更新可用 r3 安装器 `--apply --rollback`；恢复本次扩充之前的六图案版本可用首次更新前快照：

```sh
. /opt/ec26e/env.sh
python /opt/ec26e/releases/tangram-robust-20261029/install_tangram_release.py \
  --release /opt/ec26e/releases/tangram-robust-20261029 --apply --rollback
```

恢复前须确认菜单没有运行任务或预览；安装器持有公共资源锁并检查空闲状态。
