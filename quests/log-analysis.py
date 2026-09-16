#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
log-analysis.py —— nginx 访问日志分析

功能：
  1. 状态码分布统计
  2. 请求最多的 IP（TOP 5）

日志格式（nginx main）：
  IP - - [时间] "请求" 状态码 字节数 "referer" "UA"

关键点：$request 字段含空格，必须用正则解析，不能按空格切分。

运行环境：Python 3.6.8
"""

import re
from collections import Counter

LOG_FILE = "/var/log/nginx/personal-site.access.log"

# 匹配 nginx main 日志格式
# 各字段说明：
#   (?P<ip>\S+)           客户端 IP（非空白字符）
#   \S+\s+\S+             ident 和 authuser（通常是 - -）
#   \[(?P<time>[^\]]+)\]  时间戳，方括号内不含 ]
#   "(?P<req>[^"]*)"      请求行，引号内不含 "（可为空串）
#   (?P<status>\d{3})     三位状态码
#   (?P<bytes>\d+|-)      响应字节数或 -
#   "(?P<ref>[^"]*)"      referer
#   "(?P<ua>[^"]*)"       User-Agent
LINE_RE = re.compile(
    r'^(?P<ip>\S+)\s+\S+\s+\S+\s+'
    r'\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<req>[^"]*)"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<bytes>\d+|-)\s+'
    r'"(?P<ref>[^"]*)"\s+'
    r'"(?P<ua>[^"]*)"'
)


def main():
    status_counter = Counter()
    ip_counter = Counter()
    total = 0
    parsed = 0
    skipped = 0

    with open(LOG_FILE, "r", errors="replace") as f:
        for line in f:
            total += 1

            # 用正则解析；匹配失败的行计入 skipped，不中断处理
            m = LINE_RE.match(line)
            if m:
                parsed += 1
                status = m.group("status")
                status_counter[status] += 1
                ip_counter[m.group("ip")] += 1
            else:
                skipped += 1

    # ---- 输出报告 ----
    print("=== nginx 访问日志分析 ===")
    print("日志文件: %s" % LOG_FILE)
    print()
    print("总行数:   %d" % total)
    print("已解析:   %d" % parsed)
    print("已跳过:   %d" % skipped)
    print()

    print("状态码分布:")
    if parsed > 0:
        for status, count in status_counter.most_common():
            percent = count / parsed * 100
            print("  %-6s %-4d 次  %.1f%%" % (status, count, percent))
    else:
        print("  (无有效数据)")
    print()

    print("请求最多的 IP:")
    # 注意：这里用 if 判断容器是否为空，而不是 for...else。
    # Python 的 for...else 语义是「循环有没有被 break 打断」，
    # 而不是「循环是否为空」——用错会导致 else 分支总是执行。
    if ip_counter:
        # most_common(5) 直接返回前 5 名，无需手动排序
        for ip, n in ip_counter.most_common(5):
            print("  %-18s %4d 次" % (ip, n))
    else:
        print("  (无有效日志数据)")


if __name__ == "__main__":
    main()
