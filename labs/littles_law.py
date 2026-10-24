#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证：为什么「日会话数 × 时长 / 86400」= 平均同时在线会话数。

做法：造 100 万个会话，按真实早晚高峰分布到达，逐分钟统计"此刻有多少会话在挂着"。
你会看到两件事：
  1. 平均并发 = λ × W（Little's Law），跟到达是否均匀无关；
  2. 峰值/均值 由"到达有多集中"决定，Little's Law 不管这个。

  python3 littles_law.py
"""
import bisect, math, random

N = 1_000_000          # 会话数（真实是 3000 万，这里等比缩小 30 倍）
D = 600                # 每个会话挂 10 分钟
DAY = 86400
random.seed(42)


def diurnal_weight(hour):
    """早晚两个高峰：9 点、20 点。返回值越大，该时刻越容易来会话。"""
    return (0.15
            + 1.00 * math.exp(-((hour - 9) ** 2) / 2.0)
            + 0.85 * math.exp(-((hour - 20) ** 2) / 3.0))


# 1) 构造 1440 分钟的到达概率表
mins = 1440
w = [diurnal_weight(m / 60) for m in range(mins)]
total = sum(w)
cdf, acc = [], 0.0
for x in w:
    acc += x / total
    cdf.append(acc)


def sample_minute():
    r = random.random()
    return bisect.bisect_left(cdf, r)


# 2) 生成到达时刻（秒）
starts = sorted(random.random() * 60 + sample_minute() * 60 for _ in range(N))
ends = sorted(s + D for s in starts)

# 3) 逐分钟统计并发
peak, peak_at, acc = 0, 0, 0.0
points = 0
for m in range(mins):
    t = m * 60
    live = bisect.bisect_right(starts, t) - bisect.bisect_right(ends, t)
    acc += live
    points += 1
    if live > peak:
        peak, peak_at = live, m

avg = acc / points
pred = N * D / DAY
scale = 30_000_000 / N       # 换算回真实规模

print(f"会话数 N={N:,}  时长 D={D}s")
print(f"Little's Law 预测平均并发 : {pred:>10,.0f}")
print(f"逐分钟实测平均并发        : {avg:>10,.0f}   （误差 {abs(avg-pred)/pred:.2%}）")
print(f"实测峰值                  : {peak:>10,.0f}  @ {peak_at//60:02d}:{peak_at%60:02d}")
print(f"峰均比                    : {peak/avg:>10.2f}")
print()
print(f"换算到 3000 万会话：平均 {avg*scale/1e4:.0f} 万，高峰 {peak*scale/1e4:.0f} 万")
print()
print("结论：")
print("  · 平均值只由 λ 和 W 决定，跟你几点上班、有没有高峰无关——这就是 Little's Law。")
print("  · 峰值由『到达有多集中』决定，是另一个假设（我们取 3×）。")
print("  · 所以 62 万 = 平均 21 万 × 3，前者是算出来的，后者是估出来的。")
