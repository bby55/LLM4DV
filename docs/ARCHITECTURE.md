# 项目架构

## 总览

LLM4DV 现在包含两条相互独立的验证路径。原始路径面向论文中的 Cocotb/LLM 激励生成实验；严格路径面向完整 Ibex RTL 的结构覆盖率测量。两者共享 DUT 和部分历史日志，但结果不自动合并。

```text
完整 Ibex RTL 路径
build_ibex_full_coverage.ps1
        │
        ├── ibex_cpu/tools/ibex_coverage_top.sv   wrapper 与可观测信号
        ├── ibex_cpu/tools/ibex_coverage_main.cpp C++ 激励、RVFI/FSM trace
        └── tools/ibex_full_rtl_coverage.py      编译、仿真、覆盖率门禁、汇总
                         │
                         └── outputs/ 外部实验目录

原始研究路径
generate_stimulus.py → agent → Cocotb client/server → DUT → logs/
```

## 目录职责

- `agents/`、`models/`、`prompt_generators/`、`loggers/`：原始 LLM 激励生成客户端的组件。
- `examples_*`：功能覆盖计划、DUT 摘要和示例测试材料。
- `ibex_cpu/`、`ibex_decoder/`、`stride_detector/`：三个原始 Cocotb DUT 模块。
- `ibex_cpu/src/`：Ibex RTL 源码；完整覆盖率流从该模块的 Makefile 源列表构建设计。
- `tools/`：完整 RTL 覆盖率的构建、仿真、解析和报告脚本。
- `tests/`：不依赖外部模型的回归测试，覆盖解析、真实性门禁和场景选择逻辑。
- `archive/`：原始实验和模型结果的集中归档，不是运行时依赖。
- 模块内 `logs/`：旧 Cocotb 流的默认输出和历史证据。
- `docs/`：项目级架构、覆盖率和实验管理说明。

## 严格流的数据边界

一次有效迭代必须产生机器码、RVFI retirement trace、FSM trace 和 Verilator `coverage.dat`。Python 汇总器只接受通过以下门禁的程序：编译成功、仿真未截断、RVFI 有效、状态和转移合法、回放确定、trace 确实改变。模型文本本身不是覆盖率证据。

输出目录应位于仓库外，例如 `D:\coverage\llm4dv\ibex-run`，防止大体积 trace、Verilator 构建对象和覆盖率数据库污染源码目录。
