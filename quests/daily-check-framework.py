#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_check.py —— 系统巡检脚本（Python 版框架）

【这是 B 阶段练习：你需要填 5 处 ____】

设计对应 C 阶段讲的 7 个要点：
  1. 输出分层     ：完整结果写日志，问题摘要输出到控制台
  2. 退出码语义   ：0=正常 1=警告 2=严重
  3. 阈值有依据   ：见下方常量注释
  4. 告警去重     ：状态文件记录上次等级
  5. 自包含       ：绝对路径、无需 TTY、无交互
  6. 按问题分组   ：每组回答一个问题
  7. 无副作用     ：只读操作，命令调用带超时

运行环境：Python 3.6（注意不能用 3.7+ 的 API）
"""

import os
import sys
import time
import subprocess
from datetime import datetime

# ==============================================================================
# 配置区
# ==============================================================================

LOG_FILE = "/var/log/daily-check.log"
ALERT_DIR = "/var/log/daily-check-alerts"
STATE_FILE = "/var/lib/daily-check.state"

# 阈值依据：
#   DISK 80% —— 留 20% 给系统响应（日志暴涨、临时文件）
#               ext4 超过 80% 后写入性能开始下降
#   INODE 80% —— inode 耗尽会报 "No space left on device"，
#                但 df -h 看起来还有空间，极难排查，必须单独监控
DISK_THRESHOLD = 80
INODE_THRESHOLD = 80

# 负载阈值：本机 2 核，负载 > 4（即核心数×2）时明显卡顿
LOAD_THRESHOLD = 4.0

# 可用内存阈值：低于 200MB 时进程可能被 OOM Killer 杀掉
MEM_THRESHOLD_MB = 200

WATCHED_SERVICES = ["nginx", "sshd", "fail2ban", "crond", "quest02"]

# 心跳日志：正常每 60 秒写一次，超过 180 秒未更新算异常
# 依据：留 3 倍余量，避免网络抖动或调度延迟造成误报
HEARTBEAT_LOG = "/var/log/quest02.log"
HEARTBEAT_MAX_AGE = 180

# 命令超时（秒）—— 设计要点 7：某个检查卡住不能拖垮整个脚本
CMD_TIMEOUT = 5

# ==============================================================================
# 结果收集
# ==============================================================================

warn_count = 0
crit_count = 0
result_lines = []


def record(level, message):
    """记录一条检查结果。level: OK / WARN / CRIT"""
    global warn_count, crit_count
    marks = {"OK": "✅", "WARN": "⚠️ ", "CRIT": "❌"}
    if level == "WARN":
        warn_count += 1
    elif level == "CRIT":
        crit_count += 1
    print("  %s %s" % (marks.get(level, "  "), message))
    result_lines.append((level, message))


# ==============================================================================
# 通用：执行系统命令
# ==============================================================================

def run_cmd(args, timeout=CMD_TIMEOUT):
    """
    执行系统命令，返回 (退出码, 标准输出, 错误信息)

    为什么封装成函数：
      所有命令调用都走这里，统一处理超时和异常，
      避免每个检查点都写一遍 try/except。

    参数 args 是【列表】而不是字符串：
      ["df", "-h", "/"]   ✅
      "df -h /"           ❌ 这样会被当成一个可执行文件名

    注意 Python 3.6 兼容性：
      不能用 capture_output / text（3.7+ 才有）
    """
    try:
        # ____ 空 1：完成这个 subprocess.run 调用
        #   要求：
        #     · 用 args 作为命令（列表形式）
        #     · 捕获 stdout 和 stderr
        #     · 输出按文本处理（3.6 兼容的写法）
        #     · 设置超时 timeout
        #   提示：
        #     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        #     universal_newlines=True, timeout=timeout
        r = subprocess.run(
            args,
            stdout=subprocess.PIPE,
	    stderr=subprocess.PIPE,
	    universal_newlines=True,
	    timeout=timeout
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return 127, "", "命令不存在: %s" % args[0]
    except subprocess.TimeoutExpired:
        return 124, "", "命令执行超时（%d 秒）: %s" % (timeout, " ".join(args))
    except Exception as e:
        return 1, "", "执行失败: %s" % e


# ==============================================================================
# 检查组 1：磁盘空间
# ==============================================================================
def check_disk():
    """回答：磁盘还够用吗？"""
    print("【1】磁盘空间")

    rc, out, err = run_cmd(["df", "-h", "/"])
    if rc != 0:
        record("WARN", "无法获取磁盘信息: %s" % err.strip())
        print()
        return

    # df -h / 的输出格式：
    #   Filesystem      Size  Used Avail Use% Mounted on
    #   /dev/vda3        40G  9.8G   28G  27% /
    lines = out.strip().split("\n")
    if len(lines) < 2:
        record("WARN", "df 输出格式异常")
        print()
        return

    fields = lines[1].split()
    # ____ 空 2：从 fields 里取出使用率数字
    #   fields[0]=/dev/vda3  fields[1]=40G  fields[2]=9.8G
    #   fields[3]=28G  fields[4]=27%  fields[5]=/
    #   为什么必须先做处理？（想想 -ge 那个坑）
    #   提示：把 "27%" 变成整数 27
    usage = int(fields[4].replace("%",""))

    avail = fields[3]

    if usage >= DISK_THRESHOLD:
        record("WARN", "根分区使用率 %d%%（阈值 %d%%），剩余 %s" % (
            usage, DISK_THRESHOLD, avail))
    else:
        record("OK", "根分区使用率 %d%%，剩余 %s" % (usage, avail))
    print()


# ==============================================================================
# 检查组 2：inode 使用率
# ==============================================================================
def check_inode():
    """回答：还能创建新文件吗？"""
    print("【2】inode 使用率")

    rc, out, err = run_cmd(["df", "-i", "/"])
    if rc != 0:
        record("WARN", "无法获取 inode 信息: %s" % err.strip())
        print()
        return

    lines = out.strip().split("\n")
    if len(lines) < 2:
        record("WARN", "df -i 输出格式异常")
        print()
        return

    fields = lines[1].split()
    # df -i 的输出：
    #   Filesystem     Inodes  IUsed   IFree IUse% Mounted on
    #   /dev/vda3     2608144  85653 2522491    4% /
    # ____ 空 3：取 IUse% 那一列
    #   注意列的位置和 df -h 不同，自己数一下
    usage = int(fields[4].replace("%", ""))

    if usage >= INODE_THRESHOLD:
        record("WARN", "inode 使用率 %d%%（阈值 %d%%）" % (usage, INODE_THRESHOLD))
    else:
        record("OK", "inode 使用率 %d%%" % usage)
    print()


# ==============================================================================
# 检查组 3：关键服务状态
# ==============================================================================
def check_services():
    """回答：该跑的服务都在跑吗？"""
    print("【3】关键服务")

    for svc in WATCHED_SERVICES:
        # ____ 空 4：判断服务是否在运行
        #   要求：
        #     · 调用 systemctl is-active <服务名>
        #     · 注意：服务未运行时返回码是 3，这不是异常，是正常返回
        #     · 根据返回码判断，不要根据输出文本判断
        #   提示：用 run_cmd 辅助函数
        rc, out, err = run_cmd(["systemctl", "is-active", svc])

        status = out.strip()
        if rc == 0 and status == "active":
            record("OK", "%s 运行中" % svc)
        else:
            record("CRIT", "%s 未运行（状态: %s）" % (svc, status or "无响应"))
    print()


# ==============================================================================
# 检查组 4：心跳日志是否在增长
# ==============================================================================
def check_heartbeat():
    """回答：服务在'活着'还是在'工作'？

    这是任务 003 的教训：
      systemctl is-active 说 active，不代表服务真的在产出。
    """
    print("【4】心跳日志")

    if not os.path.exists(HEARTBEAT_LOG):
        record("WARN", "日志文件不存在: %s" % HEARTBEAT_LOG)
        print()
        return

    # ____ 空 5：计算日志文件多久没更新了（单位：秒）
    #   提示：
    #     os.path.getmtime(path)  取最后修改时间（Unix 时间戳）
    #     time.time()             取当前时间
    #     两者相减就是"多少秒前更新过"
    age = time.time() - os.path.getmtime(HEARTBEAT_LOG)

    import stat as stat_mod
    st = os.stat(HEARTBEAT_LOG)
    owner = "%d:%d" % (st.st_uid, st.st_gid)

    # 把 uid/gid 转成用户名，便于人工阅读
    try:
        import pwd
        import grp
        owner = "%s:%s" % (pwd.getpwuid(st.st_uid).pw_name,
                           grp.getgrgid(st.st_gid).gr_name)
    except Exception:
        pass

    size = st.st_size

    if age > HEARTBEAT_MAX_AGE:
        record("CRIT", "日志已 %.0f 秒未更新（属主 %s，大小 %d 字节）" % (
            age, owner, size))
        print("     → 检查: journalctl -u quest02 -n 20")
        print("     → 检查: sudo -u chenjin test -w %s" % HEARTBEAT_LOG)
    else:
        record("OK", "日志 %.0f 秒前更新（属主 %s，大小 %d 字节）" % (
            age, owner, size))
    print()


# ==============================================================================
# 检查组 5：系统资源
# ==============================================================================
def check_resources():
    """回答：资源是否紧张？"""
    print("【5】系统资源")

    # 负载
    try:
        with open("/proc/loadavg") as f:
            load1 = float(f.read().split()[0])
        if load1 > LOAD_THRESHOLD:
            record("WARN", "系统负载 %.2f（2 核机器，阈值 %.1f）" % (
                load1, LOAD_THRESHOLD))
        else:
            record("OK", "系统负载 %.2f" % load1)
    except Exception as e:
        record("WARN", "无法读取负载: %s" % e)

    # 可用内存
    try:
        mem_avail_mb = None
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    mem_avail_mb = int(line.split()[1]) // 1024
                    break
        if mem_avail_mb is None:
            record("WARN", "无法读取 MemAvailable")
        elif mem_avail_mb < MEM_THRESHOLD_MB:
            record("WARN", "可用内存仅 %d MB（阈值 %d MB）" % (
                mem_avail_mb, MEM_THRESHOLD_MB))
        else:
            record("OK", "可用内存 %d MB" % mem_avail_mb)
    except Exception as e:
        record("WARN", "无法读取内存: %s" % e)

    print()


# ==============================================================================
# 主流程
# ==============================================================================
def main():
    now = datetime.now()
    header = "巡检 %s" % now.strftime("%Y-%m-%d %H:%M:%S")

    print()
    print("=" * 62)
    print(" " + header)
    print("=" * 62)
    print()

    check_disk()
    check_inode()
    check_services()
    check_heartbeat()
    check_resources()

    # ---- 汇总与退出码 ----
    if crit_count > 0:
        level, exit_code = "CRITICAL", 2
    elif warn_count > 0:
        level, exit_code = "WARNING", 1
    else:
        level, exit_code = "OK", 0

    print("=" * 62)
    print(" 结果: %s（警告 %d，严重 %d）" % (level, warn_count, crit_count))
    print("=" * 62)
    print()

    # ---- 写日志文件 ----
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write("########## %s ##########\n" % header)
            for lv, msg in result_lines:
                f.write("%s|%s\n" % (lv, msg))
            f.write("LEVEL=%s WARN=%d CRIT=%d\n\n" % (
                level, warn_count, crit_count))
    except Exception as e:
        print(" 写入日志失败: %s" % e)

    # ---- 日志自身轮转（防止巡检脚本把磁盘占满）----
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE) as f:
                n = sum(1 for _ in f)
            if n > 2000:
                with open(LOG_FILE) as f:
                    lines = f.readlines()
                with open(LOG_FILE, "w") as f:
                    f.writelines(lines[-1000:])
    except Exception:
        pass

    # ---- 告警文件 ----
    if level != "OK":
        try:
            os.makedirs(ALERT_DIR, exist_ok=True)
            alert_path = os.path.join(
                ALERT_DIR,
                "alert-%s-%s.txt" % (level, now.strftime("%Y%m%d-%H%M%S")))
            with open(alert_path, "w") as f:
                f.write("巡检告警 [%s] %s\n" % (level, header))
                f.write("警告 %d 项，严重 %d 项\n\n" % (warn_count, crit_count))
                for lv, msg in result_lines:
                    if lv in ("WARN", "CRIT"):
                        f.write("[%s] %s\n" % (lv, msg))
                f.write("\n完整日志: %s\n" % LOG_FILE)
            print(" 告警已写入: %s" % alert_path)
            print()

            # 记录到 syslog，便于 journalctl 统一检索
            subprocess.run(["logger", "-t", "daily-check",
                            "level=%s warn=%d crit=%d" % (
                                level, warn_count, crit_count)],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception as e:
            print(" 写入告警失败: %s" % e)

    # ---- 状态文件（供下次运行判断等级是否变化，实现告警去重）----
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            f.write("%s\n" % level)
    except Exception:
        pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
