# M0 运动桥接接入

实际拓扑为 AXU5EV-P USB Host → 地猛星板载 CH340E → MSPM0G3507。
用户确认已烧录 `../mspm0g3507-puzzle-bridge` 固件，并接好下游设备。

| 用途 | M0 接口 | 速率/电平 |
| --- | --- | --- |
| USB 上游 | UART0 PA10 TX / PA11 RX，板内连接 CH340E | 921600 8N1 |
| XYZ | UART1 PA8 TX；PA9 不使用，接收关闭 | 115200 8N1，仅发送 |
| 旋转 | UART2 PA21 TX / PA22 RX | 115200 8N1 |
| 电磁铁驱动输入 | PB8 | 高有效 |

Linux 别名 `/dev/ec26e-m0-bridge` 绑定 VID/PID `1a86:7523` 和物理端口
`platform-xhci-hcd.0.auto-usb-0:1.4:1.0`。换 USB 插座后应重新核对该路径。
不把同一个 tty 当成两个独立串口；两通道由独立 `puzzle-m0-bridge` 库分流。

## 配置与生命周期

`MOTION_BACKEND=m0`、`M0_BRIDGE_PORT=/dev/ec26e-m0-bridge` 选择桥接。
默认后端仍是 `direct`，兼容独立串口；AXU5EV 环境模板选择 M0。
M0 后端不需要 Linux 磁铁 GPIO。下游 XYZ 波特率固定 115200。

构造执行器不打开设备。完整 MovePlan 校验通过后才打开共享会话；
初始 STATUS 必须满足 version=1、armed=false、magnet=false、fault=0。
首次实际输出才 ARM，客户端维护心跳；会话错误后禁止继续发送，不自动重发 DATA。
通道关闭不抢先断开共享串口；最后由磁铁资源关闭整个会话并 DISARM。
ACK 只表示桥接接受报文，不证明电机已完成或已停止。

当前菜单始终 `plan_only=true`、`motion_enabled=false`、CPU 后端。
本轮硬件仅发送 STATUS，没有 ARM、DATA、MAGNET 或运动命令。
物理接线、标定、完整有效解和机械验收缺一不可。

## Linux 5.4 CH341 修正

厂家驱动旧分频算法把 921600 算成约 857142.9，接收严重乱码。
`fix_ch341_baud.py` 参考 Linux v5.10 `ch341_get_divisor`，仅修正 921600 档：
48 MHz / (4 × 13) = 923076.9（约 +0.16%），寄存器值 `0xf307`，随后保留低延迟标志。
其他波特率维持厂家行为。`build_modules.sh` 可重复应用并重建模块。

每次候选系统启动后，加载匹配的新包并设置别名：

```sh
sh /opt/ec26e/install/setup_m0.sh /opt/ec26e/staging/20261020/axu5ev-reset-bundle
. /opt/ec26e/env.sh
python -m puzzle_bridge.commands.app status --port /dev/ec26e-m0-bridge
```

脚本校验包哈希，只加载驱动和安装 udev 规则，不打开串口、不 ARM。
已有驱动时不会在线替换，避免打断其他串口使用者；本轮手动替换在串口关闭后完成。
未设置运动开机自启。

## 当前实测边界

**最新版本：XYZ 仅发送。** 用户确认 XYZ 电机 TX 不需要接入。M0 SysConfig
已关闭 UART1 接收及 RX 中断，PA9 不再配置为 UART 输入；旋转轴 PA22 接收保留。
固件 `../mspm0g3507-puzzle-bridge/build/xyz-tx-only/firmware.out` 已烧录，
5072 字节读回与 BIN SHA-256 一致：
`977775d87eacff4e578a0f4f81e439d3984fbdcc1d82620f35bdf5f8a817873e`。
约 20 秒连续 100 次 STATUS 全部 fault=0、armed=false、magnet=false、
malformed=0；故障在关闭无用 XYZ 接收后消失。无需降低波特率。
本轮只查询状态，电机发送和旋转轴实物反馈仍未验收，保持仅规划与运动关闭。
当前证据见 [100 次状态记录](validation/axu5ev-migration-20261020/m0-bridge/xyz-tx-only-status.json)；
正式可分发固件在桥接仓库 `dist/firmware.hex`，BIN 与实物读回一致。
应用的只读初始化预检也已通过；下面保留历史排查过程。

修正前 STATUS 超时；修正后能解析大量 CRC 正确的 M0 RX 帧，但通道 1
持续产生 `00/80/e0/f0/f8/fe/ff` 等异常字节，STATUS ACK 多次被截断。
固件 `src/main.c` 的任一 UART 错误会进入全局 `io_fault` 并清空上游发送队列，
与截断现象相符；当时按 XYZ 双向接线排查，用户随后明确 XYZ 不需要回传，
接下来需要用完整 STATUS 与下游 UART 数据继续定位，不能仅凭异常字节认定接错线。
以下为先前版本记录；当前状态以上方 TX-only 验证为准。

桥接仓库已修复该错误处理：按 UART 记录故障，下游故障不再清空上游队列；
保留取消使能、磁铁关闭和丢弃待发下游数据。新增直接测试真实 `main.c` 的
假寄存器/IRQ 测试通过，独立固件在 `../mspm0g3507-puzzle-bridge/build/status-fix/`。
2026-10-20 再次连接后 J-Link 恢复。先读回 5112 字节，与原固件
SHA-256 `4394f0447d151de2fbdceed201248c0e0c4eb71fca9c546a5487d08689c64d57`
一致，再实际执行：

```powershell
./tools/flash-jlink.ps1 -Image build/status-fix/firmware.out -SpeedKHz 100
```

已烧录并启动修复版，读回 5136 字节与新 BIN 完全一致：
`afa87d89c39abf3cdb63e49d4855e717adbcb8de30cec137e893d191c8764926`。
连续 5 次 STATUS 均收到完整回复：`armed=false, magnet=false, fault=2,
version=1, malformed=0`。ACK 截断已修复；fault=2 表示 UART 错误/溢出，
尚未定位具体电气或下游配置原因。没有发送 ARM、DATA 或 MAGNET。
应用会拒绝在这个故障状态执行运动。证据为 `status-after-flash.json`
及 `validation.json`。

证据在 `validation/axu5ev-migration-20261020/m0-bridge/`。
桥接库 12 项测试通过；应用新增测试覆盖分片分流、共享会话、单次 ARM、
初态拒绝、故障不重发、心跳失败、无效计划零串口访问。
应用相关回归共 75 项及 27 子测试通过；新 wheel 已安装，板端 pip check 通过，
菜单继续仅规划、CPU、运动关闭。
两个继承的 Task1/Task2 配置测试仍期待 20 mm，仓库配置已是 8/12 mm；
此差异在旧工程也存在，本轮未修改识别参数。
