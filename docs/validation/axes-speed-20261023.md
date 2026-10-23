# X/Y、旋转与 Z 轴速度配置记录

## 最新配置：X/Y 127、旋转 50、Z 80

用户进一步要求 X/Y 200、旋转 50 并提高 Z 速度；确认当前 Servo42C 速度字段最高位为方向、低七位为速度，200 无法作为速度档位编码后，用户明确选择 X/Y 127。

本轮将 X/Y 120 → 127 档，外层距离时间估算 150 → 158.75 mm/s（按档位比例调整，并非实测速度）；旋转指令及软件上限 38 → 50 RPM；Z 64 → 80 档。Z 每次升降 1 秒等待、XY 固定余量 1 秒、旋转最小回零等待 1 秒及角度检查保留。Z 指令提速不代表整次任务按同比例缩短。

固定、随机两个模式完整配置对象核对通过，只改五个速度字段；核对 X/Y 正反速度字节为 `7f/ff`，Z 为 `50/d0`，旋转 50 RPM 报文为 `7a020100324b7b`。菜单重启并预热就绪，运动权限和开机自启保留，未发送实际运动命令，未修改 PL/M0 或重装应用软件。

主机相关回归：`poetry run pytest tests/unit/test_capture_settings.py tests/unit/tasks/test_task1_actuation.py tests/unit/motion/test_foc_rotation.py tests/unit/motion/test_protocol.py -q`，49 项测试、32 个子测试通过。板端证据：[配置修改](axes-speed-20261023/max-settings.json)、[实际配置与菜单核验](axes-speed-20261023/max-verified.json)。实物提速精度仍由用户手动验收。

配置工具 `configure_capture_release.py --axes-max` 在 `/opt/ec26e/releases/camera-start-20261023` 下保存独立备份 `machine-before-axes-max.env` 和记录 `axes-max-settings.json`。菜单空闲、板端 Python 环境及 `EC_PL_BITSTREAM` 配置正确时，可执行以下命令恢复本轮之前的 X/Y 120、旋转 38、Z 64：

```sh
python /opt/ec26e/releases/camera-start-20261023/configure_capture_release.py --release /opt/ec26e/releases/camera-start-20261023 --axes-max --rollback
```

若还要恢复更早速度，先完成上述回滚，再按下文 `--axes-speed --rollback` 恢复；工具拒绝覆盖后续未经合并的配置差异。

## 前次配置：按当前速度提高 1.5 倍

用户要求以当前配置提速。应用前板端为 X/Y 80 档、距离等待估算 100 mm/s、旋转 25 RPM、旋转软件上限 30 RPM。

| 配置 | 修改前 | 修改后 |
| --- | --- | --- |
| `MOTION_XY_SPEED_LEVEL` | 80 | 120 |
| `MOTION_XY_SPEED_MM_PER_SECOND` | 100 | 150 |
| `FOC_RPM` | 25 | 38 |
| `FOC_MAX_RPM` | 30 | 38 |

X/Y 两轴共用同一速度档位，120 未超过协议允许的 127。旋转协议仅接受整数 RPM，25 × 1.5 = 37.5，取最接近的 38（实际指令倍率 1.52）；软件上限只增加到支持此次目标的 38。没有扩大通用驱动默认上限。

实际 XY 报文速度和底层完成时间使用同一档位；外层距离等待估算同步调整到 150 mm/s。保留 1 秒 XY 固定余量、1 秒最小回零等待、1° 旋转到位检查，Z 速度/等待、磁铁等待、机械标定与运动顺序全部不变。末片 XY 与旋转并行回程、两路完成后 Z 下放继续生效。

## 验证和部署

`sw/` 中运行 `poetry run pytest tests/unit/test_capture_settings.py tests/unit/tasks/test_task1_actuation.py tests/unit/motion/test_foc_rotation.py -q`：42 项测试、32 个子测试通过。

仅部署配置工具，无需重装 wheel、修改 PL 或烧录 M0。工具新增 `--axes-speed`，拒绝套用到已变化的基准速度；菜单空闲时持任务锁备份并修改配置，重启菜单。对固定/随机完整配置对象做比较，验证仅四个速度字段改变；额外核对未修改的机器环境文本一致。

配置工具及备份位于当前软件发布目录 `/opt/ec26e/releases/camera-start-20261023`，备份名 `machine-before-axes.env`，修改记录 `axes-settings.json`。板端核对两个模式的运行时配置、旋转速度报文及底层 XY 估算时间，记录见 [配置修改](axes-speed-20261023/settings.json) 和 [板端核验](axes-speed-20261023/verified.json)。菜单开机自启和现有运动权限保留。

本轮未执行运动。实际运行速度受加减速和负载影响，软件配置验证不代表机械精度验收，需用户手动测试。

## 回滚

菜单空闲时，加载板端 `/opt/ec26e/env.sh` 和现有 `EC_PL_BITSTREAM` 后执行：

```sh
python /opt/ec26e/releases/camera-start-20261023/configure_capture_release.py --release /opt/ec26e/releases/camera-start-20261023 --axes-speed --rollback
```

恢复本轮修改前的 80 档、100 mm/s 估算、25 RPM 和 30 RPM 上限；若配置之后又改变，工具拒绝直接覆盖。
