# AXU5EV 运行环境部署验证：2026-10-20

通过 SSH 连接 `192.168.1.32`，使用 Windows known_hosts 中用户已接受的 RSA 主机密钥进行校验。
目标为出厂 PetaLinux 2020.1、Linux 5.4、AArch64、glibc 2.30、SysV init；无可用 apt/opkg 和板端编译工具。

## 安装结果

- 独立 CPython 3.11.17 与虚拟环境已部署在 `/opt/ec26e`；厂家 Python 3.7.6 保持原样。
- NumPy 2.0.2、SciPy 1.13.1、Shapely 2.0.7、OpenCV 4.10.0.84、pyserial 3.5、gpiod 1.5.4、python-periphery 2.4.1、Typer 0.12.5、Pillow 11.0.0 及其固定依赖已安装。
- 两个仓库分别用 Poetry 从当前工作树构建 wheel；源树包含用户已有未提交修改，提交与 dirty 状态记录在 `manifest.json`。未将求解器源码复制到主仓库。
- FFmpeg 7.0.2 来自 imageio-ffmpeg 0.6.0 AArch64 wheel；独立 Python 自带 Tcl/Tk 9.0.4。
- Noto Sans CJK SC 字体已安装到 `/usr/share/fonts/ttf/ec26e/`，`fc-match` 正确识别。该 BSP 默认字体搜索不包含 `/usr/local/share/fonts`，因此最终采用前述目录。
- 所有 wheel 上传后经 SHA-256 校验；Python 归档与 GitHub 发布摘要一致，第三方 wheel 与 PyPI 元数据摘要一致。OpenCV/FFmpeg 下载使用清华镜像，但以 PyPI 摘要验证内容。
- 校准了板端当前时间，使用新 Python 验证 PyPI HTTPS 证书及请求成功（HTTP 200）；未配置持久 NTP/RTC 同步。
- 安装后根分区剩余约 8.3GB，已执行 `sync`，板卡保持开机。

## 实际验证

| 检查 | 结果 |
|---|---|
| `pip check` | 无依赖冲突 |
| `ec-7020 --help`、`puzzle-solver --help` | 均成功 |
| 所有列出的项目运行库导入 | 成功 |
| SciPy 解线性方程及 NumPy 数值核验 | 通过 |
| Shapely 多边形面积 | 通过 |
| OpenCV PNG 编码、解码逐像素比对 | 通过 |
| 隐藏 Tk 窗口连接现有 Xorg `:0` | 通过；窗口随即销毁 |
| FFmpeg 合成一帧并解码至空输出 | 通过 |
| 固定四片真实轮廓输入 CPU 求解 | 完整四步计划，单次 2.92334 秒 |
| 与旧板 `accept.json` 的计划比较 | 离散字段一致，数值在 rtol=1e-8、atol=1e-6 内一致 |

求解输入为 `docs/validation/fixed4-clearance-20261013/live-input.json`。计时仅涵盖已导入模块后的公共求解 API，单次样本，不包含相机、模块导入、GUI 或机械运动；不是整机加速结论。
详细结果见 `runtime.json`、`plan-comparison.json`、`install-runtime.log`、`requirements-installed.txt`、`SHA256SUMS` 和 `manifest.json`。

## 未完成的迁移项

未安装 PYNQ/XRT，未重新配置 PL，未写入 BOOT.BIN、image.ub 或设备树。FPGA 加速、DMA/CMA、USB 相机实拍、完整 GUI、按键与电机接线仍需单独移植和验收。
没有启用开机自启动，也没有执行运动。TF 卡之前的文件系统告警及速度模式未在本次处理。

使用方法和离线重装说明见 `deploy/axu5ev/README.md`。
