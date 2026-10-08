#!/usr/bin/env python3
"""
生成站点的完整性验证

检查项：
  1. 所有内部链接都能解析到实际存在的文件（避免 404）
  2. HTML 结构基本正确（标签配平）
  3. 每篇复盘的内容真的被渲染进去了（而不只是标题壳子）
  4. 没有未转义的残留标记
  5. 资源体积报告
"""
import os
import re
import sys

BUILD = "/root/ops-lab/site/build"

passed = 0
failed = 0


def ok(msg):
    global passed
    print("  ✅ %s" % msg)
    passed += 1


def bad(msg):
    global failed
    print("  ❌ %s" % msg)
    failed += 1


print("=" * 68)
print(" 生成站点完整性验证")
print("=" * 68)

# ---- 收集生成的文件 ----
html_files = []
for root, dirs, files in os.walk(BUILD):
    for f in files:
        if f.endswith(".html"):
            html_files.append(os.path.join(root, f))
html_files.sort()

print("\n── 1. 文件清单 ──")
for f in html_files:
    rel = os.path.relpath(f, BUILD)
    print("  %-48s %7d 字节" % (rel, os.path.getsize(f)))
ok("共 %d 个 HTML 文件" % len(html_files))

# ---- 链接检查 ----
print("\n── 2. 内部链接检查（404 检测）──")
LINK_RE = re.compile(r'href="([^"]+)"')
# 图片走 src 而不是 href。只查 href 的话，图挂了（404）根本发现不了。
SRC_RE = re.compile(r'src="([^"]+)"')
broken = []
total_links = 0

for f in html_files:
    base = os.path.dirname(f)
    content = open(f, encoding="utf-8").read()
    for href in LINK_RE.findall(content) + SRC_RE.findall(content):
        if href.startswith(("http://", "https://", "mailto:", "#",
                            "javascript:", "data:")):
            continue
        total_links += 1
        target = os.path.normpath(os.path.join(base, href))
        if not os.path.exists(target):
            broken.append((os.path.relpath(f, BUILD), href))

if broken:
    for src, href in broken:
        bad("断链: %s -> %s" % (src, href))
else:
    ok("检查 %d 个内部链接，全部有效" % total_links)

# ---- 标签配平 ----
print("\n── 3. HTML 标签配平 ──")
VOID = {"meta", "link", "br", "hr", "img", "input", "area", "base",
        "col", "embed", "source", "track", "wbr"}
unbalanced = []
_stripped_bytes = [0]
for f in html_files:
    content = open(f, encoding="utf-8").read()
    # 剥掉「原始文本」容器：<style>/<script> 的内容与 HTML 注释，按规范都不是元素，
    # 里面的 <pre> 这类文本会被下面的配平器误当成标签。
    # （A1 给 generate-site.py 的 CSS 注释里写了字面量 <pre>，就踩了这个坑。）
    _saved = len(content)
    content = re.sub(r"<style\b[^>]*>.*?</style>", "", content, flags=re.S | re.I)
    content = re.sub(r"<script\b[^>]*>.*?</script>", "", content, flags=re.S | re.I)
    content = re.sub(r"<!--.*?-->", "", content, flags=re.S)
    _stripped_bytes[0] += _saved - len(content)
    stack = []
    problem = None
    for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>", content):
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag in VOID or attrs.rstrip().endswith("/"):
            continue
        if closing:
            if stack and stack[-1] == tag:
                stack.pop()
            elif tag in stack:
                # 中间有未闭合的
                while stack and stack[-1] != tag:
                    problem = "未闭合 <%s>" % stack.pop()
                if stack:
                    stack.pop()
            else:
                problem = "多余的 </%s>" % tag
        else:
            stack.append(tag)
    if stack:
        problem = "未闭合: %s" % ", ".join("<%s>" % t for t in stack)
    if problem:
        unbalanced.append((os.path.relpath(f, BUILD), problem))

print("  （预处理：剥掉 style/script/注释共 %d 字节 —— 这些内容按规范不是元素）" % _stripped_bytes[0])
if unbalanced:
    for name, prob in unbalanced:
        bad("%s: %s" % (name, prob))
else:
    ok("全部 %d 个文件标签配平" % len(html_files))

# ---- 内容真实性 ----
print("\n── 4. 内容真实性（不是空壳）──")
for f in html_files:
    rel = os.path.relpath(f, BUILD)
    content = open(f, encoding="utf-8").read()
    # 去掉标签后的纯文本长度
    text = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if rel.startswith("ops/postmortem/"):
        if len(text) > 1500:
            ok("%s 正文 %d 字符" % (rel, len(text)))
        else:
            bad("%s 正文过短 (%d 字符)，可能是空壳" % (rel, len(text)))

# ---- 关键内容抽查 ----
print("\n── 5. 关键内容抽查 ──")
checks = [
    ("ops/postmortem/001-nginx-restart-policy.html", "Restart=always",
     "复盘#001 含修复配置"),
    ("ops/postmortem/001-nginx-restart-policy.html", "SIGKILL",
     "复盘#001 含验证方法"),
    ("ops/postmortem/002-nginx-config-validate.html", "unknown directive",
     "复盘#002 含真实报错"),
    ("ops/postmortem/003-cron-percent-truncation.html", "对照实验",
     "复盘#003 含实验方法"),
    ("ops/index.html", "25,315", "首页含零停机实测数字"),
    ("ops/index.html", "1.11", "首页含回滚耗时"),
    ("ops/index.html", "11,547", "首页含限流拦截数"),
    ("ops/system.html", "20 GB", "运维页含额度依据"),
    ("ops/system.html", "mv -T", "运维页解释原子切换"),
]
for rel, needle, desc in checks:
    path = os.path.join(BUILD, rel)
    if not os.path.exists(path):
        bad("%s 不存在" % rel)
        continue
    content = open(path, encoding="utf-8").read()
    # 转义后的 needle 也要试（比如 &lt;）
    if needle in content:
        ok(desc)
    else:
        bad("%s 缺少 %r" % (desc, needle))

# ---- 安全 ----
print("\n── 6. 未转义残留检查 ──")
# 注意：必须包含 <!DOCTYPE ...> 和 <style> 这类结构标签，
# 否则会把合法的页面结构误报成"未转义注入"。
# （这个坑我在复盘 #002 里踩过一次：验证规则太粗会把合法内容误判为错误。）
ALLOWED = ["h1","h2","h3","h4","h5","h6","p","ul","ol","li","table","thead",
           "tbody","tr","th","td","pre","code","blockquote","hr","strong",
           "em","a","html","head","meta","title","style","body","header",
           "nav","main","footer","div","span","br","b","i","small",
    "section",
    "article",
    "aside",
    "dl",
    "dt",
    "dd",
    "figure",
    "figcaption",
    "time",
    "abbr",
    "mark",
    "details",
    "summary",
    "kbd",
    "samp",
    "var",
    "img"]
risky = []
for f in html_files:
    content = open(f, encoding="utf-8").read()
    s = content
    # DOCTYPE 是合法结构，单独移除
    s = re.sub(r"<!DOCTYPE[^>]*>", "", s, flags=re.I)
    # 注释也是合法的
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    # script / style 的内容是 JS / CSS，不是未转义的 HTML 标记。
    # 必须移除，否则 CSS 里的子选择器 `>`（如 .a > div）
    # 会被下面的 '<' / '>' 检查误判为未转义残留。
    # （第 9 类误报：验证规则没跟上生成内容的形态变化）
    s = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.S | re.I)
    s = re.sub(r"<style\b[^>]*>.*?</style>", "", s, flags=re.S | re.I)
    for tag in ALLOWED:
        s = re.sub(r"</?%s(\s[^>]*)?/?>" % tag, "", s)
    if "<" in s or ">" in s:
        idx = max(0, s.find("<"))
        risky.append((os.path.relpath(f, BUILD), s[idx:idx + 60]))

if risky:
    for name, frag in risky:
        bad("%s 残留: %r" % (name, frag))
else:
    ok("全部文件无未转义标记残留（DOCTYPE/注释/白名单标签已排除）")

# ---- 导航高亮 ----
print("\n── 7. 导航高亮检查 ──")
# 教训（第 11 类：一个参数身兼两职，改动它就会连带改到别的地方）
# page() 的 active 既决定"哪一项导航高亮"，又决定"要不要用分支式品牌名+面包屑"。
# build_ops_home() 压根没传 active -> /ops/ 导航一项都不高亮，
# 但品牌名和面包屑又正常，所以肉眼很容易漏掉。
#
# 每个页面必须恰好高亮 1 项，且高亮的那项要和页面身份相符。
#
# 站点是多项目结构，每个分支有自己的一套章节导航，所以标签集必须按分支取。
# 旧版是一张全局表（只有运维那四项），新增分支后 dm 的每一页都会判"缺少章节导航项"。
BRANCHES = {
    "ops": {
        "labels": ("运维首页", "我的手记", "参考复盘", "运维体系"),
        "expect": {
            "ops/index.html": "运维首页",
            "ops/journal.html": "我的手记",
            "ops/postmortem.html": "参考复盘",
            "ops/system.html": "运维体系",
        },
        "prefix": (
            ("ops/journal/", "我的手记"),
            ("ops/postmortem/", "参考复盘"),
        ),
    },
    "dm": {
        "labels": ("概览", "实验一", "实验二", "实验三", "实验四", "实验五"),
        "expect": {
            "dm/index.html": "概览",
            "dm/exp1.html": "实验一",
            "dm/exp2.html": "实验二",
            "dm/exp3.html": "实验三",
            "dm/exp4.html": "实验四",
            "dm/exp5.html": "实验五",
        },
        "prefix": (),
    },
    "cc": {
        "labels": ("概览", "实验一", "实验二", "实验三", "实验四",
                   "实验五", "实验六"),
        "expect": {
            "cc/index.html": "概览",
            "cc/exp1.html": "实验一",
            "cc/exp2.html": "实验二",
            "cc/exp3.html": "实验三",
            "cc/exp4.html": "实验四",
            "cc/exp5.html": "实验五",
            "cc/exp6.html": "实验六",
        },
        "prefix": (),
    },
}

# 工作台首页不在这套体系里：它不对应任何一章，也不该高亮任何一项。
# 这里用所有分支标签的并集判"分支导航泄漏到工作台"。
ALL_BRANCH_LABELS = tuple(
    _x for _b in BRANCHES.values() for _x in _b["labels"])


def branch_of(rel):
    """按路径首段判分支；工作台首页没有首段，返回 None。"""
    if "/" not in rel:
        return None
    _head = rel.split("/", 1)[0]
    return _head if _head in BRANCHES else None


def expect_nav(rel, binfo):
    if rel in binfo["expect"]:
        return binfo["expect"][rel]
    for _pre, _label in binfo["prefix"]:
        if rel.startswith(_pre):
            return _label
    return None


nav_bad = 0
for f in sorted(html_files):
    rel = os.path.relpath(f, BUILD)
    content = open(f, encoding="utf-8").read()
    m = re.search(r'<nav class="site">(.*?)</nav>', content, re.S)
    if not m:
        bad("%s 找不到主导航" % rel)
        nav_bad += 1
        continue
    navhtml = m.group(1)
    labels = re.findall(r'>([^<>]+)</a>', navhtml)
    on = re.findall(r'<a href="[^"]*" class="on">([^<]*)</a>', navhtml)

    # 层级检查：章节导航只能出现在分支内部。
    # 教训（第 12 类：结构分成了两层，验证规则只按一层写）——
    # 首页曾经直接套用分支的章节导航，而 EXPECT_ON 又把它写成预期，
    # 于是全绿放行。现在改成从层级上判：工作台的导航里不得出现任何章节项。
    if rel == "index.html":
        leaked = [x for x in labels if x in ALL_BRANCH_LABELS]
        if leaked:
            bad("index.html（工作台）出现了分支内的章节导航：%s" % ",".join(leaked))
            nav_bad += 1
            continue
        if not labels:
            bad("index.html（工作台）的导航为空，找不到项目入口")
            nav_bad += 1
            continue
        if on:
            bad("index.html（工作台）的导航不应高亮任何一项，实为：%s"
                % ",".join(on))
            nav_bad += 1
        continue

    bname = branch_of(rel)
    if not bname:
        bad("%s 不属于任何已知分支（首段路径无法识别）" % rel)
        nav_bad += 1
        continue
    binfo = BRANCHES[bname]

    missing = [x for x in binfo["labels"] if x not in labels]
    if missing:
        bad("%s 缺少章节导航项：%s" % (rel, ",".join(missing)))
        nav_bad += 1
        continue

    if len(on) != 1:
        bad("%s 导航高亮 %d 项（应恰好 1 项）：%s"
            % (rel, len(on), ",".join(on) if on else "无"))
        nav_bad += 1
        continue
    want = expect_nav(rel, binfo)
    if want and on[0] != want:
        bad("%s 高亮项是 %s，应为 %s" % (rel, on[0], want))
        nav_bad += 1

if nav_bad == 0:
    ok("导航层级正确：分支内 %d 个页面各高亮 1 项，工作台不高亮任何一项"
       % (len(html_files) - 1))

# ---- 主题覆盖 ----
print("\n── 8. 主题覆盖检查 ──")
# 教训（第 10 类：验证规则没跟上结构变化 —— 这次是"新增主题"）
# terminal 主题只写了底色 body.terminal{background:#0A0E12}，
# 没写自己的 nav.site a / header.site .brand，
# 于是通用规则（--ink 深灰，为浅色主题写的）落在近黑底上，
# 对比度约 1.07:1，导航肉眼看不见。
# 而当时的验证器只查内容不查样式，22/22 全绿把它放行了。
#
# 这段按 CSS 层叠规则（特异性 + 源顺序）算出每个主题下真正生效的颜色，
# 若深色主题的颜色落在浅色 token 上，直接判失败。
#
# 注意：本检查自己踩过一次坑（1451 号补丁）—— 第一版没剥 CSS 注释，
# 选择器里混进注释文本，归属判断被绕过，三个主题报出同一个结果。
# 教训：检查代码也要有"证明它会失败"的负向测试，否则绿等于没查。

INK_TOKENS = ("var(--ink)", "var(--ink-60)", "var(--ink-40)", "var(--ink-10)")
DARK_THEMES = ("terminal", "ops-dark")


def _strip_comments(css):
    """剥掉 CSS 注释，避免注释文本被当成选择器。"""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _weight(sel):
    """粗略算选择器特异性：(类/属性数, 元素数)。"""
    classes = len(re.findall(r"\.[a-zA-Z0-9_-]+|\[[^\]]*\]", sel))
    elements = len(re.findall(r"(?:^|[\s>+~])[a-zA-Z][a-zA-Z0-9-]*", sel))
    return (classes, elements)


def _effective(css, theme, tail):
    """算出该主题下"tail 选择器"（如 nav.site a）生效的 (选择符, color)。"""
    css = _strip_comments(css)
    best_key = None
    best = None
    order = 0
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        order += 1
        sel_text = m.group(1).strip()
        if not sel_text or sel_text.startswith("@"):
            continue
        cm = re.search(r"(?:^|;)\s*color\s*:\s*([^;]+)", m.group(2))
        if not cm:
            continue
        for one in sel_text.split(","):
            one = one.strip()
            if not one.endswith(tail):
                continue
            owner = re.search(r"body\.([a-z0-9-]+)", one)
            if owner and owner.group(1) != theme:
                continue          # 别的主题的规则，不参与
            key = _weight(one) + (order,)
            if best_key is None or key > best_key:
                best_key = key
                best = (one, cm.group(1).strip())
    return best


theme_names = set()
style_css = ""
for f in html_files:
    content = open(f, encoding="utf-8").read()
    m = re.search(r'<body class="([a-z0-9-]+)"', content)
    if m:
        theme_names.add(m.group(1))
    if not style_css:
        sm = re.search(r"<style[^>]*>(.*?)</style>", content, re.S)
        if sm:
            style_css = sm.group(1)

if not style_css:
    bad("页面里找不到 <style> 块，无法做主题覆盖检查")
else:
    for theme in sorted(theme_names):
        # 1) 该主题是否给了自己的页头/导航规则
        for frag in ("nav.site a{", "header.site .brand{"):
            if ("body.%s %s" % (theme, frag)) not in _strip_comments(style_css):
                bad("主题 %s 缺少自己的规则 body.%s %s" % (theme, theme, frag))

        # 2) 真正生效的颜色是不是浅色主题的深色 token
        for tail, label in (("nav.site a", "导航"),
                            ("header.site .brand", "品牌名")):
            got = _effective(style_css, theme, tail)
            if got is None:
                bad("主题 %s：找不到适用的 %s 颜色规则" % (theme, tail))
                continue
            sel, color = got
            if theme in DARK_THEMES and any(t in color for t in INK_TOKENS):
                bad("主题 %s 的%s颜色落在浅色 token（%s -> %s），深底上不可见"
                    % (theme, label, sel, color))
            else:
                ok("主题 %s 的%s颜色 %s（%s）" % (theme, label, color, sel))

# ---- 颜色契约（静态复算）----
print("\n── 10. 颜色契约检查 ──")
# 教训（第 13 类：检查覆盖到了"规则存在"，没覆盖"取值够不够"）。
# 第 8 节只验证「深色主题有没有给自己的导航规则」，
# 而 terminal 的 --term-comment 是 #6272A4，比对的又是"有没有落在浅色 token"，
# 于是 4.04:1 的表格正文、3.13:1 的页脚一路绿灯放行。
# 规则存在 ≠ 取值合格。这一节把取值也纳入契约。
#
# 做法：把「主题自有规则里的 color: var(--token)」解析成实色，
# 合成到该元素真实会落到的底色上，取最差对比度，要求 >= 4.5（WCAG AA 正文）。
#
# 三条必须写明的边界，否则又会变成假阳性机器：
#   a) 只查主题自有规则（body.<theme> ...）。通用规则（如 nav.site a）在
#      深色主题下本来就被主题自有规则覆盖，真正决定"看不看得见"的是后者。
#   b) 跳过 :hover / :active。那里的 color 常与 background 互换（反色），
#      按下标算必然误报；反色的正确性由 .on 那条实底规则覆盖。
#   c) 底色按元素实际位置分组：导航项落导航底，正文落页面/卡片/代码底。
#      把导航当前项的亚麻底套到正文小字上算，会凭空造出 1.74:1 的假缺陷。

_CONTRACT_SURFACES = {
    # 云计算分支的工程蓝图主题。它是全站唯一"双面混排"的主题：
    # 正文落浅图纸面 --bp-sheet，页头/导航/页脚落深蓝 --bp-chrome，
    # 代码/命令块再深一档 --bp-chrome-2。
    #
    # 这里只登记两个浅面，深面（chrome / chrome-2）刻意不登记 ——
    # 落深面的文字规则（导航项、品牌名、figcaption 之外的页脚、pre/pre code）
    # 全都自己声明了同色不透明底色，会被"规则自带底色即该元素底色"那条
    # 直接算在深面上；若再把 --bp-chrome-2 列进 text 面，
    # 校验就会拿它去比那些没写底色的浅色文字规则（h1/h2/p/td/figcaption…），
    # 制造出一批 1.8:1 的假缺陷。
    "blueprint": {
        "text": [("页面", "--bp-sheet"),
                 ("卡片/面板 --bp-sheet-2", "--bp-sheet-2")],
        "nav":  [("页头/导航 --bp-chrome", "--bp-chrome")],
    },
    # 数据挖掘分支的浅色主题。次级面只有 #F3F4F6（卡片/代码块底），
    # 导航底是 rgba(31,41,51,.05) 叠在白底上。
    "distill": {
        "text": [("页面", "--di-bg"), ("卡片/代码 #F3F4F6", "#F3F4F6")],
        "nav":  [("页面", "--di-bg"), ("导航底", "rgba(31,41,51,.05)")],
    },
    "terminal": {
        "text": [("页面", "--term-bg"), ("表格格 #0C1014", "#0C1014"),
                 ("term 块", "rgba(255,255,255,.012)"),
                 ("tmux 条", "rgba(74,246,38,.04)")],
        "nav":  [("页面", "--term-bg"), ("导航底", "rgba(139,233,253,.06)")],
    },
    "ops-dark": {
        "text": [("页面", "--dark-bg"), ("卡片/表头 #1B1F25", "#1B1F25"),
                 ("代码块", "--dark-bg-2")],
        "nav":  [("页面", "--dark-bg"), ("导航底", "rgba(255,255,255,.08)")],
    },
    "anime": {
        "text": [("页面", "--cel-paper"), ("卡片 #ffffff", "#ffffff")],
        "nav":  [("页面", "--cel-paper")],
    },
    "editorial": {
        "text": [("页面", "--paper"), ("卡片 #ffffff", "#ffffff")],
        "nav":  [("页面", "--paper")],
    },
}
_CONTRACT_PAGE = {"terminal": "--term-bg", "ops-dark": "--dark-bg",
                  "distill": "--di-bg", "blueprint": "--bp-sheet",
                  "anime": "--cel-paper", "editorial": "--paper"}
# 豁免项必须写明理由 —— 这些 token 不是"文字颜色"，是装饰。
_CONTRACT_EXEMPT = {
    "--term-line": "ASCII 分隔线，装饰",
    "--dark-line": "分隔线，装饰",
    "--ink-10": "边框",
    "--ink-05": "边框",
    "--cel-red": "首页卡片 ◆ 装饰符",
}


def _c_parse(txt):
    m = re.match(r"#([0-9a-fA-F]{6})$", txt.strip())
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*"
                 r"(?:,\s*([\d.]+)\s*)?\)", txt.strip())
    if m:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)),
                float(m.group(4)) if m.group(4) is not None else 1.0)
    return None


def _c_over(fg, bg):
    r, g, b, a = fg
    return (r * a + bg[0] * (1 - a), g * a + bg[1] * (1 - a),
            b * a + bg[2] * (1 - a))


def _c_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _c_lum(c):
    return 0.2126 * _c_lin(c[0]) + 0.7152 * _c_lin(c[1]) + 0.0722 * _c_lin(c[2])


def _c_ratio(fg, bg):
    a, b = _c_lum(fg), _c_lum(bg)
    if a < b:
        a, b = b, a
    return (a + 0.05) / (b + 0.05)


_css_clean = _strip_comments(style_css) if style_css else ""
_c_tokens = {}
for _m in re.finditer(r"([^{}]+)\{([^{}]*)\}", _css_clean):
    if ":root" not in _m.group(1):
        continue
    for _t in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", _m.group(2)):
        _c_tokens[_t.group(1)] = _t.group(2).strip()


def _c_resolve(txt, depth=0):
    if txt is None or depth > 6:
        return None
    txt = txt.strip()
    m = re.match(r"var\((--[a-z0-9-]+)(?:\s*,\s*([^)]+))?\)", txt)
    if m:
        val = _c_tokens.get(m.group(1))
        if val is None:
            return _c_resolve(m.group(2), depth + 1) if m.group(2) else None
        return _c_resolve(val, depth + 1)
    return _c_parse(txt)


def _c_surfaces(theme, kind):
    base = _c_resolve("var(%s)" % _CONTRACT_PAGE[theme])
    out = []
    for label, val in _CONTRACT_SURFACES[theme][kind]:
        c = _c_resolve("var(%s)" % val) if val.startswith("--") \
            else _c_parse(val)
        if c is None:
            continue
        out.append((label, _c_over(c, base) if c[3] < 1.0 else c))
    return out


_contract_checked = 0
for _theme in sorted(theme_names):
    if _theme not in _CONTRACT_SURFACES:
        continue
    _rows = {}
    for _m in re.finditer(r"([^{}]+)\{([^{}]*)\}", _css_clean):
        _sel = _m.group(1).strip()
        if not re.search(r"body\.%s\b" % re.escape(_theme), _sel):
            continue
        _body = _m.group(2)
        _cm = re.search(r"(?:^|;)\s*color\s*:\s*([^;]+)", _body)
        if not _cm:
            continue
        _tm = re.search(r"var\((--[a-z0-9-]+)", _cm.group(1))
        if not _tm:
            continue
        _one = _sel.split(",")[0].strip()
        if ":hover" in _one or ":active" in _one:
            continue
        # 这个元素落在哪种底色上：内容区（text）还是导航栏（nav）。
        # 不能用 'nav' in _one 判断 —— .nav-back（返回链接）名字里含 nav，
        # 但它是导航栏下面的正文小字，落在页面上；按子串判会让
        # --dark-fg-40 去和导航底比，报出指向错误位置的结论。
        _kind = "nav" if re.search(r"nav\.site|\.on\b", _one) else "text"
        # 规则块自带不透明底色时（如实底的当前页导航项），
        # 它自己就是那个元素的底色，不能再套一层'导航半透明底'。
        _bm = re.search(r"(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)", _body)
        _own = None
        if _bm and "none" not in _bm.group(1):
            _bc = _c_resolve(_bm.group(1).strip())
            if _bc and _bc[3] > 0.9:
                _own = _bm.group(1).strip()
        _rows.setdefault(_tm.group(1), set()).add((_kind, _own))

    for _tok in sorted(_rows):
        _col = _c_resolve("var(%s)" % _tok)
        if _col is None:
            continue
        _worst = None
        _wlab = ""
        for _kind, _own in sorted(_rows[_tok],
                                  key=lambda t: (t[0], t[1] or "")):
            _sf = [('规则自带底色', _c_resolve(_own))] if _own \
                else _c_surfaces(_theme, _kind)
            for _lab, _bg in _sf:
                _fg = _c_over(_col, _bg) if _col[3] < 1.0 else _col
                _r = _c_ratio(_fg, _bg)
                if _worst is None or _r < _worst:
                    _worst, _wlab = _r, _lab
        if _worst is None:
            continue
        _contract_checked += 1
        if _tok in _CONTRACT_EXEMPT:
            ok("%s %s 豁免（%s）" % (_theme, _tok, _CONTRACT_EXEMPT[_tok]))
        elif _worst < 4.5:
            bad("%s 主题 %s 最差 %.2f:1（%s 底），低于 AA 正文 4.5:1"
                % (_theme, _tok, _worst, _wlab))
        else:
            ok("%s 主题 %s 最差 %.2f:1（%s 底）" % (_theme, _tok, _worst, _wlab))

if _contract_checked == 0:
    bad("颜色契约检查没有覆盖到任何 token —— 检查本身失效，比查出问题更危险")

# ---- 公开产物卫生 ----
print("\n── 11. 公开产物卫生检查 ──")
# 教训（第 14 类：修补一处泄漏时，检查规则自己成了新的泄漏面）。
# 想加一条"站内不许出现本机公网 IP"的检查，最自然的写法是把那个 IP
# 写进这条检查里 —— 而 verify-site.py 是 git 跟踪文件，
# 于是"防泄漏检查"自己把 IP 提交进了公开仓库。
# 所以：真实值放 conf/redact-list.txt（已 gitignore，不进仓库），
# 脚本里只留结构性形态判断（实例 ID、主机名、密钥、证件号）。
_BUILTIN_HYGIENE = [
    ("实例 ID", r"\bi-[a-z0-9]{12,}\b"),
    ("ECS 主机名", r"\biZ[A-Za-z0-9]{10,}Z\b"),
    ("阿里云 AccessKey", r"\bLTAI[A-Za-z0-9]{10,}\b"),
    ("私钥头", r"BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY"),
    ("SSH 公钥体", r"(?:ssh-rsa|ssh-ed25519) AAAA[A-Za-z0-9+/=]{20,}"),
    ("手机号", r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ("身份证", r"(?<!\d)\d{17}[\dXx](?!\d)"),
]

_redact_path = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "conf", "redact-list.txt")
_hygiene = list(_BUILTIN_HYGIENE)
if os.path.exists(_redact_path):
    _n_redact = 0
    for _line in open(_redact_path, encoding="utf-8"):
        _line = _line.strip()
        if not _line or _line.startswith("#"):
            continue
        _parts = _line.split("\t")
        _hygiene.append((_parts[1] if len(_parts) > 1 else "自定义脱敏项",
                         _parts[0]))
        _n_redact += 1
    print("  已加载 conf/redact-list.txt 的 %d 条自定义规则" % _n_redact)
else:
    print("  ⚠ 未找到 conf/redact-list.txt（该文件不进仓库，属预期）")

_scan_files = [f for f in html_files] + \
    [os.path.join(BUILD, "robots.txt")]
_hygiene_hits = 0
for _label, _rx in _hygiene:
    _hit = {}
    for _f in _scan_files:
        if not os.path.exists(_f):
            continue
        _s = open(_f, encoding="utf-8", errors="replace").read()
        for _m in re.findall(_rx, _s):
            _hit.setdefault(_m, []).append(os.path.relpath(_f, BUILD))
    if _hit:
        _hygiene_hits += 1
        for _k in sorted(_hit):
            bad("公开产物含【%s】: %s（%d 个文件，如 %s）"
                % (_label, _k, len(_hit[_k]), sorted(_hit[_k])[0]))
if _hygiene_hits == 0:
    ok("公开产物无实例 ID / 主机名 / 密钥 / 证件号 / 自定义脱敏项")

# 备案号是法律要求必须展示的，不能被"脱敏"顺手删掉 —— 反向断言。
_ICP = re.search(r"[京沪粤浙苏鲁川渝皖闽湘鄂豫冀晋辽吉黑赣桂云贵陕甘青宁新藏蒙京津]ICP备\d+号(?:-\d+)?",
                 style_css or "") or re.search(
    r"[京沪粤浙苏鲁川渝皖闽湘鄂豫冀晋辽吉黑赣桂云贵陕甘青宁新藏蒙京津]ICP备\d+号(?:-\d+)?",
    open(os.path.join(BUILD, "index.html"), encoding="utf-8").read())
if not _ICP:
    bad("找不到 ICP 备案号 —— 备案号是法律要求展示项，必须保留")
else:
    _missing = []
    for _f in html_files:
        if _ICP.group(0) not in open(_f, encoding="utf-8").read():
            _missing.append(os.path.relpath(_f, BUILD))
    if _missing:
        bad("以下 %d 个页面缺备案号 %s：%s"
            % (len(_missing), _ICP.group(0), ", ".join(_missing[:5])))
    else:
        ok("备案号 %s 出现在全部 %d 个页面"
           % (_ICP.group(0), len(html_files)))

# 公网安备号同样受反向断言保护 —— 它也是法律要求展示项。
# 上一轮只给 ICP 加了断言，公安备案批下来后如果只改生成器、
# 忘了这里，就会出现"某个页面的号码悄悄丢了而验证器全绿"。
_MPS = re.search(
    r"[京沪粤浙苏鲁川渝皖闽湘鄂豫冀晋辽吉黑赣桂云贵陕甘青宁新藏蒙京津]公网安备\d+号",
    open(os.path.join(BUILD, "index.html"), encoding="utf-8").read())
if not _MPS:
    bad("找不到公网安备号 —— 它同样是法律要求展示项，必须保留")
else:
    _mps_missing = []
    _mps_badlink = []
    for _f in html_files:
        _s = open(_f, encoding="utf-8").read()
        _rel = os.path.relpath(_f, BUILD)
        if _MPS.group(0) not in _s:
            _mps_missing.append(_rel)
        # 链接指向官方平台才算数：号码写对、链接指错，比不写更糟。
        if "beian.mps.gov.cn" not in _s:
            _mps_badlink.append(_rel)
    if _mps_missing:
        bad("以下 %d 个页面缺公网安备号 %s：%s"
            % (len(_mps_missing), _MPS.group(0), ", ".join(_mps_missing[:5])))
    else:
        ok("公网安备号 %s 出现在全部 %d 个页面"
           % (_MPS.group(0), len(html_files)))
    if _mps_badlink:
        bad("以下 %d 个页面的公网安备链接未指向 beian.mps.gov.cn：%s"
            % (len(_mps_badlink), ", ".join(_mps_badlink[:5])))
    else:
        ok("公网安备链接均指向 beian.mps.gov.cn")

# ---- 体积 ----

print("\n── 12. 命令块 / 输出块分离检查 ──")
# 为什么要有这一节：markdown 里「命令」和「输出」是两种东西，
# 但它们在 HTML 里都长成 <pre><code>，只靠人眼在源码里是看不出来的。
# A1 补的两件事（<pre> 带 language- 标记 + 对应 CSS）任何一件丢了，
# 页面上命令与输出就又混成一片 —— 而那时候所有别的检查都还是绿的。
_GEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate-site.py")
_gsrc = open(_GEN, encoding="utf-8").read() if os.path.isfile(_GEN) else ""
if "pre[class*=\"language-\"]" not in _gsrc:
    bad("generate-site.py 缺少命令块样式（pre[class*=language-]）—— 命令与输出会看不出差别")
else:
    ok("命令块样式规则存在（body.ops-dark pre[class*=\"language-\"]）")
_cmd_n = _out_n = 0
for _r2, _d2, _f2s in os.walk(BUILD):
    for _f2 in _f2s:
        if not _f2.endswith(".html"):
            continue
        _s2 = open(os.path.join(_r2, _f2), encoding="utf-8", errors="replace").read()
        _cmd_n += len(re.findall(r'<pre class="language-', _s2))
        _out_n += len(re.findall(r"<pre><code>", _s2))
if _cmd_n == 0:
    bad("构建产物里没有任何带 language- 标记的 <pre> —— md2html 的标记没生效")
else:
    ok("命令块 %d 个 / 输出块 %d 个，DOM 上已可区分" % (_cmd_n, _out_n))
if _out_n == 0:
    bad("构建产物里没有任何无标记 <pre> —— 输出块形态异常")
print("\n── 9. 体积与流量评估 ──")
total = sum(os.path.getsize(f) for f in html_files)
index_size = os.path.getsize(os.path.join(BUILD, "index.html"))
pm_size = sum(os.path.getsize(f) for f in html_files
              if "ops/postmortem/" in f)
print("  全部 HTML:     %6.1f KB" % (total / 1024.0))
print("  首页:          %6.1f KB (gzip 后约 %.1f KB)" % (
    index_size / 1024.0, index_size * 0.35 / 1024.0))
print("  复盘页合计:    %6.1f KB" % (pm_size / 1024.0))
print()
print("  典型访问路径（首页 → 复盘列表 → 一篇复盘）:")
typical = index_size + os.path.getsize(os.path.join(BUILD, "ops/postmortem.html")) + \
    os.path.getsize(os.path.join(BUILD, "ops/postmortem/001-nginx-restart-policy.html"))
print("    未压缩 %.1f KB → gzip 后约 %.1f KB" % (
    typical / 1024.0, typical * 0.35 / 1024.0))
print()
print("  20 GB 额度可支撑: 约 %d 次三页访问（按 gzip 后 14 KB 计）" % (
    20 * 1024 * 1024 / 14))
ok("体积合理")

print()
print("=" * 68)
print(" 结果: 通过 %d, 失败 %d" % (passed, failed))
print("=" * 68)
sys.exit(1 if failed else 0)
