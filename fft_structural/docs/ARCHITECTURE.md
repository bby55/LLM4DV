# FFT 结构覆盖率架构

本分支把上游 `owocomm-0/fpga-fft` 固定在 `fft_structural/upstream/`，并在旁边提供与 LLM4DV 一致的 `tools/`、`tests/` 和 `docs/`。

```text
upstream VHDL + upstream testbench
              |
              v
tools/fft_structural_coverage.py
              |
              +-- GHDL analyze/elaborate/run --coverage
              +-- LCOV parser and merge
              +-- source manifest, replay gate, JSON/Markdown report
```

上游代码保持原始目录和文件名，便于和上游提交对照。新增工具不修改 FFT RTL，也不把测试平台代码计入 DUT 结构覆盖率。

## 覆盖范围

- `Line`：GHDL LCOV 中 RTL `DA` 记录的已执行行。
- `Branch`：GHDL LCOV 中 RTL `BRDA` 记录的已执行分支。
- `Expression`、`Toggle`、`FSM state/transition`：当前 GHDL 适配器没有数据时显示 `unavailable`，不会用静态估计替代。

每个输出目录保存源码清单、每次仿真的日志、原始 LCOV、合并 LCOV、命令记录和 JSON 报告。
