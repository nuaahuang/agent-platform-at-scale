# 学习计划 · Agentic RL

> 这不是书的一部分，是我自己的跟进清单。判断标准只有一条：**学完能动手改一行代码并看到结果**。
> 配套阅读：[附录 B · Agentic RL Infra 与在线平台的交界](book/appendix/B-agentic-rl-infra.md)

---

## 阶段一：算法（2 周）

目标：能读懂 GRPO 的 loss，说清它和 PPO 差在哪。

| 材料 | 用来看什么 |
|---|---|
| [datawhalechina/agentic-rl-lab](https://github.com/datawhalechina/agentic-rl-lab) 的 Loss Function、GRPO | 三种 loss（importance_sampling / ppo / cispo）的实现差异 |
| 附录 B §11 | 最小循环的结构，和我书里的伪代码对照 |
| GRPO 原论文（DeepSeekMath） | 组内归一化为什么不需要 critic |

验收：能解释 `advantage = (r − mean) / std` 里为什么除以 std，去掉会怎样。

## 阶段二：单机复现（3 周）

目标：跑通一个完整实验，改 reward 能看到曲线变化。

| 材料 | 用来看什么 |
|---|---|
| agentic-rl-lab 的 DAPO、GSPO、OPSD | 同一套数据下不同算法的曲线差异 |
| Search-R1、ReTool | 多轮工具场景的 reward 怎么设计 |
| ALFWorld | 长轨迹 + 真实环境的接法 |
| 附录 B §4、§8 | 环境池规模、奖励的三个陷阱 |

验收：把 reward 从"终局分"改成"过程分 + 终局分"，复现出方差变化。

## 阶段三：集群（4 周，可选）

目标：知道训练集群那一层长什么样，能算出显存和通信账。

| 材料 | 用来看什么 |
|---|---|
| verl | hybrid engine、3D 并行、weight sync |
| OpenRLHF | Ray 编排、colocate 实现 |
| TRL | 最小实现，对照看差异 |
| 附录 B §5、§9 | 显存构成、三种容量排布 |

验收：给定 200B 模型和 512 卡，算出每卡显存占用与 ZeRO-3 的通信占比。

---

## 为什么先写附录 B 再学

我学的目的是"能判断别人给的方案对不对"，不是"自己能训一个模型"。附录 B 把交界处算清楚了——卡怎么分、轨迹从哪来、权重怎么回灌——这三件事决定了 RL 项目在平台侧会不会崩。算法细节可以慢慢补，交界算错是立刻出事。

## 判断教程好不好用的四条

用这四条筛资料，避免浪费时间：

1. **有没有 reward 实现**：只讲原理不给 reward 代码的，基本跑不起来。
2. **有没有失败曲线**：只放成功结果的，学不到调参。
3. **依赖是否锁死厂商**：锁死的适合入门，不适合建立判断。
4. **能不能改一行重跑**：改 reward、改 group_size，半小时内出结果的才算可实验。

---

## 待办

- [ ] 阶段一：跑通 GRPO 的三种 loss 对比
- [ ] 阶段二：改 reward，复现方差变化
- [ ] 阶段三（可选）：verl 单机跑到能训 1B 模型
- [ ] 每阶段结束回填附录 B 的对应小节，把"读到"变成"验证过"
