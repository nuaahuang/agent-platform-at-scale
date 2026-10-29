#!/usr/bin/env python3
"""契约核对：把 book/WRITING-CONTRACT.md 的规则变成可执行检查。

用法:
    python3 tools/check_contract.py            # 全部章节
    python3 tools/check_contract.py FILE...    # 指定文件
退出码: 0 全通过；1 有 ❌（用于 make check）
"""
import re
import sys
import glob
import pathlib

ABBR = {
    "TTFT": "Time To First Token", "TPOT": "Time Per Output Token",
    "SLO": "Service Level Objective", "SLI": "Service Level Indicator",
    "SLA": "Service Level Agreement", "KEDA": "Kubernetes Event-driven Autoscaling",
    "HPA": "Horizontal Pod Autoscaler", "FFI": "Foreign Function Interface",
    "CTA": "Cooperative Thread Array", "EPLB": "Expert Parallel Load Balancer",
    "RDMA": "Remote Direct Memory Access",
}
FAIL_SECTION = re.compile(r"失败模式|失败清单|什么时候会坏")
NEGATE = re.compile(r"推翻条件|否了|否决|否掉")


def cjk(s):
    return len(re.findall(r"[\u4e00-\u9fff]", s))


def check_r07(text):
    if not FAIL_SECTION.search(text):
        return False, "缺失败模式小节"
    return True, ""


def check_r09(text):
    if not re.search(r"候选|选型|方案对比", text):
        return True, ""
    if NEGATE.search(text):
        return True, ""
    return False, "有选型内容但没写'否了什么/推翻条件'"


def check_r10(text):
    lines = text.split("\n")
    last_table_end = -1
    fresh_prose = True
    bad = 0
    for i, ln in enumerate(lines):
        is_row = ln.lstrip().startswith("|")
        if is_row:
            if last_table_end == -1:
                fresh_prose = True
            elif not fresh_prose:
                bad += 1
            last_table_end = i
            continue
        if ln.strip() and not ln.lstrip().startswith("#"):
            if cjk(ln) >= 20:
                fresh_prose = True
            elif last_table_end != -1 and i > last_table_end:
                pass
    # 简化实现：统计"表格块之间无正文"的次数
    blocks, cur, prose_between = [], [], []
    for ln in lines:
        if ln.lstrip().startswith("|"):
            cur.append(ln)
        else:
            if cur:
                blocks.append(cur)
                cur = []
            prose_between.append(ln)
    if cur:
        blocks.append(cur)
    bad = 0
    between = []
    cur_prose = []
    for ln in lines:
        if ln.lstrip().startswith("|"):
            between.append(cur_prose)
            cur_prose = []
            while lines and False:
                break
        else:
            cur_prose.append(ln)
    # 用块序列判断：块 n 与块 n+1 之间的文本
    idx = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("|")]
    if not idx:
        return True, ""
    groups, prev = [], idx[0]
    for i in idx[1:]:
        if i - prev > 1:
            groups.append((prev, groups[-1][1] if groups else prev))
            groups.append((i, i))
        prev = i
    spans, start, last = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - last > 1:
            spans.append((start, last))
            start = i
        last = i
    spans.append((start, last))
    for a, b in zip(spans, spans[1:]):
        gap = "\n".join(lines[a[1] + 1:b[0]])
        if not any(cjk(x) >= 20 for x in gap.split("\n")):
            bad += 1
    if bad:
        return False, f"{bad} 处两表之间没有说明文字"
    return True, ""


META = (r"我没实测|我没有实测|我不确定|我拿不准|我没有答案|本章没有验证|"
        r"没有验证的|没复现过|按块计数推断|按.{0,6}推断")


def check_r11(text):
    hits = re.findall(META, text)
    if hits:
        return False, f"写作旁白 {len(hits)} 处：{'、'.join(sorted(set(hits))[:3])}"
    return True, ""


def check_r12(text):
    lines = [l for l in text.split("\n") if l.strip()]
    tables = sum(1 for l in lines if l.lstrip().startswith("|"))
    share = tables / len(lines) * 100 if lines else 0
    closing = len(re.findall(r"一句话[:：]|这就是|这才是", text))
    n = cjk(text)
    cper = closing / (n / 1e4) if n else 0
    msgs = []
    if share > 45:
        msgs.append(f"表占比 {share:.0f}%")
    if cper > 6:
        msgs.append(f"收口 {cper:.1f}/万字")
    return (not msgs), "、".join(msgs)


def check_r13(text):
    fake = len(re.findall(r"不是[^。\n]{0,25}而是", text))
    must = len(re.findall(r"必须", text))
    n = cjk(text) / 1e4 or 1
    msgs = []
    if fake / n > 1.5:
        msgs.append(f"假对比 {fake} 处")
    if must / n > 18:
        msgs.append(f"'必须' {must} 处")
    return (not msgs), "、".join(msgs)


def check_r14(text):
    warns = []
    for ab, full in ABBR.items():
        m = re.search(rf"\b{ab}\b", text)
        if not m:
            continue
        win = text[max(0, m.start() - 60): m.start() + 60]
        if full not in win and "（" not in win and "(" not in win:
            warns.append(ab)
    return (not warns), ("首现未给全称: " + "、".join(warns)) if warns else ""


CHECKS = [
    ("R07", check_r07, True),
    ("R09", check_r09, True),
    ("R10", check_r10, False),   # 用户 2026-10-29：不调结构
    ("R11", check_r11, True),
    ("R12", check_r12, False),   # 同上：不做配额
    ("R13", check_r13, False),   # 语气类，只提示
    ("R14", check_r14, False),   # 只提示
]


def main(argv):
    files = argv[1:] or sorted(
        f for f in glob.glob("book/**/*.md", recursive=True)
        if not any(x in f for x in ("README", "OUTLINE", "GLOSSARY", "STYLE", "WRITING-CONTRACT"))
    )
    hard = 0
    print(f"{'章节':<40}" + "".join(f"{rid:>6}" for rid, _, _ in CHECKS))
    print("-" * (40 + 6 * len(CHECKS)))
    details = []
    for f in files:
        text = pathlib.Path(f).read_text(encoding="utf-8")
        marks = []
        for rid, fn, is_hard in CHECKS:
            ok, msg = fn(text)
            if ok:
                marks.append("  ✅")
            else:
                marks.append("  ❌" if is_hard else "  ⚠️")
                details.append((f.split("/")[-1], rid, msg))
                if is_hard:
                    hard += 1
        print(f"{f.split('/')[-1]:<40}" + "".join(f"{m:>6}" for m in marks))
    print("-" * (40 + 6 * len(CHECKS)))
    print("\n不合格明细：")
    for name, rid, msg in details:
        print(f"  {rid}  {name:<40}{msg}")
    print(f"\n硬性不合格 {hard} 项")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
