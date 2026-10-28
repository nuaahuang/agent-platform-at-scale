# P3 · 推理工程

| # | 章节 | 核心问题 | 交付物 | 状态 |
|---|---|---|---|---|
| 8 | [请求链路与物理约束](08-request-journey.md) | 12 跳延迟、KV 公式、Roofline | 链路图 + 物理速查 | ✅ |
| 9 | [并行与互联](09-parallelism.md) | TP/EP/PP、集合通信、RDMA、NVLink 域 | 拓扑 + 网络清单 | ✅ |
| 10 | [引擎：PagedAttention / 连续批处理 / 前缀缓存](10-inference-engine.md) | 300GB 怎么分、碎片怎么消 | 配置 + 压测脚本 | ✅ |
| 10A | [Kernel 层](10A-kernels.md) | 算子与 kernel 的区别、五种写法、开源件地图 | 技术栈表 + 调优指标 | ✅ |
| 10B | [KV 的生命周期](10B-kv-lifecycle.md) | 分配/淘汰/抢占/卸载，为什么没有 TTL | 算法 + 调参表 | ✅ |
| 11 | [调度与 PD 分离](11-pd-disaggregation.md) | 两池怎么拆、异构 SKU、成本量化 | P:D 配比 + 四方案对比 | ✅ |
| 12 | [高级特性与压测方法论](12-advanced-and-benchmarking.md) | 投机解码/结构化输出；SLO 下怎么测真实吞吐 | 特性矩阵 + 压测报告 | ✅ |

> 迁移文件的"第 N 章"引用为 v1 旧编号，见 [../../MIGRATION.md](../../MIGRATION.md)。
