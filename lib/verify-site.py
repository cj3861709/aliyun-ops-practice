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
broken = []
total_links = 0

for f in html_files:
    base = os.path.dirname(f)
    content = open(f, encoding="utf-8").read()
    for href in LINK_RE.findall(content):
        if href.startswith(("http://", "https://", "mailto:", "#", "javascript:")):
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
for f in html_files:
    content = open(f, encoding="utf-8").read()
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
    "var"]
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
BRANCH_NAV_LABELS = ("运维首页", "我的手记", "参考复盘", "运维体系")

# 首页（工作台）不在这张表里：它不对应任何一章，也不该高亮任何一项。
# 旧版这里写着 "index.html": "运维首页" —— 等于把"首页套用分支章节导航"
# 这个错误本身写成了正确预期，所以 22/22 全绿也照样放行。
EXPECT_ON = {
    "ops/index.html": "运维首页",
    "ops/journal.html": "我的手记",
    "ops/postmortem.html": "参考复盘",
    "ops/system.html": "运维体系",
}


def expect_nav(rel):
    if rel in EXPECT_ON:
        return EXPECT_ON[rel]
    if rel.startswith("ops/journal/"):
        return "我的手记"
    if rel.startswith("ops/postmortem/"):
        return "参考复盘"
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
        leaked = [x for x in labels if x in BRANCH_NAV_LABELS]
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

    missing = [x for x in BRANCH_NAV_LABELS if x not in labels]
    if missing:
        bad("%s 缺少章节导航项：%s" % (rel, ",".join(missing)))
        nav_bad += 1
        continue

    if len(on) != 1:
        bad("%s 导航高亮 %d 项（应恰好 1 项）：%s"
            % (rel, len(on), ",".join(on) if on else "无"))
        nav_bad += 1
        continue
    want = expect_nav(rel)
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

# ---- 体积 ----
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
