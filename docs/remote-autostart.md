# A7A 远程访问与菜单自启

## 网络

2026-10-21 管理链路改为：开发电脑 → Wi-Fi → Radxa Cubie A7A → 网线 → AXU5EV-P。

```powershell
ssh -J radxa@192.168.1.26 root@10.42.0.47
```

A7A 重装 Debian 后于 2026-10-22 恢复：`wlan0=192.168.1.26/24`，`end0=10.42.0.1/24`。NetworkManager 连接 `ec26e-fpga` 使用 IPv4 shared、never-default，不替换 Wi-Fi 默认路由。`/etc/NetworkManager/dnsmasq-shared.d/ec26e-fpga.conf` 将 FPGA MAC `00:0a:35:00:22:01` 保留为 `10.42.0.47`；配置已重新激活并验证双跳 SSH/SFTP。FPGA 按实际 PS 网口控制器启用 DHCP，不依赖 eth0/eth1 名称。调试串口 CP2102N 在 A7A 上使用系统自带驱动。

电脑没有直达 `10.42.0.0/24` 的路由，使用 `-J` 跳板；也可先登录 A7A，再执行 `ssh root@10.42.0.47`。SSH 和 SFTP 已实际通过双跳验证。密码只交互输入，不写入工程。

## 开机顺序

启动分区 `/media/sd-mmcblk1p1` 的 `boot.scr` 新增受 `puzzle5ev-autostart.enable` 控制的工程入口：加载已验证的 v1 `image-puzzle5ev.ub`，传入原根分区/串口参数和 `x11=false`。加载失败返回时执行原厂家脚本。原 `BOOT.BIN`、`image.ub` 和 U-Boot 持久环境不变。

候选设备树下，已有 `/home/root/board.sh` 保护会跳过厂家 PL 应用。`/etc/rc5.d/S99ec26e-menu` 启动 `/opt/ec26e/install/autostart.py`：

1. 检查工程设备树标记及进程锁，按 `ff0e0000.ethernet` 控制器身份启用 PS 网口，再检查菜单仅规划配置。厂家系统为 eth1、工程系统为 eth0，不能固定依赖编号。
2. 核验并加载 `/opt/ec26e/staging/20261020/axu5ev-reset-bundle`；已有失败状态不自动重载 PL。
3. 准备 M0 USB 驱动和设备别名，不打开串口、不 ARM；外设缺失记为警告，不阻止菜单。
4. 启动 HDMI Xorg，等显示服务就绪后启动中文菜单。

菜单默认 CPU、仅规划、实体按键关闭、运动关闭。不自动启动预览、求解或搬运。状态为 `/run/ec26e/autostart-status.json` 和 `/run/ec26e/menu-status.json`，日志位于 `/opt/ec26e/logs/autostart/`。服务启动、停止、状态命令：

```sh
/etc/init.d/ec26e-menu start
/etc/init.d/ec26e-menu stop
/etc/init.d/ec26e-menu status
```

## 恢复

关机取出 SD 卡后，在电脑可见的 FAT 启动分区将 `puzzle5ev-autostart.enable` 改名，即恢复厂家镜像选择；新服务在厂家设备树下拒绝加载 PL。也可将原 `boot.factory-20261021.scr` 复制回 `boot.scr`。原脚本另备份在根分区 `/opt/ec26e/backups/autostart-20261021/boot.scr` 和主机 `build/axu5ev/boot-original-20261021.scr`。

恢复开关仅覆盖启动镜像选择，不等同于启动后故障的自动回滚。热重启曾导致 USB3 摄像头不枚举，验证菜单和摄像头应正常关机后断电约 10 秒再上电。部署记录见 [本轮验证](validation/axu5ev-20261021/README.md)。

已实测冷启动自动进入工程系统并启动 HDMI/菜单，用户确认中文菜单与四片预览正常。首次冷启动暴露 eth1→eth0 的联网漏项，已修正并通过“停用网口后启动服务自动获取地址、恢复 SSH 和菜单”验证；网口修正后尚未再做第二次整板冷启动。
