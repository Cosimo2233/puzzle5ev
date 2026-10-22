# AXU5EV 运行环境与候选硬件部署

面向当前 AXU5EV-P 出厂 PetaLinux 2020.1（AArch64、glibc 2.30、SysV init）。
仓库示例默认 CPU、仅规划。实际板端已保留用户授权的运动权限，用户确认原机械标定下固定四片搬运成功；提速版本的 PS/PL 分工、验证及回滚见 [PL 提速说明](../../docs/pl-speed.md)。HDMI 彩条、文字和中文菜单已由用户确认；摄像头在直接冷启动候选系统后可用，热重启 USB3 恢复仍有问题。M0 接入与 CH341 速率修正见 [M0 桥接](../../docs/m0-bridge.md)。详细切换步骤和验证边界见 [Linux 上板说明](../../docs/linux-bringup.md)。不复用旧板设备树和 systemd 服务。

## 已部署位置

- 独立 CPython 3.11.17：`/opt/ec26e/python3.11`。
- 应用虚拟环境：`/opt/ec26e/venv-python311`。
- 环境入口：`/opt/ec26e/env.sh`；每次 SSH 登录后手动加载。
- 安装脚本、验证脚本：`/opt/ec26e/install/`。
- 离线安装包及 SHA-256 清单：`/opt/ec26e/wheelhouse/`。
- 安装日志及来源清单：`/opt/ec26e/logs/`。
- 固定四片测试输入和运行结果：`/opt/ec26e/validation/`。
- 文泉驿微米黑 UI 字符子集：`/usr/share/fonts/ttf/ec26e/`；许可证：`/usr/share/doc/ec26e-ui-font/copyright`。早期安装的 Noto OTF 不适用于该板 Tk 的核心字体模式。

厂家 `/usr/bin/python3` 保持 3.7.6。2026-10-21 已部署受恢复开关控制的工程 FIT 选择和菜单自启，原 BOOT.BIN/image.ub 保留，原 boot.scr 另有备份；网络、启动顺序和恢复方式见 [远程访问与菜单自启](../../docs/remote-autostart.md)。

## 增量发布入口

`install_robust_release.py` 用于已迁移系统的软件更新，默认只展示计划，`--apply` 执行，`--rollback --apply` 回滚。配套 `verify_robust_release.py` 只读核验安装文件、原机械配置、权限、自启、光标和屏幕防休眠。发布说明见 [2026-10-22 验证记录](../../docs/validation/robust-20261022.md)。

`validate_menu_runtime.py` 使用独立的强制仅规划配置验证预览和预测画面；`validate_robust_release.py` 对同一组真实录制几何做 CPU/PL 对照；`validate_accelerators.py` 持全局任务锁做掩膜和计算核一致性检查。原始日志进入板端 `/opt/ec26e/validation`，不散落源码目录。

## 使用

```sh
. /opt/ec26e/env.sh
python --version
python -m pip check
ec-7020 --help
puzzle-solver --help
ffmpeg -version
```

`ec-7020` 是现有软件包的验证入口名称，在新板上仍保留这个名字。
通用环境脚本选择 CPU 后端；正式菜单使用机器配置中的后端设置。示例默认值、已安装配置与实测范围分别记录，不能仅凭环境变量判断验收完成。

运行既有真实四片轮廓的 CPU 求解，不访问运动设备：

```sh
. /opt/ec26e/env.sh
puzzle-solver solve --input /opt/ec26e/validation/live-input.json \
  --output /opt/ec26e/validation/solve-manual.json
```

完整运行依赖验证（包括隐藏 Tk 窗口，需要板端 Xorg `:0`）：

```sh
. /opt/ec26e/env.sh
DISPLAY=:0 python /opt/ec26e/install/verify_runtime.py \
  /opt/ec26e/validation/live-input.json /opt/ec26e/validation/runtime.json
```

## 重新安装

Python 来自 Astral `python-build-standalone` 的 20261013 发布：
`cpython-3.11.17+20261013-aarch64-unknown-linux-gnu-install_only.tar.gz`。
SHA-256：`643954c07b04c18e215f812691cd5fa15d924fec1f3a5fdd9c1789484382ac24`。
解包后顶层为 `python/`；厂家 BusyBox tar 不支持 `--strip-components`，应解包到空的暂存目录后放到上述独立路径。
用该解释器的 `-m venv` 创建应用环境，不替换系统 Python。

两份项目分别在各自仓库通过 Poetry 构建 wheel；求解器源码仍在独立 `puzzle-solver` 仓库。
安装包必须匹配 CPython 3.11、AArch64 和 glibc；不能复制旧板 ARMv7 wheel。
`SHA256SUMS` 使用 LF 换行，每个 wheel 在上传后都校验。来源和哈希见部署记录。

准备好解释器、wheelhouse 和本目录脚本后执行：

```sh
sh /opt/ec26e/install/install_runtime_offline.sh /opt/ec26e/wheelhouse
```

FFmpeg 7.0.2 来自固定版本 `imageio-ffmpeg` 的 AArch64 wheel，通过虚拟环境中的 `ffmpeg` 链接调用。
图形界面使用现有 Xorg 和独立 Python 自带 Tcl/Tk。硬件包同时包含 UI 字体及授权文件，运行包内 `setup_x11_fonts.sh` 安装字体并生成 X11 核心字体索引；随后使用包内 `xorg-hdmi.conf` 启动 Xorg。

字体来自 Debian `fonts-wqy-microhei 0.2.0-beta-3.1`。`scripts/axu5ev/prepare_ui_font.py` 使用 fontTools 从原始 TTC 提取 ASCII 和 `sw/src` 中的 UI 字符；增加菜单文字后应重新生成。产物及来源哈希在 `artifacts/fonts/manifest.json`。

## 后续迁移边界

- Linux FPGA manager、设备树、图像 DMA 和两类计算核 UIO 已适配并完成有界测试；不能把 `pip install pynq` 当成硬件平台迁移。
- 未部署旧板的 framebuffer、USB 控制、GPIO/UART 地址或 systemd 服务。
- 真实 USB 摄像头已成功采集及运行 CPU 识别，但重启后枚举不稳定。HDMI、完整操作台、实体按键、电机和机械校准的验收状态见增量联调记录。
- 本次安装前校准了系统时间，但未配置持久的 NTP/RTC 同步。重启后如时钟回退，HTTPS 下载前需校时。
- 存储卡之前报告的普通速度模式和未正常卸载问题不由依赖安装修复；继续正常关机再断电。
