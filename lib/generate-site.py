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

# 运维分支配目录 —— 站点是多项目结构，运维只是其中一个项目
OPS_SUBDIR = "ops"

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
.filing-sep{opacity:.45;margin:0 6px}
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
.visit-widget{position:fixed;top:16px;right:16px;z-index:50;display:flex;align-items:center;padding:7px 12px;border-radius:12px;background:rgba(255,255,255,.16);backdrop-filter:blur(12px) saturate(160%);-webkit-backdrop-filter:blur(12px) saturate(160%);border:1px solid rgba(255,255,255,.24);box-shadow:0 4px 20px rgba(0,0,0,.12);font-size:.72rem;line-height:1.3;color:rgba(255,255,255,.94);font-variant-numeric:tabular-nums;opacity:0;animation:vw-in .45s ease-out .2s forwards;pointer-events:none}
@keyframes vw-in{to{opacity:1}}
body.anime .visit-widget{background:rgba(26,26,46,.08);border-color:rgba(26,26,46,.14);color:var(--cel-ink);box-shadow:0 4px 18px rgba(26,26,46,.1)}
body.ops-dark .visit-widget,body.terminal .visit-widget{background:rgba(255,255,255,.07);border-color:rgba(255,255,255,.13);color:rgba(255,255,255,.9);box-shadow:0 4px 20px rgba(0,0,0,.3)}
.visit-widget .vw-item{display:flex;flex-direction:column;align-items:center;padding:0 8px;position:relative}
.visit-widget .vw-item+.vw-item::before{content:"";position:absolute;left:0;top:14%;height:72%;width:1px;background:currentColor;opacity:.16}
.visit-widget .vw-num{font-size:.92rem;font-weight:700;letter-spacing:-0.02em;line-height:1.15}
.visit-widget .vw-label{font-size:.56rem;letter-spacing:.1em;text-transform:uppercase;opacity:.62;margin-top:1px}
.visit-widget.vw-loading .vw-num{opacity:.35}
.visit-widget.vw-hidden{display:none}
@media(max-width:620px){.visit-widget{top:8px;right:8px;padding:5px 8px;border-radius:10px}.visit-widget .vw-item{padding:0 5px}.visit-widget .vw-num{font-size:.78rem}.visit-widget .vw-label{font-size:.52rem;letter-spacing:.05em}}

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
]


def page(title, body, rel_root="", active="", description="", style="ops-dark",
         nav_active=None, nav_mode="branch"):
    """渲染一个完整页面。rel_root 用于子目录页面回指根。"""
    # 导航分两套，不能混用：
    #   nav_mode="branch"  —— 章节导航（运维首页/我的手记/参考复盘/运维体系），
    #                         列的是"这个分支里有什么"，只出现在分支内部；
    #   nav_mode="project" —— 项目导航，列的是"有哪些项目"，
    #                         只出现在首页（工作台，它正是分支的上一层）。
    if nav_mode == "project":
        nav = [(href, label, "") for href, label in PROJECT_NAV]
    else:
        nav = [
            (OPS_SUBDIR + "/index.html", "运维首页", "home"),
            (OPS_SUBDIR + "/journal.html", "我的手记", "journal"),
            (OPS_SUBDIR + "/postmortem.html", "参考复盘", "pm"),
            (OPS_SUBDIR + "/system.html", "运维体系", "ops"),
        ]

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
    if active == "home":
        brand_html = '<a class="brand" href="%sindex.html">%s</a>' % (
            rel_root, esc(SITE_TITLE))
    else:
        brand_html = (
            '<a class="brand brand-parent" href="%sindex.html">%s</a>'
            '<span class="brand-sep">‹</span>'
            '<span class="brand-branch">运维</span>'
        ) % (rel_root, esc(SITE_TITLE))

    # 面包屑：工作台 / 运维 / [当前章节]
    # 首页不显示（它就在最顶层，没有上级）
    if active == "home":
        crumbs = ""
    else:
        c = ['<nav class="crumbs"><a href="%sindex.html">工作台</a>' % rel_root]
        c.append('<span class="sep">›</span>')
        c.append('<a href="%s%s/index.html">运维</a>' % (rel_root, OPS_SUBDIR))
        if active == "journal":
            c.append('<span class="sep">›</span><span class="cur">我的手记</span>')
        elif active == "pm":
            c.append('<span class="sep">›</span><span class="cur">参考复盘</span>')
        elif active == "ops":
            c.append('<span class="sep">›</span><span class="cur">运维体系</span>')
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
<div class="visit-widget vw-loading" id="visitWidget" data-root="{root}"><div class="vw-item"><span class="vw-num" id="vwToday">-</span><span class="vw-label">今日</span></div><div class="vw-item"><span class="vw-num" id="vwWeek">-</span><span class="vw-label">本周</span></div><div class="vw-item"><span class="vw-num" id="vwMonth">-</span><span class="vw-label">本月</span></div><div class="vw-item"><span class="vw-num" id="vwTotal">-</span><span class="vw-label">累计</span></div></div>
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
2 核 2 GB 云服务器上的运维实践。部署、排障、自动化脚本，
每篇都有真实命令输出与验证数据。
</p>
<dl class="proj-stats">
<div><dt>实操手记</dt><dd>{nj}</dd></div>
<div><dt>故障复盘</dt><dd>{np}</dd></div>
<div><dt>巡检项</dt><dd>13</dd></div>
</dl>
<a class="proj-enter" href="ops/index.html">进入分支<span class="arrow">→</span></a>
</article>""".format(nj=n_journal, np=n_pm))

    # ── 预留位 ──
    projects.append("""<article class="proj-card proj-empty">
<div class="proj-top">
<span class="proj-mark">◇</span>
<span class="proj-state state-idle">预留</span>
</div>
<h2 class="proj-name">NEXT PROJECT</h2>
<p class="proj-intro">
这个位置留给下一个项目。
</p>
<dl class="proj-stats">
<div><dt>状态</dt><dd>—</dd></div>
</dl>
<a class="proj-enter proj-enter-off" href="#" tabindex="-1">敬请期待</a>
</article>""")

    body = """<section class="hero">
<div class="hero-inner">
<div class="hero-label">PERSONAL WORKSPACE</div>
<h1 class="hero-title">项目工作台</h1>
<p class="hero-desc">长期在做的几件事，各成一块</p>
</div>
</section>

<section class="proj-grid">
{cards}
</section>

<section class="colophon">
<h2>关于这里</h2>
<p>
一个持续更新的个人项目站点。每个项目独立成块，有自己的入口、
自己的风格，也有自己的记录。
</p>
<p class="colophon-note">
部署在一台 2 核 2 GB 云服务器上，由自研 Python 静态生成器构建，
零第三方依赖。
</p>
</section>""".format(cards="".join(projects))

    return page("项目工作台", body, active="home", style="anime",
                nav_mode="project",
                description="个人项目工作台 —— 记录长期在做的项目与实践过程")


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
从零搭建并运维一个静态技术展示站。
</div>
<div style="margin-top:10px" class="comment">
# 重点不在页面本身，而在它背后的运维体系 —— 发布流程、回滚机制、
# 崩溃自愈、流量防护、监控告警，以及每一项能力的验证证据。
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



def build_journal_index(items):
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
                    description="我自己的运维实操记录与排障手记", rel_root="../")

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
<p class="lede">个人实操记录 —— 踩过的坑、排查过程、修复方法。</p>
{cards}""".format(cards="".join(cards))
    return page("我的手记 · " + SITE_TITLE, body, active="journal",
                description="我自己的运维实操记录与排障手记", rel_root="../")


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
<p class="lede">这 8 篇是在搭建本项目过程中产生的技术记录，
涵盖 nginx 配置、systemd 服务管理、cron 陷阱、安全加固等主题。
每篇包含完整的现象描述、排查路径与验证方法。</p>

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
这类内容比只写成功结果的记录更有参考价值，因为
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
<tr><td><code>md2html.py</code></td><td>Markdown 转换器（含 XSS 防护，55 项测试）</td></tr>
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
    for d in ["", O, "%s/journal" % O, "%s/postmortem" % O]:
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
