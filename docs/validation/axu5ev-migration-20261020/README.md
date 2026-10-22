# AXU5EV-P 独立工程验证记录（2026-10-20）

本页记录外设接入前的构建阶段。接入摄像头和 HDMI 后的实际启动、PL 验证、修复和未完成项见 [外设联调记录](peripheral-bringup.md)；下文“未加载／未测试”描述的是初始阶段。

正式目录 `D:/FPGA/puzzle5ev`，入口 `puzzle5ev.xpr`；原 `puzzle7020` 保留。器件 `xczu5ev-sfvc784-2-i`，AMD 2026.1。

| 验证层次 | 结果 | 实际范围 |
| --- | --- | --- |
| 工程引用 | 通过 | 886 个工程文件存在；无旧工作区来源依赖；顶层/器件正确；综合/实现无需刷新 |
| HLS | 通过 | preprocess、rigid、clearance 的 C 仿真、综合、IP 导出及寄存器 ABI 检查；本轮未运行 C/RTL cosim |
| HDMI RTL | 通过 | 原 scanout 与 AXU 适配层分别仿真；RGB 顺序、ODDRE1 转发时钟极性、同步/消隐、断流恢复 |
| Vivado 实现 | 通过 | 新目录重新综合、布局布线、生成 bitstream；建立/保持/脉宽满足已核验约束 |
| 主机软件 | 通过 | 73 项测试、3 个子测试；菜单/仅规划、几何后端、Linux 传输异常处理、候选 FIT 保留/拒绝策略 |
| 软件交付 | 通过 | 独立求解器和应用 wheel 构建；板端安装校验、pip check、CPU 数值/图像/FFmpeg/求解及隐藏菜单 |
| Linux 驱动 | 编译通过 | 两个自定义模块和三种 USB 串口模块；vermagic 匹配；尚未加载 |
| 设备树 | 离线通过 | 候选 FIT 哈希验证、原内核逐字节保留；Linux libfdt 合并 overlay 成功；活动设备树未改变 |
| 加载保护 | 通过 | 暂存包哈希通过；当前出厂 DT 被预检拒绝；未写 FPGA manager |
| 真实外设 | 未测试 | 摄像头、USB 串口、HDMI 显示器未接；未测试 PL DMA、机械运动或电磁铁 |

## 时序与资源

- WNS **3.358 ns**，WHS **0.010 ns**，WPWS **2.000 ns**；对应失败端点均为 0。
- 无时钟、无效派生时钟、内部未约束端点、组合环均为 0。
- HDMI 的 24 位 RGB、DE、HS、VS 共 27 个输出端点受到外部时序约束；外部引脚的例外仅为异步开漏 I2C 和定义时钟的输出脚。
- LUT **26,427 / 117,120（22.56%）**；寄存器 **27,026（11.54%）**；BRAM tile **18（12.50%）**；DSP **62（4.97%）**。
- 实现阶段 0 个 Critical Warning / Error。DRC 保留 HLS DSP 流水线、BRAM 碰撞建议、AXI downsizer 的 LUT 方程及无负载网络等警告；Methodology 保留 AXI downsizer 生成 FIFO 的 3 条 LUT 异步复位警告、时钟引脚查询效率警告和 MMCM 位置建议。未降低检查严重级别。
- 首次构建发现 ODDRE1 综合后使用 OSERDESE3 `CLK` 引脚，原 `C` 路径使输出约束无效；已在新目录修正并重新完整实现。首次产物不作为本次交付。

## 板端验证与保留状态

实际系统为 AArch64 / PetaLinux 2020.1 / Linux 5.4.0-xilinx-v2020.1 / glibc 2.30。独立 Python 3.11.17 位于 `/opt/ec26e/python3.11`，应用 venv 位于 `/opt/ec26e/venv-python311`。

固定四片既有轮廓的板端 CPU 求解约 **2.91 s**（公共 API 调用范围，包含求解，不含实时采集与运动），得到四步规划。Tk 窗口在现有 Xorg 中隐藏创建并关闭；这不是 HDMI 输出验收。

活动启动文件 SHA-256 与开始时的备份一致：

```text
BOOT.BIN  62ad38078ff151cb6bbfae95df0e966aa848541e0afabcee02217d029a963412
image.ub  8daae218ef087c9ff22cac0336f2514f93a65c42553f19d1987a43838a5c769a
boot.scr  fe59d89502276f981752be4eb277b4979d8440a4ef46a28b52a23e1b84f03745
```

未重启、未替换启动镜像、未加载新 PL、未启用自启动或运动。候选包仅放在 `/opt/ec26e/staging/20261020/`。后续按 [Linux 上板说明](../../linux-bringup.md) 和 [菜单验收](../../board-menu.md) 进行。

## 证据位置

本目录保留初始板卡清单、备份校验、模块编译验证与最终报告副本。完整构建日志在 `build/axu5ev/`，实现报告及配套硬件在 `artifacts/axu5ev/`，带 SHA-256 的部署清单在 `artifacts/axu5ev-bundle/manifest.json`。`copied-source.json` 是分离目录时的来源快照，不代表后续修改后的最终文件哈希；交付清单记录当前硬件相关来源文件。

引脚依据为 AXU5EV-P 资料中的共享 AXU4EV-P 底板原理图、ACU5EV 核心板原理图及厂家 `hdmi_out.xdc`，而非存在冲突的手册引脚表。ADV7511 寄存器表保留厂家版权，见 `docs/vendor/lut_adv7511.v`。

2026-10-20 用户进一步确认：`D:/FPGA/data/AXU5EV-P/00_hardware.rar` 中 `00_hardware/01_原理图结构图等硬件资料/底板` 的 4E 与 5E 使用同一块扩展板。这与本次采用共享底板原理图的依据一致；核心板仍按 ACU5EV、芯片按 XCZU5EV-2SFVC784I 核对。此补充不改变已核对的约束。
