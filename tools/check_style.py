#!/usr/bin/env python3
"""章节写作标准检查器（对应 book/STYLE.md）。

设计原则：**结构自由，底线严格。**

硬失败（退出码 1）只有四条：
  1. 出现禁止词（空话/万能句）
  2. 全文没有任何数字
  3. 既没有表格也没有代码/图
  4. 没有一句话结论（文件末尾 30 行内无收口段）

密度只是参考区间：低于区间给提示，不判失败。

用法：
    python3 tools/check_style.py
    python3 tools/check_style.py book/part-03-inference/10-inference-engine.md
    python3 tools/check_style.py --strict     # 密度低于区间也算失败（用于终稿）
"""
import re
import sys
from pathlib import Path

BANNED = ["赋能", "抓手", "闭环生态", "全面提升", "业界领先", "颠覆", "革命性",
          "随着业务发展", "在当今时代", "众所周知"]
SOFT_VAGUE = ["很多", "一些", "大量", "较快", "比较好", "相当", "大幅", "显著", "明显", "极大"]

# 参考区间：(字数下限, 数字/百行下限, 表格行下限, 代码块下限)
RANGES = {
    "light":    (2500, 15, 8, 1),
    "standard": (5000, 40, 25, 3),
    "dense":    (8000, 70, 40, 5),
}

# 八问的弱匹配（仅用于覆盖率展示，不判失败）
QUESTIONS = {
    "问题": r"问题|背景|动机|故障|需求",
    "机制": r"原理|物理|机制|为什么|推导|公式",
    "选型": r"设计|选型|方案|架构|取舍|对比",
    "代价": r"成本|代价|延迟|容量|数字|测算|元|ms",
    "验证": r"验收|验证|压测|自测|判据|检查",
    "失败": r"失败|故障|误区|坑|边界|不成立",
    "备选": r"备选|其他方案|延伸|对比|替代",
    "收口": r"一句话|收口|结论|复盘",
}


def measure(text: str) -> dict:
    lines = text.split("\n")
    n = len(lines)
    return {
        "lines": n,
        "nums": len(re.findall(r"\d+(?:\.\d+)?", text)),
        "num_per_100": len(re.findall(r"\d+(?:\.\d+)?", text)) / max(n, 1) * 100,
        "table": sum(1 for l in lines if l.strip().startswith("|")),
        "code": text.count("```") // 2,
        "chars": len(re.sub(r"\s", "", text)),
    }


def profile_of(text: str) -> str:
    m = re.search(r"<!--\s*style-profile:\s*(\w+)\s*-->", text)
    return m.group(1) if m and m.group(1) in RANGES else "standard"


def check(path: Path):
    text = path.read_text(encoding="utf-8")
    m = measure(text)
    prof = profile_of(text)
    hard, soft = [], []

    # ---- 硬底线 ----
    for w in BANNED:
        if w in text:
            hard.append(f"禁止词「{w}」")
    if m["nums"] == 0:
        hard.append("全文没有任何数字")
    if m["table"] == 0 and m["code"] == 0:
        hard.append("既没有表格也没有代码/图")
    tail = "\n".join(text.split("\n")[-30:])
    if not re.search(r"一句话|收口|结论是|小结|记住", tail):
        hard.append("末尾 30 行没有收口句")

    # ---- 参考区间（软）----
    chars_min, num_min, tbl_min, code_min = RANGES[prof]
    if m["chars"] < chars_min:
        soft.append(f"字数 {m['chars']} < {prof} 区间下限 {chars_min}")
    if m["num_per_100"] < num_min:
        soft.append(f"数字密度 {m['num_per_100']:.0f}/百行 < 区间下限 {num_min}")
    if m["table"] < tbl_min:
        soft.append(f"表格 {m['table']} < 区间下限 {tbl_min}")
    if m["code"] < code_min:
        soft.append(f"图/代码 {m['code']} < 区间下限 {code_min}")

    # ---- 提示 ----
    for w in SOFT_VAGUE:
        c = text.count(w)
        if c:
            soft.append(f"「{w}」×{c}（有数字就忽略）")

    cover = sum(1 for p in QUESTIONS.values() if re.search(p, text))
    return hard, soft, m, prof, cover


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    strict = "--strict" in sys.argv
    files = [Path(a) for a in args] if args else sorted(
        p for p in Path("book").rglob("*.md")
        if p.name not in {"README.md", "STYLE.md", "OUTLINE.md", "OUTLINE-v3.md", "MIGRATION.md", "GLOSSARY.md"}
    )
    if not files:
        print("没有找到章节文件"); return 0

    print(f"{'章节':50}{'字':>7}{'数/百':>6}{'表':>4}{'图':>4}{'档':>6}{'八问':>5}  结果")
    print("-" * 100)
    hard_fail = 0
    for f in files:
        hard, soft, m, prof, cover = check(f)
        bad = bool(hard) or (strict and bool(soft))
        hard_fail += 1 if bad else 0
        mark = "❌" if hard else ("⚠️" if soft else "✅")
        print(f"{str(f):50}{m['chars']:>7}{m['num_per_100']:>6.0f}{m['table']:>4}"
              f"{m['code']:>4}{prof:>8}{cover:>4}/8  {mark}")
        for x in hard:
            print(f"    ✗ {x}")
        for x in soft:
            print(f"    · {x}")

    print("-" * 100)
    print(f"{len(files)-hard_fail}/{len(files)} 通过硬底线"
          + ("（strict：密度不足也计失败）" if strict else "；密度仅参考"))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
