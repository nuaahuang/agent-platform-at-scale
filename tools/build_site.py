#!/usr/bin/env python3
"""把 book/*.md 渲染成可阅读的单页 HTML（设计系统见文末 CSS）。

设计原则：
  1. Markdown 是唯一源，渲染是幂等的——改 md 重跑即可，不做内容改写
  2. 语义优先：导读块、结论块、失败模式表、验收表自动识别并升级为组件
  3. 单文件输出：CSS/JS 内联，可直接发给别人、可离线打开

用法：
    python3 tools/build_site.py                 # 全部章节 + 首页
    python3 tools/build_site.py book/part-00-method/01-scale-slo-economics.md
    python3 tools/build_site.py --open          # 生成后打开首页
"""
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"


# ---------------------------------------------------------------- markdown 解析
def join_lines(buf):
    """中文换行不加空格，英文单词之间补空格。"""
    out = buf[0]
    for nxt in buf[1:]:
        if out and nxt and re.match(r"[A-Za-z0-9,.;:%)\]\u00b0]", out[-1]) and re.match(
            r"[A-Za-z0-9(\[]", nxt[0]
        ):
            out += " " + nxt
        else:
            out += nxt
    return out


def inline(s):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: f"<em>{m.group(1)}</em>", s)
    return s


def parse(lines):
    blocks, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        m = re.match(r"^```(\w*)\s*$", line)
        if m:
            lang, i, buf = m.group(1), i + 1, []
            while i < n and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", lang, "\n".join(buf)))
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            blocks.append(("h", len(m.group(1)), m.group(2).strip()))
            i += 1
            continue

        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
            blocks.append(("hr",))
            i += 1
            continue

        if line.lstrip().startswith("|") and i + 1 < n and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            blocks.append(("table", rows))
            continue

        if line.startswith(">"):
            buf = []
            while i < n and lines[i].startswith(">"):
                buf.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            blocks.append(("quote", buf))
            continue

        if line.lstrip().startswith("<"):
            buf = []
            while i < n and lines[i].strip():
                buf.append(lines[i])
                i += 1
            blocks.append(("html", "\n".join(buf)))
            continue

        if re.match(r"^\s*[-*+]\s+", line):
            buf = []
            while i < n and re.match(r"^\s*[-*+]\s+", lines[i]):
                buf.append(re.sub(r"^\s*[-*+]\s+", "", lines[i]))
                i += 1
            blocks.append(("ul", buf))
            continue

        if re.match(r"^\s*\d+[.)]\s+", line):
            buf = []
            while i < n and re.match(r"^\s*\d+[.)]\s+", lines[i]):
                buf.append(re.sub(r"^\s*\d+[.)]\s+", "", lines[i]))
                i += 1
            blocks.append(("ol", buf))
            continue

        buf = []
        while i < n and lines[i].strip():
            l = lines[i]
            if (
                re.match(r"^(#{1,6})\s", l)
                or l.startswith("```")
                or l.startswith(">")
                or l.lstrip().startswith("|")
                or l.lstrip().startswith("<")
                or re.match(r"^\s*[-*+]\s+", l)
                or re.match(r"^\s*\d+[.)]\s+", l)
                or re.match(r"^\s*(-{3,}|\*{3,})\s*$", l)
            ):
                break
            buf.append(l)
            i += 1
        blocks.append(("p", join_lines(buf)))
    return blocks


def split_row(row):
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells


# ---------------------------------------------------------------- 语义渲染
CALLOUT_KINDS = [
    (("结论先行", "**结论", "结论是"), "conclusion", "结论"),
    (("陷阱", "误区", "**注意", "常见误解", "坑"), "warn", "注意"),
    (("验收", "判据", "自测"), "check", "验收"),
]


def render_table(rows):
    head = split_row(rows[0])
    body = [split_row(r) for r in rows[2:]]
    htxt = " ".join(head)
    cls = "table"
    if "现象" in htxt and ("根因" in htxt or "怎么发现" in htxt):
        cls += " table--failure"
    elif "指标" in htxt and ("目标" in htxt or "期望" in htxt):
        cls += " table--spec"
    out = [f'<div class="table-wrap"><table class="{cls}"><thead><tr>']
    out += [f"<th>{inline(c)}</th>" for c in head]
    out.append("</tr></thead><tbody>")
    for r in body:
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def render_quote(buf):
    text = "\n".join(buf)
    plain = re.sub(r"\*\*|`", "", text).strip()

    if "读完你能回答" in plain or "读完你能" in plain:
        items = [l for l in buf if re.match(r"^\s*[①②③④⑤⑥⑦⑧⑨]", l.strip())]
        head = next((l for l in buf if "读完" in l), "读完你能回答")
        head = re.sub(r"^\s*[①②③④⑤⑥⑦⑧⑨].*$", "", head).strip("：: ")
        lis = "".join(f"<li>{inline(re.sub(r'^\s*[①②③④⑤⑥⑦⑧⑨]\s*', '', it))}</li>" for it in items)
        return (
            f'<aside class="intro-card"><div class="intro-label">导读</div>'
            f'<div class="intro-title">{inline(head)}</div>'
            f'<ol class="intro-list">{lis}</ol></aside>'
        )

    for keys, kind, label in CALLOUT_KINDS:
        if any(k in text for k in keys):
            body = "<br>".join(inline(l) for l in buf if l.strip())
            return f'<div class="callout callout--{kind}"><span class="callout-tag">{label}</span><div>{body}</div></div>'

    body = "".join(f"<p>{inline(l)}</p>" for l in buf if l.strip())
    return f'<blockquote>{body}</blockquote>'


def render(blocks, meta):
    out, i = [], 0
    section = ""
    h2n = 0
    sec_open = False
    while i < len(blocks):
        b = blocks[i]
        kind = b[0]

        if kind == "h":
            lvl, text = b[1], b[2]
            if lvl == 1:
                out.append(
                    f'<header class="ch-head"><div class="ch-ghost">{(meta["num"] if meta["num"] != "G" else "§")}</div>'
                    f'<div class="ch-kicker">{meta["part"]}</div>'
                    f'<h1>{inline(meta["title"])}</h1>'
                    f'<div class="ch-rule"></div></header>'
                )
            elif lvl == 2:
                if sec_open:
                    out.append("</section>")
                h2n += 1
                section = re.sub(r"^\d+(\.\d+)*\s*", "", text)
                out.append(
                    f'<section class="sec" id="s{h2n}"><h2>'
                    f'<span class="sec-num">{h2n:02d}</span>{inline(text)}</h2>'
                )
                sec_open = True
            elif lvl == 3:
                out.append(f"<h3>{inline(text)}</h3>")
            else:
                out.append(f"<h4>{inline(text)}</h4>")

        elif kind == "p":
            cls = ""
            if section.startswith("收口"):
                cls = ' class="closing"'
            out.append(f"<p{cls}>{inline(b[1])}</p>")

        elif kind == "table":
            out.append(render_table(b[1]))

        elif kind == "code":
            lang, code = b[1], b[2]
            esc = html.escape(code)
            cls = "code--ascii" if lang in ("", "text", "txt") else "code--lang"
            label = f'<span class="code-lang">{lang}</span>' if lang else ""
            out.append(
                f'<figure class="code {cls}">{label}'
                f'<button class="copy" type="button">复制</button>'
                f"<pre><code>{esc}</code></pre></figure>"
            )

        elif kind == "quote":
            out.append(render_quote(b[1]))

        elif kind == "ul":
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in b[1]) + "</ul>")

        elif kind == "ol":
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in b[1]) + "</ol>")

        elif kind == "hr":
            out.append('<div class="orn"></div>')

        elif kind == "html":
            out.append(b[1])

        i += 1
    if sec_open:
        out.append("</section>")
    return "\n".join(out)


# ---------------------------------------------------------------- 站点装配
def parse_meta(path):
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    title = next((l for l in lines if l.startswith("# ")), "# 未命名")
    title = title[2:].strip()
    m = re.match(r"^第\s*(\d+)\s*章\s*[·:：]\s*(.+)$", title)
    if m:
        num, name = f"{int(m.group(1)):02d}", m.group(2)
    else:
        m = re.match(r"^附录\s*([A-Z])\s*[·:：]\s*(.+)$", title)
        if m:
            num, name = m.group(1), m.group(2)
        else:
            num, name = "00", title
    part = path.parent.name
    return {"num": num, "title": name, "part": PART_NAMES.get(part, part), "raw": text}


PART_NAMES = {
    "part-00-method": "方法篇",
    "part-01-stack": "技术栈",
    "part-02-model": "模型工程",
    "part-03-inference": "推理工程",
    "part-04-capacity": "容量与成本",
    "part-05-token-economics": "Token 经济",
    "part-06-harness": "Harness",
    "part-07-backpressure": "接入与背压",
    "part-08-storage": "数据与存储",
    "part-09-ha": "高可用",
    "part-10-security": "安全合规",
    "part-11-ops": "运营",
    "part-12-synthesis": "综合",
    "appendix": "附录",
}


def build_toc(blocks):
    items, n = [], 0
    for b in blocks:
        if b[0] == "h" and b[1] == 2:
            n += 1
            items.append((f"s{n}", f"{n:02d}", b[2]))
    return items


def page(meta, body, toc, prev=None, next_=None):
    toc_html = "".join(
        f'<a href="#{i}"><span>{num}</span>{inline(re.sub(r"^\d+(\.\d+)*\s*", "", t))}</a>'
        for i, num, t in toc
    )
    nav = ""
    if prev or next_:
        p = f'<a class="nav-prev" href="{prev[1]}"><span>上一章</span><b>{prev[0]}</b></a>' if prev else "<i></i>"
        nx = f'<a class="nav-next" href="{next_[1]}"><span>下一章</span><b>{next_[0]}</b></a>' if next_ else "<i></i>"
        nav = f'<nav class="pager">{p}{nx}</nav>'

    if meta["num"] == "G":
        crumb = "参考 · 术语表"
    elif meta["num"].isdigit():
        crumb = f'{meta["part"]} · 第 {meta["num"]} 章'
    else:
        crumb = f'{meta["part"]} {meta["num"]}'
    return TEMPLATE.replace("{{TITLE}}", html.escape(meta["title"])).replace(
        "{{CSS}}", CSS
    ).replace("{{NUM}}", meta["num"]).replace("{{PART}}", meta["part"]).replace("{{CRUMB}}", crumb).replace(
        "{{TOC}}", toc_html
    ).replace(
        "{{BODY}}", body
    ).replace(
        "{{NAV}}", nav
    ).replace(
        "{{JS}}", JS
    )


def build_index(entries):
    cards = "".join(
        f'<a class="card" href="{fn}"><div class="card-num">{e["num"]}</div>'
        f'<div class="card-body"><div class="card-part">{e["part"]}</div>'
        f'<div class="card-title">{html.escape(e["title"])}</div></div>'
        f'<div class="card-meta">{e["chars"]:,} 字</div></a>'
        for e, fn in entries
    )
    return INDEX.replace("{{CSS}}", CSS).replace("{{CARDS}}", cards).replace("{{JS}}", JS)


# ---------------------------------------------------------------- 主流程
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    files = [Path(a) for a in args] if args else sorted(
        p for p in (ROOT / "book").rglob("*.md")
        if p.name not in {"README.md", "STYLE.md", "OUTLINE.md", "OUTLINE-v3.md", "MIGRATION.md", "GLOSSARY.md"}
    )
    OUT.mkdir(exist_ok=True)
    metas = []
    for f in files:
        meta = parse_meta(f)
        blocks = parse(meta["raw"].split("\n"))
        meta["html"] = render(blocks, meta)
        meta["toc"] = build_toc(blocks)
        meta["chars"] = len(re.sub(r"\s", "", meta["raw"]))
        meta["file"] = f"{f.stem}.html"
        metas.append(meta)

    metas.sort(key=lambda m: (0, int(m["num"])) if m["num"].isdigit() else (1, m["num"]))
    for idx, m in enumerate(metas):
        prev = (f'{metas[idx-1]["num"]} · {metas[idx-1]["title"]}', metas[idx-1]["file"]) if idx > 0 else None
        nxt = (f'{metas[idx+1]["num"]} · {metas[idx+1]["title"]}', metas[idx+1]["file"]) if idx + 1 < len(metas) else None
        (OUT / m["file"]).write_text(page(m, m["html"], m["toc"], prev, nxt), encoding="utf-8")
        print(f"  + docs/{m['file']}")

    entries = [(m, m["file"]) for m in metas]

    gl = ROOT / "book" / "GLOSSARY.md"
    if gl.exists():
        gmeta = {"num": "G", "title": "术语表", "part": "参考", "raw": gl.read_text(encoding="utf-8")}
        gblocks = parse(gmeta["raw"].split("\n"))
        gmeta["html"] = render(gblocks, gmeta)
        gmeta["toc"] = build_toc(gblocks)
        gmeta["chars"] = len(re.sub(r"\s", "", gmeta["raw"]))
        gmeta["file"] = "glossary.html"
        (OUT / gmeta["file"]).write_text(page(gmeta, gmeta["html"], gmeta["toc"]), encoding="utf-8")
        print(f"  + docs/{gmeta['file']}")
        entries.append((gmeta, gmeta["file"]))

    (OUT / "index.html").write_text(build_index(entries), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"  + docs/index.html  （{len(entries)} 页）")

    if "--open" in sys.argv:
        import subprocess
        subprocess.run(["open", str(OUT / "index.html")], check=False)


# ---------------------------------------------------------------- 模板与样式
TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}} · Agent Platform at Scale</title>
<style>{{CSS}}</style>
</head>
<body>
<div class="progress"><i id="bar"></i></div>
<header class="topbar">
  <a class="brand" href="index.html">Agent Platform<span>at Scale</span></a>
  <div class="crumb">{{CRUMB}}</div>
  <button class="theme" id="theme" aria-label="切换主题">☾</button>
</header>
<div class="shell">
  <aside class="rail">
    <div class="rail-inner">
      <div class="rail-label">本章目录</div>
      <nav class="toc">{{TOC}}</nav>
      <div class="rail-foot">{{PART}}</div>
    </div>
  </aside>
  <main class="content">
    <article class="prose">{{BODY}}</article>
    {{NAV}}
    <footer class="foot">Agent Platform at Scale · 亿级 Agent 平台工程 · 教学构造</footer>
  </main>
</div>
<script>{{JS}}</script>
</body>
</html>"""

INDEX = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Platform at Scale · 亿级 Agent 平台工程</title>
<style>{{CSS}}</style>
</head>
<body class="home">
<header class="topbar">
  <a class="brand" href="index.html">Agent Platform<span>at Scale</span></a>
  <div class="crumb">从一张卡到一亿用户</div>
  <button class="theme" id="theme" aria-label="切换主题">☾</button>
</header>
<main class="home-main">
  <div class="home-head">
    <div class="home-kicker">教学构造 · 不映射任何真实产品</div>
    <h1>亿级 Agent 平台<span class="dot">.</span>工程</h1>
    <p class="home-sub">每一章都从一条约束出发：<b>要多少卡、扛不扛得住、赚不赚钱</b>。
    所有数字可复算，所有选型都写了代价与推翻条件。</p>
  </div>
  <div class="cards">{{CARDS}}</div>
</main>
<script>{{JS}}</script>
</body>
</html>"""

CSS = r"""
:root{
  --paper:#fbf9f4; --paper-2:#f4f1e9; --ink:#1a1a1a; --ink-soft:#4a4a48;
  --muted:#7a766d; --rule:#e3ded1; --navy:#16324f; --accent:#b23a24;
  --teal:#14665f; --amber:#9a5b13;
  --serif:"Songti SC","Source Han Serif SC","Noto Serif CJK SC",Georgia,serif;
  --sans:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  --num:"Charter","Iowan Old Style",Georgia,serif;
  --mono:"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --maxw:41rem;
}
[data-theme="dark"]{
  --paper:#14140f; --paper-2:#1c1c16; --ink:#f0ece1; --ink-soft:#c9c4b6;
  --muted:#928d7e; --rule:#2f2e26; --navy:#a8c6e0; --accent:#e0705a;
  --teal:#7fc9bf; --amber:#d8a45c;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--serif);
  font-size:17.5px;line-height:1.95;-webkit-font-smoothing:antialiased;
  text-align:justify;text-justify:inter-ideograph}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid color-mix(in srgb,var(--accent) 35%,transparent)}
a:hover{border-bottom-color:var(--accent)}
em{font-style:italic;color:var(--ink-soft)}

/* 顶栏 */
.progress{position:fixed;top:0;left:0;right:0;height:2px;z-index:60;background:transparent}
.progress i{display:block;height:100%;width:0;background:var(--accent);transition:width .1s linear}
.topbar{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:18px;
  padding:14px 34px;background:color-mix(in srgb,var(--paper) 88%,transparent);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--rule)}
.brand{font-family:var(--sans);font-weight:650;font-size:14px;letter-spacing:.02em;
  color:var(--ink);border:0}
.brand span{color:var(--muted);font-weight:400;margin-left:5px}
.crumb{font-family:var(--sans);font-size:12.5px;color:var(--muted);letter-spacing:.04em}
.theme{margin-left:auto;border:1px solid var(--rule);background:transparent;color:var(--ink);
  width:30px;height:30px;border-radius:50%;cursor:pointer;font-size:13px;line-height:1}
.theme:hover{border-color:var(--accent);color:var(--accent)}

/* 骨架 */
.shell{display:grid;grid-template-columns:220px minmax(0,1fr);gap:0;max-width:1120px;margin:0 auto}
.rail{border-right:1px solid var(--rule)}
.rail-inner{position:sticky;top:60px;padding:42px 20px 60px 34px}
.rail-label{font-family:var(--sans);font-size:10.5px;letter-spacing:.18em;color:var(--muted);
  text-transform:uppercase;margin-bottom:14px}
.toc{display:flex;flex-direction:column;gap:2px}
.toc a{display:flex;gap:9px;font-family:var(--sans);font-size:12.5px;line-height:1.5;
  color:var(--muted);padding:5px 8px 5px 0;border:0;border-left:2px solid transparent;
  padding-left:10px;text-align:left;transition:color .15s,border-color .15s}
.toc a span{font-family:var(--num);font-size:11px;color:color-mix(in srgb,var(--muted) 70%,transparent);
  padding-top:1px}
.toc a:hover{color:var(--ink)}
.toc a.active{color:var(--accent);border-left-color:var(--accent)}
.toc a.active span{color:var(--accent)}
.rail-foot{margin-top:26px;font-family:var(--sans);font-size:11px;color:var(--muted);
  border-top:1px solid var(--rule);padding-top:12px}

/* 正文 */
.content{padding:0 44px 80px 56px;min-width:0}
.prose{max-width:var(--maxw)}
.ch-head{position:relative;padding:52px 0 0;margin-bottom:34px}
.ch-ghost{position:absolute;top:-6px;left:-14px;font-family:var(--num);font-size:190px;
  line-height:1;color:color-mix(in srgb,var(--accent) 9%,transparent);z-index:0;
  letter-spacing:-.04em;pointer-events:none;user-select:none}
.ch-kicker{position:relative;font-family:var(--sans);font-size:11.5px;letter-spacing:.2em;
  color:var(--accent);text-transform:uppercase;margin-bottom:12px}
.ch-head h1{position:relative;font-family:var(--sans);font-size:37px;line-height:1.28;
  font-weight:640;letter-spacing:-.015em;margin:0;color:var(--ink)}
.ch-rule{height:1px;background:var(--ink);opacity:.85;margin-top:26px;width:64px}

.sec{margin-top:52px}
.sec h2{position:relative;font-family:var(--sans);font-size:24px;line-height:1.4;font-weight:620;
  letter-spacing:-.01em;margin:0 0 20px;padding-top:18px;border-top:1px solid var(--rule);
  display:flex;align-items:baseline;gap:12px}
.sec-num{font-family:var(--num);font-size:13px;color:var(--accent);letter-spacing:.06em;
  border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);border-radius:999px;
  padding:1px 7px;flex:none;position:relative;top:-2px}
h3{font-family:var(--sans);font-size:19px;font-weight:620;margin:34px 0 10px;color:var(--navy);
  letter-spacing:-.005em}
h4{font-family:var(--sans);font-size:16px;font-weight:600;margin:24px 0 8px;color:var(--ink-soft)}
p{margin:0 0 1.05em}
.prose > p:first-of-type::first-letter{font-size:2.6em;float:left;line-height:.92;
  padding:6px 8px 0 0;color:var(--accent);font-family:var(--serif)}
strong{font-weight:700;color:var(--ink);
  background:linear-gradient(transparent 60%,color-mix(in srgb,var(--accent) 15%,transparent) 60%)}
[data-theme="dark"] strong{background:linear-gradient(transparent 60%,color-mix(in srgb,var(--accent) 22%,transparent) 60%)}

ul,ol{margin:0 0 1.1em;padding-left:1.35em}
li{margin:.32em 0}
li::marker{color:var(--accent)}

/* 表格 */
.table-wrap{margin:26px 0;overflow-x:auto;border:1px solid var(--rule);border-radius:3px;
  background:color-mix(in srgb,var(--paper-2) 55%,var(--paper))}
table{width:100%;border-collapse:collapse;font-family:var(--sans);font-size:14.5px;line-height:1.62}
thead th{position:sticky;top:0;background:var(--paper-2);text-align:left;font-weight:600;
  font-size:12.5px;letter-spacing:.04em;color:var(--ink-soft);padding:11px 14px;
  border-bottom:1px solid var(--rule);white-space:nowrap}
tbody td{padding:10px 14px;border-bottom:1px solid color-mix(in srgb,var(--rule) 65%,transparent);
  vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr:nth-child(even){background:color-mix(in srgb,var(--paper-2) 35%,transparent)}
tbody tr:hover{background:color-mix(in srgb,var(--accent) 6%,transparent)}
tbody td:first-child{font-weight:600;color:var(--ink)}
.table--failure thead th{color:var(--accent);background:color-mix(in srgb,var(--accent) 7%,var(--paper-2))}
.table--spec thead th{color:var(--teal);background:color-mix(in srgb,var(--teal) 7%,var(--paper-2))}

/* 代码 */
.code{position:relative;margin:26px 0;border-radius:4px;overflow:hidden;
  background:#17181c;border:1px solid #0d0e11}
.code pre{margin:0;padding:20px 22px;overflow-x:auto}
.code code{font-family:var(--mono);font-size:13px;line-height:1.75;color:#e8e3d7;
  background:none;padding:0;white-space:pre}
.code--ascii code{font-size:12.5px;line-height:1.6;color:#d8d2c4}
.code-lang{position:absolute;top:9px;right:52px;font-family:var(--mono);font-size:10.5px;
  letter-spacing:.1em;color:#7d7768;text-transform:uppercase}
.copy{position:absolute;top:6px;right:8px;font-family:var(--sans);font-size:10.5px;
  color:#8b8577;background:transparent;border:1px solid #33333a;border-radius:3px;
  padding:3px 7px;cursor:pointer;opacity:0;transition:opacity .15s}
.code:hover .copy{opacity:1}
.copy:hover{color:#e8e3d7;border-color:#55555f}
code{font-family:var(--mono);font-size:.87em;background:color-mix(in srgb,var(--ink) 7%,transparent);
  padding:.12em .38em;border-radius:3px}

/* 引用与组件 */
blockquote{margin:24px 0;padding:4px 0 4px 18px;border-left:2px solid var(--rule);color:var(--ink-soft)}
blockquote p{margin:.4em 0}
.intro-card{margin:30px 0 34px;padding:22px 24px;background:color-mix(in srgb,var(--navy) 4%,var(--paper));
  border:1px solid color-mix(in srgb,var(--navy) 18%,var(--rule));border-radius:3px}
.intro-label{font-family:var(--sans);font-size:10.5px;letter-spacing:.22em;color:var(--navy);
  text-transform:uppercase;margin-bottom:8px}
.intro-title{font-family:var(--sans);font-size:14px;color:var(--ink);margin-bottom:10px}
.intro-list{margin:0;padding-left:1.2em;font-size:16px;line-height:1.8}
.intro-list li::marker{color:var(--navy)}

.callout{position:relative;margin:26px 0;padding:18px 22px 18px 22px;border-radius:3px;
  font-size:16.5px;line-height:1.8;border:1px solid}
.callout-tag{position:absolute;top:-9px;left:14px;font-family:var(--sans);font-size:10.5px;
  letter-spacing:.14em;padding:1px 8px;background:var(--paper);border-radius:2px}
.callout--conclusion{border-color:color-mix(in srgb,var(--navy) 30%,transparent);
  background:color-mix(in srgb,var(--navy) 5%,transparent)}
.callout--conclusion .callout-tag{color:var(--navy);border:1px solid color-mix(in srgb,var(--navy) 30%,transparent)}
.callout--warn{border-color:color-mix(in srgb,var(--amber) 35%,transparent);
  background:color-mix(in srgb,var(--amber) 6%,transparent)}
.callout--warn .callout-tag{color:var(--amber);border:1px solid color-mix(in srgb,var(--amber) 35%,transparent)}
.callout--check{border-color:color-mix(in srgb,var(--teal) 32%,transparent);
  background:color-mix(in srgb,var(--teal) 5%,transparent)}
.callout--check .callout-tag{color:var(--teal);border:1px solid color-mix(in srgb,var(--teal) 32%,transparent)}

.orn{height:1px;width:38px;background:var(--rule);margin:40px 0;position:relative}
.orn::after{content:"";position:absolute;left:50%;top:-2px;width:5px;height:5px;
  background:var(--accent);transform:translateX(-50%) rotate(45deg)}

.closing{font-family:var(--sans);font-size:19px;line-height:1.75;color:var(--ink);
  border-left:3px solid var(--accent);padding-left:18px;margin:22px 0}

/* 翻页与页脚 */
.pager{display:flex;gap:16px;margin:64px 0 0;max-width:var(--maxw)}
.pager a,.pager i{flex:1;display:flex;flex-direction:column;gap:5px;padding:16px 18px;
  border:1px solid var(--rule);border-radius:3px;border-bottom-width:1px}
.pager a:hover{border-color:var(--accent)}
.pager span{font-family:var(--sans);font-size:10.5px;letter-spacing:.16em;color:var(--muted)}
.pager b{font-family:var(--sans);font-size:13.5px;font-weight:600;color:var(--ink)}
.nav-next{text-align:right}
.foot{max-width:var(--maxw);margin:52px 0 0;padding-top:18px;border-top:1px solid var(--rule);
  font-family:var(--sans);font-size:11.5px;color:var(--muted);letter-spacing:.04em}

/* 首页 */
.home-main{max-width:980px;margin:0 auto;padding:64px 34px 90px}
.home-kicker{font-family:var(--sans);font-size:11px;letter-spacing:.22em;color:var(--accent);
  text-transform:uppercase;margin-bottom:18px}
.home-head h1{font-family:var(--sans);font-size:58px;line-height:1.1;font-weight:660;
  letter-spacing:-.03em;margin:0 0 20px}
.home-head h1 .dot{color:var(--accent)}
.home-sub{font-size:17px;color:var(--ink-soft);max-width:34em;margin:0 0 46px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.card{display:flex;gap:16px;align-items:flex-start;padding:20px 20px 18px;border:1px solid var(--rule);
  border-radius:3px;background:color-mix(in srgb,var(--paper-2) 40%,var(--paper));border-bottom-width:1px}
.card:hover{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 4%,var(--paper))}
.card-num{font-family:var(--num);font-size:26px;line-height:1;color:var(--accent);opacity:.75;flex:none}
.card-part{font-family:var(--sans);font-size:10.5px;letter-spacing:.14em;color:var(--muted);
  text-transform:uppercase;margin-bottom:6px}
.card-title{font-family:var(--sans);font-size:15.5px;font-weight:600;line-height:1.45;color:var(--ink)}
.card-meta{margin-left:auto;font-family:var(--num);font-size:11px;color:var(--muted);flex:none}

/* 响应式 */
@media(max-width:1000px){
  .shell{grid-template-columns:1fr}
  .rail{display:none}
  .content{padding:0 26px 60px}
  .ch-ghost{font-size:140px}
  .home-head h1{font-size:40px}
}
@media print{
  .topbar,.rail,.progress,.pager,.copy{display:none}
  body{background:#fff;font-size:11.5pt}
  .content{padding:0}
  .sec{break-inside:avoid-page}
}
"""

JS = r"""
(function(){
  var bar=document.getElementById('bar');
  function onScroll(){
    var h=document.documentElement;
    var max=h.scrollHeight-h.clientHeight;
    bar.style.width=(max>0? (h.scrollTop/max*100):0)+'%';
  }
  addEventListener('scroll',onScroll,{passive:true}); onScroll();

  var t=document.getElementById('theme');
  var saved=localStorage.getItem('theme');
  var sysDark=matchMedia('(prefers-color-scheme: dark)').matches;
  var cur=saved||(sysDark?'dark':'light');
  document.documentElement.dataset.theme=cur;
  t.textContent=cur==='dark'?'☀':'☾';
  t.addEventListener('click',function(){
    cur=cur==='dark'?'light':'dark';
    document.documentElement.dataset.theme=cur;
    localStorage.setItem('theme',cur);
    t.textContent=cur==='dark'?'☀':'☾';
  });

  document.querySelectorAll('.copy').forEach(function(b){
    b.addEventListener('click',function(){
      var code=b.parentElement.querySelector('code').innerText;
      navigator.clipboard.writeText(code).then(function(){
        b.textContent='已复制'; setTimeout(function(){b.textContent='复制';},1200);
      });
    });
  });

  var links=[].slice.call(document.querySelectorAll('.toc a'));
  var secs=links.map(function(a){return document.querySelector(a.getAttribute('href'));});
  if(secs.length){
    var io=new IntersectionObserver(function(es){
      es.forEach(function(e){
        if(e.isIntersecting){
          links.forEach(function(l){l.classList.remove('active');});
          var i=secs.indexOf(e.target);
          if(i>=0) links[i].classList.add('active');
        }
      });
    },{rootMargin:'-15% 0px -70% 0px'});
    secs.forEach(function(s){ if(s) io.observe(s); });
  }
})();
"""

if __name__ == "__main__":
    main()
