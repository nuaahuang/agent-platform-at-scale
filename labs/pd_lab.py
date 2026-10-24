#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 04 章实验：prefill 突发对 decode 的三种处理方式。

模拟 20 秒、7 QPS 到达的请求（70% 命中前缀只需算 1500 tok，其余 3k–8k tok）。
GPU 上同时有 ~40 个用户在 decode。对比：

  colocated：prefill 独占 GPU，期间 decode 全部被挡住（TPOT 尖刺）
  chunked   ：prefill 切小块，每步分 30% 算力给它（TPOT 从 25ms 抬到 36ms，但无尖刺）
  pd        ：prefill 在独立池（2 卡），decode 永远 25ms；额外 20ms KV 传输

资源说明：这是"decode 体验"对比实验，不做总成本对比（PD 要多占 prefill 池，
正文用容量模型算总账）。

  python3 pd_lab.py
"""
import random, statistics

random.seed(7)
SIM_MS = 20_000
ARR_RATE = 7 / 1000            # 7 QPS
PF_TOK_PER_MS = 40             # prefill 有效算力 40,000 tok/s
BASE_TPOT = 25
CHUNK_TPOT = 36                # 25 / 0.7（分给 prefill 30%）
CHUNK_RATE = PF_TOK_PER_MS * 0.3
KV_XFER = 20
INIT_USERS = 40

# ---------- 生成到达 ----------
arrivals = []
t = 0.0
while t < SIM_MS:
    t += random.expovariate(ARR_RATE)
    plen = 1500 if random.random() < 0.7 else random.choice([3000, 5000, 8000])
    out = random.randint(150, 400)
    arrivals.append((t, plen, out))


def schedule_windows(mode):
    """返回 (prefill 窗口 [(s,e)], 每个请求的首 token 时间 birth)。"""
    windows, births = [], []
    if mode == "pd":                      # prefill 池 2 卡
        import heapq
        slots = [0.0, 0.0]
        for arr, plen, _ in arrivals:
            s = heapq.heappop(slots)
            st = max(arr, s)
            e = st + plen / PF_TOK_PER_MS
            heapq.heappush(slots, e)
            windows.append((st, e))
            births.append(e + KV_XFER)    # KV 跨池 RDMA 传输
    else:
        rate = PF_TOK_PER_MS if mode == "colocated" else CHUNK_RATE
        busy_until = 0.0
        for arr, plen, _ in arrivals:
            st = max(arr, busy_until)
            e = st + plen / rate
            busy_until = e
            windows.append((st, e))
            births.append(e)
    windows.sort()
    return windows, births


def emit_times(birth, n_tok, mode, windows):
    """一个 decode 用户每个 token 的到达时间。"""
    out, last = [], birth
    for _ in range(n_tok):
        nxt = last + BASE_TPOT
        if mode == "colocated":
            for s, e in windows:          # 正好落在某个 prefill 窗口里 → 被挡到窗口结束
                if s < nxt < e:
                    nxt = e
        elif mode == "chunked":
            if any(s < nxt < e for s, e in windows):
                nxt = last + CHUNK_TPOT   # 该步与 prefill 分享 GPU
        out.append(nxt)
        last = nxt
        if last > SIM_MS:
            break
    return out


def run(mode):
    windows, births = schedule_windows(mode)
    tpots, ttfts = [], []
    users = [(0, random.randint(150, 400)) for _ in range(INIT_USERS)]
    users += list(zip(births, [a[2] for a in arrivals]))
    for i, (birth, out) in enumerate(users):
        if i >= INIT_USERS:
            plen = arrivals[i - INIT_USERS][1]
            # TTFT 只统计本请求的 prefill 服务时间（不含排队，排队是容量问题，见正文）
            if mode == "pd":
                ttft = plen / PF_TOK_PER_MS + KV_XFER
            elif mode == "chunked":
                ttft = plen / CHUNK_RATE
            else:
                ttft = plen / PF_TOK_PER_MS
            ttfts.append(ttft)
        times = emit_times(birth, out, mode, windows)
        tpots += [b - a for a, b in zip(times, times[1:])]
    return tpots, ttfts


print(f"{'模式':<12}{'TPOT p50':>10}{'TPOT p95':>10}{'TPOT max':>10}"
      f"{'TTFT p95':>10}{'超50ms占比':>12}")
for mode, label in [("colocated", "混部独占"), ("chunked", "Chunked"), ("pd", "PD 分离")]:
    tpots, ttfts = run(mode)
    over = sum(1 for x in tpots if x > 50) / len(tpots)
    print(f"{label:<12}{statistics.median(tpots):>9.0f}ms"
          f"{sorted(tpots)[int(len(tpots)*0.95)]:>9.0f}ms"
          f"{max(tpots):>9.0f}ms"
          f"{sorted(ttfts)[int(len(ttfts)*0.95)]:>9.0f}ms"
          f"{over:>11.1%}")

print("""
读法：
  · 混部：p50 正常但 p95/max 出现几百毫秒尖刺——长 prefill 一跑，所有 decode 用户卡一下；
  · Chunked：TPOT 整体抬到 ~36ms 但平滑，代价是 TTFT 变长（prefill 被拖到 3.3 倍时间）；
  · PD 分离：decode 永远 25ms，TTFT 只多 20ms KV 传输——两个指标各自独立、各自扩容。
""")
