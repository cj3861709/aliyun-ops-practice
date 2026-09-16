#!/usr/bin/env python3
"""
极简 HTTP 压测器（py3.6 兼容，仅用标准库）

两种模式：
  按请求数:  loadtest.py <url> <并发> <请求数> [超时]
  按时长:    loadtest.py <url> <并发> <时长, 如 30s 或 45>  [超时]

按时长模式是验证"发布期间是否中断"的正确工具：
它保证负载在发布【全过程】持续存在，而不是跑完了才开始发布。

关键指标解释：
  - 失败数 = 0             发布过程未丢失任何请求（零停机成立）
  - "connection refused"   服务曾经完全停掉（restart 而非 reload）
  - HTTP 5xx               服务活着但后端不可用
  - HTTP 404/403           切换期间指向了错误/无权限的目录
"""
import http.client
import socket
import ssl
import sys
import threading
import time
from collections import Counter

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

url = sys.argv[1]
concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 4
work_arg = sys.argv[3] if len(sys.argv) > 3 else "200"
timeout = float(sys.argv[4]) if len(sys.argv) > 4 else 5.0

# 判断模式
duration_mode = work_arg.lower().endswith("s")
if duration_mode:
    duration = float(work_arg[:-1])
    total_target = None
else:
    duration = None
    total_target = int(work_arg)

# 解析 URL
if url.startswith("http://"):
    rest, secure = url[7:], False
elif url.startswith("https://"):
    rest, secure = url[8:], True
else:
    rest, secure = url, False

if "/" in rest:
    hostport, path = rest.split("/", 1)
    path = "/" + path
else:
    hostport, path = rest, "/"

if ":" in hostport:
    host, port = hostport.split(":", 1)
    port = int(port)
else:
    host, port = hostport, (443 if secure else 80)

results = []
reasons = Counter()
lock = threading.Lock()
stop_flag = threading.Event()
counter = [0]  # 已完成请求数（用于按请求数模式分配）


def one_request():
    t0 = time.time()
    status = err = None
    try:
        if secure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path, headers={"Connection": "close",
                                           "User-Agent": "ops-lab-loadtest/1.0"})
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        conn.close()
    except socket.timeout:
        err = "timeout (超时)"
    except ConnectionRefusedError:
        err = "connection refused (服务未监听)"
    except ConnectionResetError:
        err = "connection reset (连接被重置)"
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)

    dt = (time.time() - t0) * 1000.0
    with lock:
        results.append((status, dt))
        if err:
            reasons[err] += 1
        elif status and status != 200:
            reasons["HTTP %d" % status] += 1


def worker_count_mode(n):
    for _ in range(n):
        one_request()


def worker_duration_mode():
    while not stop_flag.is_set():
        one_request()


print("目标: http://%s:%d%s" % (host, port, path))
if duration_mode:
    print("模式: 按时长 | 并发 %d | 持续 %.1fs | 超时 %.1fs" % (concurrency, duration, timeout))
else:
    print("模式: 按请求数 | 并发 %d | 总数 %d | 超时 %.1fs" % (concurrency, total_target, timeout))

threads = []
per_thread = remainder = 0
if not duration_mode:
    per_thread = total_target // concurrency
    remainder = total_target % concurrency

for i in range(concurrency):
    if duration_mode:
        t = threading.Thread(target=worker_duration_mode)
    else:
        n = per_thread + (1 if i < remainder else 0)
        if n <= 0:
            continue
        t = threading.Thread(target=worker_count_mode, args=(n,))
    t.daemon = True
    t.start()
    threads.append(t)

print("开始...")
t_start = time.time()

if duration_mode:
    time.sleep(duration)
    stop_flag.set()

for t in threads:
    t.join()

elapsed = time.time() - t_start

ok = sum(1 for s, _ in results if s == 200)
failed = sum(1 for s, _ in results if s is None)
other = len(results) - ok - failed
lat = sorted(d for _, d in results)


def pct(p):
    if not lat:
        return 0.0
    idx = min(len(lat) - 1, int(len(lat) * p / 100.0))
    return lat[idx]


print()
print("=" * 62)
print("结果: 总请求 %d | 耗时 %.2fs | QPS %.0f" % (
    len(results), elapsed, len(results) / elapsed if elapsed else 0))
print("=" * 62)
print("  成功 (HTTP 200):  %d" % ok)
print("  其他状态码:       %d" % other)
print("  失败 (无响应):    %d" % failed)
print()
print("  延迟  p50=%.1fms  p90=%.1fms  p99=%.1fms  max=%.1fms" % (
    pct(50), pct(90), pct(99), lat[-1] if lat else 0))

if reasons:
    print()
    print("  非 200 响应 / 异常分布:")
    for r, c in reasons.most_common():
        print("    %-42s %d" % (r, c))

print()
if failed == 0 and other == 0:
    print("  ✅ 零失败 —— 该时间段内服务未中断")
    sys.exit(0)
else:
    print("  ❌ 有 %d 个失败 + %d 个非200响应 —— 存在中断" % (failed, other))
    sys.exit(2)
