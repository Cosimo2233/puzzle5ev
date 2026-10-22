# 工程约定

- 使用个人技能 `xilinx-fpga`；软件使用 `python-poetry`。
- 本目录是 AXU5EV-P 独立工程，正式入口 `puzzle5ev.xpr`，器件 `xczu5ev-sfvc784-2-i`，Vivado/Vitis 2026.1。
- `.xpr` 和 `puzzle5ev.srcs/sources_1/bd/system/system.bd` 持久维护；日常构建保留保存的 BD。
- 手写硬件在 `src/`，约束在 `constraints/axu5ev/`，板端程序在 `sw/`，部署文件在 `deploy/axu5ev/`。
- 求解器保留在独立 `../puzzle-solver`，只通过公共 Python API 引用，不复制其源码。
- 原 `../puzzle7020` 是旧板工程和迁移来源；新工程不得依赖其路径。不要修改旧工程来修复本工程。
- `build/`、`artifacts/` 和 Vivado 缓存为生成内容；删除或移动前检查依赖和运行状态。
- 板型 AXU5EV-P；显示使用 HDMI OUT。USB 经 PS Host/Hub，PL 加速通过 DDR/DMA。
- 默认 CPU、仅规划、禁用运动和实体按键；未接外设时只记录构建和软件验证。真实接线、摄像头标定和完整有效解缺一不可执行运动。
- 出厂设备树绑定的 PL 地址与新设计冲突，禁止在出厂设备树下直接加载新 bitstream。启动镜像和驱动切换另行完成匹配验证。
