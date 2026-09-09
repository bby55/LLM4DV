# FFT 覆盖率命令

在 `LLM4DV` 根目录执行。输出目录建议放到源码树外。

## 只检查源码清单

不需要安装 GHDL，可确认上游快照和 RTL 行数：

```powershell
python fft_structural/tools/fft_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\fft-manifest `
  --manifest-only
```

## 运行真实 GHDL 结构覆盖率

```powershell
python fft_structural/tools/fft_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\fft-run `
  --iterations 5 `
  --cycles 10000 `
  --attempts 1
```

`--iterations` 是确定性 testbench 重复次数，不是模型调用次数；`--attempts` 仅为和 LLM4DV 命令风格兼容，当前 FFT 流不调用模型。

覆盖率采集依赖 GHDL mcode 后端。LLVM 后端虽然可以运行仿真，但不会产生 `coverage-*.json`，工具会拒绝将其标记为有效覆盖率。

默认 testbench 是 `test_fft1024`（1024 点），并自动编译 `generated/fft1024_wide` 的核心文件和所需 twiddle ROM。每轮会运行完整的两帧输入，`--cycles` 对应 GHDL 的 `stop-time`（按 ns 使用）。

## 自定义范围

```powershell
python fft_structural/tools/fft_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\fft-fft4 `
  --testbench test_fft4 `
  --testbench test_fft4_serial `
  --no-generated
```

若需要显式运行 1024 点测试，可写成：

```powershell
python fft_structural/tools/fft_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\fft-1024 `
  --testbench test_fft1024 `
  --iterations 10 `
  --cycles 10000
```

如果 `ghdl` 不在 `PATH`，显式指定路径：

```powershell
python fft_structural/tools/fft_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\fft-run `
  --ghdl C:\tools\ghdl\bin\ghdl.exe
```

查看结果：

```powershell
Get-Content D:\coverage\llm4dv\fft-run\summary.json
Get-Content D:\coverage\llm4dv\fft-run\report_zh.md
```
