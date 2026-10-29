#!/usr/bin/env python3
"""语气清单：把可疑的句子列出来给人读，不打分、不判阈值。

用法:
    python3 tools/check_voice.py            # 全库统计 + 抽样
    python3 tools/check_voice.py FILE       # 单文件抽样
"""
import re
import sys
import glob
import pathlib
from collections import defaultdict

PATTERNS = {
    "加粗": r"\*\*[^*\n]{2,}\*\*",
    "命令式": r"必须|务必|切记",
    "金句": r"这就是|这才是|一句话[:：]",
    "空洞对偶": r"不是[^。\n]{0,25}而是",
    "转场套话": r"真正的|事实上|换句话说|也就是说|值得注意的是",
}

SKIP = ("README", "OUTLINE", "GLOSSARY", "STYLE", "WRITING-CONTRACT")


def scan(path):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    counts = {k: 0 for k in PATTERNS}
    samples = defaultdict(list)
    for ln in text.split("\n"):
        if ln.lstrip().startswith(("|", "#", "```")) or "迁移自 v1" in ln:
            continue
        for k, p in PATTERNS.items():
            n = len(re.findall(p, ln))
            if n:
                counts[k] += n
                if len(samples[k]) < 2 and len(ln.strip()) > 20:
                    samples[k].append(ln.strip()[:88])
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return counts, samples, cjk


def main(argv):
    files = argv[1:] or sorted(
        f for f in glob.glob("book/**/*.md", recursive=True)
        if not any(x in f for x in SKIP)
    )
    print(f"{'章节':<38}{'字':>6}" + "".join(f"{k:>10}" for k in PATTERNS))
    print("-" * 100)
    rows = []
    for f in files:
        c, s, cjk = scan(f)
        print(f"{f.split('/')[-1]:<38}{cjk:>6}" + "".join(f"{c[k]:>10}" for k in PATTERNS))
        rows.append((sum(c.values()), f, c, s))

    rows.sort(reverse=True)
    print("\n最该读一遍的三章，各抽一句：")
    for total, f, c, s in rows[:3]:
        print(f"\n■ {f.split('/')[-1]}（命中 {total} 处）")
        for k in PATTERNS:
            if s[k]:
                print(f"  [{k}] {s[k][0]}")

    allc = defaultdict(int)
    for _, _, c, _ in rows:
        for k, v in c.items():
            allc[k] += v
    total_cjk = sum(scan(f)[2] for f in files) or 1
    print("\n全库：" + "，".join(f"{k} {v}" for k, v in allc.items()) + "（处）")
    print(f"每千字命中 {sum(allc.values()) / total_cjk * 1000:.1f} 处")


if __name__ == "__main__":
    main(sys.argv)
