#!/usr/bin/env python3
"""
流量预算监控（针对中国内地 20 GB/月 免费额度）

设计依据（已核实官方文档）：
  CDT 公网流量免费额度按地域区分：
    中国内地地域：20 GB/月
    非中国内地地域：200 GB/月
  本机在华北1（青岛）= 中国内地 → 20 GB/月
  日均预算 = 20 * 1024 / 30 ≈ 682 MB/天

测量方法：
  1. 站点流量：解析 nginx access log 的 $body_bytes_sent（第 10 字段）
     —— 这是精确的"本站对外发出的页面字节数"，是公网出流量的大头。
  2. 系统总流量：/proc/net/dev 的 eth0 tx
     —— 【包含内网流量】，agent 心跳都算在里面，所以不能直接当计费量。
     这里同时展示，是为了做对比：如果两者差距巨大，说明内网占主导。

诚实说明局限：
  本站脚本只能精确统计【网站】产生的流量。
  SSH、apt/yum 下载、docker pull 等也走公网，但不计入本站统计。
  真实计费数字请以控制台「云数据传输 CDT」为准。
"""
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

MONTHLY_QUOTA_MB = 20 * 1024          # 20 GB/月（中国内地）
DAYS_IN_MONTH = 30
DAILY_BUDGET_MB = MONTHLY_QUOTA_MB / DAYS_IN_MONTH
WARN_PCT = 50                          # 用到日均预算 50% 告警
CRIT_PCT = 80                          # 用到 80% 严重告警

ACCESS_LOG = "/var/log/nginx/personal-site.access.log.gz"
ACCESS_LOG_PLAIN = "/var/log/nginx/personal-site.access.log"

# nginx 默认 main 格式：
# $remote_addr - $remote_user [$time_local] "$request" $status
# $body_bytes_sent "$http_referer" "$http_user_agent" "$http_x_forwarded_for"
# 注意 $request 里含空格（"GET / HTTP/1.1"），所以不能简单按空格切分。
LINE_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<req>[^"]*)" (?P<status>\d+) (?P<bytes>\d+|-) '
    r'"(?P<ref>[^"]*)" "(?P<ua>[^"]*)"'
)


def parse_time(s):
    """[13/Sep/2026:15:06:42 +0800]"""
    try:
        return datetime.strptime(s.split()[0], "%d/%b/%Y:%H:%M:%S")
    except ValueError:
        return None


def read_logs(paths):
    """读取（含 gz）日志，返回解析后的条目列表。"""
    entries = []
    for p in paths:
        if not os.path.exists(p):
            continue
        if p.endswith(".gz"):
            import gzip
            fh = gzip.open(p, "rt", errors="replace")
        else:
            fh = open(p, "r", errors="replace")
        with fh:
            for line in fh:
                m = LINE_RE.match(line)
                if not m:
                    continue
                t = parse_time(m.group("time"))
                if t is None:
                    continue
                b = m.group("bytes")
                entries.append({
                    "time": t,
                    "bytes": int(b) if b != "-" else 0,
                    "status": int(m.group("status")),
                    "ip": m.group("ip"),
                    "req": m.group("req"),
                    "ua": m.group("ua"),
                })
    return entries


def iface_tx_bytes():
    for line in open("/proc/net/dev"):
        if line.strip().startswith("eth0:"):
            return int(line.split(":")[1].split()[8])
    return 0


def human(mb):
    if mb >= 1024:
        return "%.2f GB" % (mb / 1024.0)
    return "%.1f MB" % mb


def main():
    now = datetime.now()
    print("=" * 66)
    print(" 流量预算监控  %s" % now.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 66)
    print()
    print(" 额度设定（中国内地地域 / 华北1 青岛）")
    print("   月度免费额度:  %s" % human(MONTHLY_QUOTA_MB))
    print("   日均预算:      %s" % human(DAILY_BUDGET_MB))
    print("   告警阈值:      日均 %d%% = %s  /  %d%% = %s" % (
        WARN_PCT, human(DAILY_BUDGET_MB * WARN_PCT / 100),
        CRIT_PCT, human(DAILY_BUDGET_MB * CRIT_PCT / 100)))
    print()

    paths = []
    for i in range(1, 8):   # 最多看 7 个归档文件
        paths.append("/var/log/nginx/personal-site.access.log.%d.gz" % i)
    paths.append(ACCESS_LOG_PLAIN)

    entries = read_logs(paths)
    if not entries:
        print(" 未找到可解析的访问日志。")
        print(" 提示：若站点尚未被访问，日志可能为空。")
        return 0

    print(" 日志解析: %d 条记录，时间跨度 %s ~ %s" % (
        len(entries),
        min(e["time"] for e in entries).strftime("%m-%d %H:%M"),
        max(e["time"] for e in entries).strftime("%m-%d %H:%M")))
    print()

    # ---- 按天聚合 ----
    by_day = {}
    for e in entries:
        d = e["time"].strftime("%Y-%m-%d")
        agg = by_day.setdefault(d, {"bytes": 0, "count": 0, "err429": 0,
                                    "err404": 0, "ips": set()})
        agg["bytes"] += e["bytes"]
        agg["count"] += 1
        if e["status"] == 429:
            agg["err429"] += 1
        if e["status"] == 404:
            agg["err404"] += 1
        agg["ips"].add(e["ip"])

    print("── 按天统计（站点出流量）──")
    print("  %-12s %10s %8s %8s %8s %7s %s" % (
        "日期", "出流量", "占预算", "请求数", "429", "404", "独立IP"))
    print("  " + "-" * 66)
    for d in sorted(by_day):
        a = by_day[d]
        mb = a["bytes"] / 1048576.0
        pct = mb / DAILY_BUDGET_MB * 100 if DAILY_BUDGET_MB else 0
        flag = ""
        if pct >= CRIT_PCT:
            flag = " ⚠️严重"
        elif pct >= WARN_PCT:
            flag = " ⚠️警告"
        print("  %-12s %10s %7.2f%% %8d %8d %7d %7d%s" % (
            d, human(mb), pct, a["count"], a["err429"], a["err404"],
            len(a["ips"]), flag))

    print()
    # ---- 今日 ----
    today = now.strftime("%Y-%m-%d")
    t = by_day.get(today)
    print("── 今日状况 (%s) ──" % today)
    if t:
        mb = t["bytes"] / 1048576.0
        pct = mb / DAILY_BUDGET_MB * 100
        print("   站点出流量:  %s" % human(mb))
        print("   占日均预算:  %.2f%%" % pct)
        print("   请求数:      %d" % t["count"])
        print("   独立 IP:     %d" % len(t["ips"]))
        print()
        if pct >= CRIT_PCT:
            print("   🔴 严重：已用掉日均预算的 %.1f%%，请检查是否有异常访问" % pct)
        elif pct >= WARN_PCT:
            print("   🟡 警告：已用掉日均预算的 %.1f%%" % pct)
        else:
            print("   🟢 正常：流量消耗在预算内")
    else:
        print("   今日暂无访问记录")

    print()
    # ---- 月度累计 ----
    month_prefix = now.strftime("%Y-%m")
    month_bytes = sum(a["bytes"] for d, a in by_day.items()
                      if d.startswith(month_prefix))
    month_mb = month_bytes / 1048576.0
    print("── 本月累计（基于日志可覆盖范围）──")
    print("   站点出流量:  %s" % human(month_mb))
    print("   占月度额度:  %.3f%%" % (month_mb / MONTHLY_QUOTA_MB * 100))
    print("   剩余额度:    %s" % human(MONTHLY_QUOTA_MB - month_mb))

    print()
    # ---- 系统总流量对照 ----
    tx = iface_tx_bytes()
    tx_gb = tx / 1073741824.0
    print("── 系统总出流量对照（eth0 累计，含内网）──")
    print("   eth0 累计发送: %s" % human(tx / 1048576.0))
    print("   站点出流量:    %s" % human(month_mb))
    if tx / 1048576.0 > 0:
        ratio = month_mb / (tx / 1048576.0) * 100
        print("   站点占比:      %.2f%%" % ratio)
    print()
    print("   ⚠️ 重要：eth0 计数【包含内网流量】——阿里云 agent")
    print("      （云盾/日志采集/监控/备份）大量发往 100.64.0.0/10")
    print("      内部服务网，不出机房、不计费。所以 eth0 数字远大于计费量。")
    print("      真实计费数字请看控制台「云数据传输 CDT」。")

    print()
    # ---- 异常检测 ----
    print("── 异常访问检测 ──")
    ip_count = {}
    for e in entries:
        ip_count[e["ip"]] = ip_count.get(e["ip"], 0) + 1
    top = sorted(ip_count.items(), key=lambda kv: -kv[1])[:5]
    print("   请求最多的 IP：")
    for ip, c in top:
        b = sum(e["bytes"] for e in entries if e["ip"] == ip)
        print("     %-18s %6d 次  %s" % (ip, c, human(b / 1048576.0)))

    # 高频访问告警（单 IP 超过 1000 次）
    noisy = [(ip, c) for ip, c in ip_count.items() if c > 1000]
    if noisy:
        print()
        print("   🔴 发现高频访问 IP（可能是扫描器）：")
        for ip, c in noisy:
            print("     %-18s %d 次" % (ip, c))
    else:
        print("   🟢 未发现异常高频 IP")

    # 大量 404 = 扫描特征
    total_404 = sum(a["err404"] for a in by_day.values())
    total_req = sum(a["count"] for a in by_day.values())
    if total_req > 0:
        r404 = total_404 * 100.0 / total_req
        print("   404 占比: %.1f%%" % r404)
        if r404 > 30:
            print("     ⚠️ 404 比例偏高，通常意味着有人在扫描不存在的路径")

    # 429 = 限流生效
    total_429 = sum(a["err429"] for a in by_day.values())
    if total_429 > 0:
        print("   被限流拒绝: %d 次（占 %.1f%%）" % (
            total_429, total_429 * 100.0 / total_req if total_req else 0))
        print("     ✅ 限流在起作用，这些请求没有消耗页面流量")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
