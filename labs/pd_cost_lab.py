#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 04 章实验 2：四种部署架构的卡数 / 成本 / SLO 对比。

同一业务负载（20M DAU、1.5 亿轮/日、7000 峰值 QPS、输入 5000、输出 350、
前缀命中 70%），只改"怎么部署"：

  A 混部同构      H800 同时跑 prefill+decode，靠超配压尖刺
  B PD 分离同构   H800 池 + H800 池
  C PD 分离异构   H800 池 + H200 池
  D PD 分离异构   H800 池 + H20 池（中国区合规）

  python3 pd_cost_lab.py
"""

DAU = 20_000_000
SESSIONS_PER_DAU = 1.5
TURNS_PER_SESSION = 5
IN_TOK, OUT_TOK = 5_000, 350
HIT = 0.70
PEAK_QPS = 7_000
HEADROOM, AZ = 1.30, 1.20        # 与 capacity_model.py 一致
PREFILL_MFU = 0.35
N_ACTIVE = 30e9

# 硬件：每 GPU 参数
HW = {
    "H800": dict(fp8_pf=1.98, bw=3.35, hbm=80,  node_cny=1_200_000),
    "H200": dict(fp8_pf=1.98, bw=4.80, hbm=141, node_cny=1_560_000),  # capex ×1.30（假设）
    "H20":  dict(fp8_pf=0.30, bw=4.00, hbm=96,  node_cny=1_020_000),  # capex ×0.85（假设）
}
DEC_BASE = 3_500                 # H800 实测有效 decode tok/s/GPU（3.35TB/s 下）

turns_day = DAU * SESSIONS_PER_DAU * TURNS_PER_SESSION
out_tok_day = turns_day * OUT_TOK
in_tok_day = turns_day * IN_TOK
cached = in_tok_day * HIT
pf_tok_peak = PEAK_QPS * IN_TOK * (1 - HIT)        # 峰值 prefill token/s（未命中部分）
out_tok_peak = PEAK_QPS * OUT_TOK                  # 峰值 output token/s
concurrent = PEAK_QPS * 3.5
kv_per_sess = IN_TOK * 120 / 1e6                   # 0.6 GB @8K
kv_total_tb = concurrent * kv_per_sess / 1000

NODE_POWER_KW, KWH, PUE, COLO_M, YEARS = 6.0, 0.60, 1.25, 6_000, 3


def node_cost(hw):
    return (hw["node_cny"] / YEARS
            + NODE_POWER_KW * 24 * 365 * KWH * PUE
            + COLO_M * 12)


def pf_gpus(hw_name):
    hw = HW[hw_name]
    return pf_tok_peak * 2 * N_ACTIVE / (hw["fp8_pf"] * 1e15 * PREFILL_MFU)


def dec_gpus(hw_name):
    hw = HW[hw_name]
    per_gpu = DEC_BASE * hw["bw"] / 3.35          # 带宽受限：按带宽等比缩放
    return out_tok_peak / per_gpu


def build(name, p_hw, d_hw, contention=1.0, tpot_p95=25, slo_miss=0.0):
    p, d = pf_gpus(p_hw), dec_gpus(d_hw)
    raw = (p + d) * contention * HEADROOM * AZ
    gpus = int(-(-raw // 8)) * 8
    nodes = gpus // 8
    cost = (p / 8) * node_cost(HW[p_hw]) + (d / 8) * node_cost(HW[d_hw])
    cost *= HEADROOM * AZ * contention
    return dict(name=name, p_gpu=round(p), d_gpu=round(d), gpus=gpus, nodes=nodes,
                cost=cost, tpot=tpot_p95, miss=slo_miss)


rows = [
    build("A 混部同构 H800", "H800", "H800", contention=1.20, tpot_p95=75, slo_miss=10.8),
    build("B PD 同构 H800", "H800", "H800", contention=1.00, tpot_p95=25, slo_miss=0.0),
    build("C PD 异构 H800+H200", "H800", "H200", contention=1.00, tpot_p95=25, slo_miss=0.0),
    build("D PD 异构 H800+H20", "H800", "H20", contention=1.00, tpot_p95=25, slo_miss=0.0),
]

base = rows[0]
print(f"负载：{turns_day/1e8:.1f} 亿轮/日，峰值 prefill {pf_tok_peak/1e6:.1f}M tok/s，"
      f"output {out_tok_peak/1e6:.2f}M tok/s，并发 {concurrent/1e4:.1f} 万，KV {kv_total_tb:.1f} TB\n")
print(f"{'架构':<22}{'P卡':>6}{'D卡':>6}{'总卡':>7}{'节点':>6}"
      f"{'年成本(亿)':>11}{'vs A':>8}{'TPOT p95':>9}{'SLO未达':>8}"
      f"{'每GPU日产出(万tok)':>18}{'元/百万输出tok':>15}")
for r in rows:
    gpu_day = out_tok_day / r["gpus"] / 1e4
    cny_m = r["cost"] / (out_tok_day * 365 / 1e6)
    print(f"{r['name']:<22}{r['p_gpu']:>6}{r['d_gpu']:>6}{r['gpus']:>7}{r['nodes']:>6}"
          f"{r['cost']/1e8:>11.3f}{(r['cost']/base['cost']-1)*100:>7.1f}%"
          f"{r['tpot']:>8}ms{r['miss']:>7.1f}%{gpu_day:>18.1f}{cny_m:>15.3f}")

print(f"""
关键差值：
  B vs A：卡数 {(rows[1]['gpus']/rows[0]['gpus']-1)*100:+.1f}%，成本 {(rows[1]['cost']/rows[0]['cost']-1)*100:+.1f}%，
          TPOT p95 75ms→25ms，SLO 未达 10.8%→0
  C vs B：卡数 {(rows[2]['gpus']/rows[1]['gpus']-1)*100:+.1f}%，成本 {(rows[2]['cost']/rows[1]['cost']-1)*100:+.1f}%
          （H200 单价按 +30% 假设；若溢价超过带宽收益 43%，C 反而更贵）
  D vs B：卡数 {(rows[3]['gpus']/rows[1]['gpus']-1)*100:+.1f}%，成本 {(rows[3]['cost']/rows[1]['cost']-1)*100:+.1f}%
  C vs A：卡数 {(rows[2]['gpus']/rows[0]['gpus']-1)*100:+.1f}%，成本 {(rows[2]['cost']/rows[0]['cost']-1)*100:+.1f}%

口径说明：
  · P 卡 = 峰值 prefill token/s ÷ (单卡峰值算力 × MFU 35%)；
  · D 卡 = 峰值 output token/s ÷ 单卡有效吞吐（按 HBM 带宽等比缩放 H800 的 3500）；
  · 混部 A 乘 1.20 争用系数：两种负载互相打断，要压住尖刺只能超配（实验 pd_lab.py 的 224ms 尖刺）；
  · 未计 RDMA 交换机端口、光模块、warm pool 的额外成本（约占 GPU 成本 3–6%，见第 12 章）。
  · H200/H20 的整机价是假设值，真实采购价必须替换后重跑。
""")
