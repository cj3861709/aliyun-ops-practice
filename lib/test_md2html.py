#!/usr/bin/env python3
"""
md2html 转换器的单元测试（修正版）

上一版的两个问题：
  1. 测试框架 bug：check() 在 expect_absent 分支下传 False 当 expect_sub，
     导致 `in` 操作报 TypeError。
  2. 安全断言太naive：检查 "onclick=" 字符串是否存在，
     但转义后它只是【纯文本】，出现在 <p> 里不构成漏洞。
     正确判据应该是：输出中所有 '<' 是否只属于我们自己生成的白名单标签。

本版引入严格判据 has_only_safe_tags()：
  把所有白名单标签从输出中移除后，若仍残留 '<' 或 '>'，
  说明有未经转义的内容混进来了 —— 那就是真正的 XSS。
"""
import re
import sys
sys.path.insert(0, "/root/ops-lab/lib")

import md2html as M

passed = 0
failed = 0

# 我们【自己生成】的所有标签（转换器的合法输出）
ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
    "pre", "code", "blockquote", "hr", "strong", "em", "a",
    "figure", "figcaption", "img",
]


def has_only_safe_tags(html_out):
    """
    严格安全判据：移除所有白名单标签后，不应残留任何 '<' 或 '>'。
    残留即意味着有未转义的标记注入。
    返回 (bool, 残留片段)
    """
    s = html_out
    for tag in ALLOWED_TAGS:
        # 移除 <tag>、</tag>、<tag attr="...">
        s = re.sub(r"</?%s(\s[^>]*)?>" % tag, "", s)
    leftover = s
    if "<" in leftover or ">" in leftover:
        # 找出残留上下文，便于定位
        idx = leftover.find("<")
        if idx < 0:
            idx = leftover.find(">")
        return False, leftover[max(0, idx - 40):idx + 40]
    return True, ""


def check(name, got, expect_sub=None, expect_absent=None, must_be_safe=True):
    global passed, failed
    problems = []

    if expect_sub is not None and expect_sub not in got:
        problems.append("缺少期望内容 %r" % expect_sub)

    if expect_absent:
        for bad in expect_absent:
            if bad in got:
                problems.append("不应包含 %r" % bad)

    if must_be_safe:
        ok, leftover = has_only_safe_tags(got)
        if not ok:
            problems.append("存在未转义的标记残留: %r" % leftover)

    if problems:
        print("  ❌ %s" % name)
        for p in problems:
            print("     %s" % p)
        print("     实际输出: %r" % got)
        failed += 1
    else:
        print("  ✅ %s" % name)
        passed += 1


print("=" * 68)
print(" md2html 转换器测试（修正版）")
print("=" * 68)

print()
print("── 1. 标题 ──")
check("h1", M.md2html("# 标题一"), "<h1>标题一</h1>")
check("h2", M.md2html("## 标题二"), "<h2>标题二</h2>")
check("h4", M.md2html("#### 四级"), "<h4>四级</h4>")

print()
print("── 2. 段落 ──")
check("单段落", M.md2html("这是一段话"), "<p>这是一段话</p>")
check("空行分段", M.md2html("第一段\n\n第二段"), "<p>第一段</p>")
check("软换行合并", M.md2html("甲\n乙"), "<p>甲 乙</p>")

print()
print("── 3. 代码块 ──")
out = M.md2html("```bash\necho hello\n```")
check("围栏代码块", out,
      '<pre class="language-bash"><code class="language-bash">')
check("语言标记", out, 'class="language-bash"')
check("代码内容", out, "echo hello")
check("无语言", M.md2html("```\nplain\n```"), "<pre><code>plain</code></pre>")

print()
print("── 4. 表格 ──")
out = M.md2html("| 列A | 列B |\n|---|---|\n| 1 | 2 |")
check("表格结构", out, "<table>")
check("表头", out, "<th>列A</th>")
check("表体", out, "<td>1</td>")
check("表尾", out, "</table>")

print()
print("── 5. 列表 ──")
check("无序列表", M.md2html("- 甲\n- 乙"), "<li>甲</li>")
check("有序列表", M.md2html("1. 第一\n2. 第二"), "<ol>")
check("段落与列表分离", M.md2html("普通\n- 项"), "<p>普通</p>")

print()
print("── 6. 引用与分隔线 ──")
check("引用", M.md2html("> 内容"), "<blockquote>内容</blockquote>")
check("分隔线", M.md2html("---"), "<hr>")

print()
print("── 7. 行内格式 ──")
check("行内代码", M.md2html("用 `ls` 查"), "<code>ls</code>")
check("粗体", M.md2html("**重点**"), "<strong>重点</strong>")
check("斜体", M.md2html("*强调*"), "<em>强调</em>")
check("链接", M.md2html("[字](http://e.com)"), '<a href="http://e.com">字</a>')

print()
print("── 7b. 图片 ──")
check("整行图片",
      M.md2html("![图 1](img/image1.png)"),
      '<figure><img src="img/image1.png" alt="图 1">'
      '<figcaption>图 1</figcaption></figure>')
check("行内图片",
      M.md2html("见 ![图 2](img/image2.png) 所示"),
      '<img src="img/image2.png" alt="图 2">')
check("图片危险协议被拦截", M.md2html("![x](javascript:alert(1))"),
      "x", expect_absent=['src="javascript'])
check("整行图片危险协议退化", M.md2html("![x](data:text/html,x)"),
      "<p>x</p>", expect_absent=["<img"])
check("有序列表起始编号", M.md2html("8. 第八\n9. 第九"), '<ol start="8">')
check("有序列表从 1 起不带 start", M.md2html("1. 第一\n2. 第二"), "<ol>")

print()
print("── 8. 行内代码保护 ──")
out = M.md2html("`a * b` 和 **粗体**")
check("代码内星号不解析", out, "<code>a * b</code>")

print()
print("── 9. 【安全】HTML 转义（严格判据）──")
# 关键：这些输入里的 HTML 必须被转义成纯文本，输出中不能有未白名单的标记
check("script 标签", M.md2html("<script>alert(1)</script>"),
      "&lt;script&gt;")
check("img onerror", M.md2html('<img src=x onerror="alert(1)">'),
      "&lt;img")
check("属性注入（整段被转义，onclick 只是纯文本）",
      M.md2html('<a href="x" onclick="evil()">点</a>'),
      "&lt;a", expect_absent=["<a href=\"x\""])
check("代码块内转义", M.md2html("```\n<script>bad</script>\n```"), "&lt;script&gt;")
check("表格内转义", M.md2html("| a |\n|---|\n| <b>x</b> |"), "&lt;b&gt;")
check("标题内转义", M.md2html("# <script>x</script>"), "&lt;script&gt;")
check("列表内转义", M.md2html("- <script>x</script>"), "&lt;script&gt;")
check("引用内转义", M.md2html("> <script>x</script>"), "&lt;script&gt;")
check("行内代码内转义", M.md2html("`<script>x</script>`"), "&lt;script&gt;")

print()
print("── 10. 【安全】危险协议拦截 ──")
check("javascript:", M.md2html("[点](javascript:alert(1))"),
      "点", expect_absent=['href="javascript'])
check("data:", M.md2html("[点](data:text/html,x)"),
      "点", expect_absent=['href="data:'])
check("vbscript:", M.md2html("[点](vbscript:x)"),
      "点", expect_absent=['href="vbscript:'])
check("http 放行", M.md2html("[站](https://e.com)"), 'href="https://e.com"')
check("相对路径放行", M.md2html("[页](/a.html)"), 'href="/a.html"')

print()
print("── 11. 元信息提取 ──")
doc = """# 复盘 #099：测试

| 项目 | 内容 |
|---|---|
| 日期 | 2026-01-01 |
| 影响 | 无 |

## 1. 现象
内容
"""
meta = M.extract_meta_table(doc)
check("提取日期", str(meta), "2026-01-01")
check("提取影响", str(meta), "无")
check("提取标题", M.extract_title(doc), "复盘 #099：测试")
stripped = M.strip_first_heading(doc)

# 修正后：直接断言首标题已被移除
if "复盘 #099" not in stripped:
    print("  ✅ 去掉首标题")
    passed += 1
else:
    print("  ❌ 去掉首标题（仍存在）")
    failed += 1

print()
print("── 12. 边界情况 ──")
check("空输入", M.md2html(""), "", must_be_safe=True)
check("只有空行", M.md2html("\n\n\n"), "")
check("未闭合代码块", M.md2html("```\n未闭合内容"), "未闭合内容")
check("未闭合粗体", M.md2html("**未闭合"), "**未闭合")
check("含 & 符号", M.md2html("A & B"), "A &amp; B")
check("含 <> 符号", M.md2html("1 < 2 > 0"), "1 &lt; 2 &gt; 0")
check("中文与 emoji", M.md2html("# 中文标题 ✅"), "中文标题 ✅")

print()
print("── 13. 真实文档端到端 ──")
import os
for name in ["001-nginx-restart-policy.md", "002-nginx-config-validate.md",
             "003-cron-percent-truncation.md"]:
    path = "/root/ops-lab/docs/postmortem/" + name
    if not os.path.exists(path):
        print("  ⚠️ 跳过 %s（不存在）" % name)
        continue
    real = open(path, encoding="utf-8").read()
    out = M.md2html(real)
    check("%s 可转换" % name[:12], out, "<h2>")
    check("%s 安全性" % name[:12], out, None, must_be_safe=True)

print()
print("=" * 68)
print(" 结果: 通过 %d, 失败 %d" % (passed, failed))
print("=" * 68)
sys.exit(1 if failed else 0)
