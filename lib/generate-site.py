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

# 分支目录 —— 站点是多项目结构，每个项目一个分支
OPS_SUBDIR = "ops"
DM_SUBDIR = "dm"
CC_SUBDIR = "cc"
# 数据挖掘分支的源材料：5 篇实验报告 Markdown + img/ 下的运行截图
DM_DIR = os.path.join(LAB, "my-dm")

# 数据挖掘分支的 5 篇报告（编号, 标题, 时间, 导航 key）
DM_ITEMS = [
    ("1", "实验一 · 特征选择", "2026/4/14", "e1"),
    ("2", "实验二 · 分类", "2026/5/12", "e2"),
    ("3", "实验三 · 回归", "2026/5/14", "e3"),
    ("4", "实验四 · 集成技术", "2026/6/2", "e4"),
    ("5", "实验五 · 聚类分析", "2026/6/11", "e5"),
]

# 云计算分支的源材料：6 篇实验报告 Markdown + img/ 下的运行截图
CC_DIR = os.path.join(LAB, "my-cc")

# 云计算分支的 6 篇报告（编号, 标题, 时间, 导航 key）
CC_ITEMS = [
    ("1", "实验一 · Git安装与使用", "2025.09.09", "e1"),
    ("2", "实验二 · packstack安装OpenStack", "2025.09.29", "e2"),
    ("3", "实验三 · dacker的安装与应用", "2025.10.27", "e3"),
    ("4", "实验四 · Kubernetes集群部署", "2025.11.18", "e4"),
    ("5", "实验五 · 共享式存储的多节点虚拟系统部署", "2025.11.25", "e5"),
    ("6", "实验六 · 基于hyper-v与DoraCloud的云桌面部署", "2025.12.9", "e6"),
]

SITE_TITLE = "项目工作台"
SITE_SUBTITLE = "个人项目与实践记录"

# 备案号（法律要求显示在网站底部）。
# 两个备案彼此独立、都要展示：
#   ICP   —— 工信部，管"网站能不能开"（域名与主体实名）
#   公网安备 —— 公安部门，管"网站归谁负责"（联网单位备案）
ICP_NUMBER = "粤ICP备2026144293号-1"
ICP_URL = "https://beian.miit.gov.cn/"

MPS_NUMBER = "粤公网安备44538102000155号"
# 官方查询平台带 code 参数可直接定位到本条备案
MPS_URL = "https://beian.mps.gov.cn/#/query/webSearch?code=44538102000155"

# ---------------------------------------------------------------------------
# 样式：内联到每个页面。控制在 ~3KB 内，兼顾美观与体积。
# ---------------------------------------------------------------------------
CSS = """
/* ===========================================================================
   共四套主题：首页 anime、运维首页 terminal、分支页 ops-dark、
   其余页面 editorial（编辑杂志风）。下面按主题分组列出 token。

   终端风 token（首页）：
     --term-bg      #0A0E12   近黑
     --term-fg      #4AF626   荧光绿（主体输出）
     --term-cyan    #8BE9FD   青色（路径、链接）
     --term-magenta #FF79C6   品红（错误、关键字）
     --term-amber   #FFB86C   琥珀（数字、警告）
     --term-comment #8697BE   蓝灰（注释，可读下限之上）

   编辑杂志风 token（内页）：
     --paper        #F9F8F6   暖米色
     --ink          #1C1C1C   柔和黑
     --muted        60% 透明度
     --faint        40% 透明度
     --line         10% 透明度
   =========================================================================== */

:root{
  /* 终端风 */
  --term-bg:#0A0E12;
  --term-fg:#4AF626;
  --term-cyan:#8BE9FD;
  --term-magenta:#FF79C6;
  --term-amber:#FFB86C;
  --term-comment:#8697BE;   /* 旧值 #6272A4 在真实底色上最差 3.71:1，
                                低于 WCAG AA 正文 4.5:1；它同时是
                                th/td/.comment/.tmux 的颜色，等于表格正文
                                与注释一起不可读。现最差 5.97:1，
                                仍显著暗于 --term-cyan(13.19)，层级不变。 */
  --term-line:#1F2937;

  /* 编辑杂志风 */
  --paper:#F9F8F6;
  --ink:#1C1C1C;
  --ink-60:rgba(28,28,28,.6);
  --ink-40:rgba(28,28,28,.4);
  --ink-10:rgba(28,28,28,.1);
  --ink-05:rgba(28,28,28,.05);

  /* 语义色（复用终端风的语义映射，保证两套风格同源） */
  --warn:#FFB86C;
  --crit:#FF79C6;
  --ok:#4AF626;
}

*,*::before,*::after{box-sizing:border-box}

/* ── 终端风：等宽字体（规范要求全站 mono，无一例外）── */
.mono,body.terminal,body.terminal *{
  font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
}

/* ── 编辑杂志风：衬线标题 + 无衬线正文 ── */
.serif{
  font-family:"Songti SC","STSong","Noto Serif CJK SC","Source Han Serif SC",Georgia,"Times New Roman",serif;
  font-weight:400;
  letter-spacing:-0.02em;
}
body.editorial{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
}

body{margin:0;line-height:1.75;-webkit-font-smoothing:antialiased}

.wrap{max-width:880px;margin:0 auto;padding:0 24px}
@media(max-width:600px){.wrap{padding:0 18px}}

/* ===========================================================================
   赛璐璐动画风（首页 body.anime）

   StyleKit 规范要点：
     所有元素 3px 粗黑边框 #1a1a2e
     硬偏移阴影，禁止模糊
     平面色块，禁止渐变
     文字加粗
     交互 duration-75（抽帧感）
     active: Squash & Stretch
   =========================================================================== */

:root{
  --cel-ink:#1a1a2e;
  --cel-paper:#fafaf5;
  --cel-red:#e63946;
  --cel-blue:#4ea8de;
  --cel-green:#2ecc71;
  --cel-yellow:#f1c40f;
  --cel-shadow:3px 3px 0 var(--cel-ink);
  --cel-shadow-lg:4px 4px 0 var(--cel-ink);
}

body.anime{
  background:var(--cel-paper);
  color:var(--cel-ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:16px;
  line-height:1.75;
}

body.anime .wrap{max-width:1000px}

/* ── 页头：粗边框盒 ── */
body.anime header.site{
  border-bottom:3px solid var(--cel-ink);
  background:var(--cel-paper);
  padding:18px 0;
}
body.anime header.site .wrap{display:flex;align-items:center;gap:18px;flex-wrap:wrap}
body.anime header.site .brand{
  font-weight:900;font-size:1.1rem;color:var(--cel-ink);
  text-decoration:none;letter-spacing:-0.02em;text-transform:uppercase;
}
body.anime header.site .sub{
  color:var(--cel-ink);opacity:.55;font-size:.78rem;
  letter-spacing:.12em;text-transform:uppercase;font-weight:700;
}
body.anime nav.site{margin-left:auto;display:flex;gap:10px}
body.anime nav.site a{
  color:var(--cel-ink);text-decoration:none;
  font-size:.8rem;font-weight:800;text-transform:uppercase;
  padding:5px 11px;
  border:2px solid var(--cel-ink);
  box-shadow:2px 2px 0 var(--cel-ink);
  background:var(--cel-paper);
  transition:transform .075s linear,box-shadow .075s linear;
}
body.anime nav.site a:hover{
  transform:translate(1px,1px);
  box-shadow:1px 1px 0 var(--cel-ink);
}
body.anime nav.site a:active{
  transform:translate(2px,2px);
  box-shadow:none;
}

body.anime main{padding:0 0 70px}

/* ── Hero ── */
body.anime .hero{
  border-bottom:3px solid var(--cel-ink);
  padding:56px 0 44px;
  background:
    repeating-linear-gradient(45deg,
      rgba(26,26,46,.04) 0 1px, transparent 1px 9px);
}
body.anime .hero-inner{max-width:1000px;margin:0 auto;padding:0 24px}
body.anime .hero-label{
  display:inline-block;
  font-size:.72rem;font-weight:900;letter-spacing:.2em;
  text-transform:uppercase;
  background:var(--cel-yellow);
  border:3px solid var(--cel-ink);
  box-shadow:var(--cel-shadow);
  padding:3px 10px;
  margin-bottom:20px;
}
body.anime .hero-title{
  font-size:clamp(2.1rem,6vw,3.6rem);
  font-weight:900;
  letter-spacing:-0.045em;
  line-height:1.05;
  margin:0 0 14px;
  text-transform:uppercase;
}
body.anime .hero-desc{
  font-size:1rem;font-weight:600;
  margin:0;opacity:.7;
}

/* ── 项目卡片网格 ── */
body.anime .proj-grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(min(330px,100%),1fr));
  /* 330px 是硬性最小列宽：视口 360px 时内容宽 324px，溢出 6px；
     320px 时内容宽 284px，溢出 46px。min(330px,100%) 允许列宽
     小于 330px，窄屏不再撑破视口。 */
  gap:26px;
  padding:44px 24px;
  max-width:1000px;margin:0 auto;
}

body.anime .proj-card{
  border:3px solid var(--cel-ink);
  box-shadow:var(--cel-shadow-lg);
  background:#fff;
  padding:24px 24px 26px;
  display:flex;flex-direction:column;
  transition:transform .075s linear,box-shadow .075s linear;
  position:relative;
}
body.anime .proj-card:hover{
  transform:translate(-2px,-2px);
  box-shadow:6px 6px 0 var(--cel-ink);
}

/* 顶部：标记 + 状态 */
body.anime .proj-top{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:16px;
}
body.anime .proj-mark{
  font-size:1.3rem;color:var(--cel-red);
  font-weight:900;line-height:1;
}
body.anime .proj-state{
  font-size:.68rem;font-weight:900;letter-spacing:.14em;
  text-transform:uppercase;
  border:2px solid var(--cel-ink);
  padding:2px 8px;
}
body.anime .state-live{background:var(--cel-green);color:var(--cel-ink)}
body.anime .state-idle{background:#e5e5df;color:var(--cel-ink);opacity:.65}

/* 名称 */
body.anime .proj-name{
  font-size:1.25rem;font-weight:900;
  letter-spacing:-0.02em;text-transform:uppercase;
  margin:0 0 12px;line-height:1.2;
}

/* 简介 */
body.anime .proj-intro{
  font-size:.9rem;line-height:1.7;
  margin:0 0 20px;color:rgba(26,26,46,.76);
  flex-grow:1;
}

/* 数据 */
body.anime .proj-stats{
  display:flex;gap:0;margin:0 0 20px;
  border:2px solid var(--cel-ink);
}
body.anime .proj-stats > div{
  flex:1;padding:9px 4px;text-align:center;
  border-right:2px solid var(--cel-ink);
}
body.anime .proj-stats > div:last-child{border-right:none}
body.anime .proj-stats dt{
  font-size:.62rem;font-weight:800;letter-spacing:.1em;
  text-transform:uppercase;opacity:.6;margin-bottom:3px;
}
body.anime .proj-stats dd{
  margin:0;font-size:1.3rem;font-weight:900;
  letter-spacing:-0.03em;
}

/* 按钮 */
body.anime .proj-enter{
  display:block;text-align:center;
  background:var(--cel-red);color:#fff;
  border:3px solid var(--cel-ink);
  box-shadow:var(--cel-shadow);
  padding:11px 18px;
  font-weight:900;font-size:.88rem;
  letter-spacing:.06em;text-transform:uppercase;
  text-decoration:none;
  transition:transform .075s linear,box-shadow .075s linear;
  position:relative;overflow:hidden;
}
body.anime .proj-enter:hover{
  transform:translate(1px,1px);
  box-shadow:2px 2px 0 var(--cel-ink);
}
body.anime .proj-enter:active{
  transform:translate(3px,3px);
  box-shadow:none;
}
body.anime .proj-enter .arrow{margin-left:7px}
body.anime .proj-enter-off{
  background:#e5e5df;color:rgba(26,26,46,.5);
  pointer-events:none;
}

/* 预留卡片 */
body.anime .proj-empty{
  background:#f4f4ee;
  border-style:dashed;
  box-shadow:none;
}
body.anime .proj-empty:hover{transform:none;box-shadow:none}

/* ── 关于区块 ── */
body.anime .colophon{
  max-width:1000px;margin:0 auto;
  padding:0 24px 20px;
}
body.anime .colophon h2{
  font-size:1.05rem;font-weight:900;text-transform:uppercase;
  letter-spacing:.04em;
  margin:0 0 14px;
  padding-bottom:9px;
  border-bottom:3px solid var(--cel-ink);
}
body.anime .colophon p{
  font-size:.92rem;line-height:1.85;
  margin:0 0 12px;color:rgba(26,26,46,.78);
  max-width:640px;
}
body.anime .colophon-note{
  font-size:.82rem !important;opacity:.6;
}

/* ── 页脚 ── */
body.anime footer.site{
  border-top:3px solid var(--cel-ink);
  padding:24px 0 36px;
  font-size:.78rem;font-weight:600;
  color:rgba(26,26,46,.62);
  line-height:1.9;
}
body.anime .filing a{color:rgba(26,26,46,.62);border:none;font-weight:700}
body.anime .filing a:hover{color:var(--cel-ink)}

@media(max-width:600px){
  body.anime .hero{padding:38px 0 30px}
  body.anime .proj-grid{padding:30px 18px;gap:20px}
  body.anime .proj-card{padding:20px}
  body.anime header.site .wrap{padding:0 18px}
}

/* ===========================================================================
   暗色阅读版（运维分支的内页 body.ops-dark）

   设计意图：
     运维分支整体是深色系（与终端风统一），但内页是长文（500-800 行），
     纯终端风（荧光绿 + 等宽）阅读负担大。
     所以这里保留终端的深色底与配色，但改用衬线标题 + 宽松行距，
     接近杂志风的阅读体验。分支内部不出现浅色页面，避免黑白突变。
   =========================================================================== */

:root{
  --dark-bg:#171B21;
  --dark-bg-2:#1E242C;
  --dark-fg:#D8DEE9;
  --dark-fg-60:rgba(216,222,233,.78);  /* 第二级文字（.lede 导语、blockquote、
                                .notice p、.crumbs a）。旧 .6 是 5.09:1，
                                合格但零余量；.78 为 7.59:1。 */
  --dark-fg-40:rgba(216,222,233,.58);  /* 第三级文字（页头副标题、卡片元信息、
                                分页说明、页脚备案行）。旧 .4 在代码块底
                                #1E242C 上只有 3.06:1；.58 为 4.85:1，
                                且与二级/正文仍拉开层次。 */
  --dark-line:rgba(216,222,233,.14);
  --dark-cyan:#8BE9FD;
  --dark-amber:#FFB86C;
  --dark-green:#7FD88F;
}

body.ops-dark{
  background:var(--dark-bg);
  color:var(--dark-fg);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:16.5px;
  line-height:1.85;
}

body.ops-dark .wrap{max-width:740px}

/* ── 页头 ── */
body.ops-dark header.site{
  border-bottom:1px solid var(--dark-line);
  padding:20px 0;
}
body.ops-dark header.site .wrap{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 20px}
body.ops-dark header.site .brand{
  font-size:1rem;font-weight:600;color:var(--dark-fg);
  text-decoration:none;letter-spacing:-0.01em;
}
body.ops-dark header.site .sub{
  color:var(--dark-fg-40);font-size:.78rem;letter-spacing:.03em;
}
/* 导航：pill 形式
   为什么改：原来只是裸文字排一行，用 60% 透明灰，
   在深色背景上没有"这是一组按钮"的视觉暗示，看起来和背景融为一体。
   改成 pill（有背景、有圆角）后，导航成为一个明确的区域。

   对比度：文字从 --dark-fg-60（5.57:1）提到 --dark-fg（13.4:1） */
body.ops-dark nav.site{
  margin-left:auto;
  display:flex;
  gap:4px;
  flex-wrap:wrap;
}
/* 导航项：明确的按钮外观
   为什么原来的 3.5% 白底看不见：在 #171B21 这种近黑背景上，
   rgba(255,255,255,.035) 算出来的实际色是 #1D222A —— 和背景几乎无差。
   改成 8% 白底 + 可见边框，才能形成"按钮"的视觉。 */
body.ops-dark nav.site a{
  color:var(--dark-fg);
  opacity:.88;
  text-decoration:none;
  font-size:.85rem;
  padding:5px 12px;
  border-radius:4px;
  border:1px solid rgba(255,255,255,.16);
  background:rgba(255,255,255,.08);
  transition:background .2s ease-out,opacity .2s ease-out,
             color .2s ease-out,border-color .2s ease-out;
}
body.ops-dark nav.site a:hover{
  background:rgba(139,233,253,.16);
  border-color:rgba(139,233,253,.4);
  color:var(--dark-cyan);
  opacity:1;
}
/* 当前项：实底色，明确标出"你在这" */
body.ops-dark nav.site a.on{
  background:rgba(139,233,253,.22);
  border-color:rgba(139,233,253,.55);
  color:var(--dark-cyan);
  opacity:1;
  font-weight:600;
}

/* ── 正文 ── */
body.ops-dark main{padding:56px 0 84px}

body.ops-dark h1{
  font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
  font-weight:400;font-size:1.95rem;line-height:1.35;
  letter-spacing:-0.02em;margin:0 0 16px;
  color:#EDF1F7;
}
body.ops-dark h2{
  font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
  font-weight:400;font-size:1.32rem;line-height:1.45;
  letter-spacing:-0.015em;
  margin:52px 0 16px;padding-bottom:9px;
  border-bottom:1px solid var(--dark-line);
  color:#E4EAF2;
}
body.ops-dark h3{
  font-weight:600;font-size:1rem;margin:32px 0 11px;
  color:#DDE4EC;
}

body.ops-dark p{margin:15px 0;color:rgba(216,222,233,.84)}
body.ops-dark .lede{
  font-size:1.02rem;line-height:1.9;
  color:var(--dark-fg-60);margin-bottom:32px;
}

/* 暗色阅读版的链接：实线下划线 + 悬停变亮 */
body.ops-dark a{
  color:var(--dark-cyan);
  text-decoration:none;
  border-bottom:1px solid rgba(139,233,253,.4);
  transition:border-color .2s ease-out,color .2s ease-out;
}
body.ops-dark a:hover{
  color:#B5F0FF;
  border-bottom-color:#B5F0FF;
}

/* 卡片标题的链接不需要下划线（整卡片可点） */
body.ops-dark .card .title{border-bottom:none}
body.ops-dark .card:hover .title{color:var(--dark-cyan)}

/* 列表页的卡片：右边加箭头，提示可点 */
body.ops-dark .card .title::after{
  content:" →";
  opacity:0;
  transition:opacity .25s ease-out;
}
body.ops-dark .card:hover .title::after{opacity:.7}

body.ops-dark ul,body.ops-dark ol{padding-left:1.3em;margin:15px 0}
body.ops-dark li{margin:7px 0;color:rgba(216,222,233,.84)}

body.ops-dark blockquote{
  margin:22px 0;padding:2px 0 2px 20px;
  border-left:2px solid var(--dark-line);
  color:var(--dark-fg-60);font-style:italic;
}

/* ── 代码 ── */
body.ops-dark code{
  background:rgba(216,222,233,.09);
  color:var(--dark-amber);
  padding:1px 5px;border-radius:2px;
  font-size:.87em;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
body.ops-dark pre{
  background:var(--dark-bg-2);
  border:1px solid var(--dark-line);
  padding:16px 18px;border-radius:3px;
  overflow-x:auto;font-size:.83rem;line-height:1.7;
  margin:20px 0;
}
body.ops-dark pre code{
  background:none;padding:0;color:var(--dark-fg);font-size:inherit;
}

/* ── 命令块 / 输出块：视觉分离 ──
   为什么要分开：手记里大量出现「一条命令 + 一段回显」。
   两者都用同一套 <pre> 样式时，读者分不清哪行是自己敲的、
   哪行是机器回的 —— 等于把排查过程的关键线索抹平了。

   怎么分：md2html 对带语言的围栏（```bash）会同时给 <pre> 和 <code>
   打上 language- 标记；无语言的围栏（就是命令的输出）只有裸 <pre><code>。
     命令块 → 贴页面底色 + 3px 青色左竖条（你敲进去的那一行）
     输出块 → 面板底色，无竖条（机器吐出来的，退后一层）

   左内边距 18px → 16px：左边框从 1px 变 3px，减 2px 让两块的文字左缘对齐，
   否则命令块里的正文会比输出块右移 2px，看起来像没对齐。

   不加「命令 / 输出」文字标签 —— 标签会抢戏。 */
body.ops-dark pre[class*="language-"]{
  background:var(--dark-bg);
  border-left:3px solid var(--dark-cyan);
  padding:14px 18px 14px 16px;
}

/* ── 图片与图注（数据挖掘分支的运行截图）──
   截图是 matplotlib / IDE 的白底图，直接放在深色页面上会"发光"。
   加一层浅色边框 + 圆角收住边界即可，不动图片本身。 */
body.ops-dark figure{
  margin:26px 0;padding:0;
}
body.ops-dark figure img{
  max-width:100%;height:auto;display:block;margin:0 auto;
  background:#fff;border:1px solid rgba(255,255,255,.13);
  border-radius:3px;
}
body.ops-dark figcaption{
  margin-top:8px;font-size:.78rem;letter-spacing:.04em;
  color:var(--dark-fg-40);text-align:center;
}

/* ── 表格 ── */
body.ops-dark table{
  width:100%;border-collapse:collapse;font-size:.87rem;margin:22px 0;
}
body.ops-dark th{
  text-align:left;font-weight:600;color:#E4EAF2;
  border-bottom:1px solid var(--dark-line);
  padding:9px 12px 9px 0;
}
body.ops-dark td{
  padding:9px 12px 9px 0;
  border-bottom:1px solid rgba(216,222,233,.07);
  color:rgba(216,222,233,.78);vertical-align:top;
}

/* ── 卡片 ── */
body.ops-dark .card{
  border:1px solid var(--dark-line);
  border-radius:3px;padding:18px 20px;margin:15px 0;
  background:rgba(255,255,255,.015);
  transition:border-color .3s ease-out;
}
body.ops-dark .card:hover{border-color:rgba(216,222,233,.3)}
body.ops-dark .card .title{
  font-family:"Songti SC","STSong",Georgia,serif;
  font-size:1.05rem;color:#E4EAF2;text-decoration:none;
  border:none;display:block;margin-bottom:7px;
  transition:font-style .3s ease-out;
}
body.ops-dark .card:hover .title{font-style:italic}
body.ops-dark .card .meta{
  color:var(--dark-fg-40);font-size:.76rem;
  letter-spacing:.05em;display:flex;gap:13px;flex-wrap:wrap;
}

/* ── 统计 ── */
body.ops-dark .stats{display:flex;flex-wrap:wrap;gap:13px;margin:32px 0}
body.ops-dark .stat{
  border:1px solid var(--dark-line);
  padding:13px 17px;flex:1;min-width:126px;
}
body.ops-dark .stat b{
  display:block;
  font-family:"Songti SC",Georgia,serif;
  font-size:1.42rem;font-weight:400;
  letter-spacing:-0.02em;margin-bottom:4px;color:#EDF1F7;
}
body.ops-dark .stat span{color:var(--dark-fg-40);font-size:.74rem;letter-spacing:.03em}

/* ── 提示块 ── */
body.ops-dark .notice{
  margin:22px 0;padding:15px 19px;
  border-left:2px solid var(--dark-cyan);
  background:rgba(139,233,253,.045);
}
body.ops-dark .notice strong{display:block;margin-bottom:7px;color:#E4EAF2}
body.ops-dark .notice p{margin:6px 0;font-size:.89rem;color:var(--dark-fg-60)}
body.ops-dark .notice-info{border-left-color:var(--dark-amber);background:rgba(255,184,108,.045)}

/* ── 元信息框 ── */
body.ops-dark .metabox{
  margin:24px 0;padding:15px 19px;
  border:1px solid var(--dark-line);border-radius:3px;
  font-size:.83rem;
  background:rgba(255,255,255,.015);
}
body.ops-dark .metabox dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:6px 17px}
body.ops-dark .metabox dt{color:var(--dark-fg-40);font-size:.77rem;letter-spacing:.04em}
body.ops-dark .metabox dd{margin:0;color:rgba(216,222,233,.8)}

/* ── 翻页 ── */
body.ops-dark .pager{
  display:flex;gap:13px;margin-top:56px;
  padding-top:24px;border-top:1px solid var(--dark-line);
}
body.ops-dark .pager-side{flex:1 1 40%}
body.ops-dark .pager-mid{
  order:-1;flex:1 1 100%;text-align:center;
  color:var(--dark-fg-40);font-size:.76rem;letter-spacing:.06em;
  margin-bottom:13px;
}
body.ops-dark .pager-link{display:block;text-decoration:none;border:none;color:var(--dark-fg)}
body.ops-dark .pager-left{text-align:left}
body.ops-dark .pager-right{text-align:right}
body.ops-dark .pager-link .dir{
  color:var(--dark-fg-40);font-size:.7rem;letter-spacing:.1em;
  display:block;margin-bottom:3px;
}
body.ops-dark .pager-link .ttl{font-size:.9rem;line-height:1.5;display:block}
body.ops-dark .pager-link:hover .ttl{color:var(--dark-cyan)}
body.ops-dark .pager-disabled{color:var(--dark-fg-40);font-size:.85rem;display:block;opacity:.5}

/* ── 返回链接 ── */
body.ops-dark .nav-back{
  display:inline-block;margin-bottom:20px;
  color:var(--dark-fg-40);font-size:.79rem;
  text-decoration:none;border-bottom:none;
}
body.ops-dark .nav-back::before{content:"← "}
body.ops-dark .nav-back:hover{color:var(--dark-cyan)}

/* ── 键盘提示 ── */
body.ops-dark .kbd-hint{
  display:inline-block;margin-top:16px;
  color:var(--dark-fg-40);font-size:.75rem;letter-spacing:.04em;
}
body.ops-dark .kbd-hint kbd{
  display:inline-block;padding:1px 5px;margin:0 2px;
  border:1px solid var(--dark-line);border-radius:2px;
  font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:.71rem;color:var(--dark-fg-60);
}

/* ── 标签 ── */
body.ops-dark .tag{
  display:inline-block;font-size:.69rem;letter-spacing:.07em;
  padding:1px 7px;border:1px solid currentColor;border-radius:2px;
}
body.ops-dark .tag.warn{color:var(--dark-amber)}
body.ops-dark .tag.crit{color:#FF79C6}
body.ops-dark .tag.info{color:var(--dark-fg-60)}

/* ── 页脚 ── */
body.ops-dark footer.site{
  border-top:1px solid var(--dark-line);
  padding:26px 0 38px;
  color:var(--dark-fg-40);font-size:.78rem;line-height:1.9;
}
body.ops-dark .filing{margin-top:8px;font-size:.76rem}
body.ops-dark .filing a{color:var(--dark-fg-40);border-bottom:none}
body.ops-dark .filing a:hover{color:var(--dark-cyan)}

@media(max-width:600px){
  body.ops-dark main{padding:38px 0 58px}
  body.ops-dark h1{font-size:1.55rem}
  body.ops-dark h2{font-size:1.14rem}
  body.ops-dark header.site .wrap{gap:4px 14px;padding:0 18px}
  body.ops-dark nav.site{margin-left:0;width:100%;gap:14px}
  body.ops-dark .pager{flex-wrap:wrap;gap:10px}
}

/* ===========================================================================
   终端风布局（首页）
   =========================================================================== */
body.terminal{
  background:var(--term-bg);
  color:var(--term-fg);
  font-size:14px;
  line-height:1.7;
  min-height:100vh;
}

/* scanline 纹理（规范允许，透明度不超过 0.03） */
body.terminal::before{
  content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background:repeating-linear-gradient(
    0deg,rgba(74,246,38,.03) 0 1px,transparent 1px 3px);
}

body.terminal .wrap{max-width:860px;position:relative;z-index:1}

/* ── 终端窗口容器 ── */
.term-window{
  border:1px solid var(--term-line);
  border-radius:2px;
  margin:32px auto;
  background:rgba(255,255,255,.012);
  overflow:hidden;
}
.term-bar{
  display:flex;align-items:center;gap:8px;
  padding:9px 14px;
  border-bottom:1px solid var(--term-line);
  color:var(--term-comment);
  font-size:12px;
}
.term-bar .dot{width:8px;height:8px;border-radius:50%;background:var(--term-line)}

/* ── tmux 式状态栏 ── */
.tmux{
  margin-top:32px;padding:8px 14px;
  border-top:1px solid var(--term-line);
  background:rgba(74,246,38,.04);
  color:var(--term-comment);
  font-size:12px;
  display:flex;flex-wrap:wrap;gap:14px;
}
.tmux b{color:var(--term-fg);font-weight:400}

/* ── prompt 前缀 ── */
.prompt{color:var(--term-cyan)}
.prompt .cmd{color:var(--term-fg)}
.prompt .arg{color:var(--term-amber)}

/* ── 光标块 ── */
.cursor{
  display:inline-block;width:8px;height:15px;
  background:var(--term-fg);vertical-align:-2px;margin-left:3px;
  animation:blink 1.1s steps(1) infinite;
}
@keyframes blink{0%,50%{opacity:1}50.01%,100%{opacity:0}}

/* ── 终端里的链接 ──
   问题：原来链接和"路径文字"都用 --term-cyan，用户分不清哪个能点。
   修法：给链接加下划线（虚线，符合终端风格）+ hover 时反色。

   为什么用虚线而不是实线：
     终端风里实线是"分隔符"的语义（ASCII 线）。
     用虚线做"可点击"的标记，两者不冲突。 */
body.terminal a{
  color:var(--term-cyan);
  text-decoration:none;
  border-bottom:1px dashed rgba(139,233,253,.5);
  transition:background .12s linear,color .12s linear;
}
body.terminal a:hover{
  background:var(--term-cyan);
  color:var(--term-bg);
  border-bottom-color:var(--term-cyan);
}

/* 表格里的链接要更明显（这里是主要的导航入口） */
body.terminal td a{
  display:inline-block;
  padding:1px 0;
  font-weight:inherit;
}
body.terminal td a::after{
  content:" →";
  opacity:.6;
  font-size:.9em;
}
body.terminal td a:hover::after{opacity:1}

/* ── 终端里的输出块 ── */
body.terminal h1,body.terminal h2,body.terminal h3{
  font-weight:400;font-size:inherit;margin:0;
}
body.terminal .out{margin:10px 0 26px 0;color:var(--term-fg)}
body.terminal .out .path{color:var(--term-cyan)}
body.terminal .out .num{color:var(--term-amber)}
body.terminal .out .warn{color:var(--term-amber)}
body.terminal .out .err{color:var(--term-magenta)}
body.terminal .comment{color:var(--term-comment)}

/* ── 终端里的表格（ps aux 式）── */
body.terminal table{border-collapse:collapse;width:100%;font-size:13px}
body.terminal th{
  text-align:left;color:var(--term-comment);font-weight:400;
  border-bottom:1px solid var(--term-line);padding:6px 10px 6px 0;
}
body.terminal td{padding:5px 10px 5px 0;vertical-align:top}

/* ── exit code 徽标 ── */
.badge{display:inline-block;padding:1px 7px;border-radius:2px;font-size:12px}
.badge.ok{color:var(--term-bg);background:var(--term-fg)}
.badge.err{color:var(--term-bg);background:var(--term-magenta)}

/* ── ASCII 分隔线 ── */
.ascii-line{color:var(--term-line);letter-spacing:-1px;overflow:hidden;white-space:nowrap;display:block}

/* ===========================================================================
   编辑杂志风布局（内页）
   =========================================================================== */
body.editorial{
  background:var(--paper);
  color:var(--ink);
  font-size:16px;
}

body.editorial .wrap{max-width:760px}

/* ── 页头 ── */
header.site{
  border-bottom:1px solid var(--ink-10);
  padding:22px 0;
}
header.site .wrap{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 22px}
header.site .brand{
  font-size:1rem;font-weight:400;color:var(--ink);text-decoration:none;
  letter-spacing:-0.01em;
}
header.site .sub{color:var(--ink-40);font-size:.8rem;letter-spacing:.02em}
nav.site{margin-left:auto;display:flex;gap:20px;flex-wrap:wrap}
nav.site a{
  color:var(--ink-60);text-decoration:none;font-size:.86rem;
  position:relative;padding-bottom:2px;
}
/* hover 下划线动画（规范要求 scaleX from right-to-left） */
nav.site a::after{
  content:"";position:absolute;left:0;right:0;bottom:0;height:1px;
  background:var(--ink);transform:scaleX(0);transform-origin:right;
  transition:transform .3s ease-out;
}
nav.site a:hover::after{transform:scaleX(1);transform-origin:left}
nav.site a.on{color:var(--ink)}
nav.site a.on::after{transform:scaleX(1)}

/* ── 正文排版 ── */
main{padding:64px 0 90px}

body.editorial h1{
  font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
  font-weight:400;font-size:2.1rem;line-height:1.3;
  letter-spacing:-0.03em;margin:0 0 18px;
}
body.editorial h2{
  font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
  font-weight:400;font-size:1.4rem;line-height:1.4;
  letter-spacing:-0.02em;
  margin:56px 0 18px;padding-bottom:10px;
  border-bottom:1px solid var(--ink-10);
}
body.editorial h3{
  font-weight:600;font-size:1rem;margin:34px 0 12px;
  letter-spacing:-0.005em;
}

body.editorial p{margin:14px 0;color:rgba(28,28,28,.82)}
body.editorial .lede{
  font-size:1.05rem;line-height:1.9;color:var(--ink-60);
  margin-bottom:34px;
}

body.editorial a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--ink-40)}
body.editorial a:hover{border-bottom-color:var(--ink)}

/* ── 列表 ── */
body.editorial ul,body.editorial ol{padding-left:1.3em;margin:14px 0}
body.editorial li{margin:7px 0;color:rgba(28,28,28,.82)}

/* ── 引用 ── */
body.editorial blockquote{
  margin:22px 0;padding:2px 0 2px 20px;
  border-left:1px solid var(--ink-40);
  color:var(--ink-60);font-style:italic;
}

/* ── 代码 ── */
body.editorial code{
  background:var(--ink-05);
  padding:1px 5px;border-radius:2px;
  font-size:.88em;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
body.editorial pre{
  background:#1C1C1C;color:#EDEDED;
  padding:18px 20px;border-radius:3px;
  overflow-x:auto;font-size:.84rem;line-height:1.65;
  margin:20px 0;
}
body.editorial pre code{background:none;padding:0;color:inherit;font-size:inherit}

/* ── 表格 ── */
body.editorial table{
  width:100%;border-collapse:collapse;font-size:.88rem;margin:22px 0;
}
body.editorial th{
  text-align:left;font-weight:600;color:var(--ink);
  border-bottom:1px solid var(--ink-40);
  padding:9px 12px 9px 0;
}
body.editorial td{
  padding:9px 12px 9px 0;
  border-bottom:1px solid var(--ink-10);
  color:rgba(28,28,28,.78);vertical-align:top;
}

/* ── 卡片 ── */
body.editorial .card{
  border:1px solid var(--ink-10);
  border-radius:3px;
  padding:20px 22px;
  margin:16px 0;
  transition:border-color .3s ease-out;
}
body.editorial .card:hover{border-color:var(--ink-40)}
body.editorial .card .title{
  font-family:"Songti SC","STSong","Noto Serif CJK SC",Georgia,serif;
  font-size:1.08rem;color:var(--ink);text-decoration:none;
  border:none;display:block;margin-bottom:7px;
  transition:font-style .3s ease-out;
}
body.editorial .card:hover .title{font-style:italic}
body.editorial .card .meta{
  color:var(--ink-40);font-size:.78rem;
  letter-spacing:.06em;display:flex;gap:14px;flex-wrap:wrap;
}

/* ── 统计块 ── */
body.editorial .stats{display:flex;flex-wrap:wrap;gap:14px;margin:34px 0}
body.editorial .stat{
  border:1px solid var(--ink-10);
  padding:14px 18px;flex:1;min-width:130px;
}
body.editorial .stat b{
  display:block;font-family:"Songti SC",Georgia,serif;
  font-size:1.5rem;font-weight:400;
  letter-spacing:-0.02em;margin-bottom:4px;
}
body.editorial .stat span{color:var(--ink-40);font-size:.76rem;letter-spacing:.03em}

/* ── 提示块 ── */
body.editorial .notice{
  margin:24px 0;padding:16px 20px;
  border-left:2px solid var(--ink-40);
  background:var(--ink-05);
}
body.editorial .notice strong{display:block;margin-bottom:8px}
body.editorial .notice p{margin:6px 0;font-size:.9rem;color:var(--ink-60)}
body.editorial .notice a{border-bottom:1px solid var(--ink-40)}

/* ── 翻页 ── */
body.editorial .pager{
  display:flex;gap:14px;margin-top:60px;
  padding-top:26px;border-top:1px solid var(--ink-10);
}
body.editorial .pager-side{flex:1 1 40%}
body.editorial .pager-mid{
  order:-1;flex:1 1 100%;text-align:center;
  color:var(--ink-40);font-size:.78rem;letter-spacing:.06em;
  margin-bottom:14px;
}
body.editorial .pager a{display:block;text-decoration:none;border:none}
body.editorial .pager .dir{color:var(--ink-40);font-size:.72rem;letter-spacing:.1em;display:block;margin-bottom:3px}
body.editorial .pager .ttl{color:var(--ink);font-size:.92rem}
body.editorial .pager-prev{text-align:left}
body.editorial .pager-next{text-align:right}

/* ── 页脚 ── */
footer.site{
  border-top:1px solid var(--ink-10);
  padding:28px 0 40px;
  color:var(--ink-40);font-size:.8rem;line-height:1.9;
}
.filing{margin-top:8px;font-size:.78rem}
/* 两个备案号之间的分隔符：只用 opacity 淡出，不设 color ——
   这样四套主题共用一条规则，且不落入颜色契约检查的扫描面。 */
.filing-sep{opacity:.75;margin:0 6px}
.filing a{color:var(--ink-40);border-bottom:none}
.filing a:hover{color:var(--ink)}

/* ── 严重级别标签 ── */
.tag{
  display:inline-block;font-size:.7rem;letter-spacing:.08em;
  padding:1px 7px;border:1px solid currentColor;border-radius:2px;
}
.tag.warn{color:#9A6A1E}
.tag.crit{color:#9B2C2C}
.tag.info{color:var(--ink-60)}

/* ===========================================================================
   补充：代码块语言标记、翻页组件、元信息框
   =========================================================================== */

/* ── 代码块：md2html 生成带 language- 标记的 pre/code 标签 ── */
body.editorial pre code[class*="lang"]{
  display:block;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
/* 语言标记不额外染色 —— 杂志风禁止装饰性强调色 */

/* ── 键盘提示（kbd-hint）── */
.kbd-hint{
  display:inline-block;
  margin-top:18px;
  color:var(--ink-40);
  font-size:.76rem;
  letter-spacing:.04em;
}
.kbd-hint kbd{
  display:inline-block;
  padding:1px 5px;margin:0 2px;
  border:1px solid var(--ink-10);
  border-radius:2px;
  font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:.72rem;
  color:var(--ink-60);
}

/* ── 元信息框（metabox）── */
.metabox{
  margin:26px 0;
  padding:16px 20px;
  border:1px solid var(--ink-10);
  border-radius:3px;
  font-size:.84rem;
}
.metabox dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:6px 18px}
.metabox dt{
  color:var(--ink-40);
  letter-spacing:.05em;
  font-size:.78rem;
}
.metabox dd{margin:0;color:rgba(28,28,28,.8)}
@media(max-width:600px){
  .metabox dl{grid-template-columns:1fr;gap:2px 0}
  .metabox dt{margin-top:8px}
}

/* ── 返回链接（nav-back）── */
.nav-back{
  display:inline-block;
  margin-bottom:22px;
  color:var(--ink-40);
  font-size:.8rem;
  text-decoration:none;
  border-bottom:none;
}
.nav-back::before{content:"← ";letter-spacing:0}
.nav-back:hover{color:var(--ink)}

/* ── 翻页组件（pager-link / pager-count / pager-disabled / pager-left / pager-right）── */
.pager-link{
  display:block;
  color:var(--ink);
  text-decoration:none;
  border:none;
  transition:color .3s ease-out;
}
.pager-link:hover{color:var(--ink-60)}
.pager-left{text-align:left}
.pager-right{text-align:right}
.pager-count{
  display:block;
  color:var(--ink-40);
  font-size:.76rem;
  letter-spacing:.08em;
  margin-bottom:16px;
}
.pager-disabled{
  color:var(--ink-40);
  font-size:.86rem;
  display:block;
  opacity:.5;
}
.pager-link .dir{
  color:var(--ink-40);
  font-size:.72rem;
  letter-spacing:.1em;
  display:block;
  margin-bottom:3px;
}
.pager-link .ttl{
  font-size:.92rem;
  line-height:1.5;
  display:block;
}

/* ── 信息提示块变体（notice-info）── */
body.editorial .notice-info{
  border-left-color:var(--ink);
  background:var(--ink-05);
}


/* ── 代码块语言标记 ──
   杂志风规范要求纯单色体系，不做语法高亮配色。
   这里只保证等宽字体与缩进，语言标记不额外染色。 */
body.editorial pre code[class*="lang"],
body.editorial code[class*="lang"]{
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  color:#EDEDED;
  background:none;
}

@media(max-width:600px){
  .pager{flex-wrap:wrap;gap:10px}
  .pager-side{flex:1 1 40%}
  .pager-mid{order:-1;flex:1 1 100%}
  body.editorial h1{font-size:1.65rem}
  body.editorial h2{font-size:1.2rem}
  main{padding:40px 0 60px}
  header.site .wrap{gap:4px 14px}
  nav.site{margin-left:0;width:100%;gap:14px}
}
.visit-widget{position:fixed;top:16px;right:16px;z-index:50;display:flex;flex-direction:column;align-items:center;padding:7px 12px;border-radius:12px;background:rgba(255,255,255,.16);backdrop-filter:blur(12px) saturate(160%);-webkit-backdrop-filter:blur(12px) saturate(160%);border:1px solid rgba(255,255,255,.24);box-shadow:0 4px 20px rgba(0,0,0,.12);font-size:.72rem;line-height:1.3;color:rgba(255,255,255,.94);font-variant-numeric:tabular-nums;opacity:0;animation:vw-in .45s ease-out .2s forwards;pointer-events:none}
@keyframes vw-in{to{opacity:1}}
body.anime .visit-widget{background:rgba(26,26,46,.08);border-color:rgba(26,26,46,.14);color:var(--cel-ink);box-shadow:0 4px 18px rgba(26,26,46,.1)}
body.ops-dark .visit-widget,body.terminal .visit-widget{background:rgba(255,255,255,.07);border-color:rgba(255,255,255,.13);color:rgba(255,255,255,.9);box-shadow:0 4px 20px rgba(0,0,0,.3)}
.visit-widget .vw-item{display:flex;flex-direction:column;align-items:center;padding:0 8px;position:relative}
.visit-widget .vw-item+.vw-item::before{content:"";position:absolute;left:0;top:14%;height:72%;width:1px;background:currentColor;opacity:.16}
.visit-widget .vw-num{font-size:.92rem;font-weight:700;letter-spacing:-0.02em;line-height:1.15}
.visit-widget .vw-label{font-size:.56rem;letter-spacing:.1em;text-transform:uppercase;opacity:.62;margin-top:1px}
/* 挂件分两行：上行是四个数字，下行写口径与更新时间。
   时间戳来自 stats/visits.json 的 updated 字段（cron 每 10 分钟刷新），
   同时说明这几个数字数的是独立访客，不是页面请求数。 */
.visit-widget .vw-stats{display:flex;align-items:center}
.visit-widget .vw-time{font-size:.54rem;letter-spacing:.05em;opacity:.62;margin-top:2px;white-space:nowrap}
.visit-widget.vw-loading .vw-num{opacity:.35}
.visit-widget.vw-hidden{display:none}
@media(max-width:620px){.visit-widget{top:8px;right:8px;padding:5px 8px;border-radius:10px}.visit-widget .vw-item{padding:0 5px}.visit-widget .vw-num{font-size:.78rem}.visit-widget .vw-label{font-size:.52rem;letter-spacing:.05em}.visit-widget .vw-time{font-size:.5rem}}

/* ── 面包屑导航 ──
   重做要点：
     · 字号从 .78rem 提到 .84rem（原来太小）
     · 用 "›" 而不是 "/" 做分隔（视觉上有方向感）
     · 当前位置加粗 + 高亮色，和可点链接明显区分
     · 加一条细分隔线把面包屑区和下面内容分开
     · 链接 hover 有下划线提示可点 */
.crumbs{
  display:flex;align-items:center;flex-wrap:wrap;
  gap:0 2px;
  font-size:.84rem;
  padding:12px 0 12px;
  letter-spacing:.01em;
  border-bottom:1px solid;
}
.crumbs a{
  text-decoration:none;
  border-bottom:none;
  padding:1px 4px;
  border-radius:2px;
  transition:background .15s ease-out,color .15s ease-out;
}
.crumbs .sep{
  opacity:.4;
  padding:0 1px;
  font-size:.9em;
}
.crumbs .cur{
  font-weight:600;
  opacity:1;
  padding:1px 4px;
}

/* 终端风 */
body.terminal .crumbs{
  border-bottom-color:var(--term-line);
}
body.terminal .crumbs a{
  color:var(--term-cyan);
}
body.terminal .crumbs a:hover{
  background:rgba(139,233,253,.14);
}
body.terminal .crumbs .cur{
  color:var(--term-fg);
}
body.terminal .crumbs .sep{
  color:var(--term-comment);
}

/* 暗色阅读版 */
body.ops-dark .crumbs{
  border-bottom-color:var(--dark-line);
}
body.ops-dark .crumbs a{
  color:var(--dark-fg);
  opacity:.72;
}
body.ops-dark .crumbs a:hover{
  background:rgba(139,233,253,.1);
  color:var(--dark-cyan);
  opacity:1;
}
body.ops-dark .crumbs .cur{
  color:var(--dark-fg);
}
body.ops-dark .crumbs .sep{
  color:var(--dark-fg-40);
}

/* 动漫风（保险） */
body.anime .crumbs{
  border-bottom-color:var(--cel-ink);
  border-bottom-width:3px;
}
body.anime .crumbs a{
  color:var(--cel-ink);
  opacity:.7;
}
body.anime .crumbs a:hover{
  background:rgba(26,26,46,.08);
  opacity:1;
}
body.anime .crumbs .cur{
  color:var(--cel-ink);
}
body.anime .crumbs .sep{
  color:var(--cel-ink);
  opacity:.5;
}

@media(max-width:620px){
  .crumbs{
    font-size:.78rem;
    padding:9px 0;
    gap:0 1px;
  }
  .crumbs a,.crumbs .cur{padding:1px 3px}
}



/* 动漫风（首页）—— 实际不会出现，保险起见 */
body.anime .crumbs a{color:var(--cel-ink);opacity:.65}
body.anime .crumbs a:hover{opacity:1}
body.anime .crumbs .cur{color:var(--cel-ink);opacity:.85}

/* 终端风 */
body.terminal .crumbs a{color:var(--term-cyan)}
body.terminal .crumbs a:hover{color:var(--term-fg)}

/* 暗色阅读版（运维分支主要页面） */
body.ops-dark .crumbs a{color:var(--dark-fg-60)}
body.ops-dark .crumbs a:hover{color:var(--dark-cyan)}
body.ops-dark .crumbs .cur{color:var(--dark-fg);opacity:.85}


/* ── 品牌名（回首页的入口）──
   原来的 .brand 看不出是链接，加上明确的可点击提示 */
header.site .brand{
  position:relative;
  transition:color .2s ease-out;
}
header.site .brand::after{
  content:"";
  position:absolute;left:0;right:0;bottom:-2px;height:1px;
  background:currentColor;opacity:.35;
  transition:opacity .25s ease-out;
}
header.site .brand:hover::after{opacity:1}


/* ── 品牌名：路径形式 ──
   首页：      项目工作台
   分支内：    项目工作台 ‹ 运维
               └────┬────┘  └─┬┘
               可点回首页   当前分支（不可点）
   */
header.site .brand-parent{
  position:relative;
  transition:color .2s ease-out;
}
header.site .brand-parent::after{
  content:"";
  position:absolute;left:0;right:0;bottom:-2px;height:1px;
  background:currentColor;opacity:.3;
  transition:opacity .25s ease-out;
}
header.site .brand-parent:hover::after{opacity:1}

header.site .brand-sep{
  opacity:.4;
  padding:0 5px;
  font-size:.9em;
}

header.site .brand-branch{
  font-weight:600;
  letter-spacing:.01em;
}

/* 各主题下的分支名颜色 */
body.ops-dark header.site .brand-branch{color:var(--dark-cyan)}
body.terminal header.site .brand-branch{color:var(--term-fg)}
body.anime header.site .brand-branch{color:var(--cel-ink)}

/* ── 终端风：页头与导航 ────────────────────────────────────────────
   背景 #0A0E12 是深色，而通用规则里的 --ink / --ink-40 / --ink-60
   是浅色主题的深灰（#1C1C1C 系）。深灰落在近黑上对比度不足 1.1:1，
   文字等于看不见 —— 终端主题必须自己给出颜色，不能沿用通用规则。
   对比度实测（相对亮度按 WCAG 公式计）：
     品牌名  --term-cyan  #8BE9FD on #0A0E12          13.99:1
     副标题  rgba(139,233,253,.55) on #0A0E12          4.78:1
     导航项  rgba(139,233,253,.75) on 药丸底(#121B20)  7.61:1
     当前页  #0A0E12 on #8BE9FD（实底反白）           13.99:1
   本块放在 CSS 末尾：既靠特异性，也靠源顺序，
   避免再被通用规则用"同分看顺序"的规则压掉。 */
body.terminal header.site .brand{color:var(--term-cyan)}
body.terminal header.site .brand-parent{color:var(--term-cyan)}
body.terminal header.site .sub{color:rgba(139,233,253,.55)}
body.terminal nav.site a{
  color:rgba(139,233,253,.75);
  font-size:.85rem;
  padding:5px 12px;
  border:1px solid rgba(139,233,253,.22);
  border-radius:2px;
  background:rgba(139,233,253,.06);
}
body.terminal nav.site a:hover{
  color:var(--term-cyan);
  border-color:rgba(139,233,253,.45);
  background:rgba(139,233,253,.12);
}
body.terminal nav.site a.on{
  color:var(--term-bg);
  background:var(--term-cyan);
  border-color:var(--term-cyan);
  font-weight:600;
}
/* 通用规则的下划线动画底色是 --ink（深灰），终端主题下同样不可见；
   当前页已改用实底反白的药丸，这里直接关掉。 */
body.terminal nav.site a::after{display:none}

/* ===========================================================================
   学术科普风（数据挖掘分支 body.distill）

   参照 StyleKit「学术科普风 / Distill Style」硬性提示词：
     正文衬线窄列（42rem 正文度量）、行高 1.75
     背景纯白，次级面只有 #F3F4F6
     学术蓝只用于链接与关键强调
     分割一律 1px hairline；圆角最多 2px；不用阴影
     表格走 booktabs：只有横线，无竖线无斑马纹
     figure 在图注上方加一条 hairline，并允许突破版心

   为什么另起一套而不改 ops-dark：
     运维分支是深色长文（终端底子），数据挖掘分支是实验报告，
     白底衬线更贴论文体。两套主题互不影响，
     回滚只需把两个 style="distill" 参数去掉。

   对比度（WCAG，按真实底色复算，全部 >= 4.5:1）：
     --di-fg      #1F2933 on #FFFFFF 14.76:1 / on #F3F4F6 13.41:1
     --di-muted   #5F6874 on #FFFFFF  5.65:1 / on #F3F4F6  5.13:1
     --di-accent  #2268CC on #FFFFFF  5.36:1 / on #F3F4F6  4.87:1
   注意：StyleKit 原色的 #6B7280（4.39:1）与 #2A7AE2（4.22:1）
   落在 #F3F4F6 面上不达 AA，这里按同色相压深一档取值。
   =========================================================================== */

:root{
  --di-bg:#FFFFFF;
  --di-panel:#F3F4F6;
  --di-fg:#1F2933;
  --di-muted:#5F6874;
  --di-line:#DDE1E6;
  --di-accent:#2268CC;
  --di-warn:#8A5A12;
  --di-crit:#9B2C2C;
  --di-serif:"Songti SC","STSong","Noto Serif CJK SC","Source Han Serif SC",Georgia,"Times New Roman",serif;
  --di-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  --di-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}

body.distill{
  background:var(--di-bg);
  color:var(--di-fg);
  font-family:var(--di-serif);
  font-size:17px;
  line-height:1.75;
}

/* 窄列即尊重：42rem 是正文度量，加回 .wrap 的左右各 24px 内边距 */
body.distill .wrap{max-width:calc(42rem + 48px)}

/* ── 页头 ── */
body.distill header.site{
  border-bottom:1px solid var(--di-line);
  padding:22px 0;
}
body.distill header.site .wrap{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 18px}
body.distill header.site .brand{
  font-size:1rem;font-weight:600;color:var(--di-fg);
  font-family:var(--di-serif);letter-spacing:0;
}
body.distill header.site .brand-parent{color:var(--di-fg)}
body.distill header.site .sub{
  color:var(--di-muted);font-size:.78rem;letter-spacing:.04em;
  font-family:var(--di-sans);
}
body.distill header.site .brand-branch{color:var(--di-fg)}

body.distill nav.site{margin-left:auto;display:flex;gap:16px;flex-wrap:wrap}
body.distill nav.site a{
  color:var(--di-muted);text-decoration:none;font-size:.84rem;
  font-family:var(--di-sans);position:relative;padding-bottom:2px;
  transition:color .15s ease-out;
}
body.distill nav.site a:hover{color:var(--di-accent)}
body.distill nav.site a.on{color:var(--di-fg);font-weight:600}
body.distill nav.site a::after{background:var(--di-accent)}

/* ── 面包屑 ── */
body.distill .crumbs{border-bottom-color:var(--di-line);font-family:var(--di-sans)}
body.distill .crumbs a{color:var(--di-accent);opacity:1}
body.distill .crumbs a:hover{background:var(--di-panel);color:var(--di-accent);opacity:1}
body.distill .crumbs .cur{color:var(--di-fg)}
body.distill .crumbs .sep{color:var(--di-muted);opacity:1}

/* ── 正文 ── */
body.distill main{padding:56px 0 84px}

body.distill h1{
  font-family:var(--di-serif);font-weight:600;
  font-size:1.9rem;line-height:1.3;letter-spacing:-.01em;
  margin:0 0 16px;padding:0;color:var(--di-fg);
}
body.distill h2{
  font-family:var(--di-serif);font-weight:600;
  font-size:1.32rem;line-height:1.4;
  margin:40px 0 14px;padding:0 0 7px;
  border-bottom:1px solid var(--di-line);color:var(--di-fg);
}
body.distill h3{
  font-family:var(--di-serif);font-weight:600;
  font-size:1.1rem;line-height:1.45;
  margin:28px 0 10px;color:var(--di-fg);
}
body.distill p{margin:15px 0;color:var(--di-fg)}
body.distill .lede{color:var(--di-muted);font-size:1rem;line-height:1.75}
body.distill a{
  color:var(--di-accent);text-decoration:none;
  border-bottom:1px solid rgba(34,104,204,.35);
}
body.distill a:hover{border-bottom-color:var(--di-accent)}
body.distill ul,body.distill ol{padding-left:1.4em;margin:15px 0}
body.distill li{margin:6px 0;color:var(--di-fg)}
body.distill blockquote{
  margin:20px 0;padding:2px 0 2px 16px;
  border-left:2px solid var(--di-line);
  color:var(--di-muted);
}
body.distill hr{border:none;border-top:1px solid var(--di-line);margin:34px 0}

/* ── 代码 ── */
body.distill code{
  background:var(--di-panel);color:var(--di-fg);
  padding:1px 5px;border-radius:2px;font-size:.87em;
  font-family:var(--di-mono);
}
body.distill pre{
  background:var(--di-panel);
  border:1px solid var(--di-line);
  padding:15px 17px;border-radius:2px;
  overflow-x:auto;font-size:.82rem;line-height:1.7;
  margin:20px 0;font-family:var(--di-mono);
}
body.distill pre code{background:none;padding:0;color:var(--di-fg);font-size:inherit}
/* 命令块 / 输出块分离：带语言的围栏贴页面底色 + 2px 蓝左条，
   与 A1 在运维分支的做法同源，只是换成浅色主题的取值。 */
body.distill pre[class*="language-"]{
  background:var(--di-bg);
  border-left:2px solid var(--di-accent);
  padding:13px 17px 13px 15px;
}

/* ── 图片与图注（实验运行截图）──
   截图本身是白底，用 hairline 收边即可；
   图注上方一条 hairline，图注以「图 N」开头（md2html 生成）。 */
body.distill figure{margin:26px 0;padding:0}
body.distill figure img{
  max-width:100%;height:auto;display:block;margin:0 auto;
  background:#fff;border:1px solid var(--di-line);border-radius:2px;
}
body.distill figcaption{
  margin-top:8px;padding-top:6px;
  border-top:1px solid var(--di-line);
  font-size:.78rem;line-height:1.6;letter-spacing:.02em;
  color:var(--di-muted);text-align:left;
  font-family:var(--di-sans);
}
/* 图表突破版心：只在足够宽的视口上向两侧各借 104px，
   42rem(672px) 正文 → 880px 图面，两侧仍留有余量不会横向溢出。 */
@media(min-width:1080px){
  body.distill figure{margin-left:-104px;margin-right:-104px}
}

/* ── 表格（booktabs：只有横线）── */
body.distill table{
  width:100%;border-collapse:collapse;font-size:.86rem;margin:22px 0;
  border-top:2px solid var(--di-fg);border-bottom:2px solid var(--di-fg);
  font-family:var(--di-sans);
}
body.distill th{
  text-align:left;font-weight:600;color:var(--di-fg);
  border-bottom:1px solid var(--di-fg);
  padding:8px 12px 8px 0;
}
body.distill td{
  padding:8px 12px 8px 0;color:var(--di-fg);
  vertical-align:top;
}

/* ── 卡片（概览页的实验列表）── */
body.distill .card{
  border:1px solid var(--di-line);
  border-radius:2px;padding:16px 18px;margin:14px 0;
  background:var(--di-bg);
  transition:border-color .15s ease-out;
}
body.distill .card:hover{border-color:var(--di-accent)}
body.distill .card .title{
  font-family:var(--di-serif);
  font-size:1.05rem;color:var(--di-fg);text-decoration:none;
  border:none;display:block;margin-bottom:6px;
}
body.distill .card:hover .title{color:var(--di-accent)}
body.distill .card .meta{
  color:var(--di-muted);font-size:.74rem;letter-spacing:.08em;
  display:flex;gap:13px;flex-wrap:wrap;
  font-family:var(--di-sans);
}

/* ── 统计 ── */
body.distill .stats{display:flex;flex-wrap:wrap;gap:12px;margin:30px 0}
body.distill .stat{
  border:1px solid var(--di-line);
  padding:12px 16px;flex:1;min-width:120px;
}
body.distill .stat b{
  display:block;font-family:var(--di-serif);
  font-size:1.4rem;font-weight:600;color:var(--di-fg);
}
body.distill .stat span{
  color:var(--di-muted);font-size:.74rem;letter-spacing:.04em;
  font-family:var(--di-sans);
}

/* ── 提示块 ── */
body.distill .notice{
  margin:24px 0;padding:15px 18px;
  border-left:2px solid var(--di-line);
  background:var(--di-panel);
}
body.distill .notice strong{display:block;margin-bottom:7px;color:var(--di-fg)}
body.distill .notice p{margin:6px 0;font-size:.89rem;color:var(--di-muted)}
body.distill .notice-info{
  border-left-color:var(--di-warn);
  background:rgba(138,90,18,.05);
}

/* ── 元信息框（实验时间。里面是一张单行表，得把 booktabs 的上下线去掉）── */
body.distill .metabox{
  margin:24px 0;padding:15px 18px;
  border:1px solid var(--di-line);border-radius:2px;
  font-size:.84rem;font-family:var(--di-sans);
}
body.distill .metabox dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:6px 17px}
body.distill .metabox dt{color:var(--di-muted);letter-spacing:.08em;font-size:.74rem}
body.distill .metabox dd{margin:0;color:var(--di-fg)}
body.distill .metabox table{border:none;margin:0;font-size:inherit}
body.distill .metabox th{
  color:var(--di-muted);font-weight:400;border-bottom:none;
  padding:0 17px 0 0;
}
body.distill .metabox td{color:var(--di-fg);padding:0}

/* ── 翻页 ── */
body.distill .pager{
  display:flex;gap:14px;margin-top:56px;
  padding-top:24px;border-top:1px solid var(--di-line);
}
body.distill .pager-side{flex:1 1 40%}
body.distill .pager-mid{
  order:-1;flex:1 1 100%;text-align:center;
  color:var(--di-muted);font-size:.76rem;letter-spacing:.08em;
  margin-bottom:14px;font-family:var(--di-sans);
}
body.distill .pager-mid a{display:inline;border-bottom:1px solid rgba(34,104,204,.35)}
body.distill .pager-link{
  display:block;text-decoration:none;border:none;
  color:var(--di-fg);transition:color .15s ease-out;
}
body.distill .pager-link:hover{color:var(--di-accent)}
body.distill .pager-left{text-align:left}
body.distill .pager-right{text-align:right}
body.distill .pager-disabled{color:var(--di-muted);font-size:.85rem;display:block}

/* ── 返回链接 ── */
body.distill .nav-back{
  display:inline-block;margin-bottom:20px;
  color:var(--di-muted);font-size:.8rem;
  text-decoration:none;border-bottom:none;
  font-family:var(--di-sans);
}
body.distill .nav-back:hover{color:var(--di-accent)}

/* ── 键盘提示 ── */
body.distill .kbd-hint{
  display:inline-block;margin-top:16px;
  color:var(--di-muted);font-size:.75rem;letter-spacing:.04em;
  font-family:var(--di-sans);
}
body.distill .kbd-hint kbd{
  display:inline-block;padding:1px 5px;margin:0 2px;
  border:1px solid var(--di-line);border-radius:2px;
  font-family:var(--di-mono);font-size:.72rem;color:var(--di-fg);
}

/* ── 标签 ── */
body.distill .tag{
  display:inline-block;font-size:.7rem;letter-spacing:.08em;
  padding:1px 7px;border:1px solid currentColor;border-radius:2px;
  color:var(--di-muted);
}
body.distill .tag.warn{color:var(--di-warn)}
body.distill .tag.crit{color:var(--di-crit)}
body.distill .tag.info{color:var(--di-muted)}

/* ── 页脚 ── */
body.distill footer.site{
  border-top:1px solid var(--di-line);
  padding:26px 0 38px;
  color:var(--di-muted);font-size:.78rem;line-height:1.9;
  font-family:var(--di-sans);
}
body.distill .filing{margin-top:8px;font-size:.76rem}
body.distill .filing a{color:var(--di-muted);border-bottom:none}
body.distill .filing a:hover{color:var(--di-accent)}

/* ── 访问统计挂件 ──
   通用规则是毛玻璃（backdrop-filter + 12px 圆角 + 阴影），
   学术科普风不用玻璃态、圆角上限 2px，这里是唯一需要压平的地方。 */
body.distill .visit-widget{
  background:var(--di-bg);
  border:1px solid var(--di-line);
  border-radius:2px;
  box-shadow:none;
  -webkit-backdrop-filter:none;
  backdrop-filter:none;
  color:var(--di-fg);
}

@media(max-width:600px){
  body.distill main{padding:38px 0 58px}
  body.distill h1{font-size:1.6rem}
  body.distill h2{font-size:1.18rem}
  body.distill header.site .wrap{gap:4px 14px;padding:0 18px}
  body.distill nav.site{margin-left:0;width:100%;gap:14px}
  body.distill .pager{flex-wrap:wrap;gap:10px}
}


/* ===========================================================================
   工程蓝图风（云计算应用技术实验报告分支 body.blueprint）

   参照 StyleKit「工程蓝图 / Blueprint」硬性提示词：
     圆角一律 0；零阴影；零毛玻璃；禁止渐变填充与发光
     20px 图纸网格只作背景纹理，写在 background-image 上
     直角 + 1px 描边的图纸框感；hover 只改颜色不位移
     标题/UI/表头/图注/代码/页码走等宽，正文走中文无衬线

   为什么另起一套而不复用 distill：
     distill 是白底衬线学术风，这套是深蓝图纸面 + 浅图纸面双面混排，
     页头/页脚/代码块是深色，正文是浅色，视觉完全不同。
     回滚只需把页面的 style="blueprint" 参数去掉。

   对比度（WCAG 2.1 相对亮度实算，全部 >= 4.5:1，无一需要微调）：
     --bp-ink       #16304F on #F2F7FC 12.41:1 / on #EAF1F8 11.74:1
     --bp-ink-60    #44597A on #F2F7FC  6.59:1 / on #EAF1F8  6.24:1
     --bp-ink-40    #5A6E8C on #F2F7FC  4.82:1 / on #EAF1F8  4.56:1
     --bp-accent    #1B5FA6 on #F2F7FC  6.02:1 / on #EAF1F8  5.70:1
     --bp-fg        #FFFFFF on #1E3A5F 11.50:1 / on #16304F 13.38:1
     --bp-fg-60     #C9D6E4 on #1E3A5F  7.79:1 / on #16304F  9.06:1
     --bp-fg-40     #A5B0BF on #1E3A5F  5.24:1 / on #16304F  6.09:1
     --bp-accent-dark #8EC1F0 on #1E3A5F 6.05:1 / on #16304F 7.03:1
   最紧的一项是 --bp-ink-40 on sheet-2 = 4.56:1，色相未动。
   网格色只做背景纹理、不作文字色：--bp-grid-dark rgba(255,255,255,.055)
   叠 chrome 出 #2A4568（1.18:1），--bp-grid-light rgba(30,58,95,.05)
   叠 sheet 出 #E7EEF4（1.09:1）。
   =========================================================================== */

:root{
  --bp-chrome:#1E3A5F;
  --bp-chrome-2:#16304F;
  --bp-sheet:#F2F7FC;
  --bp-sheet-2:#EAF1F8;
  --bp-ink:#16304F;
  --bp-ink-60:#44597A;
  --bp-ink-40:#5A6E8C;
  --bp-fg:#FFFFFF;
  --bp-fg-60:#C9D6E4;
  --bp-fg-40:#A5B0BF;
  --bp-line:#CBD8E6;
  --bp-line-dark:rgba(255,255,255,.28);
  --bp-accent:#1B5FA6;
  --bp-accent-dark:#8EC1F0;
  --bp-warn:#8A5A12;
  --bp-warn-dark:#FFB37A;
  --bp-crit:#9B2C2C;
  --bp-crit-dark:#FFB3B3;
  --bp-grid-dark:rgba(255,255,255,.055);
  --bp-grid-light:rgba(30,58,95,.05);
  --bp-mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
}

/* 双面混排：body 是浅图纸面（正文阅读面），页头/导航/页脚另铺深蓝图面。
   网格线只在 background-image 上写，不用简写形式。 */
body.blueprint{
  background-color:var(--bp-sheet);
  background-image:repeating-linear-gradient(to right,var(--bp-grid-light) 0,var(--bp-grid-light) 1px,transparent 1px,transparent 20px),repeating-linear-gradient(to bottom,var(--bp-grid-light) 0,var(--bp-grid-light) 1px,transparent 1px,transparent 20px);
  background-size:20px 20px;
  color:var(--bp-ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Source Han Sans SC","Noto Sans CJK SC",sans-serif;
  font-size:17px;
  line-height:1.75;
}

/* 圆角一律 0：通用规则里 .card / .visit-widget 等处有 10~12px 圆角，
   这里一刀切平，后面的逐条规则再显式声明一次，好让读的人一眼看到。 */
body.blueprint *{border-radius:0}

/* 版心：正文列 44rem（比 distill 的 42rem 略宽，实验报告表格多），
   加回 .wrap 左右各 24px 内边距 → 覆盖其它主题的 880px / 740px。 */
body.blueprint .wrap{max-width:calc(44rem + 48px)}
/* 页头/页脚版心单独放宽：分支名「云计算应用技术实验报告」有 10 个字，
   收在 44rem 正文列里会和导航挤成两行。标题栏宽一点也符合图纸标题栏的观感。 */
body.blueprint header.site .wrap,body.blueprint footer.site .wrap{
  max-width:calc(64rem + 48px)
}

/* ── 页头（深蓝图面 + 20px 网格）── */
body.blueprint header.site{
  background-color:var(--bp-chrome);
  background-image:repeating-linear-gradient(to right,var(--bp-grid-dark) 0,var(--bp-grid-dark) 1px,transparent 1px,transparent 20px),repeating-linear-gradient(to bottom,var(--bp-grid-dark) 0,var(--bp-grid-dark) 1px,transparent 1px,transparent 20px);
  background-size:20px 20px;
  border-bottom:1px solid var(--bp-line-dark);
  padding:22px 0;
  color:var(--bp-fg);
}
body.blueprint header.site .wrap{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 18px}
/* 页头这几个选择器都长在深蓝面上，但自身不声明底色。
   站点颜色契约校验是拿「规则自带的不透明底色」当元素底色的，
   没写底色的会退回按浅色正文面（--bp-sheet）算，把白字判成 1.06:1。
   这里显式声明一次与父元素同色：既让校验算在正确的面上，
   也把「这一段属于深面」在源码里写实。 */
body.blueprint header.site .brand{
  font-family:var(--bp-mono);
  background-color:var(--bp-chrome);
  font-size:1rem;font-weight:600;color:var(--bp-fg);
  letter-spacing:.01em;
}
body.blueprint header.site .brand-parent{color:var(--bp-fg);background-color:var(--bp-chrome)}
body.blueprint header.site .brand-branch{color:var(--bp-accent-dark);background-color:var(--bp-chrome)}
body.blueprint header.site .sub{
  font-family:var(--bp-mono);
  background-color:var(--bp-chrome);
  color:var(--bp-fg-60);font-size:.78rem;letter-spacing:.06em;
}

body.blueprint nav.site{margin-left:auto;display:flex;gap:16px;flex-wrap:wrap}
/* 导航项长在深蓝页头面上。规则里显式写出与父元素同色的底色，
   既是给读源码的人一个"这段属深面"的实据，也让站点的颜色契约
   校验算在正确的面上 —— 校验只认规则自带的不透明底色。 */
body.blueprint nav.site a{
  font-family:var(--bp-mono);
  background-color:var(--bp-chrome);
  color:var(--bp-fg-60);text-decoration:none;font-size:.84rem;
  position:relative;padding-bottom:2px;
  transition:color .15s ease-out;
}
body.blueprint nav.site a:hover{color:var(--bp-fg)}
body.blueprint nav.site a.on{background-color:var(--bp-chrome);color:var(--bp-fg);font-weight:600}
body.blueprint nav.site a::after{background-color:var(--bp-accent-dark)}

/* ── 面包屑 ── */
body.blueprint .crumbs{
  font-family:var(--bp-mono);
  border-bottom-color:var(--bp-line);
}
body.blueprint .crumbs a{color:var(--bp-accent);opacity:1}
body.blueprint .crumbs a:hover{background-color:var(--bp-sheet-2);color:var(--bp-accent);opacity:1}
body.blueprint .crumbs .cur{color:var(--bp-ink)}
body.blueprint .crumbs .sep{color:var(--bp-ink-60);opacity:1}

/* ── 正文 ── */
body.blueprint main{padding:56px 0 84px}

body.blueprint h1{
  font-family:var(--bp-mono);font-weight:600;
  font-size:1.85rem;line-height:1.3;letter-spacing:-.01em;
  margin:0 0 16px;padding:0;color:var(--bp-ink);
}
body.blueprint h2{
  font-family:var(--bp-mono);font-weight:600;
  font-size:1.28rem;line-height:1.4;
  margin:40px 0 14px;padding:0 0 7px;
  border-bottom:1px solid var(--bp-line);color:var(--bp-ink);
}
body.blueprint h3{
  font-family:var(--bp-mono);font-weight:600;
  font-size:1.08rem;line-height:1.45;
  margin:28px 0 10px;color:var(--bp-ink);
}
/* 正文刻意用中文无衬线，不用等宽（等宽中文长文阅读负担重） */
body.blueprint p{margin:15px 0;color:var(--bp-ink)}
body.blueprint .lede{color:var(--bp-ink-60);font-size:1rem;line-height:1.75}
body.blueprint a{
  color:var(--bp-accent);text-decoration:none;
  border-bottom:1px solid rgba(27,95,166,.35);
}
body.blueprint a:hover{border-bottom-color:var(--bp-accent)}
body.blueprint ul,body.blueprint ol{padding-left:1.4em;margin:15px 0}
body.blueprint li{margin:6px 0;color:var(--bp-ink)}
body.blueprint blockquote{
  margin:20px 0;padding:12px 16px;
  border:1px solid var(--bp-line);border-left:3px solid var(--bp-accent);
  background-color:var(--bp-sheet-2);
  color:var(--bp-ink-60);
}
body.blueprint hr{border:none;border-top:1px solid var(--bp-line);margin:34px 0}

/* ── 代码与命令块 ──
   输出块：chrome-2 底 + 1px 深面描边（深色面，与正文浅面区分）
   命令块（带 language- 围栏）：底再深一档 + 左侧 3px accent-dark 竖条，
   与输出块可区分——站点校验器会检查这两者不能长一样。 */
body.blueprint code{
  font-family:var(--bp-mono);
  background-color:var(--bp-sheet-2);color:var(--bp-ink);
  border:1px solid var(--bp-line);
  padding:1px 5px;font-size:.87em;border-radius:0;
}
body.blueprint pre{
  font-family:var(--bp-mono);
  background-color:var(--bp-chrome-2);
  color:var(--bp-fg);
  border:1px solid var(--bp-line-dark);
  padding:15px 17px;border-radius:0;
  overflow-x:auto;font-size:.82rem;line-height:1.7;
  margin:20px 0;
}
/* 底色调成与 pre 同色，而不是 transparent：同样是为了让契约校验
   认出这段文字落在深面上（transparent 会被当成「没有自带底色」）。 */
body.blueprint pre code{
  background-color:var(--bp-chrome-2);background-image:none;
  color:var(--bp-fg);border:none;padding:0;font-size:inherit;
}
body.blueprint pre,body.blueprint pre code{box-shadow:none}
body.blueprint pre[class*="language-"]{
  background-color:var(--bp-chrome-2);
  border:1px solid var(--bp-line-dark);
  border-left:3px solid var(--bp-accent-dark);
  padding:13px 17px 13px 15px;
}

/* ── 图片与图注（实验运行截图）──
   截图白底，用 1px 图纸线收边；图注等宽小字居中，前缀 ▸ 装饰。 */
body.blueprint figure{margin:26px 0;padding:0}
body.blueprint figure img{
  max-width:100%;height:auto;display:block;margin:0 auto;
  background-color:#FFFFFF;
  border:1px solid var(--bp-line);border-radius:0;
}
body.blueprint figcaption{
  margin-top:8px;padding-top:6px;
  border-top:1px solid var(--bp-line);
  font-family:var(--bp-mono);
  font-size:.78rem;line-height:1.6;letter-spacing:.02em;
  color:var(--bp-ink-40);text-align:center;
}
body.blueprint figcaption::before{content:"▸ "}
/* 报告正文里的截图是 `![](img/x.png)`，没有 alt 文本，md2html 仍会照发
   <figcaption></figcaption>；这里把它收掉，免得每张图下面挂一条孤零零的
   前缀符。带 alt 的图不受影响。 */
body.blueprint figcaption:empty{display:none}

/* ── 表格（网格表格：与另一个分支的 booktabs 只有上下粗线拉开差异）──
   表头 sheet-2 底 + 等宽小字 + 1px 下边框；单元格 1px 分隔；无斑马纹。 */
body.blueprint table{
  width:100%;border-collapse:collapse;font-size:.86rem;margin:22px 0;
  border:1px solid var(--bp-line);
  font-family:var(--bp-mono);
}
body.blueprint th{
  font-family:var(--bp-mono);
  text-align:left;font-weight:600;color:var(--bp-ink);
  background-color:var(--bp-sheet-2);
  border-right:1px solid var(--bp-line);
  border-bottom:1px solid var(--bp-line);
  padding:8px 12px;
}
body.blueprint td{
  padding:8px 12px;color:var(--bp-ink);
  border-right:1px solid var(--bp-line);
  border-bottom:1px solid var(--bp-line);
  vertical-align:top;
}
body.blueprint th:last-child,body.blueprint td:last-child{border-right:none}
body.blueprint tr:last-child td{border-bottom:none}

/* ── 卡片（概览页的实验列表）──
   卡面用浅图纸底色，标题条走深蓝图面，是「双面混排」最直接的体现。 */
body.blueprint .card{
  border:1px solid var(--bp-line);
  border-radius:0;padding:0;margin:14px 0;
  background-color:var(--bp-sheet);
  transition:border-color .15s ease-out;
}
body.blueprint .card:hover{border-color:var(--bp-accent)}
body.blueprint .card .title{
  display:block;margin:0;
  font-family:var(--bp-mono);
  background-color:var(--bp-chrome);
  border-bottom:1px solid var(--bp-line-dark);
  color:var(--bp-fg);
  padding:12px 18px;
  font-size:1.02rem;font-weight:600;text-decoration:none;
}
body.blueprint .card:hover .title{color:var(--bp-accent-dark)}
body.blueprint .card .meta{
  font-family:var(--bp-mono);
  color:var(--bp-ink-60);font-size:.74rem;letter-spacing:.08em;
  display:flex;gap:13px;flex-wrap:wrap;
  padding:12px 18px 14px;
}

/* ── 统计 ── */
body.blueprint .stats{display:flex;flex-wrap:wrap;gap:12px;margin:30px 0}
body.blueprint .stat{
  border:1px solid var(--bp-line);border-radius:0;
  background-color:var(--bp-sheet-2);
  padding:12px 16px;flex:1;min-width:120px;
}
body.blueprint .stat b{
  display:block;font-family:var(--bp-mono);
  font-size:1.4rem;font-weight:600;color:var(--bp-ink);
}
body.blueprint .stat span{
  font-family:var(--bp-mono);
  color:var(--bp-ink-60);font-size:.74rem;letter-spacing:.04em;
}

/* ── 提示块 ── */
body.blueprint .notice{
  margin:24px 0;padding:15px 18px;
  border:1px solid var(--bp-line);border-left:3px solid var(--bp-ink-60);
  background-color:var(--bp-sheet-2);border-radius:0;
}
body.blueprint .notice strong{display:block;margin-bottom:7px;color:var(--bp-ink)}
body.blueprint .notice p{margin:6px 0;font-size:.89rem;color:var(--bp-ink-60)}
body.blueprint .notice-info{
  border-left-color:var(--bp-warn);
  background-color:rgba(138,90,18,.06);
}
body.blueprint .notice-crit{
  border-left-color:var(--bp-crit);
  background-color:rgba(155,44,44,.06);
}

/* ── 元信息框（实验时间。里面是一张单行表，得把网格表格的框线去掉）── */
body.blueprint .metabox{
  margin:24px 0;padding:15px 18px;
  border:1px solid var(--bp-line);border-radius:0;
  background-color:var(--bp-sheet-2);
  font-family:var(--bp-mono);
  font-size:.84rem;
}
body.blueprint .metabox dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:6px 17px}
body.blueprint .metabox dt{color:var(--bp-ink-60);letter-spacing:.08em;font-size:.74rem}
body.blueprint .metabox dd{margin:0;color:var(--bp-ink)}
body.blueprint .metabox table{border:none;background-color:transparent;margin:0;font-size:inherit}
body.blueprint .metabox th{
  color:var(--bp-ink-60);font-weight:400;background-color:transparent;
  border:none;padding:0 17px 0 0;
}
body.blueprint .metabox td{color:var(--bp-ink);border:none;padding:0}

/* ── 翻页 ── */
body.blueprint .pager{
  display:flex;gap:14px;margin-top:56px;
  padding-top:24px;border-top:1px solid var(--bp-line);
}
body.blueprint .pager-side{flex:1 1 40%}
body.blueprint .pager-mid{
  font-family:var(--bp-mono);
  order:-1;flex:1 1 100%;text-align:center;
  color:var(--bp-ink-60);font-size:.76rem;letter-spacing:.08em;
  margin-bottom:14px;
}
body.blueprint .pager-mid a{display:inline;border-bottom:1px solid rgba(27,95,166,.35)}
body.blueprint .pager-link{
  display:block;text-decoration:none;border:none;
  color:var(--bp-ink);transition:color .15s ease-out;
}
body.blueprint .pager-link:hover{color:var(--bp-accent)}
body.blueprint .pager-left{text-align:left}
body.blueprint .pager-right{text-align:right}
body.blueprint .pager-disabled{
  display:block;font-family:var(--bp-mono);
  color:var(--bp-ink-40);font-size:.85rem;
}

/* ── 返回链接 ── */
body.blueprint .nav-back{
  display:inline-block;margin-bottom:20px;
  font-family:var(--bp-mono);
  color:var(--bp-ink-60);font-size:.8rem;
  text-decoration:none;border-bottom:none;
}
body.blueprint .nav-back:hover{color:var(--bp-accent)}

/* ── 键盘提示 ── */
body.blueprint .kbd-hint{
  display:inline-block;margin-top:16px;
  font-family:var(--bp-mono);
  color:var(--bp-ink-60);font-size:.75rem;letter-spacing:.04em;
}
body.blueprint .kbd-hint kbd{
  display:inline-block;padding:1px 5px;margin:0 2px;
  border:1px solid var(--bp-line);border-radius:0;
  background-color:var(--bp-sheet-2);
  font-family:var(--bp-mono);font-size:.72rem;color:var(--bp-ink);
}

/* ── 标签 ── */
body.blueprint .tag{
  display:inline-block;
  font-family:var(--bp-mono);
  font-size:.7rem;letter-spacing:.08em;
  padding:1px 7px;border:1px solid currentColor;border-radius:0;
  color:var(--bp-ink-60);
}
body.blueprint .tag.warn{color:var(--bp-warn)}
body.blueprint .tag.crit{color:var(--bp-crit)}
body.blueprint .tag.info{color:var(--bp-ink-60)}

/* ── 页脚（深蓝图面，与页头对称）── */
body.blueprint footer.site{
  background-color:var(--bp-chrome);
  background-image:repeating-linear-gradient(to right,var(--bp-grid-dark) 0,var(--bp-grid-dark) 1px,transparent 1px,transparent 20px),repeating-linear-gradient(to bottom,var(--bp-grid-dark) 0,var(--bp-grid-dark) 1px,transparent 1px,transparent 20px);
  background-size:20px 20px;
  border-top:1px solid var(--bp-line-dark);
  padding:26px 0 38px;
  color:var(--bp-fg-60);font-size:.78rem;line-height:1.9;
  font-family:var(--bp-mono);
}
body.blueprint .filing{margin-top:8px;font-size:.76rem;color:var(--bp-fg-40);background-color:var(--bp-chrome)}
body.blueprint .filing a{color:var(--bp-fg-60);background-color:var(--bp-chrome);border-bottom:none}
body.blueprint .filing a:hover{color:var(--bp-accent-dark)}

/* ── 访问统计挂件 ──
   通用规则是毛玻璃（backdrop-filter + 12px 圆角 + 阴影），
   蓝图风零阴影零毛玻璃零圆角，这里是必须拍平的地方。 */
body.blueprint .visit-widget{
  background-color:var(--bp-chrome);
  border:1px solid var(--bp-line-dark);
  border-radius:0;
  box-shadow:none;
  -webkit-backdrop-filter:none;
  backdrop-filter:none;
  color:var(--bp-fg);
  font-family:var(--bp-mono);
}

/* ── 响应式 ── */
@media(max-width:600px){
  body.blueprint main{padding:38px 0 58px}
  body.blueprint h1{font-size:1.5rem}
  body.blueprint h2{font-size:1.14rem}
  body.blueprint h3{font-size:1.02rem}
  body.blueprint header.site .wrap{gap:4px 14px;padding:0 18px}
  body.blueprint header.site .brand{font-size:.92rem}
  body.blueprint nav.site{margin-left:0;width:100%;gap:14px}
  body.blueprint .pager{flex-wrap:wrap;gap:10px}
  body.blueprint .pager-side{flex:1 1 100%}
  body.blueprint .pager-left,body.blueprint .pager-right{text-align:left}
  body.blueprint figcaption{text-align:left}
  body.blueprint table{font-size:.8rem}
  body.blueprint th,body.blueprint td{padding:6px 8px}
}


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

(function () {
  var el = document.getElementById('visitWidget');
  if (!el) return;
  var root = el.getAttribute('data-root') || '';
  var url = root + 'stats/visits.json';
  function fmt(n) {
    if (n >= 100000) return (n / 10000).toFixed(0) + 'w';
    if (n >= 10000) return (n / 10000).toFixed(1).replace('.0', '') + 'w';
    if (n >= 1000) return n.toLocaleString('en-US');
    return String(n);
  }
  function set(id, v) {
    var e = document.getElementById(id);
    if (e) e.textContent = fmt(v);
  }
  fetch(url, { cache: 'no-store' })
    .then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (d) {
      set('vwToday', d.today || 0);
      set('vwWeek', d.week || 0);
      set('vwMonth', d.month || 0);
      set('vwTotal', d.total || 0);
      var t = document.getElementById('vwTime');
      if (t) {
        var u = String(d.updated || '');
        var hhmm = u.length >= 16 ? u.slice(5, 16) : '';
        t.textContent = hhmm ? ('独立访客 · 更新于 ' + hhmm) : '独立访客';
      }
      el.classList.remove('vw-loading');
    })
    .catch(function () { el.classList.add('vw-hidden'); });
})();
""".strip()


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# 首页（项目工作台）的导航：列"有哪些项目"，每条指向该项目的分支入口。
# 与分支内的章节导航是两套东西，不能混用 —— 见 page() 里的说明。
# 以后新增项目，在这里加一条即可。
PROJECT_NAV = [
    (OPS_SUBDIR + "/", "运维实践"),
    (DM_SUBDIR + "/", "数据挖掘"),
    (CC_SUBDIR + "/", "云计算应用技术实验报告"),
]


# 分支元信息：品牌名、面包屑的当前层级名。
# 之前「运维」是硬编码在 page() 里的 —— 新增分支就会在页头显示错分支名。
BRANCHES = {
    OPS_SUBDIR: {
        "name": "运维",
        "crumbs": {"journal": "我的手记", "pm": "参考复盘", "ops": "运维体系"},
    },
    DM_SUBDIR: {
        "name": "数据挖掘",
        "crumbs": {"e1": "实验一", "e2": "实验二", "e3": "实验三",
                   "e4": "实验四", "e5": "实验五"},
    },
    CC_SUBDIR: {
        "name": "云计算应用技术实验报告",
        "crumbs": {"e1": "实验一", "e2": "实验二", "e3": "实验三",
                   "e4": "实验四", "e5": "实验五", "e6": "实验六"},
    },
}


def _branch_nav(subdir):
    """分支内的章节导航。"""
    if subdir == CC_SUBDIR:
        return [
            (CC_SUBDIR + "/index.html", "概览", "home"),
            (CC_SUBDIR + "/exp1.html", "实验一", "e1"),
            (CC_SUBDIR + "/exp2.html", "实验二", "e2"),
            (CC_SUBDIR + "/exp3.html", "实验三", "e3"),
            (CC_SUBDIR + "/exp4.html", "实验四", "e4"),
            (CC_SUBDIR + "/exp5.html", "实验五", "e5"),
            (CC_SUBDIR + "/exp6.html", "实验六", "e6"),
        ]
    if subdir == DM_SUBDIR:
        return [
            (DM_SUBDIR + "/index.html", "概览", "home"),
            (DM_SUBDIR + "/exp1.html", "实验一", "e1"),
            (DM_SUBDIR + "/exp2.html", "实验二", "e2"),
            (DM_SUBDIR + "/exp3.html", "实验三", "e3"),
            (DM_SUBDIR + "/exp4.html", "实验四", "e4"),
            (DM_SUBDIR + "/exp5.html", "实验五", "e5"),
        ]
    return [
        (OPS_SUBDIR + "/index.html", "运维首页", "home"),
        (OPS_SUBDIR + "/journal.html", "我的手记", "journal"),
        (OPS_SUBDIR + "/postmortem.html", "参考复盘", "pm"),
        (OPS_SUBDIR + "/system.html", "运维体系", "ops"),
    ]


def page(title, body, rel_root="", active="", description="", style="ops-dark",
         nav_active=None, nav_mode="branch", branch=OPS_SUBDIR):
    """渲染一个完整页面。rel_root 用于子目录页面回指根。"""
    # 导航分两套，不能混用：
    #   nav_mode="branch"  —— 章节导航（运维首页/我的手记/参考复盘/运维体系），
    #                         列的是"这个分支里有什么"，只出现在分支内部；
    #   nav_mode="project" —— 项目导航，列的是"有哪些项目"，
    #                         只出现在首页（工作台，它正是分支的上一层）。
    if nav_mode == "project":
        nav = [(href, label, "") for href, label in PROJECT_NAV]
    else:
        nav = _branch_nav(branch)

    # nav 里存的是"相对站点根"的路径，渲染时统一加上 rel_root。
    # key 为空表示该项永不当前态（项目导航在首页上不该有当前项）。
    navhtml = "".join(
        '<a href="%s%s"%s>%s</a>' % (
            rel_root, href,
            ' class="on"' if key and key == (nav_active if nav_active is not None
                                             else active) else "", label)
        for href, label, key in nav)

    # 品牌名：首页只显示站名；分支内显示「站名 ‹ 分支名」
    # 为什么这么做：进入分支后，页头要能一眼看出在哪个分支里，
    # 而不是看起来和首页一模一样。
    binfo = BRANCHES.get(branch, BRANCHES[OPS_SUBDIR])
    if active == "home":
        brand_html = '<a class="brand" href="%sindex.html">%s</a>' % (
            rel_root, esc(SITE_TITLE))
    else:
        brand_html = (
            '<a class="brand brand-parent" href="%sindex.html">%s</a>'
            '<span class="brand-sep">‹</span>'
            '<span class="brand-branch">%s</span>'
        ) % (rel_root, esc(SITE_TITLE), esc(binfo["name"]))

    # 面包屑：工作台 / 运维 / [当前章节]
    # 首页不显示（它就在最顶层，没有上级）
    if active == "home":
        crumbs = ""
    else:
        c = ['<nav class="crumbs"><a href="%sindex.html">工作台</a>' % rel_root]
        c.append('<span class="sep">›</span>')
        c.append('<a href="%s%s/index.html">%s</a>'
                 % (rel_root, branch, esc(binfo["name"])))
        cur = binfo["crumbs"].get(active)
        if cur:
            c.append('<span class="sep">›</span><span class="cur">%s</span>'
                     % esc(cur))
        c.append('</nav>')
        crumbs = "".join(c)

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<style>{css}</style>
</head>
<body class="{bodyclass}">
<div class="visit-widget vw-loading" id="visitWidget" data-root="{root}"><div class="vw-stats"><div class="vw-item"><span class="vw-num" id="vwToday">-</span><span class="vw-label">今日</span></div><div class="vw-item"><span class="vw-num" id="vwWeek">-</span><span class="vw-label">本周</span></div><div class="vw-item"><span class="vw-num" id="vwMonth">-</span><span class="vw-label">本月</span></div><div class="vw-item"><span class="vw-num" id="vwTotal">-</span><span class="vw-label">累计</span></div></div><div class="vw-time" id="vwTime">独立访客</div></div>
<header class="site"><div class="wrap">
{brand}
<span class="sub">{sub}</span>
<nav class="site">{nav}</nav>
{crumbs}</div></header>
<main><div class="wrap">
{body}
</div></main>
<footer class="site"><div class="wrap">
{site} · 部署于阿里云 ECS 2C2G（Alibaba Cloud Linux 3 + nginx + systemd）<br>
本站由自研 Python 静态生成器构建，零第三方依赖。
<div class="filing">
<a href="{icp_url}" target="_blank" rel="noopener noreferrer">{icp}</a><span class="filing-sep">·</span><a href="{mps_url}" target="_blank" rel="noopener noreferrer">{mps}</a>
</div>
</div></footer>
<script>{js}</script>
</body>
</html>
""".format(title=esc(title), desc=esc(description), css=CSS,
           root=rel_root, site=esc(SITE_TITLE), sub=esc(SITE_SUBTITLE), brand=brand_html,
           nav=navhtml, crumbs=crumbs, body=body, js=PAGER_JS,
           icp=ICP_NUMBER, icp_url=ICP_URL,
           mps=MPS_NUMBER, mps_url=MPS_URL, bodyclass=style)


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
    """首页 —— 项目导航（赛璐璐动画风）

    依据 StyleKit「赛璐璐动画风」规范：
      所有元素 3px 粗黑边框
      硬偏移阴影（无模糊）
      平面色块填充，禁止渐变
      文字加粗
      交互 duration-75（模拟动画抽帧）
      active 时 Squash & Stretch
    """
    n_journal = len(journals) if journals else 0
    n_pm = len(pms)

    projects = []

    # ── 运维项目 ──
    projects.append("""<article class="proj-card">
<div class="proj-top">
<span class="proj-mark">◆</span>
<span class="proj-state state-live">进行中</span>
</div>
<h2 class="proj-name">LINUX OPS LAB</h2>
<p class="proj-intro">
一台 2 核 2 GB 云服务器上的运维记录。包含部署、排障与自动化脚本，
每篇附有实际执行过的命令与输出。
</p>
<dl class="proj-stats">
<div><dt>实操手记</dt><dd>{nj}</dd></div>
<div><dt>故障复盘</dt><dd>{np}</dd></div>
<div><dt>巡检项</dt><dd>13</dd></div>
</dl>
<a class="proj-enter" href="ops/index.html">进入分支<span class="arrow">→</span></a>
</article>""".format(nj=n_journal, np=n_pm))

    # ── 数据挖掘 ──
    projects.append("""<article class="proj-card">
<div class="proj-top">
<span class="proj-mark">◆</span>
<span class="proj-state state-live">已完成</span>
</div>
<h2 class="proj-name">DATA MINING</h2>
<p class="proj-intro">
数据挖掘课程的五次实验报告：特征选择、分类、回归、集成与聚类。
每篇包含实验原理、代码、运行截图与结果分析。
</p>
<dl class="proj-stats">
<div><dt>实验报告</dt><dd>{nd}</dd></div>
<div><dt>运行截图</dt><dd>{ni}</dd></div>
</dl>
<a class="proj-enter" href="dm/index.html">进入分支<span class="arrow">→</span></a>
</article>""".format(nd=len(DM_ITEMS), ni=_dm_img_count()))

    # ── 云计算应用技术实验报告 ──

    projects.append("""<article class="proj-card">
<div class="proj-top">
<span class="proj-mark">◆</span>
<span class="proj-state state-live">已完成</span>
</div>
<h2 class="proj-name">CLOUD COMPUTING</h2>
<p class="proj-intro">
云计算应用技术课程的六次实验报告：Git、OpenStack、Docker、
Kubernetes、共享式存储与云桌面。每篇附命令行与运行截图。
</p>
<dl class="proj-stats">
<div><dt>实验报告</dt><dd>{nc}</dd></div>
<div><dt>运行截图</dt><dd>{nci}</dd></div>
</dl>
<a class="proj-enter" href="cc/index.html">进入分支<span class="arrow">→</span></a>
</article>""".format(nc=len(CC_ITEMS), nci=_cc_img_count()))

    body = """<section class="hero">
<div class="hero-inner">
<div class="hero-label">PERSONAL WORKSPACE</div>
<h1 class="hero-title">项目工作台</h1>
<p class="hero-desc">按项目分开记录</p>
</div>
</section>

<section class="proj-grid">
{cards}
</section>

<section class="colophon">
<h2>关于这里</h2>
<p>
这是一个个人项目站点。每个项目有独立的入口页面，分别记录各自的实践过程。
</p>
<p class="colophon-note">
部署在一台 2 核 2 GB 云服务器上，由自己写的 Python 静态生成器构建，
只用标准库。
</p>
</section>""".format(cards="".join(projects))

    return page("项目工作台", body, active="home", style="anime",
                nav_mode="project",
                description="个人项目工作台 —— 记录长期在做的项目与实践过程")


def _dm_img_count():
    """数一下 DM_DIR/img 下有多少张 png。"""
    n = 0
    d = os.path.join(DM_DIR, "img")
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.lower().endswith(".png"):
                n += 1
    return n


def build_dm_index():
    """数据挖掘分支 —— 概览页"""
    cards = []
    for num, name, date, _key in DM_ITEMS:
        path = os.path.join(DM_DIR, "exp%s.md" % num)
        lines = 0
        if os.path.exists(path):
            lines = len(open(path, encoding="utf-8").read().split("\n"))
        cards.append("""<div class="card">
<a class="title" href="exp{num}.html">{name}</a>
<div class="meta"><span>{date}</span><span>{lines} 行</span></div>
</div>""".format(num=num, name=esc(name), date=esc(date), lines=lines))

    body = """<h1>数据挖掘</h1>
<p class="lede">数据挖掘课程的 5 次实验报告。每次包含实验目的、实验原理、
实验内容与步骤、实验结果与分析、结论与体会，并附运行截图。</p>
{cards}""".format(cards="".join(cards))

    # active 留空：分支首页要显示「站名 ‹ 数据挖掘」的品牌与面包屑，
    # 但导航高亮由 nav_active 单独指定（active="home" 会把品牌退回成纯站名）。
    return page("数据挖掘 · " + SITE_TITLE, body, active="", nav_active="home",
                branch=DM_SUBDIR, style="distill", rel_root="../",
                description="数据挖掘课程实验报告：特征选择、分类、回归、集成、聚类")


def build_dm_page(num, name, date, key):
    """数据挖掘分支 —— 单篇报告"""
    path = os.path.join(DM_DIR, "exp%s.md" % num)
    raw = open(path, encoding="utf-8").read()
    raw = M.strip_first_heading(raw)
    # 第 3 行的「实验名称 / 实验时间」由标题与下方 metabox 承担，正文里去掉
    raw = re.sub(r"^\*\*实验名称\*\*[^\n]*\n?", "", raw, count=1, flags=re.M)
    html = M.md2html(raw)

    idx = [x[0] for x in DM_ITEMS].index(num)
    if idx > 0:
        p = DM_ITEMS[idx - 1]
        prev_html = '<a class="pager-link" href="exp%s.html">← %s</a>' % (
            p[0], esc(p[1]))
    else:
        prev_html = '<span class="pager-disabled">← 上一篇</span>'
    if idx < len(DM_ITEMS) - 1:
        nx = DM_ITEMS[idx + 1]
        next_html = '<a class="pager-link" href="exp%s.html">%s →</a>' % (
            nx[0], esc(nx[1]))
    else:
        next_html = '<span class="pager-disabled">下一篇 →</span>'

    counter = '<span class="pager-count">第 %d 篇 / 共 %d 篇</span>' % (
        idx + 1, len(DM_ITEMS))

    pager = (
        '<div class="pager">'
        '<div class="pager-side pager-left">' + prev_html + '</div>'
        '<div class="pager-mid"><a href="index.html">数据挖掘</a>'
        + counter + '</div>'
        '<div class="pager-side pager-right">' + next_html + '</div>'
        '</div>'
    )

    body = ('<p class="nav-back"><a href="index.html">← 返回数据挖掘</a></p>\n'
            + pager + "\n<h1>" + esc(name) + "</h1>\n"
            + '<div class="metabox"><table><tr><th>实验时间</th><td>'
            + esc(date) + "</td></tr></table></div>\n"
            + html + "\n" + pager + "\n")

    return page(name + " · " + SITE_TITLE, body, rel_root="../",
                active=key, branch=DM_SUBDIR, style="distill",
                description=name)


def _cc_img_count():
    """数一下 CC_DIR/img 下有多少张 png。"""
    n = 0
    d = os.path.join(CC_DIR, "img")
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.lower().endswith(".png"):
                n += 1
    return n


def build_cc_index():
    """云计算应用技术实验报告 —— 概览页"""
    cards = []
    for num, name, date, _key in CC_ITEMS:
        path = os.path.join(CC_DIR, "exp%s.md" % num)
        lines = 0
        if os.path.exists(path):
            lines = len(open(path, encoding="utf-8").read().split("\n"))
        cards.append("""<div class="card">
<a class="title" href="exp{num}.html">{name}</a>
<div class="meta"><span>{date}</span><span>{lines} 行</span></div>
</div>""".format(num=num, name=esc(name), date=esc(date), lines=lines))

    body = """<h1>云计算应用技术实验报告</h1>
<p class="lede">云计算应用技术课程的 6 次实验报告。从 Git 与版本控制起步，
依次走过 OpenStack 私有云、Docker 容器、Kubernetes 集群、共享式存储与
Hyper-V 云桌面，每次附完整命令与运行截图。</p>
{cards}""".format(cards="".join(cards))

    return page("云计算应用技术实验报告 · " + SITE_TITLE, body,
                active="", nav_active="home",
                branch=CC_SUBDIR, style="blueprint", rel_root="../",
                description="云计算应用技术实验报告：Git、OpenStack、Docker、"
                            "Kubernetes、共享式存储与云桌面")


def build_cc_page(num, name, date, key):
    """云计算应用技术实验报告 —— 单篇报告"""
    path = os.path.join(CC_DIR, "exp%s.md" % num)
    raw = open(path, encoding="utf-8").read()
    raw = M.strip_first_heading(raw)
    html = M.md2html(raw)

    idx = [x[0] for x in CC_ITEMS].index(num)
    if idx > 0:
        p = CC_ITEMS[idx - 1]
        prev_html = '<a class="pager-link" href="exp%s.html">← %s</a>' % (
            p[0], esc(p[1]))
    else:
        prev_html = '<span class="pager-disabled">← 上一篇</span>'
    if idx < len(CC_ITEMS) - 1:
        nx = CC_ITEMS[idx + 1]
        next_html = '<a class="pager-link" href="exp%s.html">%s →</a>' % (
            nx[0], esc(nx[1]))
    else:
        next_html = '<span class="pager-disabled">下一篇 →</span>'

    counter = '<span class="pager-count">第 %d 篇 / 共 %d 篇</span>' % (
        idx + 1, len(CC_ITEMS))

    pager = (
        '<div class="pager">'
        '<div class="pager-side pager-left">' + prev_html + '</div>'
        '<div class="pager-mid"><a href="index.html">云计算应用技术实验报告</a>'
        + counter + '</div>'
        '<div class="pager-side pager-right">' + next_html + '</div>'
        '</div>'
    )

    body = ('<p class="nav-back"><a href="index.html">← 返回云计算应用技术实验报告</a></p>\n'
            + pager + "\n<h1>" + esc(name) + "</h1>\n"
            + '<div class="metabox"><table><tr><th>实验时间</th><td>'
            + esc(date) + "</td></tr></table></div>\n"
            + html + "\n" + pager + "\n")

    return page(name + " · " + SITE_TITLE, body, rel_root="../",
                active=key, branch=CC_SUBDIR, style="blueprint",
                description=name)


def build_ops_home(pms, journals=None):
    """首页 —— 终端风

    依据 StyleKit「终端风」规范：
      内容组织为 命令 + 输出的成对结构
      章节标题用 prompt 前缀
      底部有 tmux 式状态栏
      颜色只承担语义（绿=输出 青=路径 琥珀=数字 品红=错误 蓝灰=注释）
    """
    n_journal = len(journals) if journals else 0
    n_pm = len(pms)

    # ── 每个区块 = 一条命令 + 它的输出 ──
    blocks = []

    # 区块 1：whoami
    blocks.append("""<div class="out">
<span class="prompt">visitor@ops-lab:<span class="path">~</span>$</span> <span class="cmd">cat</span> <span class="arg">about.txt</span>
<div style="margin-top:12px">
一台 <span class="num">2 vCPU / 2 GB</span> 的阿里云 ECS 上，
从零搭建并运维这个静态站点。
</div>
<div style="margin-top:10px" class="comment">
# 用 nginx 提供访问，发布走软链接原子切换；另外配了回滚、
# 崩溃自愈、请求限流与监控告警。
</div>
</div>""")

    # 区块 2：ls -la（站点内容）
    blocks.append("""<div class="out">
<span class="prompt">visitor@ops-lab:<span class="path">~</span>$</span> <span class="cmd">ls</span> <span class="arg">-la</span> <span class="path">content/</span>
<table style="margin-top:11px">
<tr><th>权限</th><th>条目</th><th>数量</th><th>说明</th></tr>
<tr>
  <td class="comment">drwxr-xr-x</td>
  <td><a href="journal.html">journal/</a></td>
  <td class="num">{n_journal} 篇</td>
  <td class="comment">个人实操记录</td>
</tr>
<tr>
  <td class="comment">drwxr-xr-x</td>
  <td><a href="postmortem.html">postmortem/</a></td>
  <td class="num">{n_pm} 篇</td>
  <td class="comment">故障复盘</td>
</tr>
<tr>
  <td class="comment">drwxr-xr-x</td>
  <td><a href="system.html">system/</a></td>
  <td class="num">—</td>
  <td class="comment">技术选型与能力清单</td>
</tr>
</table>
</div>""".format(n_journal=n_journal, n_pm=n_pm))

    # 区块 3：验证数据
    blocks.append("""<div class="out">
<span class="prompt">visitor@ops-lab:<span class="path">~</span>$</span> <span class="cmd">cat</span> <span class="arg">benchmark.log</span>
<table style="margin-top:11px">
<tr><th>指标</th><th>结果</th><th>验证方式</th></tr>
<tr>
  <td>发布中断时长</td>
  <td><span class="badge ok">0 s</span></td>
  <td class="comment">25,315 请求零失败</td>
</tr>
<tr>
  <td>对照组（restart）</td>
  <td><span class="badge err">408 失败</span></td>
  <td class="comment">证明测试方法有效</td>
</tr>
<tr>
  <td>回滚耗时</td>
  <td><span class="num">1.11 s</span></td>
  <td class="comment">期间 20821 请求零失败</td>
</tr>
<tr>
  <td>限流拦截</td>
  <td><span class="num">11,547</span> 次</td>
  <td class="comment">正常用户零误伤</td>
</tr>
<tr>
  <td>SSH 爆破成功</td>
  <td><span class="badge ok">0 次</span></td>
  <td class="comment">密码认证已关闭</td>
</tr>
</table>
</div>""")

    # 区块 4：核心能力
    caps = [
        ("原子发布", "不可变发布物 + 符号链接切换，含权限预检、语法校验、健康检查与失败自动回滚"),
        ("秒级回滚", "版本目录不覆盖，回滚只需把软链接指回去并 reload"),
        ("崩溃自愈", "发现系统自带 unit 缺失 Restart 策略，用 drop-in 修复并以 SIGKILL 验证"),
        ("流量防护", "基于 20 GB/月免费额度设计：nginx 限流限连 + fail2ban 联动"),
        ("监控告警", "每日巡检 13 项 + 邮件告警（等级变化时发送，避免重复）"),
        ("数据库", "容器化部署，命名卷持久化，自动备份，崩溃恢复验证数据无丢失"),
    ]
    cap_rows = "\n".join(
        '<tr><td class="path">%s</td><td class="comment">%s</td></tr>' % (a, b)
        for a, b in caps
    )
    blocks.append("""<div class="out">
<span class="prompt">visitor@ops-lab:<span class="path">~</span>$</span> <span class="cmd">systemctl</span> <span class="arg">list-units</span> <span class="comment">--type=capability</span>
<table style="margin-top:11px">
<tr><th>能力</th><th>实现说明</th></tr>
{rows}
</table>
</div>""".format(rows=cap_rows))

    # 组装：每条命令对应一个区块
    body = []
    body.append('<div class="term-window">')
    body.append('<div class="term-bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span>'
                '<span style="margin-left:10px">{site} — ssh — 80x24</span></div>'.format(site=esc(SITE_TITLE)))
    body.append('<div style="padding:26px 24px">')

    # 欢迎行
    body.append("""<div class="out" style="margin-bottom:26px">
<span class="comment">Last login: {date} on pts/0</span>
<div style="margin-top:9px;font-size:16px">
运维练习站 — 从零搭建与运维实录<span class="cursor"></span>
</div>
</div>""".format(date="2026-09-24"))

    body.append('<span class="ascii-line">────────────────────────────────────────────────────────────</span>')

    body.extend(blocks)

    body.append('<span class="ascii-line">────────────────────────────────────────────────────────────</span>')
    body.append('<div class="out" style="margin-top:20px">')
    body.append('<span class="prompt">visitor@ops-lab:<span class="path">~</span>$</span> '
                '<span class="cmd">exit</span>')
    body.append('</div>')

    body.append('</div>')  # 结束 padding
    # tmux 状态栏
    body.append("""<div class="tmux">
<span>[<b>ops-lab</b>]</span>
<span>0:<b>home</b>*</span>
<span>{n_journal} journal</span>
<span>{n_pm} postmortem</span>
<span>uptime <b>11d</b></span>
<span>load <b>0.04</b></span>
<span>mem <b>1.2G</b></span>
</div>""".format(n_journal=n_journal, n_pm=n_pm))
    body.append('</div>')  # 结束 term-window

    return page(SITE_TITLE + " · 运维实践记录", "".join(body), style="terminal",
                nav_active="home",
                description="阿里云 ECS 2C2G 上的运维实践：零停机发布、秒级回滚、"
                            "崩溃自愈、流量防护与故障复盘", rel_root="../")



def build_journal_index(items, rel_root="../", link_prefix="journal/"):
    """我的手记 —— 列表页"""
    if not items:
        body = """<h1>我的手记</h1>
<p class="lede">这里会记录我自己动手做过的每一次实操与排障。</p>
<div class="card">
<h3>还没有内容 —— 这是你自己的区域</h3>
<p>等第一条记录写好，它就会出现在这里。</p>
<p style="ops-dark">
怎么开始：在服务器上执行<br>
<code>cat /root/ops-lab/quests/001-deploy-page.md</code><br>
领取第一个任务，做完后把手记写到 <code>/root/ops-lab/my-journal/</code>。</p>
</div>"""
        return page("我的手记 · " + SITE_TITLE, body, active="journal",
                    description="我自己的运维实操记录与排障手记", rel_root=rel_root)

    cards = []
    for it in items:
        meta = it["meta"]
        bits = "".join("<span>%s</span>" % esc(meta[k])
                       for k in ("日期", "用时", "难度") if k in meta)
        cards.append("""<div class="card">
<a class="title" href="{link_prefix}{num}-{slug}.html">{title}</a>
<div class="meta">{bits}<span>{lines} 行</span></div>
</div>""".format(num=it["num"], slug=esc(it["slug"]),
                 title=esc(it["title"]), bits=bits, lines=it["lines"],
                 link_prefix=link_prefix))

    body = """<h1>我的手记</h1>
<p class="lede">我做过的运维操作记录，每篇包含问题的现象、排查过程与解决办法。</p>
{cards}""".format(cards="".join(cards))
    return page("我的手记 · " + SITE_TITLE, body, active="journal",
                description="我自己的运维实操记录与排障手记", rel_root=rel_root)


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

    return page(it["title"] + " · " + SITE_TITLE, body, rel_root="../../",
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
<table style="ops-dark">{rows}</table>
</div>""".format(num=p["num"], slug=esc(p["slug"]),
                 title=esc(p["title"]), tag=severity_tag(meta), rows=rows))

    body = """<h1>参考复盘</h1>
<p class="lede">这 8 篇是在搭建本项目的过程中产生的技术记录，涵盖 nginx 配置、
systemd 服务管理、cron 陷阱、安全加固等主题，每篇包含现象、排查过程与验证方法。</p>

<div class="notice">
<strong>关于这部分的定位</strong>
<p>这些复盘记录的是<strong>环境搭建与调试过程中出现的技术问题</strong>，
作为复盘写作的参考样例保留在这里。</p>
<p>个人实操记录在 <a href="journal.html">我的手记</a> 中——
那里记录的是个人的部署、排障与脚本开发过程。</p>
</div>

<div class="notice notice-info">
<strong>为什么保留这些样例</strong>
<p>一份完整的复盘应该包含：现象、排查过程、根因、修复、验证、预防。
这 8 篇按这个结构写成，可以作为写作模板参考。</p>
<p>其中有两篇记录了<strong>"先判断错误、后纠正"</strong>的过程——
这类过程同样保留在记录里，因为
<strong>排查过程中走弯路是常态</strong>。</p>
</div>

{cards}""".format(cards="".join(cards))
    return page("参考复盘 · " + SITE_TITLE, body, active="pm",
                description="运维技术记录：nginx 配置、systemd 管理、安全加固", rel_root="../")


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

    return page(p["title"] + " · " + SITE_TITLE, body, rel_root="../../",
                active="pm", description=p["title"])


def build_ops_page():
    body = """<h1>运维体系</h1>
<p class="lede">站点各部分的实现方式与选择理由。</p>

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
<p><code>restart</code> 的失败数是 408 个（393 个 connection refused +
15 个 connection reset）。这一组是同时跑的对照：它说明压测方法本身
能检测到服务中断，因此 <code>reload</code> 那一组的零失败才可采信。</p>

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

<h2>日常巡检</h2>
<p>巡检的目的是在问题变成故障之前发现它。下面是每天实际执行的顺序，
从头做一遍就能确认整台服务器的状态。</p>

<h3>第一步：跑一次全量快照</h3>
<p><code>healthcheck.sh</code> 把负载、内存、磁盘、inode、失败服务、监听端口、
占用最高的进程、网卡累计流量、fail2ban、练习环境与已知偏差一次打全，
并落成一份带时间戳的快照：</p>
<pre class="language-bash"><code class="language-bash">cd /root/ops-lab
bash bin/healthcheck.sh
ls -t logs/health-*.txt | head -n 1
</code></pre>
<p>快照文件名是 <code>health-</code> 加年月日时分秒，历史快照留着用于对比。</p>

<h3>第二步：看自动巡检的结果</h3>
<p>不用手动跑也不会漏：每天 08:00 由 cron 触发
<code>lib/daily-check.py</code>，把 11 项检查跑一遍，结果写进日志。
读三个文件就能掌握现状：</p>
<pre class="language-bash"><code class="language-bash">cat logs/daily-check/latest.txt          # 最近一次巡检全文
ls -t logs/alerts/ | head -n 5            # 最近的告警文件
tail -n 50 logs/daily-check/cron.log      # 定时任务的执行记录
</code></pre>
<p>11 项检查依次是：磁盘、inode、服务、心跳、内存、负载、流量、数据库、
数据库备份、Tomcat 代理、目录占用。任何一项越界都会抬高整体等级。</p>
<p>告警不会重复轰炸：脚本把上一次的等级写在
<code>logs/daily-check.state</code>，只有等级发生变化时才发邮件。
所以邮箱里没有新信，不代表巡检没跑 —— 也可能是问题还在、等级没变。</p>

<h3>第三步：核对阈值</h3>
<p>判断越界用的是固定阈值，不是感觉：</p>
<pre><code>磁盘使用率      80%
inode 使用率    80%
可用内存        200 MB
系统负载        4.0
心跳文件年龄    180 秒
流量占日均预算  50% 警告 / 80% 严重</code></pre>
<p>这些值写成常量放在 <code>lib/daily-check.py</code> 顶部，要调只改那一处。
心跳那一项看的是 <code>/var/log/quest02.log</code> 的修改时间，而不是进程是否存在
—— 进程活着但不再干活，也是一种故障。</p>

<h3>第四步：分项排查</h3>
<p>整体巡检报出异常后，用对应的单项工具定位：</p>
<pre class="language-bash"><code class="language-bash">bash bin/f2b-status.sh                # fail2ban 是否在跑、封了多少、增长快不快
bash bin/deviation-check.sh           # 对照 docs/KNOWN-DEVIATIONS.md 审计已知偏差
python3 lib/daily-check.py --verbose  # 显示 OK 项的完整巡检
</code></pre>
<p><code>--verbose</code> 把通过的检查也打印出来，用来确认「没报错」是真的通过，
而不是检查项根本没被执行。</p>

<h3>第五步：单独看流量口径</h3>
<p>服务器按流量计费，所以流量单独看一眼。每小时与每天各跑一次
<code>bin/traffic-check.sh</code>，报告自动保留最近 48 份：</p>
<pre class="language-bash"><code class="language-bash">ls -t logs/traffic/traffic-*.txt | head -n 1 | xargs tail -n 20
</code></pre>
<p>报告里的「占日均预算」按 <code>20 GB ÷ 30 天 = 682.7 MB/天</code> 折算，
50% 警告、80% 严重。注意 <code>/proc/net/dev</code> 的计数包含内网流量，
真实计费口径要看阿里云控制台。</p>

<h2>技术选型理由</h2>
<table>
<tr><th>选择</th><th>理由</th></tr>
<tr><td>nginx 直跑静态文件</td><td>内存 14.8 MB；Node/Python 后端起步 50–100 MB</td></tr>
<tr><td>不用 Docker 跑网站</td><td>多一层 overlayfs 与网络命名空间开销，2 GB 内存要留给其他练习</td></tr>
<tr><td>不用宝塔面板</td><td>本项目目的是理解各组件的手动配置过程。面板自动生成的配置不透明，出问题时需先弄清它的生成逻辑才能定位；这类工具更适合快速建站场景，与练习目的不同</td></tr>
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
<tr><td><code>md2html.py</code></td><td>Markdown 转换器（含 XSS 防护、图片与有序列表起始编号，61 项测试）</td></tr>
</table>
"""
    return page("运维体系 · " + SITE_TITLE, body, active="ops",
                description="运维体系设计：部署架构、发布流程、限流、监控告警与技术选型", rel_root="../")


def main():
    print("=" * 60)
    print(" 静态站点生成")
    print("=" * 60)

    pms = load_postmortems()
    print("\n读取复盘文档: %d 篇" % len(pms))
    for p in pms:
        print("  #%s %s (%d 行, %d 字节)" % (
            p["num"], p["title"][:44], p["lines"], p["size"]))

    journals = load_journals()
    print("\n读取我的手记: %d 篇" % len(journals))
    for it in journals:
        print("  #%s %s (%d 行)" % (it["num"], it["title"][:40], it["lines"]))

    # ── 清空并重建输出目录 ──
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    O = OPS_SUBDIR
    D = DM_SUBDIR
    C = CC_SUBDIR
    for d in ["", O, "%s/journal" % O, "%s/postmortem" % O,
              D, "%s/img" % D, C, "%s/img" % C]:
        os.makedirs(os.path.join(BUILD_DIR, d), exist_ok=True)
    print("\n输出目录: %s" % BUILD_DIR)

    written = []

    def emit(relpath, content):
        full = os.path.join(BUILD_DIR, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        written.append((relpath, len(content.encode("utf-8"))))

    # ══ 根：项目导航首页 ══
    emit("index.html", build_home(pms, journals))

    # ══ 运维分支 ══
    # 分支首页（终端风）
    emit("%s/index.html" % O, build_ops_home(pms, journals))

    # 我的手记（列表 + 内页）
    emit("%s/journal.html" % O, build_journal_index(journals))
    # 目录索引：让 /ops/journal/ 这个路径本身可访问（否则 nginx 报 403 directory index forbidden）
    emit("%s/journal/index.html" % O,
         build_journal_index(journals, rel_root="../../", link_prefix=""))
    for k, it in enumerate(journals):
        prev_it = journals[k - 1] if k > 0 else None
        next_it = journals[k + 1] if k < len(journals) - 1 else None
        emit("%s/journal/%s-%s.html" % (O, it["num"], it["slug"]),
             build_journal_page(it, prev_it, next_it, k + 1, len(journals)))

    # 参考复盘（列表 + 内页）
    emit("%s/postmortem.html" % O, build_postmortem_index(pms))
    for p in pms:
        emit("%s/postmortem/%s-%s.html" % (O, p["num"], p["slug"]),
             build_postmortem_page(p))

    # 运维体系
    emit("%s/system.html" % O, build_ops_page())

    # ══ 数据挖掘分支 ══
    emit("%s/index.html" % D, build_dm_index())
    dm_pages = 0
    for num, name, date, key in DM_ITEMS:
        emit("%s/exp%s.html" % (D, num), build_dm_page(num, name, date, key))
        dm_pages += 1

    # 截图是二进制，直接复制，不走 emit 的文本写入
    dm_imgs = 0
    src_img = os.path.join(DM_DIR, "img")
    if os.path.isdir(src_img):
        dst_img = os.path.join(BUILD_DIR, D, "img")
        for fn in sorted(os.listdir(src_img)):
            if not fn.lower().endswith(".png"):
                continue
            shutil.copy(os.path.join(src_img, fn), os.path.join(dst_img, fn))
            dm_imgs += 1
    print("\n数据挖掘分支: %d 页, %d 张截图" % (dm_pages, dm_imgs))

    # ══ 云计算应用技术实验报告分支 ══
    emit("%s/index.html" % C, build_cc_index())
    cc_pages = 0
    for num, name, date, key in CC_ITEMS:
        emit("%s/exp%s.html" % (C, num), build_cc_page(num, name, date, key))
        cc_pages += 1

    src_img = os.path.join(CC_DIR, "img")
    cc_imgs = 0
    if os.path.isdir(src_img):
        dst_img = os.path.join(BUILD_DIR, C, "img")
        for fn in sorted(os.listdir(src_img)):
            if not fn.lower().endswith(".png"):
                continue
            shutil.copy(os.path.join(src_img, fn),
                        os.path.join(dst_img, fn))
            cc_imgs += 1
    print("\n云计算分支: %d 页, %d 张截图" % (cc_pages, cc_imgs))

    emit("robots.txt", "User-agent: *\nAllow: /\n")

    print("\n生成文件:")
    total = 0
    for name, size in written:
        print("  %-50s %7d 字节" % (name, size))
        total += size
    print("\n  合计: %d 文件, %d 字节 (%.1f KB)" % (
        len(written), total, total / 1024.0))

    # ── 体积评估（按量计费场景）──
    print("\n体积评估:")
    for name, size in written:
        if name == "index.html":
            print("  首页（项目导航）:  %.1f KB / gzip 约 %.1f KB" % (
                size / 1024.0, size * 0.35 / 1024.0))
        elif name == "%s/index.html" % O:
            print("  运维首页:          %.1f KB / gzip 约 %.1f KB" % (
                size / 1024.0, size * 0.35 / 1024.0))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
