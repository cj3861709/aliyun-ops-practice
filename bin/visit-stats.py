#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visit-stats.py —— 从 nginx 日志统计「独立访客」，生成静态 JSON

口径修订说明（2026-10-08）
--------------------------
此前统计的是「状态码 200 的页面请求数」。这个数字被一台扫描器刷到了四位数：
2026-10-08 单日 7386 次 200 来自同一个 IP（美国 Iowa 的 VPS），
路径形如 /?X-UIDH、/?X-Forwarded-Host —— 全部是 HTTP 头注入探测。
旧口径里这些请求既不是爬虫 UA（它伪装成浏览器）、又「像页面」（没剥 query），
于是被原样计入，挂件上就显示成了一个不像简历数据的数字。

新口径：**独立访客数**（unique visitors），规则如下：

  1. 只统计状态码 200 的 GET / HEAD
  2. 请求路径先剥掉 query string，再比对「站点真实存在的页面」白名单
     —— 白名单由 site/build 下生成出来的 *.html 现场枚举，不靠后缀猜
  3. 带未知 query 的请求不计入（站点自身链接都不带参数；
     只放行 utm_* / v= / ver= / from= / ref= / source= 这类正常来源标记）
  4. 排除爬虫与扫描器 UA，含伪装成浏览器的（BuiltWith、HeadlessChrome、空 UA…）
  5. 排除回环与私网地址（本机健康检查、内网探测）
  6. 去重：同一天同一 IP 只算 1 个访客，看多少页都算 1 个

输出 today / week / month / total 均为独立访客数；
另附 views（同样过滤后的页面请求数）与 meta（各项过滤计数）备查。

环境变量（仅本地测试用，生产走默认值）：
    VISIT_BUILD_DIR / VISIT_LOG_GLOB / VISIT_OUTPUT

用法：
    python3 visit-stats.py                 # 输出 JSON 到 stdout
    python3 visit-stats.py --output FILE   # 写入文件
"""

import datetime
import glob
import gzip
import ipaddress
import json
import os
import re
import sys
from urllib.parse import quote

BUILD_DIR = os.environ.get("VISIT_BUILD_DIR", "/root/ops-lab/site/build")
LOG_GLOB = os.environ.get("VISIT_LOG_GLOB",
                          "/var/log/nginx/personal-site.access.log*")
OUTPUT = os.environ.get("VISIT_OUTPUT", "/var/www/stats/visits.json")

# nginx main 格式
LINE_RE = re.compile(
    r'^(?P<ip>\S+)\s+\S+\s+\S+\s+'
    r'\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<req>[^"]*)"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<bytes>\d+|-)\s+'
    r'"(?P<ref>[^"]*)"\s+'
    r'"(?P<ua>[^"]*)"'
)

TIME_RE = re.compile(r'^(?P<d>\d{2})/(?P<mon>[A-Za-z]{3})/(?P<y>\d{4}):(?P<t>[\d:]+)')
MONTHS = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
          'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}

REQ_RE = re.compile(r'^(?P<method>[A-Z]+)\s+(?P<target>\S+)')

# 爬虫 / 扫描器 UA 特征（命中即排除）
# 最后两条不是厂商特征，而是行为特征：空 UA 与 ZZ; Linux。
BOT_UA = re.compile(
    r'zgrab|masscan|nmap|Censys|InternetMeasurement|MGLNDD|'
    r'Go-http-client|python-requests|python-urllib|libwww-perl|'
    r'curl/|Wget/|Nuclei|'
    r'BuiltWith|HeadlessChrome|PhantomJS|'
    r'bot|crawler|crawl|spider|slurp|'
    r'facebookexternalhit|TelegramBot|WhatsApp|Discordbot|Slackbot|'
    r'UptimeRobot|Pingdom|StatusCake|Site24x7|Datadog|Zabbix|Nagios|'
    r'cc-ext-verify|dm-style-extcheck|'
    r'ZZ; Linux|'
    r'(?:^|")(?:SSH-|-)$',
    re.IGNORECASE
)

# 正常来源标记（带这些 query 的访问仍算数）
BENIGN_QUERY = re.compile(r'^(?:utm_[a-z]+|v|ver|version|from|ref|source)=',
                          re.IGNORECASE)

# 白名单不可用时的兜底判据（只在这时才用，避免统计静默清零）
STATIC_EXT = re.compile(r'\.(css|js|jpg|jpeg|png|gif|ico|svg|webp|woff2?|ttf|map)$',
                        re.IGNORECASE)


def parse_time(s):
    m = TIME_RE.match(s)
    if not m:
        return None
    try:
        return datetime.datetime(
            int(m.group('y')), MONTHS[m.group('mon')], int(m.group('d')),
            *[int(x) for x in m.group('t').split(':')]
        )
    except (KeyError, ValueError):
        return None


def _variants(u):
    """同一路径在日志里的三种形态。

    nginx 记的是客户端发来的原始请求行，非 ASCII 路径可能是
    百分号编码（%E4%B8%8E），也可能是未编码的原始 UTF-8 字节。
    读日志时按 latin-1 解码（永不失败），所以白名单也要准备同样的形态。
    """
    return {u, quote(u, safe="/"), u.encode("utf-8").decode("latin-1")}


def load_pages(build_dir):
    """从构建产物里枚举真实存在的页面路径。"""
    pages = set()
    if not os.path.isdir(build_dir):
        return pages
    for root, _dirs, files in os.walk(build_dir):
        for fn in files:
            if not fn.endswith(".html"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), build_dir)
            rel = rel.replace(os.sep, "/")
            u = "/" + rel
            pages |= _variants(u)
            if rel == "index.html":
                pages |= _variants("/")
            elif rel.endswith("/index.html"):
                d = u[:-len("index.html")]
                pages |= _variants(d)
    return pages


def fallback_is_page(path):
    if not path or path == '/':
        return True
    if STATIC_EXT.search(path):
        return False
    if path.startswith('/.well-known/'):
        return False
    if path in ('/robots.txt', '/favicon.ico'):
        return False
    return True


def is_internal(ip):
    """回环 / 私网 / 保留地址 —— 不可能是外部访客。"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True          # 解析不出的（含 XFF 串）一律不计
    return bool(a.is_loopback or a.is_private or a.is_link_local
                or a.is_reserved or a.is_multicast or a.is_unspecified)


def iter_log_lines():
    """遍历所有日志文件（含 .gz）。latin-1 解码，保留原始字节。"""
    for f in sorted(glob.glob(LOG_GLOB)):
        opener = gzip.open if f.endswith('.gz') else open
        try:
            with opener(f, 'rt', encoding='latin-1', errors='replace') as fh:
                for line in fh:
                    yield line
        except Exception:
            continue


def uniq_visitors(day_visitors, pred):
    s = set()
    for d, ips in day_visitors.items():
        if pred(d):
            s |= ips
    return len(s)


def sum_views(day_views, pred):
    return sum(n for d, n in day_views.items() if pred(d))


def main():
    now = datetime.datetime.now()
    today = now.date()
    week_start = today - datetime.timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    pages = load_pages(BUILD_DIR)
    have_wl = bool(pages)

    day_visitors = {}
    day_views = {}
    n_status = n_ua = n_path = n_query = 0

    for line in iter_log_lines():
        m = LINE_RE.match(line.rstrip('\n'))
        if not m:
            continue

        if m.group('status') != '200':
            n_status += 1
            continue

        rm = REQ_RE.match(m.group('req'))
        if not rm or rm.group('method') not in ('GET', 'HEAD'):
            n_path += 1
            continue

        target = rm.group('target')
        if '?' in target:
            path, _, query = target.partition('?')
        else:
            path, query = target, ''

        if query and not BENIGN_QUERY.match(query):
            n_query += 1
            continue

        if have_wl:
            if path not in pages:
                n_path += 1
                continue
        elif not fallback_is_page(path):
            n_path += 1
            continue

        ip = m.group('ip')
        ua = m.group('ua')
        if is_internal(ip) or BOT_UA.search(ua):
            n_ua += 1
            continue

        dt = parse_time(m.group('time'))
        if dt is None:
            continue

        d = dt.date()
        day_visitors.setdefault(d, set()).add(ip)
        day_views[d] = day_views.get(d, 0) + 1

    p_today = lambda d: d == today
    p_week = lambda d: d >= week_start
    p_month = lambda d: d >= month_start
    p_all = lambda d: True

    v = {k: uniq_visitors(day_visitors, p) for k, p in
         (('today', p_today), ('week', p_week),
          ('month', p_month), ('total', p_all))}
    w = {k: sum_views(day_views, p) for k, p in
         (('today', p_today), ('week', p_week),
          ('month', p_month), ('total', p_all))}

    data = {
        "today": v['today'],
        "week": v['week'],
        "month": v['month'],
        "total": v['total'],
        "views": {"today": w['today'], "week": w['week'],
                  "month": w['month'], "total": w['total']},
        "meta": {
            "scope": "unique-visitors",
            "days": len(day_visitors),
            "since": min(day_visitors).isoformat() if day_visitors else None,
            "until": max(day_visitors).isoformat() if day_visitors else None,
            "whitelist_pages": len(pages),
            "filtered": {
                "status": n_status,
                "bot_or_internal": n_ua,
                "path_or_method": n_path,
                "query": n_query,
            },
        },
        "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 默认写 VISIT_OUTPUT（生产即 /var/www/stats/visits.json）；
    # --stdout 强制只打印不落盘，便于本地查看口径细节。
    out = OUTPUT
    if '--output' in sys.argv:
        out = sys.argv[sys.argv.index('--output') + 1]
    if '--stdout' in sys.argv:
        out = ''
    text = json.dumps(data, ensure_ascii=False, indent=2)

    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        tmp = out + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, out)
        print("已写入 %s" % out)
    else:
        print(text)

    print("访问统计口径：独立访客（同一天同一 IP 只计一次）")
    print("  页面白名单 %d 条；覆盖 %s ~ %s，共 %d 天" % (
        len(pages), data['meta']['since'], data['meta']['until'],
        data['meta']['days']))
    print("  今日 %d / 本周 %d / 本月 %d / 累计 %d" % (
        v['today'], v['week'], v['month'], v['total']))
    print("  页面浏览（同样过滤后）：今日 %d / 累计 %d" % (w['today'], w['total']))
    print("  过滤：非 200 %d、爬虫或内网 %d、非白名单路径或非 GET %d、可疑 query %d" % (
        n_status, n_ua, n_path, n_query))
    if not have_wl:
        print("  ⚠️ 未找到构建产物目录 %s —— 已退回后缀启发式判据" % BUILD_DIR)
    return 0


if __name__ == '__main__':
    sys.exit(main())
