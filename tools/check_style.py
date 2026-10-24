#!/usr/bin/env python3
"""章节写作标准检查器（对应 book/STYLE.md）。

用法：
    python3 tools/check_style.py                # 检查 book/ 下所有章节
    python3 tools/check_style.py path/to.md     # 检查单个文件
    python3 tools/check_style.py --warn-only    # 只警告，不返回非零

退出码：0 全部通过；1 有硬性失败。
"""
import re
import sys
from pathlib import Path

# ---- 阈值（与 STYLE.md §2 一致）----
MIN_NUM_PER_100 = 45
MIN_TABLE_LINES = 25
MIN_CODE_BLOCKS = 3
MIN_HEADINGS = 10
MIN_CHARS = 5000

BANNED = ["赋能", "抓手", "闭环生态", "全面提升", "业界领先", "颠覆", "革命性"]
VAGUE = ["很多", "一些", "大量", "较快", "比较好", "相当", "大幅", "显著", "明显", "极大"]

# 八段骨架的宽松匹配（允许同义）
SECTIONS = {
    "问题": r"问题|背景|动机",
    "原理": r"原理|物理|机制|为什么",
    "设计": r"设计|架构|选型|方案",
    "数字": r"数字|测算|容量|成本|延迟|实验",
    "失败模式": r"失败|故障|误区|坑|出错",
    "验收": r"验收|自测|检查|指标",
    "复盘": r"复盘",
}


def measure(text: str) -> dict:
    lines = text.split("\n")
    n = len(lines)
    nums = len(re.findall(r"\d+(?:\.\d+)?", text))
    table = sum(1 for l in lines if l.strip().startswith("|"))
    code = text.count("```") // 2
    heads = len(re.findall(r"^#{1,3} ", text, re.M))
    chars = len(re.sub(r"\s", "", text))
    return {
        "lines": n, "nums": nums,
        "num_per_100": nums / max(n, 1) * 100,
        "table": table, "code": code, "heads": heads, "chars": chars,
    }


def check(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    m = measure(text)
    problems = []

    # 附录/参考类文件可豁免部分骨架（如附录 A 没有"设计"与"验收"）
    exempt = set()
    head = text[:200]
    if head.startswith("<!-- style-exempt:"):
        exempt = {x.strip() for x in head.split(":", 1)[1].split("-->")[0].split(",")}

    # 硬指标
    if m["num_per_100"] < MIN_NUM_PER_100:
        problems.append(f"数字密度 {m['num_per_100']:.1f}/百行 < {MIN_NUM_PER_100}")
    if m["table"] < MIN_TABLE_LINES:
        problems.append(f"表格 {m['table']} 行 < {MIN_TABLE_LINES}")
    if m["code"] < MIN_CODE_BLOCKS:
        problems.append(f"代码/图块 {m['code']} < {MIN_CODE_BLOCKS}")
    if m["heads"] < MIN_HEADINGS:
        problems.append(f"小标题 {m['heads']} < {MIN_HEADINGS}")
    if m["chars"] < MIN_CHARS:
        problems.append(f"字数 {m['chars']} < {MIN_CHARS}")

    # 禁止词
    for w in BANNED:
        if w in text:
            problems.append(f"禁止词「{w}」")
    for w in VAGUE:
        hits = text.count(w)
        if hits:
            problems.append(f"模糊量词「{w}」×{hits}（应给具体值）")

    # 骨架
    for name, pat in SECTIONS.items():
        if name in exempt:
            continue
        if not re.search(rf"^#{{1,3}} .*({pat})", text, re.M):
            problems.append(f"缺少「{name}」小节")

    # 可复算：必须引用实验或脚本
    if not re.search(r"labs/[\w./-]+\.py|实验|脚本", text):
        problems.append("未引用任何 labs 脚本或实验")

    return problems


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    warn_only = "--warn-only" in sys.argv
    root = Path("book")

    files = [Path(a) for a in args] if args else sorted(
        p for p in root.rglob("*.md")
        if p.name not in {"README.md", "STYLE.md", "OUTLINE.md", "OUTLINE-v3.md", "MIGRATION.md"}
    )

    if not files:
        print("没有找到章节文件")
        return 0

    failed = 0
    print(f"{'章节':52}{'字':>7}{'数/百行':>9}{'表':>5}{'图':>4}{'题':>4}  结果")
    print("-" * 100)
    for p in files:
        probs = check(p)
        m = measure(p.read_text(encoding="utf-8"))
        ok = "✅" if not probs else "❌"
        print(f"{str(p):52}{m['chars']:>7}{m['num_per_100']:>9.1f}"
              f"{m['table']:>5}{m['code']:>4}{m['heads']:>4}  {ok}")
        if probs:
            failed += 1
            for q in probs:
                print(f"    · {q}")

    print("-" * 100)
    print(f"{len(files) - failed}/{len(files)} 通过")
    if failed and not warn_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
