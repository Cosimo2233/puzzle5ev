# Puzzle5EV：AXU5EV-P 拼图系统

面向 **AXU5EV-P / XCZU5EV-2SFVC784I** 的独立工程。正式 Vivado 入口是根目录 `puzzle5ev.xpr`，工具版本为 **AMD Vivado / Vitis 2026.1**。

本工程由旧板项目迁移，源码和生成依赖均保存在本目录；几何求解器继续使用独立的同级 `../puzzle-solver`。不要使用旧板的 bitstream、设备树或 ARMv7 软件包。

固定四片与随机 2～4 片的连续稳定审查、PL 数据路径及性能复现见 [PL 提速说明](docs/pl-speed.md)。

菜单预览、预测落点、鼠标适配和扑克牌求解改进见 [2026-10-22 验证记录](docs/validation/robust-20261022.md)；各模块职责和测试入口见 [代码结构](docs/architecture.md)。

预览进程现可重复使用，减少每次切换约 3.2 秒的依赖加载；板端连续 5 次预览首帧约 0.58～0.77 秒。任务开始即显示本次采图，不再等稳定审查通过才出画面，详见 [摄像头启动优化](docs/validation/camera-start-20261023.md)。

相机预热、轮廓显示稳定与固定模式颜色修正见 [采集与速度验证](docs/validation/capture-20261022.md)。固定四片连续三次只求解约 4.8～4.9 秒。当前 X/Y 为 127 档，旋转为 50 RPM，Z 为 80 档，见 [速度配置记录](docs/validation/axes-speed-20261023.md)。末片释放并抬升 Z 后，XY 回起点与旋转回零并行，两路完成后才下降 Z；第 3 片预测轮廓改为珊瑚红。软件与部署验证见 [预测配色与并行回程](docs/validation/return-speed-20261023.md)，实际提速搬运由用户手动验收。

固定四片针对偏蓝/偏青两种实拍颜色状态新增自动参数选择，解决无轮廓和持续采图等待；配置隔离、CPU/PL 一致性及三次菜单只求解结果见 [2026-10-23 识别稳定性修正](docs/validation/blue-20261023.md)。

随机留缝候选增加独立凹多边形批量距离核，原固定凸片核保持兼容；软件、HLS、完整构建与上板阶段分别记录在 [候选 PL 加速验证](docs/validation/candidate-20261022.md)，协议见 [接口说明](docs/pl-candidate-scan.md)。

当前板端已部署 PL 预处理、固定/随机批量配准及固定间隙粗筛，菜单保留开机自启和原运动权限。固定录制回放、随机每种片数 30 次合成回放均通过；现场重新摆放后的计时与随机实物验收待补，见 [2026-10-21 验证记录](docs/validation/pl-speed-20261021.md)。

## 目录与构建

| 路径 | 内容 |
| --- | --- |
| `puzzle5ev.xpr` | 正式工程入口 |
| `puzzle5ev.srcs/sources_1/bd/system/system.bd` | 持久维护的 Block Design |
| `src/hls/`、`src/rtl/` | 预处理、刚体拟合、间隙扫描和 HDMI 输出源码 |
| `constraints/axu5ev/hdmi.xdc` | 与底板/核心板原理图核对的引脚和时序约束 |
| `scripts/` | 硬件构建、工程校验及软件打包 |
| `sw/`、`deploy/axu5ev/` | Linux 应用、配置和驱动源码 |
| `build/axu5ev/`、`artifacts/axu5ev/` | 生成依赖、构建记录与交付产物 |

```powershell
./Build.ps1 -Stage all             # HLS C 仿真/综合/导出，Vivado 综合/实现/导出
./Build.ps1 -Stage hls -Cosim      # 额外执行 C/RTL 协同仿真
./Build.ps1 -Stage project         # 打开保存的工程、验证 BD、生成依赖
./Build.ps1 -Stage compile         # 保留保存的 BD，重新综合和实现
./Open-Project.ps1
./scripts/Build-Software.ps1       # 分别打包应用、独立求解器和 M0 桥接库
```

可通过 `-AmdRoot` 指定 AMD 安装根目录。首次从纯源码恢复时先执行 `-Stage hls`，再执行 `-Stage project`；不能删除 HLS IP 后直接打开依赖它的工程。

软件使用 Python 3.10–3.12 与 Poetry：

```powershell
$env:POETRY_VIRTUALENVS_IN_PROJECT='true'
poetry -C sw install
poetry -C sw run pytest tests/unit/test_axu5ev_menu.py -q
```

## 板卡接口与当前边界

四个 Type-A USB 插座经 GL3523 Hub 接 PS USB Host。摄像头采集进入 PS/DDR，PL 通过 DMA 处理像素；插座位置本身不会让图像预处理更快。示例配置默认 CPU；匹配的驱动、设备树和启动加载流程已完成实测，PL 提速版本的验证范围见 [PL 提速说明](docs/pl-speed.md)。

显示链路为 PL VDMA → RGB → ADV7511 → **HDMI OUT**，固定 1280×720。修复 HDMI 辅助复位接线后，用户已确认彩条颜色、文字及中文菜单正常。电机和电磁铁通过地猛星 MSPM0G3507 桥接：USB CH340E 上游 921600，通道 1 为 XYZ、通道 2 为旋转轴（下游均 115200），PB8 控制磁铁。用户已确认沿用原机械标定后固定四片搬运成功，板端菜单保留用户授权的运动权限；本轮自动验证全部只求解。接入状态见 [M0 桥接](docs/m0-bridge.md)。

板端应用位于 `/opt/ec26e`。当前通过 A7A 的 Wi-Fi 和直连网口远程管理：`ssh -J radxa@192.168.1.26 root@10.42.0.47`；A7A 有线接口为 `10.42.0.1/24`，已按 FPGA MAC 保留 DHCP 地址。凭据不写入工程。出厂设备树的 PL 地址与新设计冲突，不能直接在出厂系统加载新 bitstream。

已部署工程启动镜像选择和 SysV 菜单自启，顺序为 PL/HDMI 初始化、Xorg、菜单；开机不执行拼图或运动。原 `BOOT.BIN`、`image.ub` 不变，原 `boot.scr` 已备份，具体恢复方法和冷启动验收状态见 [远程访问与菜单自启](docs/remote-autostart.md)。串口仍可作为 U-Boot 恢复入口，115200 8N1、无流控，详见 [串口控制台](docs/serial-console.md)。

构建、主机软件测试与真实上板结果分别记录在 `docs/validation/`。迁移和后续验收步骤见 [板端菜单与验收](docs/board-menu.md)。

迁移阶段已完成独立工程打开/引用校验、三个 HLS 核的 C 仿真及综合、完整 Vivado 实现和 bit/HWH/XSA 导出；HDMI RTL 仿真通过。本轮软件提速复用已验收的 bit/HWH，没有修改 HLS、重新加载 PL 或重启板卡。

候选启动切换、HDMI 中文菜单、图像 DMA、刚体配准及间隙核已完成实际检查。用户已确认菜单、四片预览和原标定下的搬运正常。USB3 Hub 在热重启后恢复不稳定的问题仍保留，现场重新摆放后的提速计时及随机实物验收另行进行。早期证据见 [外设与 PL 联调记录](docs/validation/axu5ev-migration-20261020/peripheral-bringup.md)，部署操作见 [Linux 上板说明](docs/linux-bringup.md)。
