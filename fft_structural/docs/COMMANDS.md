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
  --iterations 2 `
  --cycles 2000 `
  --attempts 1
```

`--iterations` 是确定性 testbench 重复次数，不是模型调用次数；`--attempts` 仅为和 LLM4DV 命令风格兼容，当前 FFT 流不调用模型。

## 自定义范围

```powershell
python fft_structural/tools/fft_structural_coverage.py `
  --out-dir D:\coverage\llm4dv\fft-fft4 `
  --testbench test_fft4 `
  --testbench test_fft4_serial `
  --include-generated `
  --include-axi
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
