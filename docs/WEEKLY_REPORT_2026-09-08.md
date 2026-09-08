# LLM4DV 项目整改与完整 RTL 覆盖率周报

报告周期：2026-09-01 至 2026-09-08

## 一、本周目标

1. 清除工作区内旧 FPGA-FFT、FFT-FLAME 和相关临时产物，只保留 LLM4DV 主项目及其验证依赖。
2. 解除 LLM4DV 对 FLAME `.env` 的隐式依赖，整理源码、历史数据和输出目录。
3. 对从小型 RTL 扩展到完整 Ibex RTL 后的代码量与覆盖率变化作真实、可复核的说明。

## 二、框架整改结果

- 主项目由 `third_party/ml4dv-main` 提升到 `D:\Soft research\LLM4DV`。
- 原始实验统一归档到 `archive/experiment_logs`，模型历史统一归档到 `archive/model_results`。
- 删除 `fpga-fft-master`、`fft-llm-verify`、FLAME 中的 FFT 扩展、GHDL/NVC FFT 工具、FFT 输出以及可重建仿真对象。
- API 配置改为读取 LLM4DV 根目录 `.env`，支持 `LLM4DV_ENV_FILE` 覆盖；密钥文件被 `.gitignore` 排除。
- 完整 Ibex 流新增 `--simulator`，自定义 `-BuildRoot` 后可以显式选择对应仿真器。
- 默认结果根目录改为 `LLM4DV` 同级的 `outputs`，不再依赖项目位于特定嵌套层级。

## 三、代码量变化

代码量采用逐文件物理行数统计，不使用压缩包大小或覆盖率点数量代替源码行数。

| 范围 | 文件/条目数 | 物理行数 | 非空行数 |
| --- | ---: | ---: | ---: |
| 小型 Stride Detector RTL | 1 | 151 | 121 |
| 完整 Ibex `src` RTL 树 | 121 | 27,985 | 24,575 |
| 完整 Ibex Makefile 实际构建清单 | 106 | 25,476 | 22,317 |
| 当前项目全部 SystemVerilog `.sv` | 107 | 29,565 | 25,995 |
| 当前项目 Python 验证代码 | 45 | 8,489 | 7,308 |
| C++ harness | 2 | 378 | 341 |

实际构建口径从 151 行扩大到 25,476 行，约为原来的 168.72 倍。因此覆盖率分母从单一小模块扩展到取指、译码、执行、CSR、异常、调试、访存和乘除法等完整处理器路径，百分比显著降低是预期现象。

## 四、完整 RTL 真实覆盖率

### 原始 50 轮生成运行

结果来源：`outputs/ibex_full_rtl_fsm_llm_50iter_20260901/summary.json`。

- `coverage_valid = true`
- 51 条汇总记录包含 1 个 baseline 和 50 个迭代槽位。
- 实际模型调用 43 次；不能表述为 50 次都成功由模型生成。
- Line：490/1,130，43.36%
- Branch：518/828，62.56%
- Expression：929/1,364，68.11%
- Toggle：25,846/43,306，59.68%
- FSM state：28/28，100%
- FSM transition：39/41，95.12%

### Adaptive 50 程序重放

结果来源：`outputs/ibex_adaptive_bounded_50_replay_20260907_v2/summary.json`。

- `coverage_valid = true`
- 本次是 50 个既有有效程序的确定性重放，模型调用为 0；不能称为新增 50 次 LLM 生成。
- Line：490/1,130，43.36%
- Branch：521/828，62.92%
- Expression：931/1,364，68.26%
- Toggle：25,851/43,306，59.69%
- FSM state：28/28，100%
- FSM transition：41/41，100%

Adaptive 外部事件场景补齐了 2 条 FSM 转移，并增加 3 个 Branch、2 个 Expression、5 个 Toggle 覆盖点；Line 分子未变化。所有数字均来自保存的 `summary.json` 和原生 `coverage.dat`，不是模型估计值。

## 五、覆盖率下降原因与优化判断

1. 完整 Ibex 的结构分母远大于小 DUT，且包含软件程序难以单独触发的外部中断、调试、异常及参数化路径。
2. 普通 RV32IM 程序会反复命中主流水线，但对低频控制路径的边际收益很快下降。
3. FSM 覆盖已闭合不代表 Line/Branch/Toggle 闭合；这些指标的分母和测试目标不同。
4. 本周优化使用有界 `irq_first_fetch` 和 `debug_flush` probe，只在缺失目标弧时介入，随后恢复普通场景轮换，避免长期压制常规程序路径。
5. 后续优化应按未覆盖文件和条件分类生成测试，而不是单纯增加相同类型的随机轮数。

## 六、真实性判断

只有编译成功、仿真健康、RVFI 非空、FSM 编码/转移合法且通过确定性回放与 trace 变异检查的程序才计入聚合覆盖率。失败、截断或重复程序保留调试信息但不增加分子。报告必须同时给出分子、分母、有效程序数和模型调用数。

## 七、下周建议

- 针对未覆盖最多的 RTL 文件建立模块级目标队列，分别处理异常、CSR、调试和访存握手。
- 固定 RTL 版本和 coverage universe 后再比较不同 prompt/campaign，避免跨版本百分比误判。
- 为原始 Cocotb 功能覆盖率客户端补充统一 CLI，使 `iterations`、`cycles`、`server` 和随机种子可复现配置。

