# 摄像头、HDMI 与 PL 联调（2026-10-20）

这是接入摄像头和 HDMI 后的增量记录；初始构建记录保留于本目录 README。两个电机 USB 串口尚未接入，全程未执行运动或电磁铁操作。

## 摄像头

Generic USB Camera3，VID:PID `0bda:5858`，序列号 `200901010001`，USB3 路径 `2-1.1`。出厂平台 capture 节点为 video1，候选平台曾成功成为 video0；`/dev/ec26e-camera` 通过物理路径和 capture index 绑定。

停止占用摄像头的厂家 `alinx_ui1` 后，FFmpeg 实测 MJPEG 1280×720、30 fps，150 帧媒体时长 5 秒，墙钟 4.860 秒。单帧 JPEG 可解码。FFmpeg 退出时曾出现 `VIDIOC_QBUF: Bad file descriptor` 警告，不据此宣称采集清理无异常。

工程 `FFmpegCamera → Resize → CPU detector` 连续处理 10 帧。首帧约 1.989 秒，后续约 473–542 ms。四片模式检测到 8 个候选区域，未接受为有效解。诊断画面显示白色拼块之外，视野边缘的机架和杂物也进入当前工作区，且部分拼块超出该区域；需重新校准相机视角及工作区，不能直接接受默认参考区域。

热重启后 USB3 Hub 与相机存在不枚举现象，USB2 Hub 仍可见。一次拔插后、后续启动恢复过，另一次重启后再次丢失。单独重新绑定 DWC3 及其父驱动没有恢复。临时补充 PS GTR lane1、USB3 instance0、26MHz PHY 描述后 PLL 报告锁定，但未立即恢复相机；此试验未写入正式 FIT，也不算问题已解决。

## 平台和加载器

通过 Radxa 的 CP2102N 串口进行受控重启和登录。候选 FIT 的内核/DT 哈希由 U-Boot 验证，`chosen/ec26e,pl-base=axu5ev-v1` 生效。原 BOOT.BIN、image.ub、boot.scr 保留；新增候选文件，不修改永久启动环境。

实际定位并修复：

- 内建 I²C 字符驱动不应强制 modprobe。
- Linux 5.4 不递归创建 overlay 新总线子设备，改挂现有 `/amba`。
- ADV7511 读取需要重复起始事务；分离 write/read 总读到 revision `0x14`。
- 已加载的 PMON UIO 模块不会接受后续 modprobe 的新匹配参数，两个 HLS 设备使用核验后的显式绑定。
- NumPy 对 UIO Device Memory 的批量拷贝触发 SIGBUS；改为对齐的 32 位读写。
- 板载 Xorg fbdev 只接受实际 `/dev/fbN`，启动时生成解析过的配置。

首次各类失败均保留日志，加载失败后通过重启重试；没有在线卸载 DMA 驱动或强行清除失败状态。

## 实际 PL 检查

初步检查：8×8、128×72、1280×720 随机图像与 CPU 分割逐像素一致。对应一次调用含打包、DMA、PL 和结果复制约 1.56、1.78、76.16 ms；这不是整套视觉任务帧率或稳定性能指标。

129 个平移配准请求跨三个批次，与 CPU 的角度和位移结果一致，零数值回退；401 点间隙扫描与解析参考最大误差约 `3.22e-6 mm`。新增 `deploy/axu5ev/validate_accelerators.py` 用于扩大为多角度、连续帧和独立 GEOS 对照，并保存 JSON。

对应软件修改的主机测试：42 项通过。完整 wheel 已重新构建。CPU 仍是菜单默认后端，现场标定标志仍为 false。

## HDMI 验收界限

`ec26e-hdmi` framebuffer 已绑定，固定 1280×720 XRGB8888；Xorg 和菜单进程运行。framebuffer 截图暴露出 X11 核心字体配置问题，正在修复。用户反馈暂未看到画面，故不将截图、I²C 应答或设备节点作为显示器实际出图通过。

板端日志：`/opt/ec26e/validation/camera-20261020/`；主机串口启动日志与诊断截图：`build/axu5ev/`。最终显示、字体和扩展加速器检查结果追加在下方。

## HDMI 复位修复与第二次构建

用户确认 HDMI OUT 接线正确，显示器报告“无信号”，整屏测试彩条也不可见。检查持久化 BD 发现 `hdmi_reset` 的 `C_AUX_RESET_HIGH=0`，但 `aux_reset_in` 接到常量 0，导致扫描模块持续复位。修正为常量 1，并在创建脚本和工程核验脚本中检查极性及连接。原产物保存在 `artifacts/axu5ev-before-reset-fix/`。模块级扫描仿真未覆盖这一 BD 集成错误，不能用原仿真结果替代新的上板显示验收。

旧 bitstream 下的扩展加速器检查已通过：10 次不同尺寸及真实摄像头图像的连续预处理，零像素差异；129 个多角度刚体配准请求，最大角误差约 `2.14e-5` 度、位移误差约 `1.53e-5 mm`、零 CPU 回退；401 个间隙样点对照独立 Shapely GEOS，最大误差约 `3.22e-6 mm`。新的 bit/HWH 生成后需再次检查。

板载 Tk 使用 X11 核心字体，不支持依赖 Fontconfig 自动选择 Noto OTF。已生成并安装文泉驿微米黑 UI 字符子集及授权文件，配置 `fonts.dir`、`fonts.scale` 和 Xorg FontPath；实际 Tk 已能选择该字体。物理屏幕上的中文显示仍待 HDMI 修复后确认。

另行生成 `image-puzzle5ev-usbphy.ub`，在出厂 DT 的 USB0 节点加入厂家 PS 配置对应的 GTR lane1 / RefClk1 / 26MHz 描述，保留旧候选和出厂 FIT。主机与板端独立生成的 SHA-256 均为 `e9be8f56ed1afa00a6cbd8d0206ffa9eb3b27d081a27d7ed0eb86bc3c8d9c2e2`。5 项启动镜像单元测试通过；实际 USB 重启稳定性单独验收，不因 PHY PLL 锁定就认定已解决。

第二次完整构建已完成：WNS `3.358 ns`、WHS `0.010 ns`、WPWS `2.000 ns`，无未约束内部端点。资源为 LUT 26427、寄存器 27026、BRAM Tile 18、DSP 62。DRC 无错误/严重警告，保留 HLS DSP 流水线、IP RAM 及 IIC 三态相关警告；methodology 中的三处异步复位 LUT 告警位于厂家 AXI downsizer FIFO。IIC 异步输入/输出采用例外约束，HDMI 转发时钟单独定义 generated clock。

保存工程核验通过：886 个工程文件存在，不引用旧工作区，三个 HLS ABI 符合预期，HDMI 辅助复位连接检查通过。Bootgen 转换完成，打包程序核对 XSA 内 bit/HWH 与独立文件逐字节一致。新 bit SHA-256 为 `07beeb20ef4fcb39662f90a49fb97603a40d592e57c66aa2ba6be3d78e27f2d9`，HWH 为 `165342e544a7d82234f96a512e05c56fe9a0c3cdc78d10f6342af4932a44155a`；本次报告、清单和工程核验日志位于本目录 `hdmi-reset/`，不覆盖初始构建证据。

USB PHY 候选已实际启动，PLL 锁定但 USB3 Hub/相机未恢复，用户拔插后仍未重新枚举。此时尚未加载新版 PL，用户观察到黑白条纹，不能记为新版 HDMI 出图通过。随后用户将同一 CP2102N 改接电脑 COM10，已验证直接访问 root 控制台；新版包改由电脑串口传输。

## 新版上板结果

电脑 COM10 完成增量传输，695601 字节的包 SHA-256 为 `d5d734843c4af987b995042f3d8d6ae4d08078ce6db24827f05a12629b8abd75`。最终传输零重传；板端核验旧文件身份后，在新目录 `/opt/ec26e/staging/20261020/axu5ev-reset-bundle` 重建完整 bit、bin、HWH、XSA 和运行文件，全部与完整部署清单 SHA-256 一致。旧包保留。传输后 UART 恢复 115200。

加载器预检和加载均通过，板上运行新 bit/HWH。用户分别确认：

- “能看到彩条和文字，颜色正常”。
- “中文菜单正常”。

这是 HDMI OUT 物理出图及中文显示的实际验收。截图 `hdmi-reset/menu.jpg`、显示确认记录 `hdmi-reset/display-acceptance.json` 已保存；不等同于完整操作台或机械验收。

新硬件再次完成 10 帧连续预处理、129 个刚体配准请求、401 个 GEOS 对照间隙样点，全部通过，误差与旧版一致。1280×720 单次完整预处理约 75.42–75.59 ms，640×360 约 19.53–19.55 ms，均含软件打包、DMA 和结果复制。JSON 中记录当前 bit/HWH 身份。输入含先前采集的相机图像，这次不是实时相机测试。

USB 后续出现 Hub `error -71`；重新绑定 USB 父控制器后 USB2 Hub 恢复，临时将父控制器、根 Hub 和外置 Hub 的 runtime power control 设为 on。USB3 Hub 仍缺失。核验 GL3523 的 USB2 Hub 支持独立端口电源控制后，对已知相机端口 1 断电/上电，仍未恢复。尚需整板断电启动对照；这些临时诊断设置未固化为正式 USB 修复。

## 冷启动对照与当前运行状态

用户完成整板断电 10 秒后上电。第一次因调试串口也断开，自动进入原厂系统；USB2 Hub `05e3:0610`、USB3 Hub `05e3:0620` 和摄像头 `0bda:5858` 均恢复。随后热重启进入保留原厂 USB 配置的候选 v1，并省略 U-Boot `usb start`，摄像头仍再次丢失，因此不能将问题归因于 U-Boot USB 扫描。

第二次安全关机、断电后，通过支持 COM10 重新枚举的串口监控直接选择候选 v1 `image-puzzle5ev.ub`，USB3 Hub 和摄像头正常恢复，别名 `/dev/ec26e-camera → video0`。重新加载同一已验证的新硬件包，HDMI 菜单恢复。当前使用 v1；`image-puzzle5ev-usbphy.ub` 仅保留为失败的诊断候选，不作为推荐启动文件。

本次冷启动在新 PL 已加载的条件下，FFmpeg 再次成功采集 MJPEG 1280×720 / 30 fps / 150 帧，媒体时长 5 秒、退出码 0。随后向唯一识别到的拼图菜单窗口发送预览快捷键，实际应用预览连续更新 50 帧；所存状态中检测到纸面和 4 个候选拼块，单帧轮廓检查通过。菜单保持实时预览，CPU 后端、仅规划、运动关闭。这里的 `valid=true` 仅表示单帧检测合格，不表示稳定求解、机械标定或执行许可。

结论：冷启动下摄像头、菜单显示和新版 PL 已可共同工作；热重启后的 USB3 恢复问题仍未解决。目前应使用安全关机后的整板断电启动，不能把普通 reboot 作为可靠恢复步骤。原始启动文件及永久启动选择仍未更改。
# M0 接入增量

当前采用地猛星 MSPM0G3507 单 USB 桥接，用户已确认固件烧录及下游设备接好。
CH340E 已枚举；921600 分频与 M0 错误处理均已修正，新固件已烧录并读回校验。
用户确认 XYZ 不需要回传后，已烧录 UART1 TX-only 固件，读回校验通过；
约 20 秒连续 100 次 STATUS 均为 fault=0、armed=false、magnet=false。
未发送任何运动或磁铁控制命令，旋转轴接收保持启用。
接线与软件详情见 [M0 桥接](../../m0-bridge.md)。
下方为原始联调记录，早期“两个 USB 串口未接”不代表当前拓扑。
