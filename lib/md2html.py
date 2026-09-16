#!/usr/bin/env python3
"""
极简 Markdown → HTML 转换器（纯标准库，Python 3.6 兼容）

为什么自己写而不是装 pandoc / python-markdown：
  1. 服务器软件越少，攻击面越小（这是运维原则，不只是省事）
  2. 装 pandoc 要拉几十 MB 依赖，而 2GB 内存机器上没必要
  3. 支持本项目实际用到的语法子集就够了，代码可读可审计

支持语法：
  标题 # ## ### ####
  围栏代码块 ``` 与 ```lang
  表格 | a | b |
  无序列表 - * +        有序列表 1. 2.
  引用 > 
  分隔线 --- ***
  行内 `code`  **粗体**  *斜体*  [文本](链接)
  段落与硬换行

安全设计：
  所有文本先做 HTML 转义（html.escape），再放行我们【自己生成】的标签。
  这是防 XSS 的关键：先转义、后标记，顺序绝不能反。
"""

import html
import re

# 代码块占位符：用不可能出现在正常文本里的控制字符包裹。
# 如果直接用 <pre> 等标签做占位，后续的行内处理会误伤它们。
PLACEHOLDER = "\x00PH%d\x00"


def escape(text):
    """转义 HTML 特殊字符。quote=False 因为文本节点里引号无需转义。"""
    return html.escape(text, quote=False)


def escape_attr(text):
    """用于属性值的转义，引号也需要转义。"""
    return html.escape(text, quote=True)


def _extract_code_blocks(lines):
    """把围栏代码块抽出来，位置上留占位符。返回 (新行列表, 代码块列表)。"""
    out = []
    blocks = []
    in_fence = False
    lang = ""
    buf = []

    for line in lines:
        m_open = re.match(r"^\s*```\s*([A-Za-z0-9_+-]*)\s*$", line)
        if not in_fence and m_open:
            in_fence = True
            lang = m_open.group(1)
            buf = []
            continue
        if in_fence and re.match(r"^\s*```\s*$", line):
            in_fence = False
            idx = len(blocks)
            blocks.append((lang, "\n".join(buf)))
            out.append(PLACEHOLDER % idx)
            continue
        if in_fence:
            buf.append(line)
        else:
            out.append(line)

    # 未闭合的代码块：按已收集内容处理，避免内容丢失
    if in_fence and buf:
        idx = len(blocks)
        blocks.append((lang, "\n".join(buf)))
        out.append(PLACEHOLDER % idx)

    return out, blocks


def _split_table_row(line):
    """把 | a | b | 拆成 ['a','b']，正确处理行首行尾的竖线。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_separator(line):
    """判断是否 |---|---| 这样的分隔行。"""
    s = line.strip()
    if not s or "|" not in s:
        return False
    for cell in _split_table_row(s):
        if not re.match(r"^:?-{1,}:?$", cell):
            return False
    return True


def inline(text):
    """行内格式处理。注意：入参【已经过 HTML 转义】。"""
    # 行内代码优先：先保护起来，避免其中的 * 或 _ 被当作强调标记
    code_spans = []

    def stash(m):
        code_spans.append(m.group(1))
        return "\x01CS%d\x01" % (len(code_spans) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)

    # 链接 [文本](url) —— url 也需转义，防 javascript: 之外还需转义引号
    def link(m):
        label, url = m.group(1), m.group(2)
        safe_url = escape_attr(url)
        # 只放行 http/https/mailto/相对路径，挡掉 javascript: 之类
        low = url.strip().lower()
        if low.startswith(("javascript:", "data:", "vbscript:")):
            return label
        return '<a href="%s">%s</a>' % (safe_url, label)

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)

    # 粗体 **x** （在斜体之前，否则 ** 会被单个 * 先匹配）
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # 斜体 *x*
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)

    # 还原行内代码
    def unstash(m):
        return "<code>%s</code>" % code_spans[int(m.group(1))]

    text = re.sub(r"\x01CS(\d+)\x01", unstash, text)
    return text


def md2html(text):
    """把 Markdown 文本转成 HTML 片段。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 第一步：整体转义。必须先转义再生成标签。
    lines = [escape(l) for l in text.split("\n")]

    # 第二步：抽出代码块（此时内容已转义，正好安全）
    lines, code_blocks = _extract_code_blocks(lines)

    out = []
    i = 0
    n = len(lines)

    # 列表状态
    list_type = None   # 'ul' | 'ol'

    def close_list():
        nonlocal list_type
        if list_type:
            out.append("</%s>" % list_type)
            list_type = None

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 代码块占位符
        if re.match(r"^\x00PH\d+\x00$", stripped):
            close_list()
            idx = int(re.search(r"\d+", stripped).group(0))
            lang, code = code_blocks[idx]
            cls = ' class="language-%s"' % escape_attr(lang) if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, code))
            i += 1
            continue

        # 空行
        if not stripped:
            close_list()
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            close_list()
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2)), lvl))
            i += 1
            continue

        # 分隔线
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            close_list()
            out.append("<hr>")
            i += 1
            continue

        # 表格：当前行含 | 且下一行是分隔行
        if "|" in stripped and i + 1 < n and _is_table_separator(lines[i + 1]):
            close_list()
            header = _split_table_row(stripped)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            parts = ["<table>", "<thead><tr>"]
            for h in header:
                parts.append("<th>%s</th>" % inline(h))
            parts.append("</tr></thead>")
            if rows:
                parts.append("<tbody>")
                for r in rows:
                    parts.append("<tr>")
                    for c in r:
                        parts.append("<td>%s</td>" % inline(c))
                    parts.append("</tr>")
                parts.append("</tbody>")
            parts.append("</table>")
            out.append("".join(parts))
            continue

        # 引用
        m = re.match(r"^&gt;\s?(.*)$", stripped)
        if m:
            close_list()
            buf = [m.group(1)]
            i += 1
            while i < n:
                m2 = re.match(r"^&gt;\s?(.*)$", lines[i].strip())
                if not m2:
                    break
                buf.append(m2.group(1))
                i += 1
            out.append("<blockquote>%s</blockquote>" % inline(" ".join(buf)))
            continue

        # 无序列表
        m = re.match(r"^[-*+]\s+(.*)$", stripped)
        if m:
            if list_type != "ul":
                close_list()
                out.append("<ul>")
                list_type = "ul"
            out.append("<li>%s</li>" % inline(m.group(1)))
            i += 1
            continue

        # 有序列表
        m = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if m:
            if list_type != "ol":
                close_list()
                out.append("<ol>")
                list_type = "ol"
            out.append("<li>%s</li>" % inline(m.group(1)))
            i += 1
            continue

        # 普通段落：连续非空行合并
        close_list()
        buf = [stripped]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if (not nxt or re.match(r"^(#{1,6})\s", nxt)
                    or re.match(r"^\x00PH\d+\x00$", nxt)
                    or re.match(r"^(-{3,}|\*{3,}|_{3,})$", nxt)
                    or re.match(r"^[-*+]\s+", nxt)
                    or re.match(r"^\d+[.)]\s+", nxt)
                    or re.match(r"^&gt;\s?", nxt)):
                break
            buf.append(nxt)
            i += 1
        out.append("<p>%s</p>" % inline(" ".join(buf)))

    close_list()
    return "\n".join(out)


def extract_title(text):
    """取第一个一级标题作为页面标题。"""
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            return m.group(1).strip()
    return "(无标题)"


def extract_meta_table(text):
    """
    取正文开头第一个表格，作为"元信息表"（复盘文档用它记录日期/环境/影响等）。
    返回 dict，取不到则返回空 dict。
    """
    lines = text.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if "|" in line and i + 1 < len(lines) and _is_table_separator(lines[i + 1]):
            meta = {}
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                cells = _split_table_row(lines[j])
                if len(cells) >= 2:
                    meta[cells[0]] = cells[1]
                j += 1
            return meta
    return {}


def strip_first_heading(text):
    """去掉第一个一级标题（页面上由模板单独渲染标题，避免重复）。"""
    lines = text.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^#\s+", line.strip()):
            del lines[i]
            break
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    src = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    print(md2html(src))
