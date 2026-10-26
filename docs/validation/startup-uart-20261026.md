# 识别恢复后任务失败：M0 上电串口异常

相机工作区位置恢复后，实时预览识别 4 片有效，固定任务稳定审查通过，求解耗时 1.295 秒、confidence 0.794，生成预测图；失败发生在打开 M0 后，电机尚未使能。M0 初态 fault=2 锁存了启动/空闲阶段串口异常。

诊断明确为 HOST/ROT 的 framing/BREAK，overrun 和软件队列满为零，不是 M0 性能不足。旋转错误在上电后 0～25 ms，上游在约 23 秒，均未使能。物理噪声来源尚未用示波器确认。

用户确认 XYZ 只发送、旋转保留角度反馈。M0 固件已修复健康未使能状态的接收重同步，同时保留使能期间错误锁存及 ARM 边界保护。源码、测试和具体行为见独立仓库 `../mspm0g3507-puzzle-bridge/docs/startup-uart-20261026.md`，本仓库未复制固件源码。

已 J-Link 烧录并独立读回 5736 字节验证 SHA：`bcac3c6d14b685aabd1751377734100d7f2f248da5dc366aa31004e698308069`。旧版诊断固件及已核对的前 8 KiB Flash 备份保留。未修改 FPGA PL、软件 wheel、机械标定或速度配置。

板端验证：未使能注入 BREAK 后 fault=0，随后连续 30 次查询正常；ARM 桥接后注入同类错误仍 fault=2，输出关闭且锁存，测试明确 DISARM 才清除。只读旋转角度查询收到合法回包 13.9°，没有驱动使能或运动命令。人工注入的 armed_events 已在记录中注明，非实物搬运错误。原菜单恢复，自动化验证未搬运，完整断电冷启动及实物执行验收尚未进行。

证据：[旧故障](startup-uart-20261026/before.json)、[空闲注入与30次查询](startup-uart-20261026/after.json)、[锁存保护](startup-uart-20261026/protection-test.json)、[旋转回传](startup-uart-20261026/rotation-feedback.json)。
