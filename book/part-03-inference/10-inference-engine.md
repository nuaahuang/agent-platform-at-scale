# 第 10 章 · 推理引擎：PagedAttention 与连续批处理

> 迁移自 v1 `ch03-inference-engine.md`。文中引用"第 N 章"为 **v1 旧编号**，与 v3 的对应关系见 [../../MIGRATION.md](../../MIGRATION.md)。


> **一句话结论**：推理引擎不负责"算"（kernel 负责算），它负责一件操作系统做的事——
> **把有限的 HBM 安全、高效、不浪费地分配给成百上千个长短不一的会话。**
> 三大技术：连续批处理、PagedAttention、前缀缓存。

---

## 1. 问题：没有引擎会怎样

回到第 02 章的约束：

- 一块节点 300GB KV 预算，一个 8K 会话要 0.96GB；
- 会话长短差异巨大（200–8000 token）；
- 请求随机到达、随时结束；
- decode 是带宽游戏，**GPU 空转一秒就是钱烧掉一秒**。

如果让每个请求独占一块显存、自己跑自己的，会出现两个浪费：

1. **padding 浪费**：一个 batch 要等长，5000 token 的请求和 200 token 的请求凑一批，短的补 4800 个空位——算力浪费在空气上；
2. **全队等最慢**：batch 里 31 个请求生成完了，1 个还没结束，31 个位置全陪着等。

推理引擎就是来消灭这两个浪费的。

### 1.1 为什么"等长"是硬性要求

根本原因：**GPU 只会算稠密矩形矩阵，不接受参差不齐的行。**

一次矩阵乘 `X·W`，输入 X 必须是 `[B, T, d]` 的完整长方体：

```
batch:  [请求A 5000 token]
        [请求B  200 token]      ← 这一行只有 200，剩下 4800 个位置是什么？

GPU 要求：每行都是 5000 → B 的请求后面补 4800 个占位符（padding）
        [AAAAAAAAAAAAAAAAAAAAAA]
        [BB                   PPP]   P = padding
        [CCCC                 PPP]
```

三个技术原因：

1. **显存是连续的矩形块**：kernel 按固定步长寻址，第 2 行第 201 个位置必须存在，否则索引错乱；
2. **SIMT 并行要求整齐**：上万个线程同拍执行同一条指令（SIMD），线程 1 算第 300 个 token、线程 2 却没有第 300 个 token，无法对齐执行；
3. **padding 不只是浪费算力**：注意力里还要用 **attention mask** 屏蔽这些假 token，防止它们污染 softmax——多一步逻辑。

**浪费有多大**：5000 和 200 凑一批，有效算力利用率 = (5000+200) / (5000×2) = 52%，接近一半烧在空气上。

工程上的四种对策：

| 对策 | 做法 | 局限 |
|---|---|---|
| **Padding + mask** | 补到本批最长 | 长短混批时浪费 |
| **Bucketing（分桶）** | 相近长度凑一批（256 桶、512 桶…） | 桶有碎片，prefill 常用 |
| **Varlen / FlashAttention** | 拼成一维序列 + 偏移量，物理紧凑逻辑分条 | 需要专用 kernel 支持 |
| **连续批处理（本章 §2）** | decode 每 step 重组，且每个请求每步只出 1 token——decode 阶段天然等长（都是 1），padding 问题消失 | 引擎核心技术 |

注意区分：**decode 每个请求每步都只处理 1 个 token，batch 内天然等长**，不需要 padding；
真正的等长难题在 **prefill**（输入长短不一），所以才有 bucketing、chunked prefill 和 varlen kernel。

---

## 2. 连续批处理（Continuous Batching）

**规则只有一条：每个 decode step（约 25ms）结束后，重组一次 batch。**

> **25ms 哪来的？** 它就是 TPOT（每 token 生成间隔），由第 02 章的带宽账决定：
>
> ```
> 每步耗时 = (激活权重 30GB + B × 单会话KV 0.96GB) ÷ 3.35 TB/s
>
> B=32  → 60.7GB ÷ 3.35TB/s ≈ 18ms
> B=64  → 91.4GB ÷ 3.35TB/s ≈ 27ms   ← 我们的典型工作点
> B=128 → 152.9GB ÷ 3.35TB/s ≈ 46ms
> ```
>
> 即"约 25ms"= B≈64、8K 上下文时的每步访存时间（理论值，真实乘效率系数）。
> **它不是常数，而是一个被选中的工作点**：SLO 定 TPOT ≤ 50ms，
> 调度器就把 batch 顶到满足该红线的最大值（约 120–130），25ms 是留了余量的典型值。
> 用户体验上：25ms/token ≈ 40 tok/s ≈ 每秒约 60 个汉字，快于人眼阅读。
> batch 越大 step 越慢（字蹦得越慢），所以"聚合吞吐 vs TPOT"是调度器的核心权衡。

```
step 1:  [A 已到][B 已到][C 已到]            → 算 A B C
step 2:  [A][B][C][D 刚到]                   → 算 A B C D   ← D 直接插队
step 3:  [A][C][D]                           → B 完成了，移出
step 4:  [A][C][D][E][F]                     → E F 补进来
```

对比：

| | 静态批处理 | 连续批处理 |
|---|---|---|
| 新请求何时进 | 整批结束后 | **下一个 step**（等 25ms） |
| 完成的请求 | 占着位置直到全队结束 | 立刻释放，新人补位 |
| GPU 利用率 | 受最长请求拖累 | 接近满载 |
| 谁实现 | 老框架 | vLLM/SGLang/TGI 都这么做 |

**直觉**：静态批处理像"整团进出的旅行团"，连续批处理像"每站都上下客的公交车"。

> 注意：prefill 和 decode 的计算形状不同，能不能混在一个 step 里？能，但要切小块——
> 见第 5 节 Chunked Prefill。

---

## 3. KV 怎么分配：先看实验

三个策略，300GB 预算、2000 个长短不一的会话（[`paging_lab.py`](../../labs/paging_lab.py)）：

| 策略 | 并发会话 | 浪费率 | 问题 |
|---|---|---|---|
| A 每个会话预留 8192 长度 | **298** | **73.7%** | 怕它变长，先把位置占满 |
| B 按需连续分配（理想） | 1,153 | 0% | 真实中做不到（见下） |
| C **分页，block=16 token** | **1,151** | **0.4%** | PagedAttention 的做法 |

**为什么 B 只在纸上成立**：

- 显存是"一块连续空间"，会话一个挨一个放。会话结束留下空洞——**外部碎片**；
- 会话变长，后面没空间，得**整体搬移**（memcpy），搬一次几 GB，线上直接卡顿；
- 共享前缀（系统提示）要复制 KV，没法只存一份。

**操作系统在 1960 年代就解决过这个问题**：虚拟内存分页。vLLM 把它原封不动搬进来。

---

## 4. PagedAttention：给 KV 也装一套虚拟内存

### 4.1 核心结构

```
逻辑地址：会话 A 的 KV 序列 → [block 0][block 1][block 2]...（连续的逻辑编号）
                                      │        │        │
物理地址：block table 映射 → 物理块 #7、#23、#2（显存里可以不连续）
```

- 每个物理 **block 固定 16 个 token 的 KV**（vLLM 默认 16，SGLang 类似）；
- block table = 进程页表；KV 按需申请 block，**写满一块才申请下一块**；
- 会话结束，block 归还池子，谁都能用——**无外部碎片**；
- 浪费只有"最后一块没写满"，每会话 < 16 token，实测 0.4%（内部碎片）。

### 4.2 一个 block 多大

```
120 KB/token × 16 = 约 1.9 MB/block
一个 8K 会话 = 512 blocks ≈ 0.96 GB
```

### 4.3 真正的红利：block 可以共享

同一会话的两个分支（或 beam search、并行采样、fork），block table 指向**同一批物理块**：

```
分支 X: [7][23][2]→[新块 9]
分支 Y: [7][23][2]→[新块 10]
              ↑ 前三个块共享，引用计数 = 2
```

谁要写共享块，先复制再改——**Copy-on-Write**。系统提示这种公共前缀，所有会话也只存一份。

---

## 5. Chunked Prefill：让两种负载共乘一班车

问题：一个 5000 token 的 prefill 要独占 GPU 约 150–400ms，期间在 decode 的请求全部等住，TPOT 出现尖刺。

解法：**把 prefill 切成 chunk**（比如每块 512–2048 token），和 decode 请求混进同一个 step：

```
step: [decode A][decode B][prefill C 的第 1 块 512 token]
step: [decode A][decode B][prefill C 的第 2 块]
...
```

- decode 请求每 step 都在推进，TPOT 平滑；
- prefill 请求多花几个 step 完成，TTFT 略增——用 TTFT 换 TPOT 稳定；
- 这个**配比是调度参数**（如 vLLM 的 `--max-num-batched-tokens`、SGLang 的 chunk 策略），
  生产调优的核心旋钮之一。

PD 分离是另一个极端答案：干脆分池（第 04 章细讲）。Chunked prefill 是"不分离时的最优解"。

> 如果模型用的是**滑动窗口注意力**（SWA），还有第三种答案：不切 prefill，而是**只重放最后一段**。
> 见 §9 的 SWA bounded replay——它能把 prefill 计算量近乎砍半，但前提是注意力窗口有限。

---

## 6. 前缀缓存：block 哈希

- 每个 block 的内容 = f(前一个 block 哈希, 本 block token)；
- 算出哈希，去全局哈希表查：**命中 → block table 直接指过去，prefill 跳过这些 token**；
- 平台 Agent 的系统提示 + 工具 Schema（约 2.5K token）每会话相同——
  命中率 70% 就是这么来的，对应容量模型里 prefill 卡数少 60%。

| | vLLM | SGLang |
|---|---|---|
| 结构 | block 哈希表（块级复用） | **RadixAttention 基数树**（token 级复用，命中率更高） |
| 适合 | 通用 | 多轮 Agent、长公共前缀 |

详见 [vllm-vs-sglang.md](../vllm-vs-sglang.md)。

### 6.1 RadixAttention 原理：把所有请求存成一棵前缀树

**核心思想**：每个请求的 token 序列是树上的一条路径；**公共前缀只存一份 KV**，
节点可以按 token 粒度分叉（不是按 block）。

```
                    root
                     │
              系统提示 40 token（所有用户共享一份 KV）
            ┌────────┼────────┐
         用户1历史   用户2历史  用户3历史      ← 第一轮在这里分叉
            │         │         │
        第2轮新问题  第2轮…      …            ← 同用户多轮是路径延长
```

新请求进来，沿树找**最长匹配前缀**：命中的 token 跳过 prefill，只算后缀并把新枝挂上树。
演示 [`radix_lab.py`](../../labs/radix_lab.py)（3 用户 × 3 轮）：

```
朴素 KV 总量：720 份 → 基数树实际存：220 份，节省 69%
命中率：同用户第 2 轮起 75–80%，跨用户首请求也有 67%（共享系统提示）
```

**和 vLLM block 哈希的区别**：

| | block 哈希 | RadixAttention 基数树 |
|---|---|---|
| 复用粒度 | 整个 block（16 token） | **单个 token**（边可以任意长） |
| 部分命中 | 一个 token 不同，整块作废 | 从分叉点精确复用 |
| 结构 | 扁平哈希表 | 树，天然表达前缀关系 |
| 淘汰 | 块 LRU | 从叶子 LRU 回收（引用计数） |
| 典型强项 | 通用场景 | Agent 多轮、few-shot 模板、树搜索 |

代价：树结构在 CPU 侧维护，高 QPS 下锁竞争和树管理有开销；
收益大小取决于**前缀到底有多公共**——零公共前缀的随机请求，两者都省不了。

**和 PagedAttention 的关系（别混）**：PagedAttention 管"KV 在显存里怎么摆"（分页），
RadixAttention 管"哪段 KV 可以复用"（前缀树）；SGLang 两者都用——
**树决定复用关系，分页决定物理存放。**

### 6.2 SGLang 的分页怎么设计，如何与基数树串起来

SGLang 的内存层是**三级结构**（自顶向下：复用关系 → 逻辑索引 → 物理页）：

```
① RadixCache（基数树）
   管"哪些 token 序列互为前缀"，树节点持有一段 token 位置索引
        │
        ▼
② ReqToTokenPool（请求→token 索引）+ TokenToKVPool（token 索引→物理槽）
   后者就是页表：逻辑 token 位置 → 物理 page 内偏移
        │
        ▼
③ 预分配的分页 KV 大张量（扁平 pool，16 token/page）
   FlashInfer/Triton 的 paged kernel 按页表间接寻址读取
```

**一次请求的完整生命周期**：

```
1. match_prefix(seq)
   沿树走，命中 2,560 token → 返回这些 token 的逻辑索引

2. 建页表（零拷贝！）
   ReqToToken 中，前 2,560 项直接指向树上的物理页；
   后缀新 token 才从空闲池申请新页

3. prefill 后缀
   paged kernel 按页表读，命中页只读不写，新页写入

4. decode 循环
   每生成 1 token 申请 1 个槽，注意力按页表读全部物理页（可不连续）

5. 请求结束 → insert/commit
   新后缀挂成树的新枝，树节点引用这些物理页（引用计数 +1）

6. 显存紧张 → LRU 淘汰
   从叶子端摘枝，把引用计数=0 的页还回空闲池；
   正在被请求引用的节点（ref>0）受保护，不能淘汰
```

**三个关键设计**：

| 设计 | 说明 |
|---|---|
| **KV 不可变** | 一个 token 的 KV 算出来后永不改变（权重固定即确定），所以共享是**只读共享**，分叉就申请新页——主路径不需要 copy-on-write |
| **两级间接寻址** | 树只管 token 序列的复用关系，不关心物理位置；物理位置由页表决定。树重组/换页互不影响 |
| **层级缓存 HiCache** | 被淘汰的热前缀可下沉到 CPU 内存/SSD，下次命中先搬回（类似 OS 的内存↔磁盘换页） |

**与 vLLM 对照**：

| | vLLM | SGLang |
|---|---|---|
| 复用结构 | block 内容哈希表 | 基数树（token 精确前缀，边可任意长） |
| 物理层 | 分页 block + block table | 分页 pool + TokenToKVPool 页表 |
| 共享粒度 | block（16 tok） | token（逻辑层），物理仍按页 |
| 淘汰保护 | 引用计数 | 引用计数 + 叶子 LRU + HiCache |

一句话：**树是"复用地图"，页表是"物理地址簿"，分页大张量是"仓库"——三者用一次 match→建页表→算后缀→提交挂枝的流程串起来。**

### 6.3 树的作用域：什么时候有几棵树

**答案：一个引擎实例（一个模型）一棵逻辑树，不是全局一棵。**

| 边界 | 树的情况 | 原因 |
|---|---|---|
| 不同模型 | **各自独立的树** | 权重不同，同一段 token 的 KV 也不同，复用无意义 |
| 同模型 TP=8 | **一棵逻辑树**（树在 CPU 侧的调度器里），KV 物理页分片在 8 张卡上 | 匹配只看 token 序列，与卡无关；页表再把索引翻译到各卡分片 |
| 不同实例/Pod | **各有各的树，默认不共享** | 树是进程内内存结构；这正是要做 KV-aware 路由的原因 |
| 多租户 | 物理上一棵树，前缀在根处自然分叉；强隔离要求则按租户拆实例 | KV 不可变且需精确匹配，跨租户不会"误中"；但合规场景物理隔离 |
| 模型版本更新 | **整棵树作废重建** | 权重变了，全部旧 KV 失效（缓存版本号必须和模型版本绑定） |

**跨实例想共享怎么办**（第 04/11 章展开）：

1. **路由层亲和**：同系统提示/同租户的请求固定路由到同一实例——把"共享"问题变成"路由"问题；
2. **分布式 KV 传输**：Mooncake / NIXL / LMCache 这类 connector 把前缀 KV 在实例间搬运，
   SGLang 的 HiCache 还可以把被淘汰的前缀下沉到 CPU 内存/磁盘；
3. **PD 分离场景**：prefill 实例树上的 KV 传给 decode 实例（两实例各有树，传的是 KV 数据本身）。

> 树节点只存 token 索引和引用关系（CPU 内存，几 MB 级），真正的 KV 在 GPU pool。
> 所以"多棵树"很便宜，跨机"共享树"很贵——这决定了业界选择"本地树 + 路由亲和 + 显式传输"，
> 而不是做一棵全局分布式树。

### 6.4 一个实例对应几张卡

**1 个引擎实例 = 1 个并行组 = TP × PP 张卡**；扩容靠多副本（数据并行），不靠做大单实例。

| 模型 | 权重 | 单实例卡数 | 约束 |
|---|---|---|---|
| 8B FP16 | 16GB | 1 卡（可一卡多实例/MIG） | |
| 70B FP16 | 140GB | TP=2–4 | |
| **200B MoE FP8（本项目）** | 200GB | **TP=8（一台 H800）** | ≥3 卡装权重，NVLink 域 8 卡 |
| DeepSeek 671B FP8 | 670GB | **16–32 卡**（TP=8 × EP 跨机 RDMA） | MoE 专家并行必须跨机 |

**卡数下限 = 权重装得下；上限 = 通信不拖后腿**：
H800 NVLink 域只有 8 卡（跨机退到 IB）；GB200 NVL72 才把域扩到 72 卡。

不做超大实例的三个原因：all-reduce ring 随卡数变长；故障域大（重载权重几分钟）；
弹性粒度粗（闲 10% 就是几十张卡白烧）。所以线上是"几十个 8 卡副本 + 前缀亲和路由"，
每副本各有自己的基数树——**扩容 = 复制"8 卡 + 一棵树"。**

---

## 7. 一个 step 里，调度器在决策什么

每 25ms 一次循环：

```
① 看新队列：有没有等待的 prefill？
② 看显存：还剩多少空闲 block？（不够 → 抢占：换出或丢弃最旧请求的 KV）
③ 组 batch：running 的 decode + 能塞下的 prefill chunk
④ 执行 kernel（attention 时按 block table 取 KV）
⑤ 回收：完成的请求释放 block，引用计数减一
⑥ 统计：waiting/running、KV 使用率、抢占次数（全部暴露成指标）
```

**你在第 01 章看到的 `vllm:num_requests_waiting` 和 `gpu_cache_usage_perc`，就是 ②③ 的输出。**
KEDA 按这两个数扩缩容——调度器的内部状态直接决定了基础设施的行为。

---

## 8. 三个误区

**误区 1：vLLM 的"连续批处理"靠 GPU 多线程。**
错。GPU 本身就是批量并行；连续批处理是**调度器在 CPU 侧每步重组请求集合**，是软件调度创新。

**误区 2：PagedAttention 是一种新的注意力算法（数学不同）。**
错。注意力的数学完全不变，变的是 **KV 的内存管理方式**——访存地址从"连续读"变成"按 block table 读"。

**误区 3：开了前缀缓存就不花钱。**
缓存只省 prefill 的算力；命中的 KV 照样占显存、decode 照样读。**缓存命中率是容量和显存的权衡，不是免费午餐。**

---

## 9. 业界对照：DeepInfer 如何解同一组问题

> **本节是外部事实，全部带出处**，与本书的教学案例（200B MoE / H800 / 32K）不是同一个系统。
> 读法：先看它和你已经理解的机制**哪里一样、哪里不一样**，不要直接把它的数字搬进我们的容量模型。

2026 年 10 月 20 日，DeepSeek 把支撑其生产服务的推理框架 **DeepInfer** 开源（Apache-2.0，
[GitHub](https://github.com/deepseek-ai/DeepInfer)、[PyPI](https://pypi.org/project/deepinfer/)）。
它只支持一个模型：**DeepSeek-V4.1-Flash**（1M 上下文，MIT 许可，
[模型卡](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)）。

它对本章八个机制的答案，横向对比如下：

| 本章机制 | DeepInfer 的实现 | 关键差异 / 代价 |
|---|---|---|
| 连续批处理 | **异步调度**：CPU 组批与 GPU 执行重叠，默认开启 | 消除"组批气泡"；代价是调度状态机复杂度上升 |
| Chunked Prefill | **SWA bounded replay**：prefill 只重放有界后缀，默认保留 140 token | 官方称 prefill 计算量近乎减半；**只在滑动窗口注意力下成立** |
| 前缀缓存 | 未开源（KV 管理与内部基础设施耦合） | 说明"缓存"不是引擎单体能做干净的 |
| 投机解码 | **DSpark**：草稿模型一次提 5 个 token + 置信度调度验证 | 每步验证 token 数 −30%，吞吐 +4%–7% |
| 专家并行 | MoE EP + **EPLB**（专家负载均衡） | 直接针对 all-to-all 热点 |
| 可复现性 | **batch-invariant** + 确定性采样（仅 NVIDIA） | 换 batch 组合结果不变，评测与回放才可比 |
| 结构化输出 | 正则 / JSON Schema / EBNF 约束 | 把"格式对"从提示词问题变成解码问题 |
| 引擎底座 | 最初基于 **vLLM** 构建，后自研 kernel | 连头部团队也是"先站在 vLLM 上，再换心脏" |

### 9.1 DSpark：把"猜几个"变成"猜几个值得验"

第 10 章我们在 §1 就说过 decode 是带宽受限。投机解码用"小模型猜、大模型验"换速度，
但朴素做法有个隐形成本：**猜得越多，验证越浪费**——高并发下，验证 token 占的是本该给
别人 decode 的 batch 容量。

DSpark 的两处改动（[论文](https://arxiv.org/abs/2607.05147)）：

1. **半自回归草稿**：并行骨干 + 轻量顺序模块，补上块内依赖，缓解"越往后接受率越低"的衰减；
2. **置信度调度验证**：按前缀存活概率 × 引擎吞吐画像，给每个请求动态决定验多长。

生产数据（DeepSeek 自述）：

| 指标 | 结果 |
|---|---|
| 每步需验证的草稿 token | **−30%** |
| 生产负载吞吐 | **+4%–7%** |
| 单用户生成速度（对齐吞吐，对比 MTP-1） | **+60%–85%** |

注意最后两行的区别：**吞吐只涨了几个点，但用户体感快了 60% 以上**。
这是投机解码真正的价值——它买的不是总吞吐，是**在同样吞吐下的交互性**。
这正好呼应第 1 章的 SLO 分层：TPOT 属于体验层，吞吐属于成本层，两者不是一回事。

### 9.2 SWA bounded replay：为什么它能砍掉一半 prefill

标准做法（§5）是把长 prefill 切片，和 decode 混跑。DeepInfer 走另一条路：

```
常规：   [──────────────── 5000 token 全部重算 ────────────────]
SWA：    [ 丢弃 ][ 只重算最后 140 token ]
```

前提是模型用**滑动窗口注意力**：只有最后 W 个 token 参与注意力，更早的位置已经被窗口滑出。
既然它们不影响结果，就没必要重算——只需保证保留的后缀覆盖"窗口 + 最大回溯长度"。

官方 README 的默认配置是 `KEEP_TOKENS=140`（`compact` 模式）；
媒体报道写作"最后 128 个 token"，两者略有出入，**以 README 为准**。

**这就是"模型结构决定引擎优化空间"的最好例子**：同样的 prefill 问题，
稠密全注意力只能切片，滑动窗口可以直接截断。第 06 章讲压缩时会再回到这一点。

### 9.3 它没开源的三块，恰好是本书的三章

DeepInfer 的 README 明确写了"完整服务还需要、但本次未包含"：

| 缺的部分 | 对应本书 | 为什么不开源 |
|---|---|---|
| KV cache 系统 | 第 26 章 存储选型 | 与内部基础设施强耦合 |
| VE/Prefill/Decode 分离 | 第 11 章 PD 分离 | 需要跨机传输与调度配合 |
| DP 副本间的负载均衡 | 第 33 章 过载保护 | 依赖全局视图 |

**这恰好说明一件事：引擎（engine）不等于服务（service）。**
引擎解决"一次前向怎么算得快"，服务解决"一万个请求怎么排队、放哪、坏了怎么办"。
本书把后者拆成 Part VII–IX，就是这个原因。

它的最小高效部署是 **64 GPU prefill + 64 GPU decode**，P:D ≈ 1:1——
和我们第 11 章算出来的 909:700 ≈ 1.3:1 同一个量级，这是相互印证的。

### 9.4 边界：哪些数字不能抄

| DeepInfer / V4.1 的事实 | 我们的教学案例 | 能不能类比 |
|---|---|---|
| 1M 上下文 + 极限 KV 压缩 | 32K 上下文，KV 120 KB/token | ❌ 每 token 显存完全不同 |
| SM100（Blackwell）/ 昇腾 950 | H800 / H200 | ❌ Hopper 不在其支持列表 |
| 155 万亿 token/日（单集群） | 0.8 万亿 token/日 | ⚠️ 差约 190 倍，只能看趋势 |
| 非高峰每卡时收入 38 美元 | 单卡成本 7.3 元/时 | ⚠️ 收入 vs 成本，口径不同 |

最后一行特别值得停一下：38 美元/卡时 ≈ 273 元，是我们单卡成本（7.3 元/时）的 **37 倍**。
但它是**收入**、且对应旗舰模型定价，不是毛利，也不能反推"我们也能卖这么贵"。
第 17 章讲定价时会专门拆这个口径。

### 9.5 一句话

> 同一个问题（prefill 太贵、decode 太慢、batch 不可复现），
> DeepSeek 的答案是"改模型结构 + 自研 kernel + 只在自家硬件上极致优化"；
> 本书的答案是"通用硬件 + 标准协议 + 可替换组件"。
> **两条路都对，取决于你有多少卡、多少人和多少时间。**

---

## 10. 引擎的解剖：kernel 在哪一层，各层各自提供什么

前面九节讲的都是"策略"——怎么组批、怎么分块、怎么缓存。
但策略最终要落到 GPU 上执行，中间隔着一层**没人讲清楚的东西：kernel**。

### 10.1 六层解剖

```
┌──────────────────────────────────────────────────────┐
│ ① 接口层   OpenAI 兼容 API、SSE 流式、token 计量       │
├──────────────────────────────────────────────────────┤
│ ② 调度层   batching、chunked prefill、抢占、优先级      │
├──────────────────────────────────────────────────────┤
│ ③ 内存层   block table、前缀哈希、swap、KV 量化         │
├──────────────────────────────────────────────────────┤
│ ④ 执行层   逐层 forward：norm → gemm → attn → moe      │
├──────────────────────────────────────────────────────┤
│ ⑤ Kernel 层  attention / gemm / 通信 / 采样            │  ← 本节
├──────────────────────────────────────────────────────┤
│ ⑥ 硬件     SM90 / SM100 / Ascend + HBM + NVLink        │
└──────────────────────────────────────────────────────┘
```

**kernel 是第 ⑤ 层，属于引擎的一部分**，但通常**不在引擎仓库里**——它被拆成独立库
（FlashMLA、DeepGEMM、DeepEP），引擎在编译期或运行期链接进来。

| 层 | 谁写 | 对外提供的能力 | 这层坏了会怎样 |
|---|---|---|---|
| ① 接口 | 平台团队 | 协议兼容、流式、计量 | 客户端接不上 |
| ② 调度 | 引擎团队 | 吞吐、公平、TTFT/TPOT 折中 | 长尾、饥饿 |
| ③ 内存 | 引擎团队 | 并发数上限、前缀复用 | OOM、碎片 |
| ④ 执行 | 引擎团队 | 正确的 forward、并行切分 | 结果错、跑不动 |
| ⑤ Kernel | 少数人 | **峰值算力利用率** | 只有峰值的 30% |
| ⑥ 硬件 | 厂商 | 算力、带宽、互联 | — |

关键事实：**上面四层决定"能不能用"，最下面一层决定"贵不贵"**。
同一台 H800，attention kernel 写得好不好，能差 2–3 倍吞吐。

### 10.2 层、算子、kernel、指令：四个概念别混

| 概念 | 是什么 | 例子 | 谁定义 |
|---|---|---|---|
| **层（layer）** | 一组算子的组合 | 一个 Transformer block | 模型结构 |
| **算子（operator）** | 数学语义上的一个操作 | `matmul`、`softmax`、`rmsnorm`、`topk` | 框架（ATen 等） |
| **kernel** | 算子在某硬件上的**具体实现** | `cuBLAS gemm`、`flash_mla_with_kvcache` | kernel 作者 |
| **指令** | GPU 线程执行的机器指令 | `HMMA`、`LDGSTS` | 编译器 |

关键区别：**算子是不变的语义，kernel 是可变的实现。**

同一个 `matmul` 算子，背后可能是：

```
torch.matmul
   ├─ 小矩阵      → cuBLAS 的某个 kernel
   ├─ FP8 大矩阵  → DeepGEMM 的 fp8_gemm_nt
   └─ MoE 分组    → grouped GEMM kernel
```

调用链完整长这样：

```
model.forward()          ← 层
  └─ F.linear(x, w)      ← 层内的一步
      └─ aten::matmul    ← 算子（语义，与硬件无关）
          └─ cuBLAS / DeepGEMM  ← kernel（实现，与硬件强相关）
              └─ HMMA 指令       ← 指令
```

**"融合"就发生在这条链的第三层**：把 5 个算子映射成 1 个 kernel，
省掉 4 次启动和 4 次中间结果的 HBM 往返。

### 10.3 kernel 住在哪里：六种形态

答案是：**kernel 通常是一个独立发布的库，不是引擎的一个文件夹。**

| 形态 | 例子 | 与引擎的关系 | 升级影响 |
|---|---|---|---|
| 引擎内 `csrc/` | vLLM 的 `csrc/attention/`、`cache_kernels.cu` | 同仓库编译 | 跟引擎一起升 |
| **独立仓库 + wheel** | FlashMLA、DeepGEMM、DeepEP | pip 依赖，各自版本 | **可单独升级/废弃** |
| 引擎内 DSL 源码 | DeepInfer 的 `model_executor/layers/tilelang_ops` | 随引擎发 | 跟引擎一起升 |
| 运行时 JIT | DeepGEMM 的 DeepJIT | 安装时不编译，首次调用才编译 | 首次延迟 |
| 厂商库 | cuBLAS、cuDNN、NCCL | 系统级依赖 | 驱动兼容 |
| 编译器生成 | torch.compile、Triton | 运行时生成 | 不可预期 |

以 DeepInfer 为例的依赖图：

```
DeepInfer（引擎）
 ├─ deep_gemm      GEMM kernel 库      ← 独立版本、独立 CI
 ├─ flash_mla      attention kernel 库 ← 独立版本、独立支持矩阵
 ├─ deep_ep        通信 kernel 库      ← 独立版本
 └─ tilelang_ops   引擎内 DSL 算子    ← 随引擎
        └─ CUDA runtime / CANN（昇腾）
```

**这解释了一件很重要的事**：为什么 FlashMLA 可以单方面宣布"2026.09 起不再支持 Hopper"，
而引擎只能被动跟随——因为 kernel 库和引擎是两个独立的发布物。

也解释了"换个引擎为什么这么难"：换的不是调度器，是**一整套 kernel 契约**——
KV 的字节布局、权重的排列方式、支持的 SM 架构，全都绑在一起。

### 10.4 kernel 长什么样

一句话：**在 GPU 上并行执行的一个函数**。它接收 tensor 和元数据，返回 tensor。

以 DeepSeek 开源的 FlashMLA 为例，解码时每层就一行：

```python
o_i, lse_i = flash_mla_with_kvcache(
    q_i, kvcache_i, block_table, cache_seqlens, dv,
    tile_scheduler_metadata, num_splits, indices=indices,
)
```

看起来像普通 Python 调用，实际上是：启动 N 个 block、每 block M 个线程，
把 Q 分块搬进 shared memory、算分块 softmax、累加输出——全部手写在 CUDA/CUTLASS 里。

三种写法，越往下越难、越赚：

| 形态 | 代表 | 门槛 | 典型收益 |
|---|---|---|---|
| 手写 CUDA / CUTLASS | DeepGEMM、FlashMLA | 极高 | 接近硬件峰值 |
| DSL（TileLang / Triton） | DeepInfer 的 `tilelang_ops` | 中 | 峰值的 70%–90% |
| 直接调库 | vLLM 内置 kernel | 低 | 够用 |

**绝大多数团队不该写 kernel**，但必须知道它在哪、瓶颈在哪，否则调优会一直在错误的层打转。

### 10.5 一次 decode step 有多少个 kernel

以 60 层模型、每层 12 个算子估算：

| 阶段 | 每层 kernel 数 |
|---|---|
| RMSNorm、RoPE | 2 |
| QKV / O 投影 | 3 |
| Attention | 1 |
| MoE gate + topk | 2 |
| dispatch / grouped GEMM ×2 / SwiGLU / combine | 4–5 |
| **每层合计** | **约 12–13** |

```
单 token step ≈ 60 层 × 12.5 ≈ 750 次 kernel 启动
```

单次启动开销 3–5 μs（CPU 侧下发 + GPU 侧调度），**750 × 4 μs ≈ 3 ms**。
而我们的 TPOT 预算是 25 ms——**光启动就吃掉 12%**，还没开始算。

这就是 **CUDA Graph** 存在的原因：把整条 kernel 序列录制成一张图，一次提交、零重复下发，
启动开销趋近于 0。代价是**形状必须固定**——所以变长 batch 要用 padding 或 masked 方案
（DeepGEMM 的 masked grouped GEMM 就是为此而生）。

### 10.6 融合：把 5 个 kernel 变 1 个

FlashMLA 有个很能说明问题的设计——把 Q-norm、Q-RoPE、attention、O-RoPE、转 FP8
**五个操作融进一个 kernel**。

```python
out_fp8, out_sf, max_logits, lse = fused_norm_rope_attn_rope_cast.prefill(...)
```

收益不只是省 4 次启动，更重要的是**省掉中间结果的 HBM 往返**：
每层少写读 2 次 `[batch, heads, 512]` 的 bf16 tensor，在 decode 这种带宽受限的场景下就是净赚。

代价很硬：**权重要提前重排**。README 里要调 `permute_q_b_proj`、`permute_wv_proj`
把权重和 scale 改成 kernel 要求的布局——一旦换了 kernel，权重格式就得跟着变。

> 这是全书反复出现的一个模式：**性能优化会把"数据布局"变成契约**。
> 你优化的是 kernel，被锁定的是权重格式、checkpoint 格式、甚至训练侧。

### 10.7 Mega MoE：把通信塞进 kernel 里

MoE 的传统流程是四步、四次 kernel、两次跨卡：

```
dispatch(EP) → GEMM1 → SwiGLU → GEMM2 → combine(EP)
```

DeepGEMM 的 Mega MoE 把它**合成一个 kernel**：dispatch、linear1、linear2、SwiGLU、combine
全部融进去，用对称内存（symmetric memory）让 **NVLink 通信与 tensor core 计算重叠**。

意义在于：MoE 推理的瓶颈本来不是算力，是 all-to-all 的等待。
把通信藏进 kernel 里，等于把"等"变成了"顺便做"。

`deep_gemm.fp8_fp4_mega_moe(y, l1, l2, buffer)` —— 一行调用背后是一次
通信-计算流水线编排。

### 10.8 数字：kernel 峰值 vs 模型 MFU

| 指标 | 数值 | 说明 |
|---|---|---|
| H800 FP8 理论峰值 | 1,979 TFLOPS | 硬件 |
| DeepGEMM 实测（H800） | **1,550 TFLOPS** | kernel 级，占峰值 78% |
| 我们模型级有效算力 | 693 TFLOPS | 第 8 章的 35% MFU |
| 差距 | **43 个百分点** | 访存、attention、通信、气泡 |

**kernel 做到 78% 很了不起，但模型级只有 35%**——中间那 43 个点不在 kernel 里，
在调度、在 batch 形状、在通信、在长尾。

这解释了为什么"优化 kernel"和"优化吞吐"是两件事：
前者是少数人的活，后者是平台团队的活。

### 10.9 边界：kernel 与硬件强绑定

FlashMLA 在 2026.09.30 的版本里**移除了 Hopper 支持**（只留 SM100/SM103），
同时改了 FP8/FP4 的 KV 格式，与旧版本不兼容；DeepInfer 也只支持 SM100 与昇腾 950。

| 事实 | 含义 |
|---|---|
| kernel 按 SM 架构编译 | 换卡就要重编，甚至重写 |
| KV 格式随 kernel 版本变 | 升级引擎要让旧缓存失效 |
| 昇腾 950 已能到峰值 95% | 国产卡在特定算子上不再落后 |

**选硬件时，先看 kernel 支持矩阵，再看纸面算力。**
一张算力更强但没有 kernel 的卡，实际吞吐可能不如一张"弱"卡。

### 10.10 一句话

> **调度器决定"谁先算"，kernel 决定"算得多快"。
> 前者是你能改的，后者通常要等生态——所以架构设计要把 kernel 的可获得性算进选型。**

---

## 11. 练习（20 分钟）

**1. 跑实验**：

```bash
cd lab && python3 paging_lab.py
```

改三个参数再跑，记录结果：

- `BLOCK_TOK` 改成 8 和 32（block 大小怎么影响浪费率？）；
- `BUDGET_GB` 改成 100（预算变小时三种策略差距怎么变？）；
- 把会话分布改成均匀分布（改 `sample_len`），看策略 A 还蠢不蠢。

**2. 手算**：一个 4096 token 的会话，block=16，占几个 block？
如果系统提示 1024 token 被 100 个会话共享（COW），比分着存省多少 GB？

**3. 读源码（选做）**：`mock_llm.py` 里用一个 set 模拟前缀缓存——
对比本章的 block 哈希，说出它为什么只能做"全有或全无"的命中。

---

## 12. 复盘卡

```markdown
## 第 03 章复盘

**一句话**：……

**我算的**：4096 token 会话 = ___ blocks；共享前缀省 ___ GB

**我原来以为**：……  **实际是**：……  **差在**：……

**还没懂**：……
```

---

## 13. 验收：怎么证明引擎配置是对的

| 指标 | 期望 | 怎么测 | 脚本 |
|---|---|---|---|
| 单实例并发会话 | 与 §6.4 公式误差 <20% | 压到 KV 打满 | `labs/paging_lab.py` |
| 块内碎片 | <2% | 统计 block 利用率 | `labs/paging_lab.py` |
| 前缀命中率 | 与 radix 实验差 <10 pt | 引擎 metrics | `labs/radix_lab.py` |
| chunked 下 TTFT p95 | ≤ 800 ms | 压测 | — |
| 调度器空转 | <1% step | 引擎日志 | — |

**判据**：先用两个实验拿到理论值，再压测真实引擎。
两者差超过 20% 时，先查口径（token 算不算 chat template、block 是不是 16），再怀疑引擎。

## 14. 延伸

- PagedAttention 论文（OSDI'23）：重点读 §3 Memory Management 和 §5 Sharing
- vLLM 文档：Prefix Caching、Chunked Prefill、Scheduler Metrics
- 下一章：[第 04 章 · 调度与 PD 分离](ch04-pd-disaggregation.md)
