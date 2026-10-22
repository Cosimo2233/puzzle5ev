# 旧 Zynq-7020 测试参考

这三个文件来自迁移前工程，依赖旧板的部署文件和显示路径，仅保留为迁移参考，不纳入 AXU5EV 的 pytest 收集。

新板测试位于 `sw/tests/unit/` 和 `sw/tests/integration/`，真实硬件验证入口位于 `deploy/axu5ev/validate_*.py`。`sw/tests/external/` 需要额外设备或外部库，应单独指定后运行。
