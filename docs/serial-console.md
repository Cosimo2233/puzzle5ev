# FPGA 串口控制台

> 2026-10-21 当前使用 A7A `radxa@192.168.1.26` 的网口中转 SSH，见 [远程访问](remote-autostart.md)。本轮电脑未枚举调试串口；下文 COM10 和旧 Radxa 地址为历史恢复方式，应重新接线并检查设备标识后使用。

当前（2026-10-20 晚）USB 调试串口已从 Radxa 改接开发电脑，Windows 识别为 **COM10 / Silicon Labs CP210x USB to UART Bridge**。序列号仍为 `3604098b8d60ec11bc32385f25bfaa52`，已直接进入板端 root 控制台。常规终端参数仍为 **115200 8N1，无流控**，Windows 驱动已可用，无需重复安装。

本次直传在 Linux 控制台临时使用 460800 波特率，采用逐包 CRC、ACK/重试及最终 SHA-256 校验。传输期间不要另开终端占用 COM10；完成后恢复 115200。COM 编号可能随电脑插座变化，应按设备唯一标识确认。直连调试 UART 不提供网络，也不是电机串口。

## Radxa 中转（备用）

连接链路：开发电脑 → SSH → `radxa@192.168.1.139` → USB 串口 → AXU5EV-P 调试控制台。

2026-10-20 已通过 SSH 检查 Radxa Cubie A7S，系统为 AArch64 Linux 5.15.147-16-a733。Radxa 已识别 Silicon Labs CP2102N（USB VID:PID `10c4:ea60`），设备为 `/dev/ttyUSB0`；`radxa` 用户属于 `dialout` 组，可直接访问，无需为该串口额外安装驱动或使用 sudo。

稳定设备路径如下，不依赖 ttyUSB 编号：

```text
/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_3604098b8d60ec11bc32385f25bfaa52-if00-port0
```

串口参数沿用 FPGA 系统控制台设置：**115200、8 数据位、无校验、1 停止位、无流控**。

在 Windows 的工程根目录运行：

```powershell
./scripts/Open-BoardConsole.ps1
```

也可先 `ssh radxa@192.168.1.139`，再运行：

```sh
picocom --baud 115200 --flow n --parity n --databits 8 --stopbits 1 --noreset \
  /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_3604098b8d60ec11bc32385f25bfaa52-if00-port0
```

退出终端：先按 **Ctrl+A**，再按 **Ctrl+X**。一次只让一个程序占用串口。密码通过交互输入，不保存在脚本或文档中。

2026-10-20 用户纠正接线后，串口设备唯一标识由 `20cf...aa52` 变为 `3604098b8d60ec11bc32385f25bfaa52`。已收到 `PetaLinux 2020.1 petalinux /dev/ttyPS0` 登录提示，并成功进入 `root@petalinux:~#`。此后已通过同一通道完成多次正常重启、中断 U-Boot 倒计时、加载候选 FIT、登录新 Linux。Radxa 的 SSH、串口枚举/权限及 FPGA 控制台双向通信均已验证。此前无输出对应错误接线，不是缺少驱动。

该连接用于管理和查看启动日志。它不自动提供 FPGA 的网络接入，也不替代摄像头和两个电机 USB 串口的接线。

大文件传输不能满速持续灌入 UART：本轮曾观察到 `ttyPS0 input overrun`，随后取消该次传输。限速传输并核对 SHA-256 后才安装；没有将不完整的文件作为部署产物。
