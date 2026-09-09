<!--
Copyright Zixi Zhang
Copyright lowRISC contributors.
Licensed under the Apache License, Version 2.0, see LICENSE for details.
SPDX-License-Identifier: Apache-2.0
-->

# LLM4DV

LLM4DV 是一个面向硬件设计验证的研究框架：使用大语言模型生成测试激励，并观察 DUT 的功能覆盖率反馈。仓库同时保留了原始 Cocotb 研究框架和一条面向完整 Ibex RTL 的严格结构覆盖率验证流。

## 先看这里

| 文档 | 内容 |
| --- | --- |
| [架构说明](docs/ARCHITECTURE.md) | 目录职责、两条执行流和数据流 |
| [覆盖率工作流](docs/COVERAGE_WORKFLOW.md) | Line、Branch、Expression、Toggle、FSM 的定义、采集与真实性门禁 |
| [命令与参数](docs/COMMANDS.md) | 结构覆盖率、功能覆盖率、构建、迭代和输出选项 |
| [实验与清理策略](docs/EXPERIMENTS.md) | 历史日志、输出目录和可安全清理的临时文件 |
| [本周整改报告](docs/WEEKLY_REPORT_2026-09-08.md) | 完整 RTL 代码量、框架整改和真实覆盖率对比 |
| [Ibex 模块说明](ibex_cpu/README.md) | 原始 Ibex Cocotb 仿真入口 |
| [Stride Detector 模块说明](stride_detector/README.md) | 原始 Stride Detector 仿真入口 |

## 目录结构

```text
.
├── agents/ prompt_generators/ models/ loggers/  # 原始 LLM 激励生成框架
├── examples_IC/ examples_ID/ examples_SD/      # 功能覆盖计划与示例输入
├── ibex_cpu/                                    # 完整 Ibex DUT、Cocotb 流和覆盖率工具
│   ├── src/                                     # RTL 源码
│   ├── tools/                                   # 完整 RTL wrapper/harness
│   └── logs/                                    # 历史 Ibex 实验日志
├── ibex_decoder/                                # Decoder DUT 与原始 Cocotb 流
├── stride_detector/                             # Stride Detector DUT 与原始 Cocotb 流
├── tools/                                       # 现代化构建、仿真、覆盖率分析脚本
├── tests/                                       # 回归测试
├── archive/                                      # 原始实验与模型结果，非运行时依赖
├── docs/                                        # 项目级维护文档
├── python-requirements.txt
├── flake.nix
├── LICENSE
└── README.md
```

## 两条运行路径

### 1. 完整 Ibex RTL 结构覆盖率

这条路径使用 `ibex_cpu/Makefile` 的完整 RTL 源文件列表，Verilator 原生生成 Line、Branch、Expression、Toggle 覆盖率；wrapper/harness 额外输出控制器、ID/EX、Load/Store、乘除法和乘法器 FSM 的状态及合法转移证据。所有程序必须通过 RVFI、合法状态/转移、可重复回放和 trace 变更门禁后才计入覆盖率。

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_ibex_full_coverage.ps1
python tools\ibex_full_rtl_coverage.py `
  --out-dir D:\coverage\llm4dv\ibex-run `
  --model glm-5.2 --iterations 6 --cycles 800 --attempts 3
```

主要参数：`--iterations` 控制模型迭代次数，`--cycles` 控制每个程序的仿真周期上限，`--attempts` 控制每轮生成尝试次数，`--model` 选择模型，`--seed-run` 重放已有有效程序。完整命令表和功能覆盖率入口见 [命令与参数](docs/COMMANDS.md)。

输出目录应放在源码树外。每个有效迭代会保存模型请求/响应、机器码、RVFI trace、FSM trace 和 `coverage.dat`。外部事件使用有界的 adaptive probes 优先命中；没有模型响应或没有通过真实性门禁的程序不会被伪装成有效测试。

### 2. 原始 Cocotb 研究框架

```powershell
python -m pip install -r python-requirements.txt
cd stride_detector
make
python generate_stimulus.py
```

这里的 `make` 是 Cocotb/Verilator 服务端，`generate_stimulus.py` 是功能覆盖率客户端。旧客户端目前通过交互输入服务端地址，迭代次数和周期数仍是脚本常量；需要命令行自定义迭代次数时使用完整 Ibex 结构覆盖率入口或多 campaign 入口。

`ibex_decoder` 和 `ibex_cpu` 目录也保留同样的模块级入口。运行日志默认写入各模块的 `logs/`，这些目录是历史研究证据，不应与新的完整 RTL 覆盖率结果混合统计。

## 开发检查

```powershell
python -m unittest discover -s tests -p "test_*.py"
python -m py_compile tools\ibex_full_rtl_coverage.py tests\test_ibex_full_rtl_coverage.py
```

## 结果解释边界

- 结构覆盖率分母来自实际 elaborated RTL 的 Verilator coverage model；wrapper-only 点单独标记，不冒充 DUT 源码覆盖率。
- FSM 状态/转移分母来自 wrapper/harness 声明的状态编码和合法弧列表；状态达到 100% 不等于所有 RTL 行达到 100%。
- 代码量增加后覆盖率下降通常是分母扩张或新增未触达逻辑，不代表原有测试失效；应同时报告分子、分母和有效程序数。
- `experiment_logs/`、各模块 `logs/` 和 `claude_results/` 是历史资产，当前运行不会自动把它们合并进覆盖率。
- `archive/` 和各模块 `logs/` 是历史资产，当前运行不会自动把它们合并进覆盖率。

## 依赖

原始 Cocotb 流需要 Python 依赖、Cocotb 和 Verilator。完整 Ibex Windows 流默认使用 `D:\verilator-5.050` 和 `C:\msys64\ucrt64\bin\g++.exe`；如路径不同，请在构建脚本参数中调整。模型配置默认从项目根目录 `.env` 读取，也可通过 `LLM4DV_ENV_FILE` 指向其他文件；参考 `.env.example`，密钥文件不应提交到仓库。

## FFT 结构覆盖率分支

分支 `fft-structural-coverage` 在 `fft_structural/` 中保留上游 [fpga-fft](https://github.com/owocomm-0/fpga-fft) 快照，并提供独立的 GHDL/LCOV 结构覆盖率流。上游 RTL 和测试不改写，新增工具、命令和真实性门禁与本项目风格一致：

```powershell
python -m unittest discover -s fft_structural\tests -p "test_*.py"
python fft_structural\tools\fft_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\fft-run `
  --iterations 5 --cycles 10000
```

默认目标是 1024 点 `test_fft1024`；该流只报告 GHDL 实际产生的 Line/Branch LCOV 数据，Expression、Toggle 和 FSM 在没有专用适配器时明确标记为 `unavailable`。详见 [`fft_structural/README.md`](fft_structural/README.md)。
