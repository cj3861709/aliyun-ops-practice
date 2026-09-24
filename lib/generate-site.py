#!/usr/bin/env python3
"""
静态站点生成器（纯标准库，Python 3.6 兼容）

输入：
  /root/ops-lab/docs/postmortem/*.md    故障复盘（自动生成索引页和详情页）
  /root/ops-lab/site/content/*.md       其他页面（可选）

输出：
  /root/ops-lab/site/build/             完整静态站点（可直接 rsync 到 web 根目录）

设计理念：
  - 零依赖：不装 python-markdown、不装 hugo/jekyll
  - 纯函数式：输入目录 → 输出目录，无副作用，可重复执行
  - 幂等：重复构建结果一致（时间戳除外，用文件 mtime 而非 now）
  - 【不发外链】：CSS 内联进 HTML，减少请求数（省流量、首屏更快）

为什么内联 CSS：
  一个页面若不内联，浏览器要额外发一次请求取 CSS。
  对只有几页的小站，内联能省掉 1 个 RTT 和一次 TCP 连接，
  在弱网下体感差别明显，而且少一个请求就少一次被限流的可能。
"""

import glob
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import md2html as M

LAB = "/root/ops-lab"
POSTMORTEM_DIR = os.path.join(LAB, "docs", "postmortem")
CONTENT_DIR = os.path.join(LAB, "site", "content")
JOURNAL_DIR = os.path.join(LAB, "my-journal")
BUILD_DIR = os.path.join(LAB, "site", "build")

SITE_TITLE = "运维练习站"
SITE_SUBTITLE = "阿里云 ECS 2C2G · 从零搭建与运维实录"

# ICP 备案号（法律要求显示在网站底部）
ICP_NUMBER = "粤ICP备2026144293号-1"
ICP_URL = "https://beian.miit.gov.cn/"

# ---------------------------------------------------------------------------
# 样式：内联到每个页面。控制在 ~3KB 内，兼顾美观与体积。
# ---------------------------------------------------------------------------
CSS = """
:root{--fg:#1f2933;--muted:#6b7684;--accent:#2b6cb0;--accent2:#2f855a;
--bg:#f7f9fb;--card:#fff;--line:#e4e9f0;--code-bg:#f2f5f9;--warn:#c05621;--crit:#c53030}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI",
"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
color:var(--fg);background:var(--bg)}
.wrap{max-width:820px;margin:0 auto;padding:0 22px}
header.site{background:var(--card);border-bottom:1px solid var(--line);padding:18px 0}
header.site .wrap{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
header.site a.brand{font-weight:700;font-size:1.06rem;color:var(--fg);text-decoration:none}
header.site span.sub{color:var(--muted);font-size:.84rem}
nav.site{margin-left:auto;display:flex;gap:16px}
nav.site a{color:var(--muted);text-decoration:none;font-size:.9rem}
nav.site a:hover,nav.site a.on{color:var(--accent)}
main{padding:36px 0 64px}
h1{font-size:1.72rem;line-height:1.35;margin:0 0 10px;letter-spacing:-.01em}
h2{font-size:1.2rem;margin:34px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line)}
h3{font-size:1.04rem;margin:24px 0 9px;color:var(--accent)}
h4{font-size:.97rem;margin:18px 0 7px}
p{margin:11px 0}
a{color:var(--accent)}
.lede{color:var(--muted);font-size:1.02rem;margin:0 0 26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:20px 22px;margin:0 0 15px}
.card h3{margin-top:0}
.card .meta{color:var(--muted);font-size:.84rem;margin:8px 0 0;
display:flex;gap:14px;flex-wrap:wrap}
.card a.title{font-size:1.1rem;font-weight:650;color:var(--fg);text-decoration:none}
.card a.title:hover{color:var(--accent)}
code{background:var(--code-bg);padding:2px 6px;border-radius:4px;
font:.88em/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
word-break:break-word}
pre{background:#1f2933;color:#e4e9f0;padding:15px 17px;border-radius:8px;
overflow-x:auto;font-size:.86rem;line-height:1.6;margin:14px 0}
pre code{background:none;padding:0;color:inherit;font-size:inherit;word-break:normal}
blockquote{margin:14px 0;padding:10px 16px;border-left:3px solid var(--accent);
background:#eef4fb;color:#33414f;border-radius:0 6px 6px 0}
blockquote p{margin:5px 0}
table{border-collapse:collapse;width:100%;margin:15px 0;font-size:.9rem;
display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:7px 11px;text-align:left;vertical-align:top}
th{background:var(--code-bg);font-weight:650;white-space:nowrap}
ul,ol{margin:11px 0;padding-left:24px}
li{margin:5px 0}
hr{border:0;border-top:1px solid var(--line);margin:30px 0}
.tag{display:inline-block;background:#e6fffa;color:var(--accent2);border-radius:999px;
padding:2px 11px;font-size:.78rem;font-weight:600;margin-right:6px}
.tag.warn{background:#fffaf0;color:var(--warn)}
.tag.crit{background:#fff5f5;color:var(--crit)}
.metabox{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:4px 18px 12px;margin:0 0 26px}
.metabox table{margin:8px 0;font-size:.87rem;display:table}
.metabox th{width:110px;background:none;color:var(--muted);font-weight:500;
border:0;border-bottom:1px solid var(--line);white-space:normal}
.metabox td{border:0;border-bottom:1px solid var(--line)}
.metabox tr:last-child th,.metabox tr:last-child td{border-bottom:0}
footer.site{border-top:1px solid var(--line);color:var(--muted);font-size:.82rem;
padding:22px 0 40px;background:var(--card)}
footer.site code{font-size:.94em}
.nav-back{margin:0 0 20px;font-size:.9rem}
.nav-back a{text-decoration:none}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 28px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:9px;
padding:13px 17px;flex:1;min-width:118px}
.stat b{display:block;font-size:1.32rem;line-height:1.2;color:var(--accent)}
.stat span{color:var(--muted);font-size:.8rem}
.pager{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 24px;padding:11px 16px;background:var(--card);border:1px solid var(--line);border-radius:9px;font-size:.9rem}
.pager-side{flex:1;min-width:0}
.pager-left{text-align:left}
.pager-right{text-align:right}
.pager-mid{flex:0 0 auto;text-align:center;color:var(--muted);font-size:.85rem}
.pager-mid a{text-decoration:none}
.pager-count{display:block;font-size:.76rem;margin-top:2px}
.pager-link{text-decoration:none}
.pager-disabled{color:#c3cbd6}
.kbd-hint{color:var(--muted);font-size:.78rem;text-align:center;margin-top:26px}
.notice{margin:22px 0;padding:16px 20px;border-radius:9px;background:var(--card);border-left:3px solid var(--line)}
.notice strong{display:block;margin-bottom:8px;font-size:.95rem}
.notice p{margin:8px 0;font-size:.9rem;color:var(--muted);line-height:1.7}
.notice p:last-child{margin-bottom:0}
.notice-info{border-left-color:#7ba7d4}
.notice a{text-decoration:none;border-bottom:1px solid currentColor}
.filing{margin-top:10px;font-size:.8rem}
.filing a{color:var(--muted);text-decoration:none;border-bottom:1px solid transparent}
.filing a:hover{border-bottom-color:currentColor}
@media(max-width:600px){.pager{flex-wrap:wrap;gap:8px}.pager-side{flex:1 1 40%}.pager-mid{order:-1;flex:1 1 100%}}
@media(max-width:600px){h1{font-size:1.42rem}header.site .wrap{gap:8px}
nav.site{margin-left:0;width:100%}.wrap{padding:0 16px}}
""".strip()


# 键盘导航脚本（独立常量：避免裸花括号被 str.format() 误解析）
PAGER_JS = """
(function(){
  var prev = document.querySelector('.pager-left a.pager-link');
  var next = document.querySelector('.pager-right a.pager-link');
  if(!prev && !next) return;
  document.addEventListener('keydown', function(e){
    if(e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if(e.key === 'ArrowLeft'  && prev) location.href = prev.getAttribute('href');
    if(e.key === 'ArrowRight' && next) location.href = next.getAttribute('href');
  });
})();
""".strip()


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def page(title, body, rel_root="", active="", description=""):
    """渲染一个完整页面。rel_root 用于子目录页面回指根。"""
    nav = [
        ("index.html", "首页", "home"),
        ("journal.html", "我的手记", "journal"),
        ("postmortem.html", "参考复盘", "pm"),
        ("ops.html", "运维体系", "ops"),
    ]
    navhtml = "".join(
        '<a href="%s%s"%s>%s</a>' % (
            rel_root, href, ' class="on"' if key == active else "", label)
        for href, label, key in nav)

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<style>{css}</style>
</head>
<body>
<header class="site"><div class="wrap">
<a class="brand" href="{root}index.html">{site}</a>
<span class="sub">{sub}</span>
<nav class="site">{nav}</nav>
</div></header>
<main><div class="wrap">
{body}
</div></main>
<footer class="site"><div class="wrap">
{site} · 部署于阿里云 ECS 2C2G（Alibaba Cloud Linux 3 + nginx + systemd）<br>
本站由自研 Python 静态生成器构建，零第三方依赖。
<div class="filing">
<a href="{icp_url}" target="_blank" rel="noopener noreferrer">{icp}</a>
</div>
</div></footer>
<script>{js}</script>
</body>
</html>
""".format(title=esc(title), desc=esc(description), css=CSS,
           root=rel_root, site=esc(SITE_TITLE), sub=esc(SITE_SUBTITLE),
           nav=navhtml, body=body, js=PAGER_JS,
           icp=ICP_NUMBER, icp_url=ICP_URL)


def load_journals():
    """读取用户手记（my-journal/*.md）。跳过 _ 开头的模板文件。"""
    items = []
    if not os.path.isdir(JOURNAL_DIR):
        return items
    for path in sorted(glob.glob(os.path.join(JOURNAL_DIR, "*.md"))):
        name = os.path.basename(path)
        if name.startswith("_"):
            continue
        raw = open(path, encoding="utf-8").read()
        m = re.match(r"^(\d+)[-_](.+)\.md$", name)
        if m:
            num, slug = m.group(1), m.group(2)
        else:
            num, slug = "000", name[:-3]
        items.append({
            "path": path,
            "num": num,
            "slug": slug,
            "title": M.extract_title(raw),
            "meta": M.extract_meta_table(raw),
            "raw": raw,
            "mtime": os.path.getmtime(path),
            "size": os.path.getsize(path),
            "lines": raw.count("\n") + 1,
        })
    items.sort(key=lambda x: x["num"])
    return items


def load_postmortems():
    """读取所有复盘文档，返回按编号排序的列表。"""
    items = []
    for path in sorted(glob.glob(os.path.join(POSTMORTEM_DIR, "*.md"))):
        raw = open(path, encoding="utf-8").read()
        name = os.path.basename(path)
        m = re.match(r"^(\d+)-(.+)\.md$", name)
        num = m.group(1) if m else "999"
        slug = m.group(2) if m else name[:-3]
        meta = M.extract_meta_table(raw)
        items.append({
            "path": path,
            "num": num,
            "slug": slug,
            "title": M.extract_title(raw),
            "meta": meta,
            "raw": raw,
            "mtime": os.path.getmtime(path),
            "size": os.path.getsize(path),
            "lines": raw.count("\n") + 1,
        })
    items.sort(key=lambda x: x["num"])
    return items


def severity_tag(meta):
    """根据影响字段生成标签。"""
    impact = (meta.get("影响") or meta.get("严重级别") or "")
    if "中断" in impact or "不可用" in impact:
        return '<span class="tag crit">服务中断</span>'
    if "静默" in impact:
        return '<span class="tag crit">静默失败</span>'
    if impact:
        return '<span class="tag">%s</span>' % esc(impact[:22])
    return '<span class="tag">已修复</span>'


def build_home(pms, journals=None):
    total_lines = sum(p["lines"] for p in pms)
    total_size = sum(p["size"] for p in pms)

    cards = []
    for p in pms:
        date = p["meta"].get("日期", "")
        cards.append("""<div class="card">
<a class="title" href="postmortem/{num}-{slug}.html">{title}</a>
<div class="meta">{tag}<span>{date}</span><span>{lines} 行</span></div>
</div>""".format(num=p["num"], slug=esc(p["slug"]),
                 title=esc(p["title"]), tag=severity_tag(p["meta"]),
                 date=esc(date), lines=p["lines"]))

    body = """<h1>{site}</h1>
<p class="lede">{sub}</p>

<div class="stats">
<div class="stat"><b>2 vCPU / 2 GB</b><span>阿里云 ECS · 华北1 青岛</span></div>
<div class="stat"><b>0 s</b><span>发布中断时长（实测 25315 请求零失败）</span></div>
<div class="stat"><b>1.11 s</b><span>回滚耗时（实测）</span></div>
<div class="stat"><b>{n} 篇</b><span>故障复盘</span></div>
</div>

<h2>这是什么</h2>
<p>一台 2 核 2G 的云服务器上，从零搭建并运维一个静态技术展示站。
重点不在于页面本身，而在于<strong>它背后的运维体系</strong>——
发布流程、回滚机制、崩溃自愈、流量防护、监控告警，
以及每一项能力的<strong>验证证据</strong>。</p>

<h2>两部分内容</h2>
<div class="card">
<h3><a href="journal.html">我的手记</a></h3>
<p>我自己动手做过的实操记录 —— 从部署静态页面、用 systemd 托管服务、
排查静默失效故障，到用 Python 写日志分析与探活脚本。
每篇包含真实命令、实际输出、遇到的错误与修复过程。</p>
</div>
<div class="card">
<h3><a href="postmortem.html">参考复盘</a></h3>
<p>搭建本环境过程中产生的技术记录，涵盖 nginx、systemd、cron、
安全加固等主题。按标准复盘结构写成，作为写作样例参考。</p>
</div>

<h2>核心能力</h2>
<div class="card">
<h3>原子发布与零停机</h3>
<p>不可变发布物 + 符号链接原子切换，发布流程含权限预检、
语法校验、健康检查与失败自动回滚。
用<strong>反向对照实验</strong>验证：发布期间 25,315 个请求零失败，
而对照组用 <code>restart</code> 复现了 408 个失败——证明测试方法有效，不是盲区。</p>
</div>
<div class="card">
<h3>秒级回滚</h3>
<p>版本目录不覆盖，回滚只是把软链接指回去并 reload。
实测 <strong>1.11 秒</strong>完成，期间 20,821 个请求零失败。</p>
</div>
<div class="card">
<h3>崩溃自愈</h3>
<p>发现发行版 nginx unit <strong>缺失 <code>Restart</code> 策略</strong>
（OOM 后不会自动恢复），用 systemd drop-in 修复，
并以 <code>SIGKILL</code> 注入验证。审计全机发现 10 个服务存在同类隐患。</p>
</div>
<div class="card">
<h3>流量防护与成本控制</h3>
<p>基于 <strong>20 GB/月</strong> 免费额度设计双层防护：
nginx 限流限连（实测正常用户零误伤、攻击流量拦截 11,547 次）
+ 自研流量预算监控与分级告警（阈值 682.7 MB/天）。</p>
</div>

<h2>故障复盘 <span style="font-size:.85rem;color:var(--muted);font-weight:400">
（{n} 篇，共 {lines} 行）</span></h2>
<p class="lede">每篇都记录了真实现象、排查路径（含走过的弯路）、根因、
修复方案、验证方法与可迁移的通用经验。</p>
{cards}
""".format(site=esc(SITE_TITLE), sub=esc(SITE_SUBTITLE), n=len(pms),
           lines=total_lines, cards="".join(cards))
    return page(SITE_TITLE + " · 运维实践记录", body,
                active="home",
                description="阿里云 ECS 2C2G 上的运维实践：零停机发布、秒级回滚、"
                            "崩溃自愈、流量防护与故障复盘库")


def build_journal_index(items):
    """我的手记 —— 列表页"""
    if not items:
        body = """<h1>我的手记</h1>
<p class="lede">这里会记录我自己动手做过的每一次实操与排障。</p>
<div class="card">
<h3>还没有内容 —— 这是你自己的区域</h3>
<p>等第一条记录写好，它就会出现在这里。</p>
<p style="color:var(--muted);font-size:.9rem;margin-top:14px">
怎么开始：在服务器上执行<br>
<code>cat /root/ops-lab/quests/001-deploy-page.md</code><br>
领取第一个任务，做完后把手记写到 <code>/root/ops-lab/my-journal/</code>。</p>
</div>"""
        return page("我的手记 · " + SITE_TITLE, body, active="journal",
                    description="我自己的运维实操记录与排障手记")

    cards = []
    for it in items:
        meta = it["meta"]
        bits = "".join("<span>%s</span>" % esc(meta[k])
                       for k in ("日期", "用时", "难度") if k in meta)
        cards.append("""<div class="card">
<a class="title" href="journal/{num}-{slug}.html">{title}</a>
<div class="meta">{bits}<span>{lines} 行</span></div>
</div>""".format(num=it["num"], slug=esc(it["slug"]),
                 title=esc(it["title"]), bits=bits, lines=it["lines"]))

    body = """<h1>我的手记</h1>
<p class="lede">我自己动手做过的实操记录 —— 踩过的坑、排查过程、修复方法。</p>
{cards}""".format(cards="".join(cards))
    return page("我的手记 · " + SITE_TITLE, body, active="journal",
                description="我自己的运维实操记录与排障手记")


def build_journal_page(it, prev_it=None, next_it=None, idx=0, total=0):
    """单篇手记（含上一篇/下一篇导航）"""
    raw = M.strip_first_heading(it["raw"])
    html = M.md2html(raw)
    meta = it["meta"]
    rows = "".join("<tr><th>%s</th><td>%s</td></tr>" % (esc(k), esc(v))
                   for k, v in meta.items())
    metabox = '<div class="metabox"><table>%s</table></div>' % rows if rows else ""

    def nav_link(o, arrow, label):
        if not o:
            return '<span class="pager-disabled">%s %s</span>' % (arrow, label)
        return '<a class="pager-link" href="%s-%s.html">%s %s</a>' % (
            o["num"], esc(o["slug"]), arrow, label)

    prev_html = nav_link(prev_it, "←", "上一篇")
    next_html = nav_link(next_it, "→", "下一篇")
    counter = ('<span class="pager-count">第 %d 篇 / 共 %d 篇</span>' % (idx, total)) if total else ""

    pager = (
        '<div class="pager">'
        '<div class="pager-side pager-left">' + prev_html + '</div>'
        '<div class="pager-mid"><a href="../journal.html">我的手记</a>' + counter + '</div>'
        '<div class="pager-side pager-right">' + next_html + '</div>'
        '</div>'
    )

    kbd = ('<script>(function(){'
           'var ids=[];'
           'if(%s)ids.push("prev");'
           'if(%s)ids.push("next");'
           '})();</script>') % ("1" if prev_it else "0", "1" if next_it else "0")

    body = (pager + "\n<h1>" + esc(it["title"]) + "</h1>\n" + metabox + "\n"
            + html + "\n" + pager + "\n"
            + '<p class="kbd-hint">可用键盘 ← → 切换上下篇</p>\n')

    return page(it["title"] + " · " + SITE_TITLE, body, rel_root="../",
                active="journal", description=it["title"])


def build_postmortem_index(pms):
    cards = []
    for p in pms:
        meta = p["meta"]
        rows = ""
        for k in ("日期", "环境", "影响", "严重级别", "处理耗时"):
            if k in meta:
                rows += "<tr><th>%s</th><td>%s</td></tr>" % (esc(k), esc(meta[k]))
        cards.append("""<div class="card">
<a class="title" href="postmortem/{num}-{slug}.html">{title}</a>
<div class="meta">{tag}</div>
<table style="margin-top:12px">{rows}</table>
</div>""".format(num=p["num"], slug=esc(p["slug"]),
                 title=esc(p["title"]), tag=severity_tag(meta), rows=rows))

    body = """<h1>参考复盘</h1>
<p class="lede">这 8 篇是在搭建本项目过程中产生的技术记录，
涵盖 nginx 配置、systemd 服务管理、cron 陷阱、安全加固等主题。
每篇包含完整的现象描述、排查路径与验证方法。</p>

<div class="notice">
<strong>关于这部分的定位</strong>
<p>这些复盘记录的是<strong>环境搭建与调试过程中出现的技术问题</strong>，
作为复盘写作的参考样例保留在这里。</p>
<p>我自己的实操记录在 <a href="journal.html">我的手记</a> 中——
那里记录的是我亲手执行的部署、排障与脚本开发过程。</p>
</div>

<div class="notice notice-info">
<strong>为什么保留这些样例</strong>
<p>一份完整的复盘应该包含：现象、排查过程、根因、修复、验证、预防。
这 8 篇按这个结构写成，可以作为写作模板参考。</p>
<p>其中有两篇记录了<strong>"先判断错误、后纠正"</strong>的过程——
这类内容比只写成功结果的记录更有参考价值，因为
<strong>排查过程中走弯路是常态</strong>。</p>
</div>

{cards}""".format(cards="".join(cards))
    return page("参考复盘 · " + SITE_TITLE, body, active="pm",
                description="运维技术记录：nginx 配置、systemd 管理、安全加固")


def build_postmortem_page(p):
    raw = M.strip_first_heading(p["raw"])
    html = M.md2html(raw)

    meta = p["meta"]
    rows = ""
    for k, v in meta.items():
        rows += "<tr><th>%s</th><td>%s</td></tr>" % (esc(k), esc(v))
    metabox = '<div class="metabox"><table>%s</table></div>' % rows if rows else ""

    body = """<p class="nav-back"><a href="../postmortem.html">← 返回复盘库</a></p>
<h1>{title}</h1>
<p>{tag}</p>
{metabox}
{content}""".format(title=esc(p["title"]), tag=severity_tag(meta),
                    metabox=metabox, content=html)

    return page(p["title"] + " · " + SITE_TITLE, body, rel_root="../",
                active="pm", description=p["title"])


def build_ops_page():
    body = """<h1>运维体系</h1>
<p class="lede">这个站点背后的每一层设计，以及为什么这么选。</p>

<h2>部署架构</h2>
<pre><code>/var/www/releases/&lt;时间戳&gt;-&lt;标签&gt;/   不可变发布物，每个版本独立
/var/www/personal-site              软链接 → 当前生效版本
/var/www/releases/.current          当前版本指针
/var/www/releases/.history          版本历史（回滚依据）</code></pre>
<p>用软链接而不是直接覆盖文件，因为覆盖是<strong>破坏性</strong>的：
切一半失败会得到半新半旧的状态。软链接切换是
<strong>原子</strong>的（<code>mv -T</code> 的 rename 语义），
要么全新要么全旧，没有中间态。</p>

<h2>发布流程（9 步，每步失败都不留半成品）</h2>
<table>
<tr><th>步骤</th><th>动作</th><th>失败时</th></tr>
<tr><td>1</td><td>校验源目录（必须有 index.html）</td><td>拒绝发布</td></tr>
<tr><td>2</td><td>记录回滚点 + 备份 nginx 配置</td><td>中止</td></tr>
<tr><td>3</td><td>创建新 release 目录（<code>cp -a</code>）</td><td>中止</td></tr>
<tr><td>4</td><td>权限预检（以 nginx 用户试读）</td><td>删除新目录，中止</td></tr>
<tr><td>5</td><td><code>nginx -t</code> 语法校验</td><td>删除新目录，中止</td></tr>
<tr><td>6</td><td>原子切换软链接</td><td>—</td></tr>
<tr><td>7</td><td><code>reload</code>（非 restart）</td><td>—</td></tr>
<tr><td>8</td><td>健康检查 5 次</td><td><strong>自动回滚</strong></td></tr>
<tr><td>9</td><td>清理旧版本（保留 5 个）</td><td>跳过生效版本</td></tr>
</table>

<h2>为什么是 reload 而不是 restart</h2>
<table>
<tr><th>方式</th><th>总请求</th><th>失败</th><th>master 进程</th></tr>
<tr><td><code>reload</code></td><td>25,496</td><td><strong>0</strong></td><td>保留</td></tr>
<tr><td><code>restart</code></td><td>25,612</td><td><strong>408</strong></td><td>更换</td></tr>
</table>
<p><code>restart</code> 的 408 个失败（393 个 connection refused +
15 个 connection reset）不是"顺带发现"，而是<strong>刻意设计的对照组</strong>——
它证明我的压测方法确实能检测到服务中断，
所以 <code>reload</code> 的零失败才是可信证据。</p>

<h2>流量防护（按 20 GB/月 额度设计）</h2>
<p>CDT 免费额度按地域区分：中国内地 <strong>20 GB/月</strong>，
非中国内地 200 GB/月。本机在青岛，所以额度是 20 GB ≈ 682.7 MB/天。</p>
<pre><code>limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=perip:10m;
limit_req  zone=general burst=20 nodelay;
limit_conn perip 20;
limit_req_status 429;</code></pre>
<p>取值依据：真人浏览瞬时峰值约 10–20 请求/秒，自动化工具通常 100+ 请求/秒，
所以 <code>10r/s + burst=20</code> 只挡后者。</p>
<p><strong>双向验证</strong>（只测"能拦住"是不完整的——过严的限流赶走真人更糟）：</p>
<table>
<tr><th>实验</th><th>负载</th><th>结果</th></tr>
<tr><td>A 正常用户</td><td>2 请求/秒 × 10</td><td>10/10 成功，<strong>零误伤</strong></td></tr>
<tr><td>B 攻击负载</td><td>8 并发，2320 QPS</td><td>11,547 个被 429 拦截</td></tr>
</table>

<h2>监控与告警</h2>
<pre><code>lib/traffic-monitor.py          日志分析引擎（纯标准库）
bin/traffic-check.sh            阈值判定 + 告警
/etc/cron.d/ops-lab-traffic     每小时巡检
/etc/cron.d/ops-lab-daily-report 每日日报
logs/traffic/                   历史报告（自动保留 48 份）
logs/alerts/                    告警记录</code></pre>
<p>阈值来自额度换算而非拍脑袋：<code>20GB ÷ 30 天 = 682.7 MB/天</code>，
50% 警告、80% 严重。</p>
<p><strong>测量局限</strong>（这点很重要）：<code>/proc/net/dev</code> 的计数
<strong>包含内网流量</strong>——阿里云 agent 大量发往
<code>100.64.0.0/10</code> 内部服务网，不出机房、不计费。
实测站点出流量只占 eth0 发送量的 5.58%，真实计费数字要看控制台 CDT。</p>

<h2>技术选型理由</h2>
<table>
<tr><th>选择</th><th>理由</th></tr>
<tr><td>nginx 直跑静态文件</td><td>内存 14.8 MB；Node/Python 后端起步 50–100 MB</td></tr>
<tr><td>不用 Docker 跑网站</td><td>多一层 overlayfs 与网络命名空间开销，2 GB 内存要留给其他练习</td></tr>
<tr><td>不用宝塔面板</td><td>掩盖底层原理，面试说不清它改了什么</td></tr>
<tr><td>自研 Markdown 转换器</td><td>服务器软件越少攻击面越小；且零依赖可审计</td></tr>
<tr><td>CSS 内联进 HTML</td><td>省一次 HTTP 请求，弱网下体感明显，也少一次被限流的可能</td></tr>
</table>

<h2>工具集</h2>
<table>
<tr><th>工具</th><th>用途</th></tr>
<tr><td><code>backup-config.sh</code></td><td>改配置前自动备份（保留 30 份）</td></tr>
<tr><td><code>healthcheck.sh</code></td><td>一键系统巡检</td></tr>
<tr><td><code>deploy.sh</code></td><td>原子发布（9 步 + 自动回滚）</td></tr>
<tr><td><code>rollback.sh</code></td><td>秒级回滚</td></tr>
<tr><td><code>traffic-check.sh</code></td><td>流量巡检 + 分级告警</td></tr>
<tr><td><code>loadtest.py</code></td><td>标准库并发压测器（支持按时长）</td></tr>
<tr><td><code>md2html.py</code></td><td>Markdown 转换器（含 XSS 防护，55 项测试）</td></tr>
</table>
"""
    return page("运维体系 · " + SITE_TITLE, body, active="ops",
                description="运维体系设计：部署架构、发布流程、限流、监控告警与技术选型")


def main():
    print("=" * 60)
    print(" 静态站点生成")
    print("=" * 60)

    pms = load_postmortems()
    print("\n读取复盘文档: %d 篇" % len(pms))
    for p in pms:
        print("  #%s %s (%d 行, %d 字节)" % (
            p["num"], p["title"][:44], p["lines"], p["size"]))

    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR)
    os.makedirs(os.path.join(BUILD_DIR, "postmortem"))
    print("\n输出目录: %s" % BUILD_DIR)

    written = []

    def emit(relpath, content):
        full = os.path.join(BUILD_DIR, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        written.append((relpath, len(content.encode("utf-8"))))

    journals = load_journals()
    print("\n读取我的手记: %d 篇" % len(journals))
    for it in journals:
        print("  #%s %s (%d 行)" % (it["num"], it["title"][:40], it["lines"]))

    emit("index.html", build_home(pms, journals))
    emit("journal.html", build_journal_index(journals))
    for i, it in enumerate(journals):
        prev_it = journals[i - 1] if i > 0 else None
        next_it = journals[i + 1] if i < len(journals) - 1 else None
        emit("journal/%s-%s.html" % (it["num"], it["slug"]),
             build_journal_page(it, prev_it, next_it, i + 1, len(journals)))
    emit("postmortem.html", build_postmortem_index(pms))
    emit("ops.html", build_ops_page())
    for p in pms:
        emit("postmortem/%s-%s.html" % (p["num"], p["slug"]),
             build_postmortem_page(p))

    # robots.txt：允许收录，但屏蔽构建产物外的路径
    emit("robots.txt", "User-agent: *\nAllow: /\n")

    print("\n生成文件:")
    total = 0
    for name, size in written:
        print("  %-46s %7d 字节" % (name, size))
        total += size
    print("\n  合计: %d 文件, %d 字节 (%.1f KB)" % (
        len(written), total, total / 1024.0))

    # 体积报告：对按量计费有意义
    print("\n体积评估（单次访问首页的传输量）:")
    for name, size in written:
        if name == "index.html":
            print("  首页未压缩: %.1f KB" % (size / 1024.0))
            print("  gzip 后约:  %.1f KB（按 35%% 压缩率估算）" % (size * 0.35 / 1024.0))
    print("\n  按 gzip 后 8 KB 计算，20 GB 额度可支撑约 %d 次首页访问" % (
        20 * 1024 * 1024 / 8))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
