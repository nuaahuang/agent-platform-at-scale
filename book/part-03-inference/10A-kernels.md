# 第 10A 章 · Kernel 层：算子如何变成每秒千次运算

> 读完你能回答：
> ① kernel 到底是什么，和"算子"差在哪一层？
> ② 用什么写？有哪些开源件可以直接用？
> ③ 为什么换一张卡就可能要重写？
> ④ 自己的团队该不该写 kernel？

> 本章是第 10 章的展开，不是必读。主线读法可以跳过，
> 但只要你需要回答"为什么同样的卡我们只有别人 1/3 吞吐"，就必须读。

---

## 1. 问题：同一张 H800，吞吐能差 3 倍

第 10 章讲了引擎的四种机制：连续批处理、PagedAttention、chunked prefill、前缀缓存。
但机制只决定"谁先算"，不决定"算得多快"。

同样 8 张 H800、同样 200B MoE、同样 batch，
attention 与 GEMM 的 kernel 写得好不好，端到端吞吐能差 **2–3 倍**。

| 写法 | H800 上的 attention 吞吐 | 相对 |
|---|---|---|
| 朴素 PyTorch（eager，逐头循环） | 约 12% 峰值 | 1× |
| 通用 SDPA（cuDNN/flash 后端） | 约 45% 峰值 | 3.7× |
| 针对性 kernel（FlashMLA 级别） | 约 78% 峰值 | 6.5× |

**这不是调参能补上的差距**，是"谁来写这个循环"的差距。

---

## 2. 四个概念：层、算子、kernel、指令

| 概念 | 是什么 | 例子 | 谁定义 |
|---|---|---|---|
| **层（layer）** | 一组算子的组合 | 一个 Transformer block | 模型结构 |
| **算子（operator）** | 数学语义上的一个操作 | `matmul`、`softmax`、`rmsnorm`、`topk` | 框架（ATen 等） |
| kernel | 算子在某硬件上的**具体实现** | `cuBLAS gemm`、`flash_mla_with_kvcache` | kernel 作者 |
| **指令** | GPU 线程执行的机器指令 | `HMMA`（半精度矩阵乘加）、`LDGSTS`（全局→共享异步拷贝）、`TMA`（张量内存加速器） | 编译器/架构 |

关键区别：算子是不变的语义，kernel 是可变的实现。

```
model.forward()            ← 层
  └─ F.linear(x, w)        ← 层内一步
      └─ aten::matmul      ← 算子（语义，与硬件无关）
          └─ cuBLAS / DeepGEMM   ← kernel（实现，与硬件强相关）
              └─ HMMA 指令        ← 指令
```

同一个 `matmul` 算子，背后可能是三套完全不同的 kernel：

```
torch.matmul
   ├─ 小矩阵      → cuBLAS 的某个 kernel
   ├─ FP8 大矩阵  → DeepGEMM 的 fp8_gemm_nt
   └─ MoE 分组    → grouped GEMM kernel
```

**"融合"就发生在第三层**：把 5 个算子映射成 1 个 kernel，
省掉 4 次启动和 4 次中间结果的 HBM 往返。

---

## 3. 一次 decode step 有多少个 kernel

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
750 × 4 μs ≈ 3 ms
```

而 TPOT（Time Per Output Token） 预算是 25 ms——**光下发就吃掉 12%**，还没开始算。

这就是 CUDA Graph 存在的理由：把整条 kernel 序列录成一张图，一次提交，
重复下发归零。代价是**形状必须固定**，所以变长 batch 要用 padding 或 masked 方案
（DeepGEMM 的 masked grouped GEMM 就是为此而生）。

---

## 4. 一个 kernel 的内部结构

GPU 的执行层次：

```
Grid                     一次 kernel 启动
└── CTA（Cooperative Thread Array）                  跑在一个 SM 上，最多 1024 线程
    └── Warp             32 个线程，同一条指令
        └── Thread       一个 lane
```

**CTA 是什么**：Cooperative Thread Array，NVIDIA 对 thread block 的正式叫法
（CUDA 里 `blockIdx` 指的就是它；OpenCL 叫 work-group，WebGPU 叫 workgroup）。

它是 GPU 上**资源分配的单位**——shared memory 按 CTA 分配，所以：

| 约束 | 数值（H800/H100） | 后果 |
|---|---|---|
| 每 CTA 最多线程 | 1024 | 再大要拆 |
| 每 SM 最多线程 | 2048（64 warp） | 一个 CTA 占满，其他进不来 |
| 每 SM shared memory | 228 KB | CTA 要 200 KB → 单 SM 只能跑 1 个 |
| SM 总数 | 132 | 决定并行上限 |

写 kernel 就是在回答四个问题：

| 问题 | 典型答案（paged attention） |
|---|---|
| 怎么切 grid？ | 每个 `(请求, 头)` 一个 CTA |
| 每个 CTA 搬什么进 shared memory？ | 一个 KV block（16 token × 576 维） |
| 怎么并行？ | warp 内按 16×16 分块做 QK^T |
| 怎么处理长序列？ | online softmax：边算边更新 running max/sum |

shared memory 是稀缺资源：H800/H100 每个 SM 只有 228 KB。
一个 CTA 要 200 KB，那这个 SM 同时只能跑一个 CTA——占用率直接掉到下限。

**访存合并**是第二个坎：一个 warp 的 32 个线程如果读地址连续，一次事务搞定；
如果散着读，就变成 32 次。PagedAttention 之所以用 16 token 一块，
部分原因就是让这个 16×576 的块在内存里连续。

---

## 5. 用什么写：五种技术栈

| 技术栈 | 代表 | 门槛 | 能达到的峰值 | 可移植性 |
|---|---|---|---|---|
| **CUDA C++** | 大多数生产 kernel | 极高 | 90%+ | 只 NVIDIA |
| CUTLASS / CuTe | DeepGEMM 起点、cuBLAS 同源 | 高 | 90%+ | 只 NVIDIA，按架构模板化 |
| **PTX / SASS 手写** | 少量极致优化 | 极高 | 接近 100% | 绑死具体架构（PTX 是 NVIDIA 虚拟指令集，SASS 是 GPU 真正执行的机器码） |
| DSL：Triton | PyTorch 生态、FlashInfer | 中 | 70%–90% | NVIDIA/AMD |
| DSL：TileLang | DeepInfer、昇腾适配 | 中 | 70%–95% | CUDA 与昇腾双后端 |
| Ascend C | 昇腾原生算子 | 中高 | 依算子 | 只昇腾 |

**趋势是**：新项目用 DSL 起步，遇到热点再下沉到 CUDA/CUTLASS。
DeepInfer 就是这个组合——`tilelang_ops` 写通用部分，FlashMLA/DeepGEMM 写极致部分。

---

## 6. 融合的收益与代价

FlashMLA 把 Q-norm、Q-RoPE、attention、O-RoPE、转 FP8 五个算子融成一个 kernel：

```python
out_fp8, out_sf, max_logits, lse = fused_norm_rope_attn_rope_cast.prefill(...)
```

收益不只是省 4 次启动，更重要的是**省掉中间结果的 HBM 往返**：
每层少写读 2 次 `[batch, heads, 512]` 的 bf16 tensor——在 decode 这种带宽受限场景下就是净赚。

代价很硬：**权重要提前重排**。README 要求先调 `permute_q_b_proj`、`permute_wv_proj`
把权重和 scale 改成 kernel 要求的布局。一旦换了 kernel，权重格式就得跟着变。

> 这是全书反复出现的模式：性能优化会把"数据布局"变成契约。
> 你优化的是 kernel，被锁定的是权重格式、checkpoint 格式、甚至训练侧。

---

## 7. 通信也是算子

MoE 的传统流程是四步、四次 kernel、两次跨卡：

```
dispatch(EP) → GEMM1 → SwiGLU → GEMM2 → combine(EP)
```

DeepGEMM 的 Mega MoE 把它合成一个 kernel：dispatch、linear1、linear2、SwiGLU、combine
全部融进去，用对称内存（symmetric memory）让 NVLink 通信与 tensor core 计算重叠。

```python
y = deep_gemm.fp8_fp4_mega_moe(y, l1, l2, buffer)
```

意义在于：MoE 推理的瓶颈本来不是算力，是 all-to-all 的等待。
把通信藏进 kernel，等于把"等"变成"顺便做"。

---

## 8. 硬件绑定：为什么换卡要重写

kernel 是按 **SM 架构**编译的，架构一换，指令集、tensor core 形状、shared memory 大小都可能变。

| 事实 | 时间 | 含义 |
|---|---|---|
| FlashMLA 移除 Hopper 支持 | 2026.09.30 | 只留 SM100/SM103，KV 格式也变了 |
| DeepInfer 只支持 SM100 与昇腾 950 | 2026.10 | Hopper 不在列表 |
| 昇腾 950 稀疏注意力 | 2026.09.30 | prefill 410 TFLOPS（峰值 95%）、decode 360（83%） |

选硬件时先看 kernel 支持矩阵，再看纸面算力。
一张算力更强但没有 kernel 的卡，实际吞吐可能不如一张"弱"卡。

---

## 9. 开源地图：谁在维护什么

| 项目 | 许可证 | 定位 | 适合谁 | 学习价值 |
|---|---|---|---|---|
| [FlashMLA](https://github.com/deepseek-ai/FlashMLA) | MIT | MLA / 稀疏注意力 kernel | 用 MLA 类模型 | ★★★★★ 读 attention 优化 |
| [DeepGEMM](https://github.com/deepseek-ai/DeepGEMM) | MIT | FP8/FP4 GEMM、Mega MoE | 需要 FP8 推理 | ★★★★★ 代码极简，注释讲原理 |
| [DeepEP](https://github.com/deepseek-ai/DeepEP) | MIT | EP 通信 kernel | MoE 大规模部署 | ★★★★ 通信-计算重叠 |
| [flash-attention](https://github.com/Dao-AILab/flash-attention) | BSD-3 | 通用注意力 | 所有人 | ★★★★★ 入门必读 |
| [FlashInfer](https://github.com/flashinfer-ai/flashinfer) | Apache-2.0 | 推理算子集合 | 自建推理服务 | ★★★★ 覆盖面广 |
| [CUTLASS](https://github.com/NVIDIA/cutlass) | BSD-3 | GEMM 模板库 | 写 GEMM 的人 | ★★★★ 门槛高但正统 |
| [Triton](https://github.com/triton-lang/triton) | MIT | GPU DSL | 想快速试验 | ★★★★★ 上手最快 |
| [TileLang](https://github.com/tile-ai/tilelang) | 见仓库 | tile 级 DSL | 多后端（含昇腾） | ★★★★ 新范式 |
| [vLLM `csrc/`](https://github.com/vllm-project/vllm/tree/main/csrc) | Apache-2.0 | 引擎内 kernel | 已在用 vLLM | ★★★★ 工程完整度 |
| [NCCL](https://github.com/NVIDIA/nccl) | 见仓库 | 集合通信 | 所有人 | ★★★ 底层实现 |

**怎么挑**：先用 Triton/FlashInfer 把功能跑通，热点再用 CUTLASS/FlashMLA 级别的库，
最后才考虑自己写。顺序反了会浪费半年。

---

## 10. 怎么学：四级路径

| 阶段 | 做什么 | 时间 | 验收 |
|---|---|---|---|
| 1 | 用 Triton 写一个 softmax kernel | 2 小时 | 与 PyTorch 结果一致 |
| 2 | 读 FlashAttention 前向，手推 online softmax | 1 天 | 能讲清为什么省 HBM |
| 3 | 跑 CUTLASS `examples/00_basic_gemm` | 2 天 | 能改 tile 大小看性能变化 |
| 4 | 用 ncu（Nsight Compute）把一个小 kernel 从 40% 优化到 70% | 1 周 | 能说出每一步改动的理由 |

第 4 阶段才是真正的分水岭：**能看指标定位瓶颈**，而不是靠猜。

---

## 11. 失败模式：kernel 最常见的六个坑

| 现象 | 根因 | 怎么发现 | 怎么修 |
|---|---|---|---|
| 正确但比预期慢 2 倍 | 访存未合并 / bank conflict | ncu 看 DRAM 吞吐 <60% | 调整 tile 布局，shared memory 加 padding |
| 跑到一半崩或变慢 | register spill | ncu 看 local memory 读写量 | 减 tile、加 `__launch_bounds__` |
| 结果偶发错误 | mask / 尾块边界错 | 与参考实现对拍 | 补 tail 分支 |
| 换 batch 组合结果就变 | 浮点归约顺序不同 | 固定 seed 仍复现 | 用 batch-invariant kernel |
| 首 token 卡 5 秒 | JIT 首次编译 | 日志时间戳 | 预热或 AOT 编译 |
| 升级驱动后变慢 | 新架构 SASS 变了 | 版本对比 | 锁 toolchain 版本 |

第二条最隐蔽：寄存器溢出不会报错，只在某些 shape 下慢 30%，很容易被当成"正常波动"。

---

## 11.5 这些 kernel 什么时候不值得

| 结论 | 推翻条件 |
|---|---|
| 用 FlashMLA 而不是 FlashAttention | 序列短于 512 时，MLA 的额外元数据开销盖过收益 |
| 自研 GEMM 而不是 cuBLAS | 只有 FP8 且形状固定时才赚；FP16 下 cuBLAS 更成熟 |
| 手写融合 kernel | 融合后寄存器压力超限，occupancy 反而下降 |
| SWA 限定窗口 | 任务需要跨长距离回忆时，截断直接掉点 |

融合的收益上限可以先算出来：750 次 launch × 4 μs ≈ 3 ms，占 25 ms TPOT 的 12%；融合到 300 次能省 1.8 ms。省不到 1 ms 的融合不值得做，维护成本比收益高。


## 12. 验收：用 ncu（Nsight Compute）看五个指标

| ncu 指标 | 期望 | 说明 |
|---|---|---|
| `sm__throughput` | >60% | SM 整体是否忙 |
| `gpu__dram_throughput` | 按 roofline 判断 | 是否撞带宽墙 |
| `sm__pipe_tensor_cycles_active` | >50% | tensor core 是否真在用 |
| `launch__registers_per_thread` | <128 | 过高会掉占用率 |
| `l1tex__data_bank_conflicts` | 接近 0 | shared memory 冲突 |

**判据**：一个 kernel 优化完，这五个数里至少有两项改善超过 10 个百分点，且端到端吞吐提升可测。
只看 kernel 时间不看端到端，容易优化了 5% 的算子、丢了 20% 的收益。

---

## 13. 收口

> **调度器决定"谁先算"，kernel 决定"算得多快"。
> 前者是你能改的，后者通常要等生态——所以选型时要把 kernel 的可获得性算进去。**

---

## 14. 复盘卡

```
一句话：kernel 是算子在硬件上的实现，融合省的是启动和 HBM 往返，代价是布局变成契约。
我算的：750 次启动 × 4 μs = 3 ms，占 TPOT 25 ms 的 12%。
我原来以为…实际…差在：以为 kernel 只是性能优化，实际它决定了权重格式和硬件选型。
还没懂：online softmax 在稀疏注意力下的数值稳定性怎么保证。
```
