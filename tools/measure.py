#!/usr/bin/env python3
"""统计已完稿章节的写作密度，用于校准 STYLE.md 的阈值。"""
import re
import sys
from pathlib import Path

files = sys.argv[1:] or sorted(Path("book").rglob("*.md"))
print(f"{'文件':52}{'行':>6}{'数字':>7}{'数/百行':>9}{'表行':>6}{'块':>4}{'标题':>5}")
for f in map(Path, files):
    if not f.is_file():
        continue
    s = f.read_text(encoding="utf-8")
    L = s.split("\n")
    n = len(L)
    nums = len(re.findall(r"\d+(?:\.\d+)?", s))
    tbl = sum(1 for l in L if l.strip().startswith("|"))
    code = s.count("```") // 2
    heads = len(re.findall(r"^#{1,3} ", s, re.M))
    print(f"{str(f):52}{n:>6}{nums:>7}{nums/max(n,1)*100:>9.1f}{tbl:>6}{code:>4}{heads:>5}")
