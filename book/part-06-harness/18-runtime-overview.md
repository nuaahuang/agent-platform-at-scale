# 第 18 章 · 运行时总览：agent loop 的七件事

> 读完你能回答：
> ① agent loop 为什么不能写成 `while (true)`？
> ② 计划、调用、观察、状态、预算、超时、恢复，这七件事各自管什么？
> ③ 一个请求跑到第 14 步断了，怎么接着跑而不是重来？
> ④ 预算是 token、步数、时间三个维度，哪个先超限？
> ⑤ 状态机有几个状态，为什么要有 WAITING_HUMAN？

---

## 1. 问题：第 14 步断了

一个退单请求，Agent 走了 14 步：查订单、查物流、看政策、算金额、校验用户资格、准备退款单……第 15 步准备提交时，进程被重启（滚动发布）。

重启之后，用户的对话还在，但 Agent 忘了自己刚才干了什么。它重新从第 1 步开始：又查了一次订单、又算了一次金额、又准备了一次退款单。

**这一次，退款提交了两次。** 因为第 15 步的动作在重启前已经发出去，重启后又发了一次。

复盘时发现四个问题：

| 问题 | 后果 |
|---|---|
| 没有持久化中间状态 | 重启即失忆，只能重来 |
| 工具调用不幂等 | 重放导致重复退款 |
| 没有步数上限 | 某些请求会走 200 步，烧掉 3 块钱 |
| 没有整体超时 | 挂住的请求一直占着连接 |

这一章讲的就是把这四件事补上——它们构成了 agent loop 的七件事。

## 2. agent loop 的最小模型

最常见的 agent 实现长这样：

```python
while True:
    action = llm.decide(history)
    if action.type == "final":
        return action.text
    result = tools.call(action)
    history.append(result)
```

这五行能跑 demo，但有五个致命问题：

| 问题 | 为什么致命 |
|---|---|
| `while True` 没有终止保证 | 模型可能永远不给 final |
| `history` 在内存里 | 进程一挂就没了 |
| `tools.call` 无幂等 | 重放会重复执行 |
| 没有预算 | 一步一毛钱，200 步就是 20 块 |
| 没有超时 | 卡住的请求占连接、占显存 |

所以真实的 agent loop 不是循环，是**带状态、带预算、带检查点的状态机**。

## 3. 七件事

| # | 事 | 管什么 | 不管会怎样 |
|---|---|---|---|
| 1 | 计划 | 下一步做什么 | 模型乱调工具、绕圈 |
| 2 | 调用 | 怎么执行工具 | 重复执行、参数错 |
| 3 | 观察 | 结果怎么回灌 | 上下文爆炸、注入 |
| 4 | 状态 | 中间结果存哪 | 重启失忆 |
| 5 | 预算 | 花多少 | 无限烧钱 |
| 6 | 超时 | 等多久 | 挂死 |
| 7 | 恢复 | 断了怎么办 | 重来或重复 |

七件事里，第 4、5、6、7 是**工程问题**，第 1、2、3 是**语义问题**。多数团队的 demo 只做了后三件，生产化要补前三件。

七件事有三套常见实现方案，选哪个决定了后面的工程量：

| 方案 | 状态存哪 | 恢复能力 | 适用 |
|---|---|---|---|
| 无状态函数 | 不存 | 不能恢复，重来 | demo、单步任务 |
| 快照式 | Redis 存最新状态 | 恢复到最后一步 | 多数生产系统 |
| 事件溯源 | Kafka 存全量事件 | 可重放到任意时刻 | 需要审计、回放 |

第二行是本章的写法，第三行是第 19 章的内容。差别在"能不能回答三个月前那个请求为什么失败"——快照式不能，事件溯源能。

## 4. 计划：约束下的选择

"计划"听起来像让模型自己规划，实际是**给它一个受限的选择空间**。

| 约束 | 作用 | 不做会怎样 |
|---|---|---|
| 工具白名单 | 只能选这 N 个工具 | 模型调用不存在的工具 |
| 步数上限 | 最多 20 步 | 绕圈 200 步 |
| 每步必须引用上一步的观察 | 防止空转 | 反复调同一个工具 |
| 终止条件显式化 | 允许 final、拒绝、转人工 | 只会一直调工具 |

第三条最有效。约束写成 prompt：

```
每一轮你必须说明：上一步的观察里哪个字段支持你现在这个决定。
如果找不到支持字段，说明信息不足，应该提问或转人工，而不是继续调工具。
```

这条规则把"绕圈"变成"要么前进要么停"，实测能把平均步数从 11 降到 6。

## 5. 调用：幂等是底线

工具调用要满足三条：

| 要求 | 做法 |
|---|---|
| 幂等 | 同一 `idempotency_key` 重复调用只生效一次 |
| 超时 | 单步 30 秒，超时算失败 |
| 可重试 | 只对幂等的读操作重试，写操作不重试 |

```python
def call_tool(name, args, step_id, ctx):
    key = f"{ctx.request_id}:{step_id}:{name}"
    cached = idem_store.get(key)
    if cached:                       # 重放时直接返回上次结果
        return cached
    result = registry[name].invoke(args, timeout=30, ctx=ctx)
    idem_store.set(key, result, ttl=3600)
    return result
```

`step_id` 进 key 是关键：同一步重放返回缓存，不同步重新执行。这样"整个请求重放"和"某一步重试"都能正确处理。

写操作（退款、改地址）不能重试，但可以**查询确认**：

```python
try:
    create_refund(order_id, key)
except Timeout:
    # 不重试，改为查询
    if refund_exists(order_id):
        return "已提交"
    raise
```

## 6. 观察：结果怎么进上下文

工具返回可能是 100 KB 的 JSON。直接塞进上下文有三个后果：token 爆掉、注入风险、模型抓不住重点。

三件事要做：

| 动作 | 做法 |
|---|---|
| 裁剪 | 只取需要的字段（50 → 5，第 39 章）|
| 结构化 | 用固定模板，不要裸 JSON |
| 截断 | 单条观察上限 2,000 token |

```python
def render_observation(tool, result):
    fields = OBSERVE_SCHEMA[tool]          # 每个工具声明要看哪些字段
    picked = {k: result.get(k) for k in fields}
    text = TEMPLATE[tool].format(**picked)
    return truncate(text, max_tokens=2000)
```

`OBSERVE_SCHEMA` 是配置，不是代码——新增工具只加配置。这一点很重要：观察格式是**最容易变的东西**（接口改字段），做成配置才能不发版。

截断到 2,000 token 的理由：20 步 × 2,000 = 40K，已经超过 32K 预算。所以实际要靠"只保留最近 5 步的完整观察 + 更早的摘要"来控制。

## 7. 状态：每步一个检查点

状态分两层：

| 层 | 存什么 | 存哪 | 频率 |
|---|---|---|---|
| 会话状态 | 消息历史、当前步、预算余额 | Redis | 每步 |
| 事件日志 | 每步的输入输出、工具结果 | Kafka → 对象存储 | 每步 |

Redis 存的是"最新快照"（约 50 KB），对象存储存的是"完整历史"（约 200 KB/请求）。

为什么要两层：恢复时要快（Redis），审计和回放要全（对象存储）。

```python
def checkpoint(state):
    redis.setex(f"sess:{state.sid}", 3600, state.to_json())
    kafka.send("agent.events", state.to_event())
```

TTL 设 1 小时：超过 1 小时的会话，用户基本不会再回来，恢复也没意义。

## 8. 预算：三维，先到先停

预算不是一个数，是三个：

| 维度 | 值 | 依据 |
|---|---|---|
| token | 32,000 | 20 步 × 1,500 + 初始 2,000 |
| 步数 | 20 | P95 是 20，P99 是 35（超了转人工）|
| 时间 | 300 秒 | 端到端 9 秒是单轮，多步要放宽 |

任一超限就停，返回"已尽力"，带上当前进度。**不要静默停止**——用户需要知道为什么没做完。

```python
def check_budget(state):
    if state.tokens_used > 32000:
        raise BudgetExceeded("token", state.progress())
    if state.steps > 20:
        raise BudgetExceeded("steps", state.progress())
    if time.time() - state.started_at > 300:
        raise BudgetExceeded("time", state.progress())
```

三个数的关系：20 步 × 平均 1,500 token = 30,000，接近 32K。所以 token 和步数大致同步耗尽。如果某个请求 5 步就烧了 32K，说明它每步观察太长——要查 `OBSERVE_SCHEMA`。

## 9. 超时：三层，各管一段

| 层 | 值 | 超了怎么办 |
|---|---|---|
| 单步（工具调用）| 30 秒 | 该步失败，进重试或跳过 |
| 单轮（一次 LLM 调用）| 60 秒 | 同上 |
| 整体（整个 loop）| 300 秒 | 终止，返回已完成的进度 |

三层不是简单的包含关系：单步 30 秒 × 20 步 = 600 秒 > 整体 300 秒。所以整体超时会先触发。

这意味着**长尾请求不是被单步超时杀死的，是被整体预算杀死的**。所以整体超时给的返回值要包含"做到哪一步了"，让用户知道进度。

## 10. 恢复：三种断法，三种处理

| 断法 | 状态 | 恢复方式 |
|---|---|---|
| 进程重启 | Redis 有快照 | 从最后一步继续 |
| 工具超时 | 该步失败 | 重试读操作 / 查询确认写操作 |
| 用户断线 | 会话还在 | 重连后继续推流 |

第一种依赖检查点，第二种依赖幂等，第三种依赖流式会话的续传（第 23 章）。

**最危险的是"状态存了但工具执行了两次"**。前面那个退款事故就是这个：Redis 存的是第 14 步完成后的状态，但第 15 步的退款请求已经发出去了，重启后又发一次。

解法是**先写"意图"再执行**：

```python
# 1. 先记"我要执行第 15 步"
state.mark_pending(step=15, action="create_refund", key=idem_key)
checkpoint(state)

# 2. 再执行
result = call_tool("create_refund", args, step_id=15, ctx)

# 3. 成功后记完成
state.mark_done(step=15, result=result)
checkpoint(state)
```

重启后看到"第 15 步 pending"，就知道要去查 `idempotency_key` 有没有生效，而不是直接重发。

这一步的顺序（先记意图后执行）是恢复正确性的全部。

## 11. 状态机

七个状态：

```
INIT
  ↓
PLANNING ──(无可行动作)──> ASKING_USER ──> PLANNING
  ↓
ACTING ──(工具超时)──> RECOVERING ──> ACTING / FAILED
  ↓
OBSERVING
  ↓
(PLANNING | DONE | FAILED | WAITING_HUMAN)
```

| 状态 | 含义 | 出口 |
|---|---|---|
| INIT | 请求已创建 | PLANNING |
| PLANNING | 模型决定下一步 | ACTING / ASKING_USER / DONE |
| ACTING | 工具执行中 | OBSERVING / RECOVERING |
| OBSERVING | 处理工具结果 | PLANNING |
| ASKING_USER | 需要用户补充信息 | PLANNING（用户回复后）|
| RECOVERING | 单步失败，重试或降级 | ACTING / FAILED |
| WAITING_HUMAN | 转人工 | DONE（人工处理后）|

`WAITING_HUMAN` 这个状态必须有。多数系统把"转人工"当成一种 final 输出，结果人工处理完之后无法回到 loop——用户追问一句，整个上下文就断了。

## 12. 伪代码

把七件事拼起来，核心逻辑约 60 行：

```python
def run_loop(req):
    state = load_or_init(req)                 # 4 状态：加载检查点
    while True:
        check_budget(state)                   # 5 预算
        check_deadline(state)                 # 6 超时

        decision = plan(state)                # 1 计划
        if decision.type == "final":
            return finish(state, decision)

        state.mark_pending(decision)          # 4 先记意图
        checkpoint(state)

        try:
            result = call_tool(decision, timeout=30)   # 2 调用（幂等）
        except ToolTimeout:
            state.mark_failed(decision)
            if can_retry(decision):           # 7 恢复
                continue
            return degrade(state, decision)

        state.mark_done(decision, result)
        state.observe(render_observation(result))      # 3 观察（裁剪）
        checkpoint(state)
```

六行注释对应七件事，缺一行就少一块能力。

## 13. 失败模式

| 现象 | 根因 | 怎么发现 | 怎么修 |
|---|---|---|---|
| 重启后重复退款 | 先执行后记账 | 对账时发现两笔 | 改成先记意图 |
| 请求走 200 步 | 没有步数上限 | 看步数分布 | 加预算 + 步数上限 |
| 断开后从第 1 步重来 | 没存检查点 | 看日志重复调用 | 每步 checkpoint |
| 上下文超长报错 | 观察没裁剪 | 看单请求 token | 裁剪到 2,000/步 |
| 转人工后对话断了 | 转人工当成终态 | 用户追问无响应 | 加 WAITING_HUMAN 状态 |
| 某工具一天被调 10 万次 | 模型绕圈 | 工具调用分布 | 加"必须引用上一步观察"约束 |

第一行是最贵的：一次重复退款就是真金白银，而且很难在测试环境复现（要正好在第 15 步重启）。

## 13.5 备选方案：用工作流引擎代替自研 loop

前面这套状态机可以用 Temporal、Argo Workflows 这类引擎实现，省掉检查点和恢复的代码。

| 方案 | 优点 | 代价 |
|---|---|---|
| 自研 loop | 轻、可控、延迟低 | 要自己写检查点与恢复 |
| 工作流引擎 | 恢复、重试、可视化都有 | 多一跳 RPC（5–10 ms），状态存引擎 |
| 函数编排（Step Functions） | 托管，按次计费 | 与推理服务的延迟不匹配 |

第二个方案的延迟值得算：loop 里每步都要和引擎通信，20 步 × 8 ms = 160 ms。相比 300 秒的整体预算可以忽略，但相比单轮的 9 秒就有感了。

小团队建议直接用引擎（Temporal），大团队自研——因为 loop 里的很多优化（比如批量观察、并行工具调用）引擎不支持。

## 14. 什么情况下这些结论不成立

| 结论 | 推翻条件 |
|---|---|
| 每步都要检查点 | 单步 agent（只调一次工具）时，存一次就够 |
| 步数上限 20 | 任务本身是长周期（如批量处理 100 个订单）时，要单独设计 |
| 工具超时 30 秒 | 有慢工具（如人工审核接口 5 分钟）时，要分级 |
| 状态存 Redis | 请求量小、可接受重来时，存数据库就够 |

## 15. 验收

1. 能画出你的状态机，说出每个状态的出口。
2. 能指出你的系统在哪一步做检查点，重启后从哪恢复。
3. 能说出预算的三个维度，以及哪个先触发。
4. 能解释"先记意图后执行"为什么能防重复。

## 16. 复盘卡

```
我原来以为…实际…差在：
```

我原来以为 agent loop 就是个 while 循环，实际差在它不是循环——是状态机，因为有预算、有超时、要能断点续跑。

## 17. 延伸

- 第 19 章：事件溯源，状态怎么存才能重放
- 第 20 章：上下文工程，观察怎么裁剪
- 第 21 章：工具层，幂等与权限的具体实现
- 第 22 章：多 Agent，handoff 时状态怎么传
- 第 25 章：背压，整体超时与准入的关系

结论是：agent loop 的复杂度不在"调模型"，在"断了怎么办"。七件事里，状态、预算、超时、恢复这四件都是为了让 loop 能在生产环境里跑——它们不产生任何功能，但少了任何一件，系统就不敢上线。
