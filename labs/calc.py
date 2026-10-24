#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
参数化容量计算器：改一个数，立刻看见 GPU/成本/时延怎么变。

用法：
  python3 calc.py                           # 基准
  python3 calc.py --dau 30m --ctx 3000      # 3000 万 DAU、3K 上下文
  python3 calc.py --hit 0.5                 # 缓存命中降到 50%
  python3 calc.py --preset small            # 换成 8B 小模型
  python3 calc.py --sweep hit               # 扫一遍缓存命中率
"""
import argparse, math

P = dict(
    dau=20_000_000, sessions=1.5, turns=5,
    ctx=5_000, out=350, hit=0.70,
    peak_share=0.12, burst=1.4,
    total_b=200, active_b=30,
    fp8_pf=1.98, mfu=0.35, dec_tok_s=3500,
    headroom=1.3, az=1.2,
    node_cny=1_200_000, years=3, kw=6.0, kwh=0.60, pue=1.25, colo=6000,
    train_gpu=512, other=90_000_000,
)


def compute(p):
    turns = p["dau"] * p["sessions"] * p["turns"]
    qps = turns * p["peak_share"] / 3600 * p["burst"]
    prefill_tok = p["ctx"] * (1 - p["hit"])
    prefill_gpu = qps * prefill_tok * 2 * p["active_b"] * 1e9 / (p["fp8_pf"] * 1e15 * p["mfu"])
    decode_gpu = qps * p["out"] / p["dec_tok_s"]
    gpus = math.ceil((prefill_gpu + decode_gpu) * p["headroom"] * p["az"] / 8) * 8
    nodes = gpus // 8
    node_cost = p["node_cny"] / p["years"] + p["kw"] * 24 * 365 * p["kwh"] * p["pue"] + p["colo"] * 12
    year = nodes * node_cost + (p["train_gpu"] / 8) * node_cost + p["other"]
    tokens_day = turns * (p["ctx"] + p["out"])
    return {
        "日轮次": turns, "峰值QPS": qps,
        "Prefill卡": prefill_gpu, "Decode卡": decode_gpu, "总卡": gpus, "节点": nodes,
        "年成本": year, "单轮(分)": year / 365 / turns * 100,
        "单DAU(分)": year / 365 / p["dau"] * 100,
        "每百万token(元)": year / 365 / (tokens_day / 1e6),
        "并发": qps * 3.5,
    }


def fmt(r):
    return (f"日轮次 {r['日轮次']/1e8:>6.2f} 亿 | 峰值 QPS {r['峰值QPS']:>6.0f} | "
            f"Prefill {r['Prefill卡']:>6.0f} 卡 | Decode {r['Decode卡']:>6.0f} 卡 | "
            f"总 {r['总卡']:>5.0f} 卡(含1.56冗余) | 年成本 {r['年成本']/1e8:>5.2f} 亿 | "
            f"单轮 {r['单轮(分)']:>4.2f} 分")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dau", type=str, help="如 20m / 30m / 500000")
    ap.add_argument("--ctx", type=int, help="输入 token")
    ap.add_argument("--out", type=int, help="输出 token")
    ap.add_argument("--hit", type=float, help="缓存命中率 0-1")
    ap.add_argument("--active", type=float, help="激活参数 B")
    ap.add_argument("--preset", choices=["base", "small", "nocache", "longctx"])
    ap.add_argument("--sweep", choices=["hit", "ctx", "dau"])
    a = ap.parse_args()

    p = dict(P)
    if a.preset == "small":
        p.update(total_b=8, active_b=8, fp8_pf=1.98, dec_tok_s=9000)
    elif a.preset == "nocache":
        p.update(hit=0.0)
    elif a.preset == "longctx":
        p.update(ctx=30_000)
    if a.dau:
        p["dau"] = int(float(a.dau.rstrip("m")) * 1e6) if a.dau.endswith("m") else int(a.dau)
    if a.ctx: p["ctx"] = a.ctx
    if a.out: p["out"] = a.out
    if a.hit is not None: p["hit"] = a.hit
    if a.active: p["active_b"] = a.active

    if a.sweep:
        print(f"{'参数':<12}{'总卡':>7}{'年成本(亿)':>12}{'单轮(分)':>10}")
        vals = {"hit": [0.3, 0.5, 0.7, 0.85, 0.95],
                "ctx": [2000, 3000, 5000, 8000, 16000],
                "dau": [5e6, 1e7, 2e7, 3e7, 5e7]}[a.sweep]
        for v in vals:
            q = dict(p)
            key = {"hit": "hit", "ctx": "ctx", "dau": "dau"}[a.sweep]
            q[key] = v
            r = compute(q)
            label = f"{v:.0%}" if a.sweep == "hit" else (f"{v/1e6:.0f}m" if a.sweep == "dau" else str(v))
            print(f"{label:<12}{r['总卡']:>7.0f}{r['年成本']/1e8:>12.2f}{r['单轮(分)']:>10.2f}")
        return

    r = compute(p)
    print(fmt(r))
    print(f"  └ 并发 {r['并发']/1e4:.1f} 万 | 每百万 token {r['每百万token(元)']:.2f} 元 | 单 DAU 日 {r['单DAU(分)']:.2f} 分")
    print(f"  └ 假设：输入 {p['ctx']} / 输出 {p['out']} token，命中 {p['hit']:.0%}，"
          f"激活 {p['active_b']:.0f}B，单卡 {p['dec_tok_s']} tok/s")


if __name__ == "__main__":
    main()
