#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 03 章实验：KV 显存分配——三种策略能装多少会话？

背景：KV 预算 300GB，每 token 120KB（8K 上下文 ≈ 0.96GB）。
会话长度差异很大（200–8000 token，对数均匀分布）。

三种策略：
  A 预留最大长度：来一个会话就预留 8192 token 的位置（最保守的"连续分配"）
  B 连续按需分配：实际多长占多少（理想情况，忽略外部碎片）
  C 分页分配    ：切成 16 token 的 block，向上取整（PagedAttention 的做法）

  python3 paging_lab.py
"""
import math, random

random.seed(3)
BUDGET_GB = 300
KB_PER_TOKEN = 120
BLOCK_TOK = 16
MAX_LEN = 8192
N_SAMPLE = 2000

budget_tok = BUDGET_GB * 10**9 // KB_PER_TOKEN // 1024   # GB→KB→token
# = 300GB / 120KB = 260 万 token


def sample_len():
    # 对数均匀：短会话多、长会话少（真实分布）
    return int(200 * (8000 / 200) ** random.random())


lens = [sample_len() for _ in range(N_SAMPLE)]


def strategy_reserve():
    """A：每个会话预留 max_len。"""
    used, n = 0, 0
    for L in lens:
        if used + MAX_LEN > budget_tok:
            break
        used += MAX_LEN
        n += 1
    return n, used, used - sum(lens[:n])


def strategy_contiguous():
    """B：按需连续分配（理想：无外部碎片）。"""
    used, n = 0, 0
    for L in lens:
        if used + L > budget_tok:
            break
        used += L
        n += 1
    return n, used, 0


def strategy_paged():
    """C：分页，block=16，向上取整。每请求浪费 < 16 token（内部碎片）。"""
    used, n = 0, 0
    waste = 0
    for L in lens:
        alloc = math.ceil(L / BLOCK_TOK) * BLOCK_TOK
        if used + alloc > budget_tok:
            break
        used += alloc
        waste += alloc - L
        n += 1
    return n, used, waste


print(f"KV 预算 {BUDGET_GB}GB = {budget_tok/1e4:.0f} 万 token")
print(f"会话长度样本 {N_SAMPLE} 个（200–8000，对数均匀，均值 {sum(lens)/len(lens):.0f}）\n")
print(f"{'策略':<22}{'并发会话':>9}{'占用(万tok)':>12}{'浪费率':>8}")
for name, fn in [("A 预留最大长度", strategy_reserve),
                 ("B 连续按需(理想)", strategy_contiguous),
                 ("C 分页 block=16", strategy_paged)]:
    n, used, waste = fn()
    print(f"{name:<22}{n:>9}{used/1e4:>12.1f}{waste/max(used,1):>8.1%}")

print("""
结论：
  · A 看似安全，但 60–80% 显存被"怕它变长"白白预留——并发只有理想的 1/4；
  · C 用固定 block 向上取整，浪费被锁死在每会话 < 16 token，却换来了：
      - 内存按需增长，不用猜最大长度
      - 会话结束立刻回收 block 给别人（无外部碎片）
      - block 可以在会话间共享（前缀复用、beam search 用 copy-on-write）
  · B 只在纸上成立：真实创建/销毁会产生外部碎片，变长后还要搬移——
    PagedAttention 就是把操作系统"虚拟内存分页"的成熟设计搬进来。
""")
nblocks = math.ceil(8192 / 16)
print(f"手算校验：1 个 block = {120 * 16 // 1024} MB；一个 8K 会话 = {nblocks} blocks "
      f"= {nblocks * 120 * 16 // 1024} MB ≈ 0.96 GB")
