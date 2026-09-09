# FFT 结构覆盖率验证报告

本报告只统计 GHDL 实际产生的 LCOV 数据。没有数据的覆盖类型明确标记为 unavailable，不用程序数量或静态语法推算。

## 运行信息

- 上游快照：`c64c89e`
- RTL 文件：46 个，物理行：5135
- 迭代：5；测试平台：test_fft1024
- 覆盖率有效：`True`

## 指标

| 指标 | 已覆盖 | 总数 | 百分比 |
| --- | ---: | ---: | ---: |
| line | 288 | 293 | 98.29% |
| branch | unavailable | unavailable | unavailable |
| expression | unavailable | unavailable | unavailable |
| toggle | unavailable | unavailable | unavailable |
| fsm_state | unavailable | unavailable | unavailable |
| fsm_transition | unavailable | unavailable | unavailable |

## 真实性门禁

- 编译与仿真成功：`True`
- LCOV 含 RTL 数据：`True`
- 确定性重放：`True`
- 源码清单已保存：`True`

FSM、表达式和翻转覆盖率没有被本运行器伪造；需要额外的 VHDL 覆盖率适配器时再启用。
