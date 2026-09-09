# 覆盖率工作流与真实性门禁

结构覆盖率只接受以下证据链：

1. GHDL 能分析并展开真实上游 VHDL。
2. 选定的 upstream testbench 仿真返回码为 0。
3. `ghdl coverage --format=lcov` 输出至少包含一个 RTL 行或分支记录。
4. 首轮测试平台使用新工作目录重放，覆盖率签名一致。
5. `source_manifest.json` 保存了文件数量、物理行和 SHA-256，便于确认覆盖率对应的 RTL 版本。

任一门禁失败时，`coverage_valid` 为 `false` 或程序直接失败。模型响应、测试平台数量、静态 `if/case` 计数和旧报告都不能充当覆盖率数据。

Windows/MSYS2 必须使用带 mcode 后端的 GHDL；LLVM 后端可以仿真，但不会生成本工作流所需的 `coverage-*.json`。例如：`pacman -S mingw-w64-ucrt-x86_64-ghdl-mcode`。

当前实现只声明 GHDL 实际能提供的 Line/Branch。Expression、Toggle 和 FSM 的分母没有可靠来源，因此报告为 `unavailable`；后续接入专用 VHDL 覆盖率工具时应新增适配器和独立测试，不能复用 Line/Branch 数字。
