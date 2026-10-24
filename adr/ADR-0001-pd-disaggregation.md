# ADR-0001 采用 PD 分离而非混部

- 状态：已接受
- 决策者：平台架构组

## 背景

混部时 prefill 与 decode 争抢同一批 GPU：一个长 prompt 的 prefill 会把同卡上
其他会话的 TPOT 从 25ms 拉到 200ms 以上。业务 SLO 要求 TPOT p95 ≤ 50ms。

## 决策

Prefill 与 Decode 拆成两个资源池，按 909 : 700（卡）配置，KV 通过 RDMA 传输。

## 备选方案

| 方案 | 代价 |
|---|---|
| 混部 + 超配 | 卡数 +20%，TPOT p95 仍会尖刺 |
| 混部 + chunked prefill | TPOT 稳定，但 TTFT 变差（约 667ms） |
| PD 分离 | 多一次 KV 传输（20ms）与一套调度器 |

## 后果

- 好：TPOT p95 25ms、TTFT 220ms，卡数反而少 16.7%；
- 坏：多一跳网络依赖；无 RDMA 时收益归零；两池比例需随业务漂移调整；
- 监控：KV 传输 P99、P 池队列深度、D 池 KV 水位。

## 推翻条件

1. 集群 < 30 节点（碎片化吃掉收益）；
2. 没有 RDMA / GPUDirect；
3. 平均输出 < 150 token（传输开销占比过高）。

数据：[`labs/pd_cost_lab.py`](../labs/pd_cost_lab.py)
