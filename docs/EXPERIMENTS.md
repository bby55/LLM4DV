# 实验与清理策略

## 历史资产

仓库中保留了论文和早期实验产生的大型目录：

- `archive/experiment_logs/`：原始 LLM 实验日志和模型响应。
- `ibex_decoder/logs/`、`stride_detector/logs/`、`ibex_cpu/logs/`：模块级 Cocotb 运行日志。
- `archive/model_results/`：历史模型结果和分析材料。

这些文件体积较大，但仍可能用于复核历史结果，所以本次整理不删除它们，也不把它们并入新的完整 RTL 覆盖率统计。需要归档时应先复制到外部存储，再从工作区移除。

## 已清理的运行时垃圾

- 根目录和 `ibex_cpu/` 下的临时 `trace_core_00000000.log`。
- 所有递归 `__pycache__/` 目录以及 `*.pyc`、`*.pyo` 文件。
- `obj_*`、`sim_build_*` 等可由 Verilator/Icarus 重新生成的构建目录。
- 与旧 FPGA-FFT、FFT-FLAME 实验相关的源码、工具和输出。

`ibex_cpu/test_prog.bin` 被保留，因为旧 Cocotb 入口仍直接引用它。新的运行输出请放在仓库外，并通过 `.gitignore` 忽略本地构建和覆盖率产物。

## 输出命名建议

```text
D:\coverage\llm4dv\
├── ibex-run-YYYYMMDD\
├── ibex-run-YYYYMMDD-replay\
└── reports\
```

每个运行目录应包含配置快照、有效程序列表、coverage 摘要、FSM trace 和门禁结果。报告中明确注明 RTL 版本、Verilator 版本、迭代数、周期上限、模型调用数和失败程序数。
