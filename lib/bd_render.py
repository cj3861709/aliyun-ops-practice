# -*- coding: utf-8 -*-
"""大数据平台实训分支（bd）的正文渲染。

源材料：my-bd/ch1.md … ch11.md 与 my-bd/img/ 下的运行截图。
页面外壳（页头、导航、面包屑、页脚、备案号）仍由 generate-site.py 的
page() 统一提供 —— 全站只有一套外壳，分支之间只在正文与主题上不同。

本模块在 md2html 之外多做两件事（md2html 自己不认这两种写法）：
    ![alt](IMG:文件名)              -> <figure><img><figcaption>
    <!-- FLOW: 标题 || 节点 -> 节点 --> -> 内联 SVG 流程图
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import md2html as M

# my-bd 与 lib 同级，都在仓库根 /root/ops-lab 下
LAB = os.path.dirname(_HERE)
BD_DIR = os.path.join(LAB, "my-bd")

CH = [
    (1, "大数据平台及安装配置", "ch1.md"),
    (2, "案例概述", "ch2.md"),
    (3, "Spark SQL 原理及应用", "ch3.md"),
    (4, "Spark 流式计算", "ch4.md"),
    (5, "使用 Spark Mlib 实现协同过滤算法", "ch5.md"),
    (6, "Node.js 的安装和使用方法", "ch6.md"),
    (7, "电影推荐系统（基础版）的实现过程", "ch7.md"),
    (8, "电影推荐系统（升级版）的设计与实现", "ch8.md"),
    (9, "综合实验：基于 CO2 排放量数据集的数据分析及可视化", "ch9.md"),
    (10, "综合实验：Spark + Kafka 构建实时分析 Dashboard", "ch10.md"),
    (11, "常见问题及解决方案", "ch11.md"),
]

# 第 10、11 章不是做完的实验：10 是待做的方案，11 是跨章的排错速查。
# 加角标是为了不让读者把这两页当成"已经跑出结果"的实验记录。
TAGS = {10: "方案", 11: "速查"}

THEME_CSS = r"""
/* ══════════════════════════════════════════════════════════════════
   实训讲义风（大数据平台实训）
   暖纸底 + 砖红强调：直角、无阴影，层级只靠横线与留白；
   章节标题带编号色块，结果验证用三线表，配图统一白卡。
   既有分支的强调色：运维终端绿、运维体系青+琥珀、数据挖掘蓝、云计算藏青，
   蓝色被占用了两次，这里改用砖红（色相约 11°），与四者都不撞。
   ══════════════════════════════════════════════════════════════════ */
:root{
  --mn-bg:#FBF9F4;
  --mn-panel:#FFFFFF;
  --mn-ink:#1F1B16;
  --mn-muted:#6B6157;
  --mn-line:#E2DCD1;
  --mn-accent:#B4442E;
  --mn-accent-d:#8C2F1E;
  --mn-soft:#F3EDE1;
  --mn-warn:#8A5A00;
  --mn-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --mn-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
}
*,*::before,*::after{box-sizing:border-box}
body.manual{
  margin:0;background:var(--mn-bg);color:var(--mn-ink);
  font-family:var(--mn-sans);font-size:16.5px;line-height:1.8;
  -webkit-font-smoothing:antialiased;
}
body.manual .wrap{max-width:calc(50rem + 48px);margin:0 auto;padding:0 24px}
body.manual a{color:var(--mn-accent);text-decoration:none}
body.manual a:hover{text-decoration:underline}

/* ── 页头 ── */
body.manual header.site{background:var(--mn-panel);border-bottom:1px solid var(--mn-line)}
body.manual header.site .wrap{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 14px;padding-top:20px;padding-bottom:20px}
body.manual header.site .brand{font-weight:700;font-size:1.02rem;color:var(--mn-ink);letter-spacing:.01em}
body.manual .brand:hover{text-decoration:none}
body.manual .brand-sep{color:var(--mn-muted);margin:0 2px}
body.manual .brand-branch{color:var(--mn-accent);font-weight:700;font-size:1.02rem}
body.manual header.site .sub{color:var(--mn-muted);font-size:.8rem;letter-spacing:.05em}
body.manual nav.site{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
body.manual nav.site a{
  color:var(--mn-muted);font-size:.82rem;padding:3px 9px;
  border:1px solid transparent;
}
body.manual nav.site a:hover{color:var(--mn-accent);background:var(--mn-soft);text-decoration:none}
body.manual nav.site a.on{color:#fff;background:var(--mn-accent);border-color:var(--mn-accent);font-weight:600}
body.manual .crumbs{
  flex-basis:100%;font-size:.79rem;color:var(--mn-muted);
  padding-top:10px;margin-top:10px;border-top:1px dashed var(--mn-line);
}
body.manual .crumbs .sep{margin:0 7px;color:var(--mn-muted);opacity:.6}
body.manual .crumbs .cur{color:var(--mn-ink);font-weight:600}

/* ── 正文 ── */
body.manual main{padding:36px 0 72px}
body.manual .doc{
  background:var(--mn-panel);border:1px solid var(--mn-line);
  padding:40px 52px 52px;
}
body.manual h1{
  font-size:1.72rem;line-height:1.35;margin:0 0 26px;padding:0 0 16px;
  border-bottom:2px solid var(--mn-ink);font-weight:700;letter-spacing:-.01em;
}
body.manual h2{
  font-size:1.2rem;margin:38px 0 16px;padding:9px 14px;
  background:var(--mn-soft);border-left:4px solid var(--mn-accent);
  font-weight:700;line-height:1.5;
}
body.manual h3{
  font-size:1.03rem;margin:28px 0 12px;padding-left:11px;
  border-left:3px solid var(--mn-accent);font-weight:700;color:var(--mn-accent-d);
  line-height:1.55;
}
body.manual p{margin:14px 0}
body.manual ul,body.manual ol{margin:14px 0;padding-left:1.5em}
body.manual li{margin:6px 0}
body.manual li::marker{color:var(--mn-accent)}
body.manual hr{border:none;border-top:1px solid var(--mn-line);margin:32px 0}
body.manual blockquote{
  margin:20px 0;padding:10px 16px;background:var(--mn-bg);
  border-left:3px solid var(--mn-muted);color:var(--mn-muted);
}
body.manual strong{font-weight:700}

/* ── 代码 ── */
body.manual code{
  background:var(--mn-soft);color:var(--mn-accent-d);
  font-family:var(--mn-mono);font-size:.86em;padding:1px 5px;
}
body.manual pre{
  background:#2C241C;color:#EDE3D6;font-family:var(--mn-mono);
  font-size:.8rem;line-height:1.7;padding:15px 18px;margin:18px 0;
  overflow-x:auto;
}
body.manual pre code{background:none;color:inherit;padding:0;font-size:inherit}
/* 命令块：深色底 + 暖橙左条，与输出块区分 */
body.manual pre[class*="language-"]{
  background:#241D16;border-left:3px solid #E0A05A;padding:14px 18px 14px 16px;
}

/* ── 图片 ── */
body.manual figure{
  margin:24px 0;padding:12px;background:var(--mn-panel);
  border:1px solid var(--mn-line);
}
body.manual figure img{max-width:100%;height:auto;display:block;margin:0 auto}
body.manual figcaption{
  margin-top:10px;padding-top:8px;border-top:1px solid var(--mn-line);
  font-size:.76rem;color:var(--mn-muted);text-align:center;letter-spacing:.03em;
}

/* ── 流程图 ── */
body.manual figure.flow{background:var(--mn-bg);padding:20px 14px 12px}
body.manual figure.flow svg{display:block;width:100%;height:auto}
body.manual .fnode{fill:#FFFDF9;stroke:var(--mn-accent);stroke-width:1.6}
body.manual .ftext{
  fill:var(--mn-ink);font-size:13px;font-family:var(--mn-sans);
  text-anchor:middle;dominant-baseline:middle;
}
body.manual .farrow{stroke:var(--mn-accent);stroke-width:1.6}
body.manual .fhead{fill:var(--mn-accent)}

/* ── 表格：三线表 ── */
body.manual table{
  width:100%;border-collapse:collapse;margin:22px 0;font-size:.86rem;
  border:none;border-top:2px solid var(--mn-ink);border-bottom:2px solid var(--mn-ink);
}
body.manual th{
  background:none;color:var(--mn-ink);text-align:left;
  font-weight:700;padding:9px 12px;border-bottom:1px solid var(--mn-ink);
  white-space:nowrap;
}
body.manual td{padding:9px 12px;border:none;vertical-align:top}

/* ── 页内小节目录 ── */
body.manual .toc{
  margin:0 0 26px;padding:14px 18px;background:var(--mn-bg);
  border:1px solid var(--mn-line);
}
body.manual .toc-h{
  font-size:.78rem;letter-spacing:.08em;color:var(--mn-muted);
  font-weight:700;margin-bottom:8px;
}
body.manual .toc-list{list-style:none;margin:0;padding:0}
body.manual .toc-list>li{margin:0 0 9px}
body.manual .toc-list>li:last-child{margin-bottom:0}
body.manual .toc-list>li>a.t1{display:block}
body.manual .toc-sub{
  list-style:none;margin:6px 0 0;padding:0 0 0 13px;
  display:flex;flex-wrap:wrap;gap:6px 8px;align-items:baseline;
  border-left:2px solid var(--mn-line);
}
body.manual .toc a{
  font-size:.83rem;padding:2px 9px;border:1px solid var(--mn-line);
  background:var(--mn-panel);color:var(--mn-muted);display:inline-block;
}
body.manual .toc a.t1{
  border-color:transparent;background:transparent;color:var(--mn-ink);font-weight:700;
  padding-left:0;
}
body.manual .toc a.t2{font-size:.78rem;color:var(--mn-muted);background:transparent}
body.manual .toc a:hover{border-color:var(--mn-accent);color:var(--mn-accent);text-decoration:none}

/* ── 翻页 ── */
body.manual .pager{
  display:flex;align-items:center;justify-content:space-between;gap:12px;
  margin-top:22px;
}
body.manual .pg{
  padding:9px 18px;background:var(--mn-panel);border:1px solid var(--mn-line);
  font-size:.86rem;
}
body.manual .pg:hover{border-color:var(--mn-accent);text-decoration:none}
body.manual .pg-off{color:var(--mn-muted);background:transparent;border-style:dashed}
body.manual .pg-mid{font-size:.78rem;color:var(--mn-muted);letter-spacing:.05em}

/* ── 概览页 ── */
body.manual .hero{
  background:var(--mn-panel);border:1px solid var(--mn-line);
  padding:40px 44px 34px;margin-bottom:22px;
}
body.manual .hero-label{font-size:.76rem;letter-spacing:.18em;color:var(--mn-accent);font-weight:700}
body.manual .hero-title{font-size:1.86rem;margin:10px 0 12px;border:none;padding:0}
body.manual .hero-desc{margin:0;color:var(--mn-muted);max-width:42rem}
body.manual .hero-stats{display:flex;gap:34px;margin:24px 0 0;padding-top:20px;border-top:1px solid var(--mn-line)}
body.manual .hero-stats dt{font-size:.76rem;color:var(--mn-muted);letter-spacing:.06em}
body.manual .hero-stats dd{margin:3px 0 0;font-size:1.3rem;font-weight:700;color:var(--mn-accent-d)}
body.manual .cards{display:grid;gap:12px}
body.manual .card{
  display:grid;grid-template-columns:46px minmax(0,1fr) 22px;
  grid-template-rows:auto auto;
  column-gap:14px;row-gap:3px;align-items:center;
  padding:15px 18px;
  background:var(--mn-panel);border:1px solid var(--mn-line);
  color:var(--mn-ink);transition:border-color .15s ease-out;
}
body.manual .card:hover{border-color:var(--mn-accent);text-decoration:none}
body.manual .card-n{
  grid-column:1;grid-row:1/3;
  font-family:var(--mn-mono);font-size:1.05rem;font-weight:700;line-height:1;
  color:var(--mn-accent);background:var(--mn-soft);
  text-align:center;padding:13px 0;
}
body.manual .card-t{
  grid-column:2;grid-row:1;
  font-weight:700;font-size:.98rem;color:var(--mn-ink);
}
body.manual .card-s{
  grid-column:2;grid-row:2;
  font-size:.78rem;color:var(--mn-muted);
}
body.manual .card-a{
  grid-column:3;grid-row:1/3;justify-self:end;
  color:var(--mn-accent);font-size:1.05rem;
}
body.manual .card-tag{
  display:inline-block;margin-left:8px;padding:0 7px;
  font-size:.7rem;font-weight:700;letter-spacing:.04em;
  background:var(--mn-soft);color:var(--mn-accent-d);vertical-align:1px;
  border:1px solid var(--mn-line);
}
body.manual .card-off{background:#F8F5EE}
body.manual .card-off .card-n{background:#EDE6D8;color:var(--mn-muted)}
body.manual .card-off:hover{border-color:var(--mn-line);text-decoration:none}
body.manual .note{
  margin-top:22px;padding:22px 26px;background:var(--mn-panel);
  border:1px solid var(--mn-line);
}
body.manual .note h2{margin:0 0 10px;background:none;border:none;padding:0;font-size:1rem;color:var(--mn-ink)}
body.manual .note p{margin:8px 0;color:var(--mn-muted);font-size:.9rem}

/* ── 页脚 ── */
body.manual footer.site{
  margin-top:34px;padding:22px 0 30px;border-top:1px solid var(--mn-line);
  color:var(--mn-muted);font-size:.78rem;line-height:1.9;text-align:center;
}

/* 访客统计挂件：通用规则是白字 + 毛玻璃，落在浅色页上几乎看不见，
   所以每个浅色主题都要自己声明一条。 */
body.manual .visit-widget{background:rgba(31,27,22,.06);border-color:rgba(31,27,22,.12);color:var(--mn-ink);box-shadow:0 4px 18px rgba(31,27,22,.08)}

@media(max-width:720px){
  body.manual .doc{padding:26px 20px 34px}
  body.manual .hero{padding:28px 22px 24px}
  body.manual figure.flow{padding:12px 6px 8px}
  body.manual .card{grid-template-columns:40px minmax(0,1fr) 18px;padding:13px 14px}
}
"""


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _pager(i):
    total = len(CH)
    left = ('<a class="pg" href="ch%d.html">← 第 %d 章</a>' % (i - 1, i - 1)
            if i > 1 else '<span class="pg pg-off">已是第一章</span>')
    right = ('<a class="pg" href="ch%d.html">第 %d 章 →</a>' % (i + 1, i + 1)
             if i < total else '<span class="pg pg-off">已是最后一章</span>')
    return ('<nav class="pager">%s<span class="pg-mid">第 %d 章 / 共 %d 章</span>'
            '%s</nav>' % (left, i, total, right))


def flow_html(spec):
    """把 <!-- FLOW: 标题 || A -> B --> 画成内联 SVG。

    用 SVG 而不是位图：流程图是矢量文字，缩放不糊，体积也只有几百字节。
    """
    if "||" in spec:
        title, rest = spec.split("||", 1)
    else:
        title, rest = "", spec
    nodes = [x.strip() for x in rest.split("->") if x.strip()]
    bw, bh, gap = 158, 54, 36
    n = max(1, len(nodes))
    W = n * bw + (n - 1) * gap
    H = bh + 10
    p = ['<figure class="flow"><svg viewBox="0 0 %d %d" role="img" '
         'aria-label="%s">' % (W, H, esc(title))]
    for i, nd in enumerate(nodes):
        x = i * (bw + gap)
        p.append('<rect class="fnode" x="%d" y="5" width="%d" height="%d" rx="4"/>'
                 % (x, bw, bh))
        p.append('<text class="ftext" x="%d" y="%d">%s</text>'
                 % (x + bw // 2, 5 + bh // 2 + 5, esc(nd)))
        if i < n - 1:
            ax = x + bw
            cy = 5 + bh // 2
            p.append('<line class="farrow" x1="%d" y1="%d" x2="%d" y2="%d"/>'
                     % (ax + 5, cy, ax + gap - 9, cy))
            p.append('<polygon class="fhead" points="%d,%d %d,%d %d,%d"/>'
                     % (ax + gap - 10, cy - 5, ax + gap - 2, cy,
                        ax + gap - 10, cy + 5))
    p.append("</svg>")
    if title:
        p.append('<figcaption>%s</figcaption>' % esc(title))
    p.append("</figure>")
    return "".join(p)


def slug(text, used):
    """标题转锚点 id。纯符号标题会退化成 "s"，撞了就加序号。"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text).strip("-")[:40] or "s"
    base, k = s, 1
    while s in used:
        k += 1
        s = "%s-%d" % (base, k)
    used.add(s)
    return s


def render(num, title, fn):
    """渲染一章，返回 <main> 里的片段（正文 + 翻页）。"""
    raw = open(os.path.join(BD_DIR, fn), encoding="utf-8").read()
    imgs, flows = {}, {}

    def _img(m):
        i = len(imgs)
        imgs[i] = (m.group(1), m.group(2))
        return "@@IMG%d@@" % i

    def _flow(m):
        i = len(flows)
        flows[i] = m.group(1)
        return "@@FLOW%d@@" % i

    # 先把两种标记规整成独立段落：源稿里图片偶尔紧跟在一段文字后面，
    # md2html 会把它们并进同一个 <p>，后面按 <p>@@IMGn@@</p> 替换就匹配不到。
    raw = re.sub(r"[ \t]*!\[([^\]]*)\]\(IMG:([^)\s]+)\)[ \t]*",
                 lambda m: "\n\n![%s](IMG:%s)\n\n" % (m.group(1), m.group(2)),
                 raw)
    raw = re.sub(r"[ \t]*(<!--\s*FLOW:\s*(?:.*?)\s*-->)[ \t]*",
                 lambda m: "\n\n%s\n\n" % m.group(1), raw, flags=re.S)

    raw = re.sub(r"!\[([^\]]*)\]\(IMG:([^)\s]+)\)", _img, raw)
    raw = re.sub(r"<!--\s*FLOW:\s*(.*?)\s*-->", _flow, raw, flags=re.S)
    html = M.md2html(raw)

    # 二、三级标题都加锚点 id，并抽出目录条目
    used, heads = set(), []

    def _head(m):
        lvl, inner = m.group(1), m.group(2)
        t = re.sub(r"<[^>]+>", "", inner)
        sid = slug(t, used)
        heads.append((lvl, sid, t))
        return '<h%s id="%s">%s</h%s>' % (lvl, sid, inner, lvl)

    html = re.sub(r"<h([23])>(.*?)</h\1>", _head, html, flags=re.S)

    def _fig(i):
        alt, f = imgs[i]
        return ('<figure><img src="img/%s" alt="%s" loading="lazy">'
                '<figcaption>实验环境截图</figcaption></figure>'
                % (esc(f), esc(alt)))

    html = re.sub(r"<p>@@IMG(\d+)@@</p>",
                  lambda m: _fig(int(m.group(1))), html)
    html = re.sub(r"<p>@@FLOW(\d+)@@</p>",
                  lambda m: flow_html(flows[int(m.group(1))]), html)

    # 章内目录：主节一条，子节按编号前缀挂到所属主节下面。
    # 五段结构决定了操作步骤的子节都叫 x.3.y，单独列出来会让人以为章节从 x.3 开始。
    entries = [(s, t, []) for lv, s, t in heads if lv == "2"]
    idx = {}
    for i, (s, t, _x) in enumerate(entries):
        mm = re.match(r"(\d+\.\d+)", t)
        if mm:
            idx[mm.group(1)] = i
    cur = -1
    for lv, s, t in heads:
        if lv == "2":
            mm = re.match(r"(\d+\.\d+)", t)
            if mm and mm.group(1) in idx:
                cur = idx[mm.group(1)]
        elif lv == "3" and cur >= 0:
            entries[cur][2].append((s, t))
    if entries:
        items = []
        for s, t, subs in entries:
            sub = ""
            if subs:
                sub = ('<ul class="toc-sub">%s</ul>'
                       % "".join('<li><a class="t2" href="#%s">%s</a></li>'
                                 % (a, esc(b)) for a, b in subs))
            items.append('<li><a class="t1" href="#%s">%s</a>%s</li>'
                         % (s, esc(t), sub))
        toc = ('<nav class="toc"><div class="toc-h">本章目录</div>'
               '<ol class="toc-list">%s</ol></nav>' % "".join(items))
        m = re.search(r"<h2[^>]*>.*?</h2>", html, flags=re.S)
        html = html[:m.start()] + toc + html[m.start():] if m else toc + html

    return '<article class="doc">%s</article>%s' % (html, _pager(num))


def img_count():
    """数 my-bd/img 下有多少张截图（首页卡片用）。"""
    n = 0
    d = os.path.join(BD_DIR, "img")
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.lower().endswith((".png", ".jpg", ".jpeg")):
                n += 1
    return n


def index_body():
    """概览页的正文片段：统计 + 章节卡片 + 说明。"""
    refs, flows = set(), 0
    for _num, _title, fn in CH:
        raw = open(os.path.join(BD_DIR, fn), encoding="utf-8").read()
        refs |= set(re.findall(r"!\[[^\]]*\]\(IMG:([^)\s]+)\)", raw))
        flows += len(re.findall(r"<!--\s*FLOW:", raw))

    cards = []
    for num, title, _fn in CH:
        tag = ('<span class="card-tag">%s</span>' % TAGS[num]) \
            if num in TAGS else ""
        cards.append(
            '<a class="card%s" href="ch%d.html"><span class="card-n">%02d</span>'
            '<span class="card-t">%s%s</span>'
            '<span class="card-s">这是什么 · 前置条件 · 操作步骤 · 结果验证 · 常见问题</span>'
            '<span class="card-a">→</span></a>'
            % (" card-off" if num in TAGS else "", num, num, esc(title), tag))

    return (
        '<section class="hero"><div class="hero-label">BIG DATA PLATFORM</div>'
        '<h1 class="hero-title">大数据平台实训</h1>'
        '<p class="hero-desc">按课程章节整理的操作手册：每章从「这是什么」讲起，'
        '给出前置条件、操作步骤、结果验证与常见问题。</p>'
        '<dl class="hero-stats"><div><dt>章节</dt><dd>%d 章</dd></div>'
        '<div><dt>运行截图</dt><dd>%d 张</dd></div>'
        '<div><dt>流程图</dt><dd>%d 个</dd></div></dl></section>'
        '<section class="cards">%s</section>'
        '<section class="note"><h2>说明</h2>'
        '<p>第 1～9 章对应课程目录中的九个实验章节，每章一页，按「这是什么 — 前置条件'
        ' — 操作步骤 — 结果验证 — 常见问题」组织，操作步骤配实验环境截图。</p>'
        '<p>第 10 章是一份尚未完成的实时链路实验方案（标「方案」），第 11 章是把'
        '各章常见故障集中起来的排错速查表（标「速查」），两者都不含运行截图。</p>'
        '</section>') % (len(CH), len(refs), flows, "".join(cards))
