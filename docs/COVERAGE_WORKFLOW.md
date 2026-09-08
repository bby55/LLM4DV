# 结构覆盖率工作流

## 指标

| 指标 | 采集来源 | 统计对象 |
| --- | --- | --- |
| Line | Verilator `coverage.dat` | 实际 elaborated RTL 行/基本块 |
| Branch | Verilator `coverage.dat` | 条件分支的 true/false 方向 |
| Expression | Verilator `coverage.dat` | 表达式覆盖点 |
| Toggle | Verilator `coverage.dat` | 信号 0→1/1→0 翻转方向 |
| FSM state | wrapper/harness FSM trace | 已观测的合法状态编码 |
| FSM transition | wrapper/harness FSM trace | 已观测的合法状态弧 |

Line、Branch、Expression、Toggle 使用同一次 RTL 仿真的原生 Verilator 数据库。FSM 指标使用 trace 中的合法状态/弧集合，分母固定写入结果 JSON，避免把“观察到的任意值”误报为合法状态。

## 有效性门禁

一条程序只有在以下条件全部满足时才进入聚合覆盖率：

1. 机器码编译成功并完成仿真。
2. RVFI retirement trace 非空且格式有效。
3. 仿真没有超出周期上限、异常终止或缺失 trace。
4. 每个状态和状态转移属于预先声明的合法集合。
5. 重复回放得到相同的覆盖率摘要和 FSM 证据。
6. 改变程序输入会改变对应 trace，证明测试不是固定输出或空跑。

报告必须同时保存有效程序数、coverage 分子/分母、门禁计数和原始数据库路径。失败程序可以保留用于调试，但不能计入分子。

## Adaptive probes

完整 Ibex 流对少数外部事件使用有界场景选择：第一次有效尝试优先验证 `irq_first_fetch`，随后有限次数验证 `debug_flush`，之后在普通、非法指令、异常、IRQ 和调试场景间轮换。每个 probe 都仍然要经过同一套 RVFI/FSM/确定性门禁；probe 名称不会直接增加覆盖率。

## 如何判断结果真实

- 覆盖率分母必须来自实际编译的 RTL，而不是手工填写或从旧报告复制。
- 至少一次 deterministic replay 必须逐项一致。
- 至少一次输入变异必须导致 RVFI 或 FSM trace 发生变化。
- FSM state/transition 的证据必须能在原始 trace 中定位到周期和状态编码。
- 不能把模型生成次数、请求次数或历史日志文件数当作有效测试数。

## 代码量增加后的解读

新增 RTL 会扩大分母，尤其是未被当前程序路径触达的错误处理、调试、乘除法和参数化逻辑。因此百分比下降本身并不说明测试退化。应并列查看分子、分母、有效程序数、未覆盖点分类和新增模块命中情况；只有在固定分母或相同版本的对比中，百分比差异才可直接解释为测试效果变化。

