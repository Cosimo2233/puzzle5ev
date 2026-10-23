# 预测配色、旋转提速与并行回程

## 修改范围

- `plan_preview.py` 第 3 片改为珊瑚红（RGB `#ff554b`），源轮廓 S3、目标轮廓 T3、箭头、缩略图和图例使用同一颜色。各片轮廓及箭头增加深色描边，提高蓝底和纸面上的可辨识度。
- 固定/随机共用执行器：末片释放、Z 抬升后，启动 XY 返回本次起点，同时逆时针旋转回零；等待两路均完成，再执行原完成提示，关闭磁铁并下降 Z。下一片预定位流程保持原逻辑。
- 板端 `FOC_RPM` 从 20 调至 25，`FOC_MAX_RPM=30`、1° 角度反馈容差、1 秒最小回零等待及其他机械标定不变。
- `configure_capture_release.py --rotation-speed` 仅修改旋转速度，逐一比较固定/随机完整配置对象，确保其他字段不变；支持独立备份和回滚。

## 主机验证

在 `sw/` 使用 Poetry：

```powershell
poetry run pytest tests/unit/tasks/test_task1_actuation.py tests/unit/test_plan_preview.py tests/unit/test_capture_settings.py tests/unit/motion/test_foc_rotation.py tests/unit/motion/test_foc_rotation_cli.py tests/unit/test_m0_bridge.py -q
poetry build -f wheel
```

67 项测试、32 个子测试通过。模拟时钟分别覆盖 XY 较慢和旋转较慢，确认并行耗时为两路较大值，完成提示与 Z 下降均在两路结束之后；覆盖零距离、单轴回程、旋转超时、取消和 XY 发送失败，异常不下降 Z、不报告完成。

已人工查看蓝底合成预测图，S3/T3 珊瑚红及描边清晰可辨。图像位于生成目录 `build/axu5ev/return-speed-20261023/preview-colors.png`，不作为实拍识别精度验收。

## 板端部署

发布目录 `/opt/ec26e/releases/return-speed-20261023`。安装前备份软件，核对发布文件与现有 bit/HWH 哈希；只在菜单空闲时持锁更新并重启菜单。未修改 PL、M0 固件或启动镜像。

安装后固定/随机运行时配置均为 25 RPM、上限 30 RPM、Z 抬升 90°/下降 0°。配置文本比较确认只有 `FOC_RPM` 改动。菜单 PID 1084，预览和任务 worker 就绪，运动权限保留；鼠标光标存在 118 个可见像素。

证据：[安装检查](return-speed-20261023/installed.json)、[速度配置记录](return-speed-20261023/rotation-settings.json)、[发布清单](return-speed-20261023/manifest.json)。

本轮没有发出实际运动命令。提高速度后的精度、机械稳定性与并行回程效果需用户手动实物验收，模拟测试不能替代机械验收。

## 回滚

使用板端 `/opt/ec26e/env.sh` 配置的 Python 和现有 `EC_PL_BITSTREAM`，菜单空闲时运行：

```sh
python /opt/ec26e/releases/return-speed-20261023/configure_capture_release.py --release /opt/ec26e/releases/return-speed-20261023 --rotation-speed --rollback
python /opt/ec26e/releases/return-speed-20261023/install_robust_release.py --release /opt/ec26e/releases/return-speed-20261023 --apply --rollback
```

先还原 20 RPM，再还原软件。若配置已被后续修改，工具拒绝覆盖，需先合并差异。配置备份为 `machine-before-rotation.env`，软件备份为 `production-before/`。
