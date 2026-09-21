#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
log-analysis.py —— nginx 访问日志分析器

用法：
    python3 log-analysis.py                           # 分析默认路径
    python3 log-analysis.py /var/log/nginx/xxx.log     # 分析指定文件
    python3 log-analysis.py '/var/log/nginx/*.log*'    # 支持通配符

功能：
    1. 状态码分布统计
    2. 请求最多的客户端 IP
    3. 404 探测路径统计
    4. 429 限流分析
    5. 扫描器特征识别
    6. 支持 .gz 压缩日志

设计说明（几个关键决策）：

  【为什么用正则而不是 split()】
    nginx main 格式的 $request 字段含空格（"GET /path HTTP/1.1"），
    按空格切分会导致字段错位。必须按引号边界匹配。

  【为什么读 gz 用 gzip.open(mode='rt')】
    'rt' 表示「文本模式读取」，Python 会自动解码为 str，
    省去手工 decode()。Python 3.6+ 支持。

  【为什么匹配失败只计数不中断】
    日志里存在协议错配产生的畸形行（TLS 握手发到 HTTP 端口、
    SSH banner 发到 HTTP 端口）。这些是有效数据的一部分，
    不应该让整个分析失败。

  【扫描器识别为什么用多特征打分而不是单一规则】
    单个特征误报率高：
      · 只有 UA 可疑  → 可能是正常的自动化脚本
      · 只有 404 多   → 可能是链接失效
    多个特征同时命中才判定为扫描器，降低误报。

运行环境：Python 3.6+
依赖：仅标准库（re / gzip / glob / os / sys / collections / datetime）
"""

import datetime
import glob
import gzip
import os
import re
import sys
from collections import Counter, defaultdict

# ==============================================================================
# 配置区
# ==============================================================================

DEFAULT_PATTERN = "/var/log/nginx/personal-site.access.log*"

# 单文件行数上限（防止误传大文件导致内存/时间失控）
MAX_LINES_PER_FILE = 500000

# ------------------------------------------------------------------------------
# nginx main 日志格式的正则
# ------------------------------------------------------------------------------
LINE_RE = re.compile(
    r'^(?P<ip>\S+)\s+\S+\s+\S+\s+'
    r'\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<req>[^"]*)"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<bytes>\d+|-)\s+'
    r'"(?P<ref>[^"]*)"\s+'
    r'"(?P<ua>[^"]*)"'
)

# 正常的 HTTP 请求行：方法 路径 协议
REQ_RE = re.compile(r'^(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+(?P<proto>HTTP/[\d.]+)$')

# nginx 默认的 time_local 格式：21/Sep/2026:03:58:08 +0800
TIME_RE = re.compile(r'^(?P<d>\d{2})/(?P<mon>[A-Za-z]{3})/(?P<y>\d{4}):(?P<t>[\d:]+)')

MONTHS = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}


def parse_time(tstr):
    """把 nginx 时间字符串解析成 datetime，失败返回 None"""
    m = TIME_RE.match(tstr)
    if not m:
        return None
    try:
        return datetime.datetime(
            int(m.group('y')), MONTHS[m.group('mon')], int(m.group('d')),
            *[int(x) for x in m.group('t').split(':')]
        )
    except (KeyError, ValueError):
        return None

# ------------------------------------------------------------------------------
# 扫描器特征库
# ------------------------------------------------------------------------------

# 已知扫描器 / 自动化工具的 User-Agent 特征
SCANNER_UA_PATTERNS = [
    (r'Go-http-client', 'Go HTTP 客户端'),
    (r'^curl/', 'curl'),
    (r'^Wget/', 'wget'),
    (r'python-requests', 'python-requests'),
    (r'libwww-perl', 'libwww-perl'),
    (r'masscan', 'masscan'),
    (r'zgrab', 'zgrab'),
    (r'nmap', 'nmap'),
    (r'Nuclei', 'Nuclei 漏洞扫描器'),
    (r'Censys', 'Censys 测绘'),
    (r'InternetMeasurement', '互联网测量'),
    (r'MGLNDD', 'MGLNDD 端口扫描'),
    (r'^SSH-', 'SSH 客户端（错发到 HTTP）'),
    (r'^$|^-$', '无 User-Agent'),
]

# 攻击者常探测的敏感路径
SENSITIVE_PATH_PATTERNS = [
    (r'^/\.env', '环境变量文件'),
    (r'^/\.git', 'Git 仓库'),
    (r'^/\.aws', 'AWS 凭据'),
    (r'^/\.ssh', 'SSH 目录'),
    (r'^/wp-admin|^/wp-login|^/wp-content', 'WordPress'),
    (r'^/phpmyadmin|^/pma', 'phpMyAdmin'),
    (r'^/admin|^/manager|^/console', '管理后台'),
    (r'^/login|^/signin', '登录页'),
    (r'^/SDK/', 'SDK 接口探测'),
    (r'^/actuator', 'Spring Actuator'),
    (r'^/config|^/backup|^/dump', '配置/备份文件'),
    (r'\.php$', 'PHP 脚本'),
    (r'\.sql$|\.bak$|\.zip$|\.tar', '备份文件'),
    (r'^/cgi-bin', 'CGI'),
    (r'^/shell|^/cmd|^/exec', '命令执行'),
]

# 协议错配的特征（nginx 日志把不可打印字符转义成 \xXX 字面量）
PROTOCOL_ANOMALY_PATTERNS = [
    (r'^\\x16\\x03', 'TLS 握手发到 HTTP 端口'),
    (r'^\\x03\\x00', 'TLS 记录层数据'),
    (r'^\\x00', '空字节开头'),
    (r'^SSH-', 'SSH 协议发到 HTTP 端口'),
    (r'^\\x80', '非 ASCII 字节流'),
]

# ==============================================================================
# 输出辅助
# ==============================================================================

COLOR = sys.stdout.isatty()


def bold(s):
    return '\033[1m' + s + '\033[0m' if COLOR else s


def dim(s):
    return '\033[2m' + s + '\033[0m' if COLOR else s


def red(s):
    return '\033[31m' + s + '\033[0m' if COLOR else s


def yellow(s):
    return '\033[33m' + s + '\033[0m' if COLOR else s


def section(title):
    print()
    print(bold('── %s ' % title).ljust(62, '─'))


def bar(count, total, width=24):
    """生成一个简单的条形图，用于直观对比数量"""
    if total <= 0:
        return ''
    n = int(round(count / total * width))
    n = max(1, min(n, width)) if count > 0 else 0
    return '▏' + '█' * n


# ==============================================================================
# 数据模型
# ==============================================================================

class LogStats(object):
    """汇总所有日志行的统计结果"""

    def __init__(self):
        self.files = []                  # [(path, lines, parsed, skipped)]
        self.total_lines = 0
        self.parsed = 0
        self.skipped = 0

        self.status = Counter()          # 状态码 -> 次数
        self.ip_total = Counter()        # IP -> 请求数
        self.ip_status = defaultdict(Counter)   # IP -> 状态码 Counter
        self.ip_ua = defaultdict(Counter)       # IP -> UA Counter

        self.path_404 = Counter()        # 404 路径 -> 次数
        self.path_all = Counter()        # 所有路径 -> 次数
        self.ip_429 = Counter()          # 429 IP -> 次数

        self.time_min = None
        self.time_max = None
        self.time_min_raw = None
        self.time_max_raw = None

        # 异常特征：IP -> 特征名 -> 次数
        self.ip_anomaly = defaultdict(Counter)
        # 全局特征计数
        self.anomaly_total = Counter()

        self.bad_lines = []              # [(文件, 行号, 内容摘要)]

    def add_anomaly(self, ip, feature):
        self.ip_anomaly[ip][feature] += 1
        self.anomaly_total[feature] += 1


# ==============================================================================
# 日志读取
# ==============================================================================

def open_log(path):
    """打开日志文件（自动处理 .gz）"""
    if path.endswith('.gz'):
        # 'rt' = 文本模式读取，Python 自动解码
        return gzip.open(path, 'rt', errors='replace')
    return open(path, 'r', errors='replace')


def collect_files(pattern):
    """展开通配符，返回日志文件列表"""
    files = sorted(glob.glob(pattern))
    return [f for f in files if os.path.isfile(f)]


# ==============================================================================
# 分类函数
# ==============================================================================

def classify_request(req):
    """
    对 $request 字段分类。

    返回 (类别, 路径)
      类别: http / empty / mismatch / malformed
      路径: 仅在类别为 http 时有值，否则为 None
    """
    if req == '':
        return 'empty', None

    # 协议错配检查（先于 HTTP 格式检查，因为这些不是合法 HTTP 请求）
    for pat, _ in PROTOCOL_ANOMALY_PATTERNS:
        if re.match(pat, req):
            return 'mismatch', None

    m = REQ_RE.match(req)
    if m:
        return 'http', m.group('path')

    return 'malformed', None


def match_scanner_ua(ua):
    """返回匹配到的扫描器 UA 描述，无匹配返回 None"""
    for pat, desc in SCANNER_UA_PATTERNS:
        if re.search(pat, ua, re.IGNORECASE):
            return desc
    return None


def match_sensitive_path(path):
    """返回匹配到的敏感路径类型，无匹配返回 None"""
    for pat, desc in SENSITIVE_PATH_PATTERNS:
        if re.search(pat, path, re.IGNORECASE):
            return desc
    return None


# ==============================================================================
# 主分析流程
# ==============================================================================

def analyze_file(path, stats):
    """分析单个日志文件，结果累加到 stats"""
    lines = parsed = skipped = 0

    try:
        fh = open_log(path)
    except Exception as e:
        stats.files.append((path, 0, 0, 0, str(e)))
        return

    with fh:
        for lineno, raw in enumerate(fh, 1):
            lines += 1
            if lines > MAX_LINES_PER_FILE:
                break

            line = raw.rstrip('\n')
            m = LINE_RE.match(line)
            if not m:
                skipped += 1
                if len(stats.bad_lines) < 10:
                    stats.bad_lines.append((path, lineno, line[:100]))
                continue

            parsed += 1
            ip = m.group('ip')
            status = m.group('status')
            req = m.group('req')
            ua = m.group('ua')
            tstr = m.group('time')

            # ── 基础统计 ──
            stats.status[status] += 1
            stats.ip_total[ip] += 1
            stats.ip_status[ip][status] += 1
            stats.ip_ua[ip][ua] += 1

            # ── 时间范围 ──
            # 注意：不能用「第一行」和「最后一行」，因为文件是按文件名排序的，
            # 而文件名顺序不等于时间顺序（如 access.log 早于 access.log-20260915.gz）。
            # 正确做法是取所有行中的最小/最大时间。
            dt = parse_time(tstr)
            if dt is not None:
                if stats.time_min is None or dt < stats.time_min:
                    stats.time_min = dt
                if stats.time_max is None or dt > stats.time_max:
                    stats.time_max = dt
            # 保留原始字符串供无法解析时回退显示
            if stats.time_min_raw is None:
                stats.time_min_raw = tstr
            stats.time_max_raw = tstr

            # ── 请求分类 ──
            # 注意变量命名：这里用 req_path 而不是 path，
            # 因为 path 是函数参数（日志文件路径），不能被覆盖。
            # （v2 初版就是踩了这个坑：path 被 URL 覆盖，导致文件统计表全错）
            kind, req_path = classify_request(req)

            if kind == 'empty':
                stats.add_anomaly(ip, '空请求（裸连接）')
            elif kind == 'mismatch':
                for pat, desc in PROTOCOL_ANOMALY_PATTERNS:
                    if re.match(pat, req):
                        stats.add_anomaly(ip, desc)
                        break
            elif kind == 'malformed':
                stats.add_anomaly(ip, '畸形请求行')
            else:
                stats.path_all[req_path] += 1
                if status == '404':
                    stats.path_404[req_path] += 1
                sp = match_sensitive_path(req_path)
                if sp:
                    stats.add_anomaly(ip, '探测敏感路径：%s' % sp)

            # ── 限流 ──
            if status == '429':
                stats.ip_429[ip] += 1

            # ── UA 特征 ──
            sua = match_scanner_ua(ua)
            if sua:
                stats.add_anomaly(ip, '自动化客户端：%s' % sua)

    stats.total_lines += lines
    stats.parsed += parsed
    stats.skipped += skipped
    stats.files.append((path, lines, parsed, skipped, None))


def run(pattern):
    stats = LogStats()

    files = collect_files(pattern)
    if not files:
        print(red('未找到匹配的日志文件：%s' % pattern))
        return 1

    for f in files:
        analyze_file(f, stats)

    # ==========================================================================
    # 输出报告
    # ==========================================================================
    print()
    print('=' * 62)
    print(' nginx 访问日志分析')
    print('=' * 62)

    # ── 1. 文件概览 ──
    section('1. 日志文件')
    print('  %-52s %8s %8s %6s' % ('文件', '行数', '已解析', '跳过'))
    print('  ' + '-' * 78)
    for item in stats.files:
        path, lines, parsed, skipped = item[0], item[1], item[2], item[3]
        err = item[4] if len(item) > 4 else None
        name = os.path.basename(path)
        if len(name) > 50:
            name = name[:47] + '...'
        if err:
            print('  %-52s %s' % (name, red('读取失败: %s' % err)))
        else:
            print('  %-52s %8d %8d %6d' % (name, lines, parsed, skipped))
    print('  ' + '-' * 78)
    print('  %-52s %8d %8d %6d' % ('合计', stats.total_lines, stats.parsed, stats.skipped))

    if stats.skipped:
        print()
        print('  %s' % yellow('未能解析的行（最多显示 10 条）：'))
        for path, lineno, content in stats.bad_lines:
            print('    %s:%d' % (os.path.basename(path), lineno))
            print('      %s' % content)

    if stats.time_min and stats.time_max:
        span = stats.time_max - stats.time_min
        print()
        print('  时间范围：%s  →  %s' % (
            stats.time_min.strftime('%Y-%m-%d %H:%M:%S'),
            stats.time_max.strftime('%Y-%m-%d %H:%M:%S')))
        days = span.days
        hours = span.seconds // 3600
        if days > 0:
            print('  跨度：    %d 天 %d 小时' % (days, hours))
        else:
            print('  跨度：    %d 小时 %d 分' % (hours, (span.seconds % 3600) // 60))
    elif stats.time_min_raw:
        print()
        print('  时间范围：%s  →  %s' % (stats.time_min_raw, stats.time_max_raw))

    # ── 2. 状态码分布 ──
    section('2. 状态码分布')
    if not stats.status:
        print('  （无数据）')
    else:
        total = sum(stats.status.values())
        for status, n in stats.status.most_common():
            pct = n / total * 100
            mark = ''
            if status.startswith('4') or status.startswith('5'):
                mark = dim('  ← 客户端/服务端错误')
            print('  %-5s %6d 次  %5.1f%%  %s%s' % (
                status, n, pct, bar(n, total), mark))

    # ── 3. TOP IP ──
    section('3. 请求最多的客户端 IP')
    if not stats.ip_total:
        print('  （无数据）')
    else:
        for ip, n in stats.ip_total.most_common(10):
            codes = stats.ip_status[ip]
            # 该 IP 的 404 占比
            n404 = codes.get('404', 0)
            r404 = n404 / n * 100 if n else 0
            note = ''
            if ip.startswith('127.'):
                note = dim('本机')
            elif r404 >= 50:
                note = yellow('404 占比 %.0f%%' % r404)
            print('  %-18s %6d 次  %s  %s' % (ip, n, bar(n, stats.ip_total.most_common(1)[0][1]), note))

    # ── 4. 404 探测路径 ──
    section('4. 404 路径统计（探测行为特征）')
    if not stats.path_404:
        print('  未发现 404 响应')
    else:
        total404 = sum(stats.path_404.values())
        print('  404 响应共 %d 次，涉及 %d 个不同路径' % (total404, len(stats.path_404)))
        print()
        for path, n in stats.path_404.most_common(15):
            sp = match_sensitive_path(path)
            note = yellow('  ← %s' % sp) if sp else ''
            p = path if len(path) <= 40 else path[:37] + '...'
            print('  %-42s %5d 次%s' % (p, n, note))

    # ── 5. 限流分析 ──
    section('5. 限流情况（HTTP 429）')
    n429 = stats.status.get('429', 0)
    if n429 == 0:
        print('  未发现 429 响应')
        print(dim('  （说明当前流量未触发 nginx limit_req / limit_conn 阈值）'))
    else:
        # 区分本机流量和外部流量 —— 否则压测数据会掩盖真实情况
        local_429 = sum(n for ip, n in stats.ip_429.items() if ip.startswith('127.'))
        extern_429 = n429 - local_429

        print('  被限流请求共 %d 次' % n429)
        print('    · 本机（压测/测试）: %6d 次' % local_429)
        print('    · 外部客户端:        %6d 次' % extern_429)
        print()

        if extern_429 == 0:
            print(yellow('  注意：全部限流来自本机，属于压测流量。'))
            print(dim('        当前没有外部 IP 触发限流阈值。'))
            print()
        else:
            print('  %s' % yellow('外部 IP 触发了限流：'))
            print()

        print('  %-18s %8s  %s' % ('IP', '次数', '说明'))
        print('  ' + '-' * 46)
        for ip, n in stats.ip_429.most_common(10):
            note = dim('本机') if ip.startswith('127.') else yellow('外部')
            print('  %-18s %8d  %s' % (ip, n, note))

    # ── 6. 扫描器识别 ──
    section('6. 扫描器特征识别')
    if not stats.anomaly_total:
        print('  未发现异常特征')
    else:
        # 按特征类型汇总
        by_kind = Counter()
        for ip, feats in stats.ip_anomaly.items():
            for feat, n in feats.items():
                by_kind[feat] += n

        print('  %-46s %6s %8s' % ('特征', '次数', '涉及 IP'))
        print('  ' + '-' * 62)
        for feat, n in by_kind.most_common():
            nips = sum(1 for ip, f in stats.ip_anomaly.items() if feat in f)
            f2 = feat if len(feat) <= 44 else feat[:41] + '...'
            print('  %-46s %6d %8d' % (f2, n, nips))

        # ── 可疑 IP 打分 ──
        print()
        print(bold('  可疑 IP 排行（多特征打分）'))
        print(dim('  评分依据：404 占比、自动化 UA、协议错配、敏感路径探测'))
        print()

        scored = []
        for ip, n in stats.ip_total.items():
            if ip.startswith('127.'):
                continue

            score = 0
            reasons = []

            codes = stats.ip_status[ip]
            n404 = codes.get('404', 0)
            ratio = n404 / n if n else 0
            if n >= 3 and ratio >= 0.5:
                score += 2
                reasons.append('404 占比 %.0f%%' % (ratio * 100))
            elif n404 > 0:
                score += 1
                reasons.append('含 404')

            feats = stats.ip_anomaly.get(ip, Counter())
            has_proto = any('协议' in k or 'TLS' in k or 'SSH' in k or '字节' in k for k in feats)
            has_ua = any('客户端' in k for k in feats)
            has_sens = any('敏感路径' in k for k in feats)

            if has_proto:
                score += 2
                reasons.append('协议错配')
            if has_ua:
                score += 1
                reasons.append('自动化 UA')
            if has_sens:
                score += 1
                reasons.append('探测敏感路径')

            if score > 0:
                scored.append((score, ip, n, reasons))

        scored.sort(key=lambda x: (-x[0], -x[2]))

        if not scored:
            print('  无')
        else:
            for score, ip, n, reasons in scored[:10]:
                level = red('高') if score >= 4 else (yellow('中') if score >= 2 else dim('低'))
                print('  [%s] %-18s %5d 次请求   %s' % (
                    level, ip, n, '、'.join(reasons)))

    # ── 汇总结论 ──
    print()
    print('=' * 62)
    print(bold(' 结论'))
    print('=' * 62)

    total = sum(stats.status.values())
    err4 = sum(n for s, n in stats.status.items() if s.startswith('4'))
    err5 = sum(n for s, n in stats.status.items() if s.startswith('5'))
    ok2 = sum(n for s, n in stats.status.items() if s.startswith('2'))

    print()
    print('  正常响应 (2xx)：%6d 次  %.1f%%' % (ok2, ok2 / total * 100 if total else 0))
    print('  客户端错误 (4xx)：%6d 次  %.1f%%' % (err4, err4 / total * 100 if total else 0))
    print('  服务端错误 (5xx)：%6d 次  %.1f%%' % (err5, err5 / total * 100 if total else 0))
    print()

    if total and err4 / total > 0.3:
        print('  %s' % yellow('4xx 占比超过 30% —— 存在明显的扫描或探测活动'))

    extern_429 = sum(n for ip, n in stats.ip_429.items() if not ip.startswith('127.'))
    if extern_429 > 0:
        print('  %s' % yellow('外部 IP 触发限流 %d 次 —— 有客户端超过速率阈值' % extern_429))
    elif stats.ip_429:
        print('  %s' % dim('限流仅由本机压测触发，无外部 IP 超限'))

    if not err5:
        print('  %s' % dim('无 5xx 错误 —— 服务端未出现故障'))

    print()
    return 0


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATTERN
    return run(pattern)


if __name__ == '__main__':
    sys.exit(main())
