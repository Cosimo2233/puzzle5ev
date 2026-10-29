# 七巧板模式

软件、实拍和 HDMI 菜单验证记录见 [2026-10-27 验证](validation/tangram-20261027/README.md)。

后续已修复七巧板及下拉图案名称缺字，并按用户要求开启生产板的手动运动入口，见 [字体修复与试机启用](validation/tangram-font-20261027/README.md)。默认模板仍只规划；生产板由用户点击“开始拼图”执行。

搬运中的机械单圈角 1° 中止条件已按用户要求取消，启动及每片拾取前仍严格检查；原位置等待、通信和超时检查保留，见 [运动阈值调整](validation/rotation-check-20261027.md)。

HDMI 菜单提供“七巧板”和十二个图案：正方形、猫、兔、鱼、船、房子、狗、天鹅、鸟、企鹅、海豹、椅子。后六项按用户参考图重建为精确标准片布局；[图案目录](validation/tangram-robust-20261029/patterns.svg) 可预览全部片形。下拉菜单显示八行并支持滚动，字体生成器校验全部名称的中文字形。使用白色标准七巧板，整套正方形边长 100 mm；识别不依赖碎片颜色编号。两大三角和两小三角可互换，平行四边形不能自动翻面，必要时预览整个图案的镜像。

## 操作和配置

相机画面右侧放七片，左侧留空。碎片不得接触或重叠，至少间隔 2 mm。选择图案后点“开始”，程序采集一张新照片，用同一张图识别和计算，显示七个源位置、目标位置、拾放顺序和旋转角。单帧失败立即提示，不循环重拍；仅规划时不创建运动执行器。`stable_frames=1` 为默认值，需要调试多帧检查时可设置为 6。

部署配置为 `/opt/ec26e/config/axu5ev/tasks/tangram.json`。`base_profile=random.json` 复用当前原程序的相机、透视、机械限位、A4 原点、Z 高度、旋转方向、速度和等待参数。`actuation_overrides={}` 表示不覆盖原机械标定。七巧板固定使用 CPU，不依赖 PL 预处理或新 bitstream。

工作区沿用原程序竖向坐标：207×292 mm，`counterclockwise` 将相机右半区对应到较小的 y。当前取料区 `[2,2,205,138]`，拼装区 `[2,140,205,290]`。预览绘制 SOURCE/TARGET 边框；坐标方向不应按屏幕左右直接推算。

`side_mm=100` 定义标准片型尺寸。`vertex_tolerance_mm` 默认 3 mm，供片型匹配使用，不代表机械定位精度。规划保留实测多边形，使用纯旋转和平移，禁止缩放或反射碎片。模板位置允许在该容差内微调，实际目标之间保留 `assembly_gap_mm=1` mm 的装配间隙；没有满足全部几何和限位条件的完整七步方案时拒绝运动。

`motion_verified` 默认 false，新模式不会继承固定/随机菜单的运动开关。沿用原程序机械参数完成七巧板的现场核验后，设置此项为 true；生产菜单的既有运动权限也必须启用。手工入口仍须明确传入 `--execute`。不在开机时自动采图或运动。

```sh
. /opt/ec26e/env.sh
python -m ec_26_e.commands.app tangram \
  --env /opt/ec26e/config/axu5ev/machine.env \
  --config /opt/ec26e/config/axu5ev/tasks/tangram.json \
  --pattern cat --plan-only
```

注册任务名为 `tangram`，也可通过统一任务入口执行。结果写入 `/run/ec26e/tangram-result.json`；落点预览使用本次稳定快照和独立令牌，不复用上次方案。

## 软件边界和验证

独立 `../puzzle-solver` 提供 `TangramLayout`、`TangramPlanningOptions`、`get_tangram_layout`、`classify_tangram_piece` 和 `plan_tangram_moves`。返回 `MovePlan(mode='tangram')`；目标宽高表示图案包围盒。原 `PlanningOptions` 和 `plan_sheet_moves` 仍只支持 fixed/random。应用通过公共 API 引用求解器，没有复制其实现。

匹配的求解器源码位于 `feature/puzzle-solver` 分支，十二图案及间隙规划提交为 [`5b16154`](https://github.com/Cosimo2233/puzzle-solver/commit/5b16154)。恢复开发环境时应检出该提交或包含它的后续版本，再执行应用的 Poetry 安装。

七巧板预览和任务使用同一个 CPU 白片分割器：以归一化色度选取占多数的蓝底颜色簇，估计局部背景以补偿阴影和反光，弱色差像素必须连接到可靠的白片核心。整张照片独立估参；不沿用上一帧阈值、不按面积取前七个物体，也不强行拆开接触碎片。轮廓使用多个分边尺度和鲁棒直线拟合，通过实测直边、凸性、交并比及原有残差门槛后才接受，保留实测形状，不用理想模板补全被遮挡的边。片型和完整七片检查仍在规划之前执行。原固定/随机纸片分割路径保持原方法。

规划枚举同形片分配、旋转对称解和整图镜像，先检查每 45° 的整图朝向，全部无解时再细化到 15°。使用分离轴与工作区约束求最小平移，生成至少 1 mm 的真实装配间隙，避免沿质心统一拉开造成细长图案不必要地超限。实测轮廓仍只旋转和平移；用动态规划选择取放路径。源区或目标区不适合时要求重新摆放，不做占位中转。

板端 `validate_tangram.py` 采一张照片供全部图案共用并只规划，`--gui` 额外验证图案菜单滚动、进程切换、新增椅子图案的单张采图和预测显示。`--replay <live-results.json>` 可复用保存的实测轮廓重算，不再采图。验证脚本显式禁止创建运动执行器。几何单元测试覆盖精确模板和现场实拍轮廓；软件通过不等同于实物搬运或 2 mm / 3° 精度验收。

2026-10-29 的扩展及鲁棒性测试见 [验证记录](validation/tangram-robust-20261029/README.md)。

## 发布和恢复

通过 A7A `192.168.1.26` → FPGA `10.42.0.47` 的网口通道部署两个 wheel。安装器核对文件哈希，检查任务/预览空闲并持有公共锁，备份原包和配置，再更新软件和重启菜单。原菜单权限、机械参数、XPR、BD、bitstream、设备树和启动镜像不随本功能更新。

`install_tangram_release.py --release <发布目录>` 默认仅显示变更；加 `--apply` 安装，加 `--apply --rollback` 从该发布目录的 `production-before` 恢复。备份不可覆盖，失败自动回滚。

2026-10-27 的功能前完整快照位于 `/opt/ec26e/releases/tangram-20261027-r2/production-before`，恢复原固定/随机软件：

```sh
. /opt/ec26e/env.sh
python /opt/ec26e/releases/tangram-20261027-r2/install_tangram_release.py \
  --release /opt/ec26e/releases/tangram-20261027-r2 --apply --rollback
```

远程访问已通过 A7A 直连串口核对 Dropbear RSA 主机密钥。板端原 `/home/root` 属主为 1000:1000，已按 root 账户实际家目录修正为 0:0，并安装开发电脑公钥；没有保存密码。旧 Dropbear 需要显式允许 `ssh-rsa` 用户认证与主机密钥算法。
