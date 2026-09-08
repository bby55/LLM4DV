# 测试命令与可选参数

本文档只列出仓库中当前实际存在的命令行入口。先在项目根目录执行命令，Windows PowerShell 示例使用反引号 `` ` `` 换行。

## 1. 环境和构建

安装原始 Cocotb 依赖：

```powershell
python -m pip install -r python-requirements.txt
```

构建完整 Ibex RTL 覆盖率仿真器：

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_ibex_full_coverage.ps1
```

构建脚本选项：

| 选项 | 默认值 | 作用 |
| --- | --- | --- |
| `-VerilatorRoot` | `D:\verilator-5.050` | Verilator 安装目录 |
| `-MsysRoot` | `C:\msys64` | MSYS2 安装目录 |
| `-BuildRoot` | `D:\tmp-llm4dv` | Verilator 对象文件和临时文件目录 |
| `-Clean` | 关闭 | 仅清理 `BuildRoot` 内的对象目录后再构建 |

例如：

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_ibex_full_coverage.ps1 `
  -VerilatorRoot C:\tools\verilator `
  -MsysRoot C:\msys64 `
  -BuildRoot D:\coverage-build `
  -Clean
```

## 2. 完整 Ibex RTL 结构覆盖率

这是测量完整 Ibex elaborated RTL 的主入口，输出 Line、Branch、Expression、Toggle 以及 FSM 状态/转移覆盖率：

```powershell
python tools\ibex_full_rtl_coverage.py `
  --out-dir D:\coverage\llm4dv\ibex-50 `
  --model glm-5.2 `
  --iterations 50 `
  --cycles 1200 `
  --attempts 4
```

可用选项：

| 选项 | 默认值 | 作用 |
| --- | ---: | --- |
| `--out-dir PATH` | 必填 | 本轮结果目录，建议放在源码树外 |
| `--model NAME` | `glm-5.2` | OpenAI 兼容接口使用的模型名 |
| `--iterations N` | `6` | 最多生成并验证的 LLM 迭代数 |
| `--cycles N` | `800` | 每个程序的 RTL 仿真周期上限 |
| `--attempts N` | `4` | 每轮生成程序的最大尝试次数 |
| `--simulator PATH` | `D:\tmp-llm4dv\obj_ibex_cov\Vibex_coverage_top.exe` | 完整 Ibex 仿真器路径 |
| `--seed-run PATH` | 不使用 | 先重放旧结果中的 `program.hex`；可重复传入 |

常用命令：

```powershell
# 只增加迭代次数
python tools\ibex_full_rtl_coverage.py --out-dir D:\coverage\ibex-100 --iterations 100

# 增加程序运行窗口，减少长程序被截断的概率
python tools\ibex_full_rtl_coverage.py --out-dir D:\coverage\ibex-long --iterations 20 --cycles 2000

# 构建目录自定义后，显式指定对应仿真器
python tools\ibex_full_rtl_coverage.py --out-dir D:\coverage\ibex-custom `
  --simulator D:\coverage-build\obj_ibex_cov\Vibex_coverage_top.exe

# 先复用一轮已经通过真实性门禁的程序，再继续生成新程序
python tools\ibex_full_rtl_coverage.py --out-dir D:\coverage\ibex-resume `
  --seed-run D:\coverage\ibex-50
```

每轮结果位于 `iteration_XX_*`，最终重点查看：

- `summary.json`：覆盖率、有效程序数和真实性门禁结果
- `report_zh.md`：中文报告
- `coverage.dat`：原始 Verilator 覆盖率数据库
- `program.hex`、RVFI trace、FSM trace：可复核的测试证据

## 3. Stride Detector 原生结构覆盖率

该入口使用固定的定向测试轮次，可选择是否在部分轮次调用 LLM：

```powershell
python tools\llm4dv_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\stride-directed `
  --rebuild

python tools\llm4dv_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\stride-llm `
  --rebuild `
  --use-llm `
  --llm-model glm-5.2
```

可用选项：

| 选项 | 默认值 | 作用 |
| --- | --- | --- |
| `--out-dir PATH` | 自动生成 | 输出目录 |
| `--rebuild` | 关闭 | 重新编译 Verilator 仿真器 |
| `--use-llm` | 关闭 | 在预定轮次请求 LLM 生成刺激 |
| `--llm-model NAME` | `glm-4-flash` | LLM 模型名 |

该脚本的定向轮次数量由 `directed_rounds()` 固定，不提供 `--iterations`。需要显式控制 campaign 数和每个 campaign 的迭代次数时，使用下面的入口。

## 4. 多 campaign 结构覆盖率

```powershell
python tools\llm4dv_llm_only_campaign.py `
  --out-dir D:\coverage\llm4dv\stride-campaigns `
  --model glm-5.2 `
  --campaigns 5 `
  --iterations 50 `
  --max-generation-attempts 4 `
  --rebuild
```

| 选项 | 默认值 | 作用 |
| --- | ---: | --- |
| `--out-dir PATH` | 必填 | campaign 总输出目录 |
| `--model NAME` | `glm-5.2` | 模型名 |
| `--campaigns N` | `3` | 相互独立的 campaign 数 |
| `--iterations N` | `10` | 每个 campaign 的迭代数 |
| `--max-generation-attempts N` | `4` | 每轮模型生成最大尝试数 |
| `--rebuild` | 关闭 | 重新编译仿真器 |

不同 campaign 的 coverage 不能直接替代真实性验证；脚本会分别保存结果，并记录是否达到固定 coverage universe。

## 5. 原始功能覆盖率（Cocotb）

功能覆盖率指各 DUT 原始 coverage plan 中的功能 bin，不等同于 Verilator 结构覆盖率。以 Stride Detector 为例，需要两个终端：

终端 A，启动 Cocotb/Verilator 服务端：

```powershell
cd stride_detector
make
```

终端 B，启动 LLM 功能激励客户端：

```powershell
cd stride_detector
python generate_stimulus.py
```

客户端会交互询问服务端地址，例如 `127.0.0.1:5050`。Ibex Decoder 和完整 Ibex 的原始功能流分别是：

```powershell
cd ibex_decoder
make
python generate_stimulus.py

cd ..\ibex_cpu
make
python generate_stimulus.py
```

这三个旧入口目前没有统一的 `--iterations`、`--cycles` 或 `--server` CLI 参数；`generate_stimulus.py` 内部的 `CYCLES`、`dialog_bound` 和实验模式仍是源码常量，不能在命令行中假装支持自定义。若需要可复现的结构覆盖率和可调迭代次数，优先使用第 2 节和第 4 节入口。

## 6. 查看帮助和验证结果

```powershell
python tools\ibex_full_rtl_coverage.py --help
python tools\llm4dv_structural_coverage.py --help
python tools\llm4dv_llm_only_campaign.py --help

Get-Content D:\coverage\llm4dv\ibex-50\summary.json
Get-Content D:\coverage\llm4dv\ibex-50\report_zh.md
```

结果必须以 `summary.json` 中的 `coverage_valid` 和 validity gates 为准，不能仅凭终端打印的百分比判断测试有效。
