# 术语表（Glossary）

> **这是索引，不是教材。** 每个词的完整推导在对应章节，这里只给"英文全称 + 一句话定义 + 一个数字锚点 + 一个常见误用"。
> 首次遇到某个词，请读对应章节；回来查的时候用这里。
> 英文全称尽量写全——读英文文档、看厂商发布会、搜论文时能直接对上。

---

## A. 规模与性能

| 术语 | 英文全称 | 一句话定义 | 数字锚点 | 常见误用 |
|---|---|---|---|---|
| **DAU** | Daily Active Users | 日活跃用户数 | 2,000 万 | 与"注册用户"混用（1 亿注册 ≠ 2000 万 DAU） |
| **轮次（turn）** | Turn / Dialogue Turn | 用户发一次、模型答一次 | 1.5 亿轮/日 | 把"会话"当"轮次"，差 5 倍 |
| **会话（session）** | Session | 一次连续对话，含多轮 | 3,000 万会话/日（5 轮/会话） | 与会话有效期混为一谈 |
| **QPS** | Queries Per Second | 每秒**新到达**的请求数 | 峰值 7,000 | 当成"并发数"，差 3.5 倍 |
| **在飞请求** | In-flight Requests | 已到达但未完成的请求 | 2.5 万（3.5 s × 7000） | 与 QPS 混用；它才决定 KV 显存 |
| **并发连接** | Concurrent Connections | 同时保持的连接数 | 62 万 | 拿它买 GPU，会多买 89 倍 |
| **Little's Law** | Little's Law（排队论） | 在飞 = 到达率 × 停留时间 | 7000 × 3.5 s = 2.45 万 | 用它算"要多少卡"，它只算并发 |
| **p50 / p95 / p99** | Percentile Latency | 分位数延迟 | TTFT p95 ≤ 800 ms | 只报平均值，掩盖长尾 |
| **峰值系数** | Peak-hour Ratio | 峰值小时占日量比例 | 12% | 抄别人家的，差 30% |
| **突发系数** | Burst Factor | 峰值小时内再放大的倍数 | 1.4 | 与峰值系数重复计算 |
| **冗余系数（headroom）** | Headroom / Capacity Buffer | 为故障与突发预留的余量 | ×1.3（业务）+ ×1.2（双 AZ） | 只乘一次，漏掉 AZ |
| **可用区（AZ）** | Availability Zone（AZ） | 独立供电与网络的机房单元 | 2 AZ | 当成"异地多活" |
| **错误预算** | Error Budget | 1 − SLO，允许失败的额度 | 99.9% → 43.2 分钟/月 | 当成"可以随便花" |
| **单元化（cell）** | Cell-based Architecture | 把用户切片到独立小集群 | 单 cell ≤ 50 万 DAU | 与"多活"混为一谈 |

## B. 推理与模型

| 术语 | 英文全称 | 一句话定义 | 数字锚点 | 常见误用 |
|---|---|---|---|---|
| **Prefill** | Prefill / Prompt Processing | 一次性算完所有输入 token | 5,000 token / 450 ms | 以为它按 token 逐个算 |
| **Decode** | Decoding / Autoregressive Generation | 逐 token 生成，每步重读权重 | 25 ms/token | 以为它算力受限（其实是带宽） |
| **TTFT** | Time To First Token | 首 token 延迟 = 排队 + prefill + 网络 | ≤ 800 ms | 只算 prefill，漏掉排队 |
| **TPOT** | Time Per Output Token | 相邻 token 间隔 | ≤ 50 ms | 与"端到端 ÷ 字数"混用 |
| **KV Cache** | Key-Value Cache | 已算过的注意力键值，避免重算 | 120 KB/token（H800 TP8） | 按模型参数量估显存 |
| **PagedAttention** | Paged Attention | 把 KV 切成固定块按需分配 | 块内浪费 <2% | 以为它省算力（它省显存） |
| **连续批处理** | Continuous Batching | 每步重组 batch，完成的退出、新来的加入 | decode 阶段天然等长 | 以为 prefill 也能这样 |
| **前缀缓存** | Prefix Caching | 相同前缀只算一次，命中直接复用 KV | 命中率 70% | 以为能省 decode |
| **RadixAttention** | Radix Attention | 用基数树管理前缀缓存 | 系统提示词命中率 >90% | 以为跨租户可共享 |
| **Chunked Prefill** | Chunked Prefill | 把长 prefill 切片，与 decode 混跑 | 切片 512 token | 切太小，调度开销吃掉收益 |
| **PD 分离** | Prefill-Decode Disaggregation | Prefill 与 Decode 跑在不同卡池 | P:D ≈ 909:700 | 以为任何规模都值得拆 |
| **MFU** | Model FLOPs Utilization | 实际算力 ÷ 峰值算力 | 35% | 拿 100% 算容量 |
| **MBU** | Memory Bandwidth Utilization | 实际带宽 ÷ 峰值带宽 | decode 60%–70% | 与 MFU 混用 |
| **投机解码** | Speculative Decoding | 小模型草稿 + 大模型校验 | 接受率 60%–80%，提速 1.5–2× | 以为无损（分布会偏） |
| **MoE / 激活参数** | Mixture of Experts | 总参数大、每 token 只走部分专家 | 200B 总 / 30B 激活 | 用总参数算算力 |
| **SWA** | Sliding Window Attention | 只对最近 W 个 token 做注意力 | 深求保留 140 token 重算 | 以为能省 decode（它省 prefill） |
| **DSpark** | Confidence-Scheduled Speculative Decoding | 半自回归草稿 + 置信度调度验证 | 验证 token −30%，吞吐 +4%–7% | 以为买的是吞吐（其实买交互性） |
| **EPLB** | Expert Parallel Load Balancer | 按热度重排专家，压 all-to-all 热点 | 专家负载在线均衡 | 与 TP 混为一谈 |
| **算子** | Operator | 数学语义上的一个操作 | matmul / softmax / topk | 与 kernel 混为一谈 |
| **Kernel** | GPU Kernel | 在 GPU 上并行执行的一个函数 | 单 step 约 750 次启动 | 以为它是"库"（它是一个函数） |
| **CUDA Graph** | CUDA Graph | 把 kernel 序列录制为图，一次提交 | 启动开销趋近 0 | 以为能处理变长 batch（形状须固定） |
| **Mega MoE** | Fused MoE with Overlapped Communication | 通信与计算融进一个 kernel | NVLink 与 tensor core 重叠 | 以为是调度优化（其实是 kernel 融合） |
| **TileLang** | TileLang | 面向 tile 的 GPU DSL | CUDA 与昇腾双后端 | 与 Triton 混为一谈 |
| **Roofline** | Roofline Model | 算力与带宽的瓶颈分界 | 脊点 590 FLOP/Byte | 以为 GPU 永远算力受限 |

## C. 平台与工程

| 术语 | 英文全称 | 一句话定义 | 数字锚点 | 常见误用 |
|---|---|---|---|---|
| **Harness** | Agent Harness | 模型之外、让 Agent 跑起来的那层工程 | 本书第 18–25 章 | 与"框架"混为一谈 |
| **Agent Runtime** | Agent Runtime（Durable Execution） | 把一次任务跑成可恢复状态机 | 单任务 ≤ 200 步 | 用无状态函数实现 |
| **事件溯源** | Event Sourcing | 只存事件，状态由重放得出 | 会话事件 ≤ 500 条 | 存快照当唯一真相 |
| **上下文窗口** | Context Window | 一次能塞进模型的 token 上限 | 32K / 128K | 以为窗口大就能塞满 |
| **L0–L7 分层** | Context Layering（L0–L7） | 上下文的八层组装 | L0 系统提示 ≤ 800 token | 把所有内容平铺 |
| **工具调用** | Tool Calling | 模型通过协议调用外部能力 | 单轮 ≤ 8 次工具 | 让模型直接执行代码 |
| **RAG** | Retrieval-Augmented Generation | 检索后拼进上下文 | Top-8，召回率 >85% | 以为能替代微调 |
| **背压** | Backpressure | 下游扛不住时向上游要减速 | deadline 逐跳递减 | 用无界队列"缓冲" |
| **熔断** | Circuit Breaking | 连续失败后快速失败，不再打下游 | 5 次失败 / 10 s | 与限流混用 |
| **降级** | Graceful Degradation | 主路径不可用时走次优路径 | 降级率 <5% | 降级后不监控 |
| **限流** | Rate Limiting | 主动丢弃超出配额的请求 | 令牌桶 3 QPS/用户 | 只在网关限，不管下游 |
| **幂等** | Idempotency | 同一请求重放结果不变 | 幂等键 = 会话+轮次 | 靠"应该不会重复" |
| **批不变性** | Batch Invariance | 换 batch 组合结果不变 | 评测与回放才可复现 | 以为只是"随机种子"问题 |
| **RPO / RTO** | Recovery Point / Time Objective | 能丢多少数据 / 多久恢复 | RPO ≤ 5 min，RTO ≤ 30 min | 只做备份不做恢复演练 |

## D. 成本与运营

| 术语 | 英文全称 | 一句话定义 | 数字锚点 | 常见误用 |
|---|---|---|---|---|
| **capex / opex** | Capital / Operating Expenditure | 资本支出 / 运营支出 | 单节点 120 万 / 年运营 11.1 万 | 只算 capex 不算电与机位 |
| **折旧** | Depreciation | 硬件成本按年限摊 | 3 年，40 万/节点/年 | 按 5 年摊，低估成本 40% |
| **PUE** | Power Usage Effectiveness | 总用电 ÷ IT 用电 | 1.25 | 忽略不计（实际占电费 25%） |
| **单轮成本** | Cost per Turn | 总成本 ÷ 总轮次 | 0.52 分/轮 | 用"每百万 token"代替 |
| **口径（caliber）** | Measurement Basis | 分母的定义 | 总 token 0.97 元/M；输出 token 14.8 元/M | 对内对外用同一口径 |
| **毛利率** | Gross Margin | (收入 − 成本) ÷ 收入 | 48%（2000 万 DAU） | 用规模化后的毛利算小规模 |

---

## 维护规则

1. **新词先写进章节**，被引用第二次时才进本表；
2. 每条必须有"数字锚点"，没有锚点的词说明还没想清楚；
3. "常见误用"一栏不许空——空着就说明这个术语不必要单列。

> 本书只做模型工程与应用，不重讲预训练、并行策略与 RL 理论；那些请读 [diy-llm](https://github.com/datawhalechina/diy-llm)。
