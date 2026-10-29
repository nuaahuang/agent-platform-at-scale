#!/usr/bin/env python3
"""人味体检：报告模板化写作的密度指标。不判失败，只给数。

用法:
    python3 tools/check_voice.py                 # 全部章节
    python3 tools/check_voice.py book/part-02-model/04-model-selection-eval.md
"""
import re
import sys
import glob
import statistics
import pathlib

# 每万字的目标区间（下限, 上限），None 表示只限一侧
LIMITS = {
    "元评论": (None, 0.0),       # "我没实测/我不确定" 这类旁白
    "收口": (None, 6.0),          # 一句话/这就是/这才是
    "表行": (None, 400.0),        # 表格行数
    "必须": (None, 18.0),         # 命令式语气
    "假对比": (None, 1.5),        # 不是X而是Y
}

PATTERNS = {
    "元评论": r"我没实测|我没有实测|我不确定|我拿不准|我没有答案|本章没有验证|没有验证的|没复现过",
    "收口": r"一句话[:：]|这就是|这才是",
    "假对比": r"不是[^。\n]{0,25}而是",
    "必须": r"必须",
}


def analyze(text: str) -> dict:
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    chars = len(re.findall(r"[\u4e00-\u9fff]", body))      # 与 check_style 口径一致
    prose = re.sub(r"^\|.*$", "", body, flags=re.M)         # 段落统计剔除表格行
    m = {k: len(re.findall(v, body)) for k, v in PATTERNS.items()}
    table_lines = len(re.findall(r"^\|", text, flags=re.M))
    nonempty = len([l for l in text.split("\n") if l.strip()])
    m["表行"] = table_lines
    m["表占比"] = table_lines / nonempty * 100 if nonempty else 0.0
    paras = [len(re.findall(r"[\u4e00-\u9fff]", p)) for p in re.split(r"\n\s*\n", prose)]
    paras = [p for p in paras if p > 20]
    m["段长CV"] = (statistics.pstdev(paras) / statistics.mean(paras)) if len(paras) > 3 else 0.0
    m["汉字"] = chars
    return m


def report(m: dict) -> list:
    rows = []
    per = m["汉字"] / 1e4 if m["汉字"] else 0
    for k, (lo, hi) in LIMITS.items():
        v = m[k] / per if per else 0
        flag = ""
        if lo is not None and v < lo:
            flag = "↓"
        if hi is not None and v > hi:
            flag = "↑"
        rows.append((k, m[k], v, flag))
    if m["表占比"] > 45:
        rows.append(("表占比", m["表占比"], m["表占比"], "T"))
    return rows


def main(argv):
    files = argv[1:] or sorted(
        f for f in glob.glob("book/**/*.md", recursive=True)
        if not any(x in f for x in ("README", "OUTLINE", "GLOSSARY", "STYLE", "WRITING-CONTRACT"))
    )
    print(f"{'章节':<40}{'字':>6}{'元评论':>7}{'收口':>6}{'表行':>6}{'必须':>6}{'假对比':>7}{'CV':>7}{'表占比':>8}")
    print("-" * 108)
    bad = []
    for f in files:
        m = analyze(pathlib.Path(f).read_text(encoding="utf-8"))
        name = f.split("/")[-1]
        marks = "".join(r[3] for r in report(m))
        print(f"{name:<40}{m['汉字']:>6}{m['元评论']:>7}{m['收口']:>6}{m['表行']:>6}"
              f"{m['必须']:>6}{m['假对比']:>7}{m['段长CV']:>7.2f}{m['表占比']:>6.0f}%  {marks}")
        if marks:
            bad.append(name)
    print("-" * 108)
    print("↓ 缺元评论为0   ↑ 模板化超标   T 表格占比>45%")
    print(f"{len(bad)}/{len(files)} 个文件需要人工看一遍：{', '.join(bad[:6])}")


if __name__ == "__main__":
    main(sys.argv)
