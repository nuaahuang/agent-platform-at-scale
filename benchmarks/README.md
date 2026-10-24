# benchmarks

压测脚本与报告。核心原则：**在 SLO 下测，不测峰值吞吐**。

| 指标 | 口径 |
|---|---|
| TTFT p95 | 从请求到首 token |
| TPOT p95 | 相邻 token 间隔 |
| 可持续 tok/s | TPOT p95 ≤ 50ms 时的输出速率 |
| 会话密度 | 单机在 SLO 内的并发会话数 |

计划：

- `bench_engine.py`：对 vLLM/SGLang 打固定长度分布，输出 TTFT/TPOT/吞吐曲线；
- `bench_gateway.py`：对网关打混合租户流量，验证限流与背压；
- `report/`：每次压测的机器、参数、结论。

> 反例：只报"最大吞吐 12,000 tok/s"而不说 TPOT p95 已经 300ms —— 这种数字没有意义。
