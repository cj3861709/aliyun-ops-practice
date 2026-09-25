#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visit-stats.py —— 从 nginx 日志统计访问量，生成静态 JSON

设计要点：

  1. 【过滤扫描流量】
     服务器每天被扫描器打几十次（400/404/429）。
     不过滤的话计数会被刷成无意义的数字。
     过滤规则：
       · 状态码必须是 200
       · User-Agent 不含已知扫描器特征
       · 请求路径是页面（.html 或无扩展名），不含静态资源

  2. 【四个维度】
     今日 / 本周 / 本月 / 累计

  3. 【零依赖】
     只用 Python 标准库。运行环境 Python 3.6。

  4. 【增量友好】
     读当前日志 + 轮转后的 .gz，时间范围内去重由日志本身保证
     （每条记录只存在于一个文件里）

用法：
    python3 visit-stats.py                 # 输出 JSON 到 stdout
    python3 visit-stats.py --output FILE   # 写入文件
"""

import datetime
import glob
import gzip
import json
import os
import re
import sys
from collections import Counter

LOG_GLOB = "/var/log/nginx/personal-site.access.log*"
OUTPUT = "/var/www/stats/visits.json"

# 扫描器 UA 特征（命中即排除）
SCANNER_UA = re.compile(
    r'zgrab|masscan|nmap|Censys|InternetMeasurement|MGLNDD|'
    r'Go-http-client|python-requests|libwww-perl|curl/|Wget/|'
    r'Nuclei|nuclei|'
    r'^SSH-|'
    r'^-$|^$',
    re.IGNORECASE
)

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
MONTHS = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
          'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}

REQ_RE = re.compile(r'^[A-Z]+\s+(?P<path>\S+)')

# 静态资源后缀（不计入页面访问）
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


def is_page_request(path):
    """判断是否算一次「页面访问」"""
    if not path or path == '/':
        return True
    if STATIC_EXT.search(path):
        return False
    if path.startswith('/.well-known/'):
        return False
    if path in ('/robots.txt', '/favicon.ico'):
        return False
    return True


def iter_log_lines():
    """遍历所有日志文件（含 .gz）"""
    files = sorted(glob.glob(LOG_GLOB))
    for f in files:
        try:
            if f.endswith('.gz'):
                with gzip.open(f, 'rt', errors='replace') as fh:
                    for line in fh:
                        yield line
            else:
                with open(f, 'r', errors='replace') as fh:
                    for line in fh:
                        yield line
        except Exception:
            continue


def main():
    now = datetime.datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - datetime.timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)

    total = 0
    n_today = 0
    n_week = 0
    n_month = 0
    n_scanner_filtered = 0

    for line in iter_log_lines():
        m = LINE_RE.match(line.rstrip('\n'))
        if not m:
            continue

        # 只统计 200
        if m.group('status') != '200':
            continue

        # 排除扫描器
        ua = m.group('ua')
        if SCANNER_UA.search(ua):
            n_scanner_filtered += 1
            continue

        # 只统计页面
        rm = REQ_RE.match(m.group('req'))
        if not rm or not is_page_request(rm.group('path')):
            continue

        dt = parse_time(m.group('time'))
        if dt is None:
            continue

        total += 1
        if dt >= month_start:
            n_month += 1
        if dt >= week_start:
            n_week += 1
        if dt >= today_start:
            n_today += 1

    data = {
        "today": n_today,
        "week": n_week,
        "month": n_month,
        "total": total,
        "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
    }

    out = sys.argv[sys.argv.index('--output') + 1] if '--output' in sys.argv else None
    text = json.dumps(data, ensure_ascii=False, indent=2)

    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        # 原子写入：先写临时文件再 rename
        tmp = out + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, out)
        print("已写入 %s" % out)
        print("  今日 %d / 本周 %d / 本月 %d / 累计 %d" % (
            n_today, n_week, n_month, total))
        print("  过滤扫描器请求 %d 条" % n_scanner_filtered)
    else:
        print(text)

    return 0


if __name__ == '__main__':
    sys.exit(main())
