# 随机候选距离 PL 核

`puzzle_candidate_scan` 用于随机 2～4 片留缝规划。原四凸片扫描核保持原接口；新核独立处理简单凹/凸多边形，不使用凸包或凸多边形分离轴判据。PS 仍计算完整拓扑、重叠面积、间隙、机械限位及执行许可。

## 数据和计算边界

- 顶点单位为 mm，支持每片 3～8 点、两种绕序；公共随机竞赛入口仍要求 3～5 边。
- 顶点坐标在 `[-512, 512]`，每片平移在 `[-128, 128]`，主机要求简单非退化多边形、边长至少 0.01 mm。范围不支持时明确记录 CPU 路径；协议或超时错误终止当次任务。
- 输入在一次求解内持锁使用。每批 1～64 个候选，190 个候选按 64/64/62 分批，保持原有顺序。
- 核计算双向点到线段距离、线段相交和射线包含测试；相交、包含、接触均返回零距离。它不计算重叠面积，不判定布局可执行。
- 单精度结果使用 **0.02 mm** 保护带。只有已有 PS 验证的无重叠且端点合格候选时，才用乐观评分下界排除更差候选。近似相等及保护带内的候选继续原 PS 检查，最终胜出方案始终重新完整检查。

## AXI-Lite ABI

时钟沿用 PL0 约 100 MHz，同步低有效复位。地址 `0xA0060000`，窗口 64 KiB，Linux UIO 名为 `puzzle_candidate_scan_0`。只使用对齐 32 位访问；浮点数组传输 IEEE-754 float32 的原始位。

| 相对地址 | 内容 |
| --- | --- |
| `0x00` | HLS `ap_ctrl_hs`：启动、完成、空闲 |
| `0x10` | 非零请求序号；回绕后从 1 开始 |
| `0x18` | 本批候选数，1～64 |
| `0x20` | 完成序号，必须与请求一致 |
| `0x28` | ABI 标识 `0x434e0101` |
| `0x30` | 状态：0 成功，1 输入非法 |
| `0x1000` | 输入 580 个 float32 |
| `0x2000` | 输出最多 384 个 float32，距离平方 |

输入前 4 个数为各片顶点数，未使用的尾部片数为 0；接着 64 个数为 `[4][8][2]` 顶点；后 512 个数为 `[64][4][2]` 平移。每个候选输出 6 个数，顺序为 `01,02,03,12,13,23`。未使用的碎片对必须返回 `-1`；主机验证填充值和所有有效结果。无效请求返回状态及序号，不消费旧输出。

## 软件入口

- HLS：`src/hls/candidate/`，独立 GEOS 参考向量生成器为 `scripts/axu5ev/generate_candidate_vectors.py`。
- 板端：`sw/src/ec_26_e/control/pl_candidate.py`；启用变量为 `EC_CANDIDATE_BACKEND=pl`，默认 CPU。旧硬件缺核时明确报错。
- 算法库：独立 `puzzle-solver` 的 `CandidateScanBackend`，通过 `plan_sheet_moves(..., candidate_backend=...)` 注入；输入输出 `PuzzlePiece → MovePlan` 保持兼容。
- 新核对正常搜索和恢复路径都生效；所有分支共享当前任务的后端，不创建额外硬件使用进程。
- 硬件包选择由 `deploy/axu5ev/select_candidate_hardware.py` 备份和更新，下次冷启动生效。不会在 DMA/HDMI 驱动运行时重编程 PL。

## 构建与验证入口

```powershell
poetry -C sw run python ../scripts/axu5ev/generate_candidate_vectors.py --output ../src/hls/candidate/candidate_vectors.txt
$env:EC_AXU_HLS_CORES='candidate'
./Build.ps1 -Stage hls -Cosim
./Build.ps1 -Stage compile
./scripts/Build-Deployment.ps1
```

不设置 `EC_AXU_HLS_CORES` 时构建全部核。构建保留持久维护的 XPR 和 BD，只增加一个 AXI 从设备。HLS 仿真、HLS 综合、完整实现/时序、生成包和上板验收分别记录，不能以核估算代替完整求解计时。

板端 `validate_candidate.py` 校验真实硬件与独立向量；`benchmark_candidate.py` 对同一输入集测完整只求解时间及实际 PL 批次、传输、核等待和剪枝数。两者均不访问执行器。
