# FFT1024 覆盖率证据

本目录保存 `test_fft1024` 的 5 轮真实 GHDL mcode 结构覆盖率运行结果，以及一次确定性回放结果。

- `summary.json`：最终指标和真实性门禁
- `source_manifest.json`：RTL 文件清单、物理行数和 SHA-256
- `commands.json`：每个编译、展开、仿真和 LCOV 命令及返回码
- `iteration_*`：每轮 coverage JSON、LCOV 和仿真日志
- `replay_test_fft1024`：首轮确定性回放证据
- `report_zh.md`：中文汇总报告

`work/` 编译缓存未上传；可由覆盖率命令重新生成。
