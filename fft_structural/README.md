# FPGA-FFT 结构覆盖率分支

本目录把 [owocomm-0/fpga-fft](https://github.com/owocomm-0/fpga-fft) 的源码和测试固定为可复核上游快照，并增加一条与 LLM4DV 一致的结构覆盖率验证流。

## 目录

```text
fft_structural/
├── upstream/       # 上游 fpga-fft 源码、generated、codegen、tests、axi-util
├── tools/           # GHDL 覆盖率运行器和 LCOV 合并器
├── tests/           # 不依赖 GHDL 的解析器/门禁回归测试
├── docs/            # 架构、命令、真实性门禁
└── LICENSE.upstream
```

上游源码未改写，新增工具和文档独立放置。`axi-util` 是上游提交 `cfcb30f` 的内容快照，不再依赖嵌套 Git 子模块。

## 快速开始

```powershell
python -m unittest discover -s fft_structural/tests -p "test_*.py"
python fft_structural/tools/fft_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\fft-run `
  --iterations 2 `
  --cycles 2000
```

当前环境若没有 GHDL，第二条命令会明确报错并且不会生成伪造覆盖率；先安装 GHDL，或用 `--manifest-only` 只检查源码清单。详细参数见 [`docs/COMMANDS.md`](docs/COMMANDS.md)，真实性规则见 [`docs/COVERAGE_WORKFLOW.md`](docs/COVERAGE_WORKFLOW.md)。

## 许可

上游工程使用 CC BY-NC-SA 2.0，详见 [`LICENSE.upstream`](LICENSE.upstream)。本目录保留上游署名和许可证；LLM4DV 其余文件继续使用仓库根目录许可证。
