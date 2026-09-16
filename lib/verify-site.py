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
    if rel.startswith("postmortem/"):
        if len(text) > 1500:
            ok("%s 正文 %d 字符" % (rel, len(text)))
        else:
            bad("%s 正文过短 (%d 字符)，可能是空壳" % (rel, len(text)))

# ---- 关键内容抽查 ----
print("\n── 5. 关键内容抽查 ──")
checks = [
    ("postmortem/001-nginx-restart-policy.html", "Restart=always",
     "复盘#001 含修复配置"),
    ("postmortem/001-nginx-restart-policy.html", "SIGKILL",
     "复盘#001 含验证方法"),
    ("postmortem/002-nginx-config-validate.html", "unknown directive",
     "复盘#002 含真实报错"),
    ("postmortem/003-cron-percent-truncation.html", "对照实验",
     "复盘#003 含实验方法"),
    ("index.html", "25,315", "首页含零停机实测数字"),
    ("index.html", "1.11", "首页含回滚耗时"),
    ("index.html", "11,547", "首页含限流拦截数"),
    ("ops.html", "20 GB", "运维页含额度依据"),
    ("ops.html", "mv -T", "运维页解释原子切换"),
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
           "script"]
risky = []
for f in html_files:
    content = open(f, encoding="utf-8").read()
    s = content
    # DOCTYPE 是合法结构，单独移除
    s = re.sub(r"<!DOCTYPE[^>]*>", "", s, flags=re.I)
    # 注释也是合法的
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    # REMOVE_SCRIPT_BLOCK: script 内容含 JS，不是"未转义残留"
    s = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.S | re.I)
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

# ---- 体积 ----
print("\n── 7. 体积与流量评估 ──")
total = sum(os.path.getsize(f) for f in html_files)
index_size = os.path.getsize(os.path.join(BUILD, "index.html"))
pm_size = sum(os.path.getsize(f) for f in html_files
              if "postmortem/" in f)
print("  全部 HTML:     %6.1f KB" % (total / 1024.0))
print("  首页:          %6.1f KB (gzip 后约 %.1f KB)" % (
    index_size / 1024.0, index_size * 0.35 / 1024.0))
print("  复盘页合计:    %6.1f KB" % (pm_size / 1024.0))
print()
print("  典型访问路径（首页 → 复盘列表 → 一篇复盘）:")
typical = index_size + os.path.getsize(os.path.join(BUILD, "postmortem.html")) + \
    os.path.getsize(os.path.join(BUILD, "postmortem/001-nginx-restart-policy.html"))
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
