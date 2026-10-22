# AXU5EV-P Linux 候选平台与后续上板

> 2026-10-21 更新：管理链路已改为 A7A `192.168.1.26` → FPGA `10.42.0.47`；已部署可恢复的工程启动选择和菜单自启，见 [远程访问与菜单自启](remote-autostart.md)。下文“一次性启动、原 boot.scr 不变、无自启”描述的是 2026-10-20 的历史联调过程。

候选平台已通过串口选择并启动，新 PL、图像 DMA、HDMI framebuffer 和几何核 UIO 已加载。摄像头和 HDMI 已接入；电机与磁铁改由一块已烧录的 M0 桥接接入，见 [桥接状态](m0-bridge.md)。当前应用仍使用 CPU，仅规划，不执行运动。逐项上板结果见 [外设与 PL 联调记录](validation/axu5ev-migration-20261020/peripheral-bringup.md)。

## 交付内容

- `artifacts/axu5ev/`：同一 Vivado 构建的 bit、HWH、XSA、时序/资源/DRC 报告。
- `artifacts/axu5ev-bundle/`：经过 Bootgen 转换的 FPGA manager 二进制、上述硬件文件、设备树 overlay、驱动和加载器；`manifest.json` 记录 SHA-256。打包程序逐字节核对 XSA 内 bit 和顶层 HWH。
- `artifacts/axu5ev-boot/image-puzzle5ev.ub`：由备份的出厂 FIT 派生，仅修改其中的设备树，保留内核数据、原有启动参数和 PS 设备。停用旧 `amba_pl@0` 及 MIPI 相机 I2C0，增加加载器识别标记。FIT 原始哈希和修改后哈希均已校验。
- `artifacts/software/`：应用、独立求解器和 M0 桥接库 wheel、源码身份和依赖清单。

候选包暂存于板端 `/opt/ec26e/staging/20261020/`。活动启动分区上的 `BOOT.BIN`、`image.ub`、`boot.scr` 保持原样。原始备份在 `build/axu5ev/backup-20261020/`。

当前已验证的 HDMI 复位修复包位于 `/opt/ec26e/staging/20261020/axu5ev-reset-bundle`；后续加载使用此目录，不使用旧 `axu5ev-bundle`。用户已确认 1280×720 彩条颜色与中文菜单正常。当前管理链路为电脑直连 COM10，115200 8N1。

当前实际运行候选 v1 `image-puzzle5ev.ub`，整板断电后的启动已恢复 USB3 摄像头，并验证实时菜单预览。热重启丢失 USB3 Hub 的问题尚未解决；追加 PHY 的 `image-puzzle5ev-usbphy.ub` 未改善该问题，仅保留作诊断。需要可靠恢复摄像头时，先安全关机，再整板断电约 10 秒后上电并选择 v1。断电也可能让 COM10 重新枚举，终端需及时重连捕获 U-Boot 倒计时。没有修改永久启动选择或配置菜单自启。

## 已验证范围

设备树 overlay 已由 Linux 5.4 源码中的 libfdt 离线合并，PS 时钟和中断引用可以解析；这不代表实际启动成功。候选驱动以 `xilinx-v2020.1` 源码和运行内核配置编译，vermagic 匹配 `5.4.0-xilinx-v2020.1 SMP mod_unload aarch64`。

编译器是隔离环境中的 GCC 11.4.0，运行内核使用 GCC 9.2.0。内核关闭 MODVERSIONS，厂家未提供 Module.symvers，构建因此报告缺失符号版本表。两个自定义模块现已成功加载，真实图像 DMA 已与 CPU 输出比对。运行内核关闭 KALLSYMS_ALL，`jiffies`、`kmalloc_caches` 等数据符号不显示在 `/proc/kallsyms`，不能仅凭该列表认定缺失。

内核源码下载及 SHA-256 固定在 `deploy/axu5ev/build_modules.sh`，编译依赖位于 `/opt/ec26e/build-tools`，没有替换系统编译工具或应用 Python。脚本只编译，另行生成 CH341、FTDI、PL2303 模块；CP210X 和 UVC 已在出厂内核启用。

## 接线后的切换顺序

串口恢复入口采用 Radxa 中转，连接参数和当前验证状态见 [串口控制台](serial-console.md)。

1. 确认原始启动备份和恢复入口可用，停用并停止厂家 `alinx_ui1` 及旧 Xorg 启动项。不要在厂家应用仍可能访问旧 PL 地址时切换硬件。
2. 使用候选 `image-puzzle5ev.ub` 做一次可恢复的启动测试，保留原始 `BOOT.BIN` 和 `image.ub`。没有修改 boot.scr 或持久启动选择。已实测 U-Boot 为 2020.01，SD 卡为 `mmc 1:1`；候选文件单独放在启动分区。中断倒计时后使用：

   ```text
   fatload mmc 1:1 0x18000000 image-puzzle5ev.ub
   setenv bootargs earlycon console=ttyPS0,115200 clk_ignore_unused root=/dev/mmcblk1p2 rw rootwait x11=false
   bootm 0x18000000
   ```

   不执行 `saveenv`，下一次普通启动仍使用出厂镜像。`/home/root/board.sh` 已备份并增加候选 DT 标记检查：候选平台退出，原平台保留原行为。`x11=false` 避免启动 PS DisplayPort 的 Xorg。
3. 新系统启动后先验证 SSH、PS 网口、USB、`chosen/ec26e,pl-base` 和旧 PL 驱动未绑定，再运行加载器的预检：

   ```sh
   . /opt/ec26e/env.sh
   bundle=/opt/ec26e/staging/20261020/axu5ev-reset-bundle
   python "$bundle/load_hardware.py" --bundle "$bundle"
   ```

4. 预检通过后用同一命令增加 `--apply`，加载一次 FPGA、overlay 和驱动，并初始化 ADV7511。失败后保留故障状态，重启再处理；不在线卸载带 DMA 缓冲区的设计。加载器在出厂设备树下会拒绝执行。
5. HDMI framebuffer 和 ADV7511 就绪后，将包内 `xorg-hdmi.conf` 放到 `/opt/ec26e/install/`，停止原 Xorg，再手动运行 `start_hdmi_x.sh`；另一个 SSH 会话运行 `/opt/ec26e/install/start_menu.sh`。先验收颜色、同步、刷新和菜单操作，再考虑自启。
6. 使用 `probe_board.py` 检查外设。已观察到的摄像头规则为 `deploy/axu5ev/99-ec26e-camera.rules`，跟随 USB 插座，不依赖 video 编号。两个电机模块接好后再运行 `bind_usb.py --camera /dev/videoN --xyz /dev/ttyUSBX --rotation /dev/ttyUSBY --output /tmp/99-ec26e.rules` 并核对角色；程序不打开串口发送命令。
7. 先完成 CPU 摄像头识别与标定，再验证 PL 与 CPU 的图像和几何结果一致。PL 配置使用 `EC_PL_ACCESS=linux` 和 `EC_PL_BITSTREAM` 指向该包的 `puzzle5ev.bit`。菜单及 machine.env 的后端选择应一致。默认配置继续保留 CPU，不能仅因设备节点存在就启用 PL。

HDMI 目标是 1280×720，MMCM 实际像素时钟约 74.253 MHz。外部时序按 ADV7511 setup 1 ns / hold 0.7 ns 加 0.5 ns 板级裕量约束，仍需显示器与必要的电气实测确认。

Linux 5.4 的动态 OF 通知不会递归创建新 simple-bus 下的设备，因此 overlay 将外围节点直接挂在现有 `/amba`。出厂 `uio_pdrv_genirq` 可能早已为 AXI PMON 加载；加载器只对核验过的两个计算核使用 `driver_override` 绑定，不卸载共享模块。两核采用轮询，没有中断，内核出现 `IRQ index 0 not found` 后仍可正确绑定。

ADV7511 寄存器读回使用 `I2C_RDWR` 的重复起始事务。系统内建 `i2c-dev` 时通过 sysfs 判断可用性，避免错误要求加载模块。UIO 批量访问逐字进行，避免 NumPy SIMD 拷贝在 ARM Device Memory 上触发 SIGBUS。Xorg 的 fbdev 探测要求真实 `/dev/fbN` 路径，启动脚本根据 framebuffer 名称生成运行时配置。

本阶段不进行 XYZ/旋转运动，不接电磁铁，不使能旧按键 GPIO。真实机械接线、标定和完整有效解验证完成前保持仅规划模式。
