#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily-check.py —— 系统日常巡检

用途：
    每天定时检查服务器状态，异常时发邮件告警。

用法：
    python3 daily-check.py              # 正常巡检
    python3 daily-check.py --no-mail    # 不发邮件（只写日志）
    python3 daily-check.py --verbose    # 显示所有检查项（含正常的）
    python3 daily-check.py --test-mail  # 测试邮件配置

退出码：
    0 = 一切正常
    1 = 有警告（需要关注）
    2 = 有严重问题（需要立即处理）

被检查的项目及阈值依据：
    · 磁盘 80%      留 20% 给系统响应；ext4 超过 80% 后写入性能下降
    · inode 80%     inode 耗尽会报 "No space left on device"，但 df -h 看起来还有空间
    · 内存 200MB    低于此值时进程可能被 OOM Killer 杀掉
    · 负载 4.0      本机 2 核，负载 > 核心数×2 时明显卡顿
    · 日志 180 秒   quest02 每 60 秒写一次，留 3 倍余量避免误报
    · 流量 50%/80%  基于 20GB/月免费额度折算日均

运行环境：Python 3.6+
"""

import os
import re
import subprocess
import sys
import time
from datetime import datetime

# 复用同目录的 notify 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import notify
except ImportError:
    notify = None

# ==============================================================================
# 配置区
# ==============================================================================

LOG_DIR = "/root/ops-lab/logs/daily-check"
ALERT_DIR = "/root/ops-lab/logs/alerts"
STATE_FILE = "/root/ops-lab/logs/daily-check.state"
MAIL_CONF = "/etc/ops-lab/mail.conf"

# 阈值（依据见文件头注释）
DISK_THRESHOLD = 80          # 磁盘使用率 %
INODE_THRESHOLD = 80         # inode 使用率 %
MEM_MIN_MB = 200             # 最低可用内存 MB
LOAD_THRESHOLD = 4.0         # 系统负载（2 核机器）

# 需要监控的服务
WATCHED_SERVICES = ["nginx", "sshd", "fail2ban", "crond", "quest02"]

# 心跳日志
HEARTBEAT_LOG = "/var/log/quest02.log"
HEARTBEAT_MAX_AGE = 180      # 秒

# 流量预算
TRAFFIC_MONTHLY_GB = 20
TRAFFIC_WARN_PCT = 50
TRAFFIC_CRIT_PCT = 80

# 日志保留
LOG_KEEP_LINES = 3000        # 主日志最多保留行数
ALERT_KEEP_DAYS = 30         # 告警文件保留天数

CMD_TIMEOUT = 5              # 单条命令超时（秒）

# MySQL/MariaDB 检查
DB_ENABLED = True
DB_CHECK_SCRIPT = "/root/ops-lab/bin/db/check-mariadb.sh"
DB_BACKUP_DIR = "/root/ops-lab/logs/db-backup"
DB_BACKUP_MAX_AGE_H = 48     # 备份超过此年龄则告警

# ==============================================================================
# 结果收集
# ==============================================================================

class Report(object):
    def __init__(self):
        self.items = []       # [(level, name, message)]
        self.warn = 0
        self.crit = 0

    def add(self, level, name, message):
        self.items.append((level, name, message))
        if level == 'WARN':
            self.warn += 1
        elif level == 'CRIT':
            self.crit += 1

    def level(self):
        if self.crit:
            return 'CRITICAL'
        if self.warn:
            return 'WARNING'
        return 'OK'

    def code(self):
        return {'OK': 0, 'WARNING': 1, 'CRITICAL': 2}[self.level()]


def run_cmd(args, timeout=CMD_TIMEOUT):
    """执行命令，返回 (返回码, stdout, stderr)。Python 3.6 兼容写法。"""
    try:
        r = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout
        )
        return r.returncode, (r.stdout or ''), (r.stderr or '')
    except FileNotFoundError:
        return 127, '', '命令不存在: %s' % args[0]
    except subprocess.TimeoutExpired:
        return 124, '', '命令超时（%d 秒）' % timeout
    except Exception as e:
        return 1, '', '%s: %s' % (type(e).__name__, e)


# ==============================================================================
# 检查项
# ==============================================================================

def check_disk(rep):
    """磁盘空间"""
    rc, out, err = run_cmd(['df', '-h', '/'])
    if rc != 0:
        rep.add('WARN', '磁盘', '无法获取: %s' % err.strip())
        return
    lines = out.strip().split('\n')
    if len(lines) < 2:
        rep.add('WARN', '磁盘', 'df 输出格式异常')
        return
    f = lines[1].split()
    try:
        pct = int(f[4].rstrip('%'))
    except (ValueError, IndexError):
        rep.add('WARN', '磁盘', '无法解析使用率: %s' % lines[1])
        return

    if pct >= DISK_THRESHOLD:
        rep.add('WARN', '磁盘', '使用率 %d%%（阈值 %d%%），剩余 %s' % (
            pct, DISK_THRESHOLD, f[3]))
    else:
        rep.add('OK', '磁盘', '使用率 %d%%，剩余 %s' % (pct, f[3]))


def check_inode(rep):
    """inode 使用率 —— 容易被忽略，耗尽时报错难排查"""
    rc, out, err = run_cmd(['df', '-i', '/'])
    if rc != 0:
        rep.add('WARN', 'inode', '无法获取: %s' % err.strip())
        return
    lines = out.strip().split('\n')
    if len(lines) < 2:
        rep.add('WARN', 'inode', 'df -i 输出格式异常')
        return
    f = lines[1].split()
    try:
        pct = int(f[4].rstrip('%'))
    except (ValueError, IndexError):
        rep.add('WARN', 'inode', '无法解析: %s' % lines[1])
        return

    if pct >= INODE_THRESHOLD:
        rep.add('WARN', 'inode', '使用率 %d%%（阈值 %d%%）' % (pct, INODE_THRESHOLD))
    else:
        rep.add('OK', 'inode', '使用率 %d%%' % pct)


def check_services(rep):
    """关键服务状态"""
    down = []
    for svc in WATCHED_SERVICES:
        rc, out, err = run_cmd(['systemctl', 'is-active', svc])
        status = out.strip()
        if rc != 0 or status != 'active':
            down.append('%s(%s)' % (svc, status or 'no-response'))

    if down:
        rep.add('CRIT', '服务', '未运行: %s' % ', '.join(down))
    else:
        rep.add('OK', '服务', '%d 个服务全部运行中' % len(WATCHED_SERVICES))


def check_heartbeat(rep):
    """心跳日志是否在增长 —— 验证「服务在工作」而不仅是「进程活着」"""
    if not os.path.exists(HEARTBEAT_LOG):
        rep.add('WARN', '心跳', '日志不存在: %s' % HEARTBEAT_LOG)
        return

    try:
        age = time.time() - os.path.getmtime(HEARTBEAT_LOG)
        st = os.stat(HEARTBEAT_LOG)
    except OSError as e:
        rep.add('WARN', '心跳', '无法读取: %s' % e)
        return

    # uid → 用户名（便于人工判断）
    owner = '%d:%d' % (st.st_uid, st.st_gid)
    try:
        import pwd
        import grp
        owner = '%s:%s' % (pwd.getpwuid(st.st_uid).pw_name,
                           grp.getgrgid(st.st_gid).gr_name)
    except (ImportError, KeyError):
        pass

    if age > HEARTBEAT_MAX_AGE:
        rep.add('CRIT', '心跳', '日志已 %.0f 秒未更新（属主 %s，大小 %d 字节）' % (
            age, owner, st.st_size))
    else:
        rep.add('OK', '心跳', '%.0f 秒前更新（属主 %s）' % (age, owner))


def check_memory(rep):
    """可用内存"""
    try:
        avail = None
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    avail = int(line.split()[1]) // 1024
                    break
        if avail is None:
            rep.add('WARN', '内存', '无法读取 MemAvailable')
            return
    except (IOError, OSError) as e:
        rep.add('WARN', '内存', '读取失败: %s' % e)
        return

    if avail < MEM_MIN_MB:
        rep.add('WARN', '内存', '可用仅 %d MB（阈值 %d MB）' % (avail, MEM_MIN_MB))
    else:
        rep.add('OK', '内存', '可用 %d MB' % avail)


def check_load(rep):
    """系统负载"""
    try:
        with open('/proc/loadavg') as f:
            load1 = float(f.read().split()[0])
    except (IOError, OSError, ValueError) as e:
        rep.add('WARN', '负载', '读取失败: %s' % e)
        return

    if load1 > LOAD_THRESHOLD:
        rep.add('WARN', '负载', '%.2f（阈值 %.1f，2 核机器）' % (load1, LOAD_THRESHOLD))
    else:
        rep.add('OK', '负载', '%.2f' % load1)


def check_traffic(rep):
    """流量用量 —— 涉及费用，优先级高"""
    script = '/root/ops-lab/bin/traffic-check.sh'
    if not os.path.isfile(script):
        rep.add('WARN', '流量', '监控脚本不存在: %s' % script)
        return

    rc, out, err = run_cmd(['bash', script], timeout=30)
    # 从输出里提取日均预算百分比
    m = re.search(r'占日均预算:\s*([0-9.]+)%', out)
    if not m:
        m = re.search(r'([0-9.]+)%', out)
    if not m:
        rep.add('WARN', '流量', '无法解析监控脚本输出')
        return

    try:
        pct = float(m.group(1))
    except ValueError:
        rep.add('WARN', '流量', '解析失败: %s' % m.group(1))
        return

    if pct >= TRAFFIC_CRIT_PCT:
        rep.add('CRIT', '流量', '日均预算用量 %.2f%%（阈值 %d%%）' % (pct, TRAFFIC_CRIT_PCT))
    elif pct >= TRAFFIC_WARN_PCT:
        rep.add('WARN', '流量', '日均预算用量 %.2f%%（阈值 %d%%）' % (pct, TRAFFIC_WARN_PCT))
    else:
        rep.add('OK', '流量', '日均预算用量 %.2f%%' % pct)


def check_disk_detail(rep):
    """磁盘占用 TOP 目录 —— 便于快速定位什么东西在涨"""
    targets = [
        ('/var/log', '系统日志'),
        ('/root/ops-lab/logs', '项目日志'),
        ('/root/ops-lab/backups', '配置备份'),
        ('/var/www/releases', '站点版本'),
    ]
    biggest = None
    for path, label in targets:
        if not os.path.isdir(path):
            continue
        rc, out, err = run_cmd(['du', '-sm', path], timeout=15)
        if rc != 0:
            continue
        try:
            mb = int(out.split()[0])
        except (ValueError, IndexError):
            continue
        if biggest is None or mb > biggest[1]:
            biggest = (label, mb, path)

    if biggest:
        rep.add('OK', '占用', '最大目录: %s %d MB (%s)' % (
            biggest[0], biggest[1], biggest[2]))




def check_mariadb(rep):
    """
    数据库健康检查

    调用 bin/db/check-mariadb.sh（它做 8 项检查），
    然后提取关键信息汇总到巡检报告里。

    为什么单独封装而不是重写检查逻辑：
      脚本已经过测试，且有独立的退出码语义。
      巡检只负责汇总和告警，不重复实现。
    """
    if not DB_ENABLED:
        return

    if not os.path.isfile(DB_CHECK_SCRIPT):
        rep.add('WARN', '数据库', '检查脚本不存在: %s' % DB_CHECK_SCRIPT)
        return

    rc, out, err = run_cmd(['bash', DB_CHECK_SCRIPT], timeout=30)

    # 从输出里提取关键行
    def grab(pattern):
        m = re.search(pattern, out)
        return m.group(1).strip() if m else None

    version = grab(r'版本:\s*(\S+)')
    memory = grab(r'(\d+\.\d+MiB / \d+MiB)')
    binding = grab(r'(127\.0\.0\.1:3306|0\.0\.0\.0:3306)')

    # 用退出码判定总体状态
    if rc == 0:
        detail = []
        if version:
            detail.append(version)
        if memory:
            detail.append(memory)
        rep.add('OK', '数据库', '运行正常' + ('（%s）' % '，'.join(detail) if detail else ''))
    elif rc == 1:
        rep.add('WARN', '数据库', '有警告项，详见 %s' % DB_CHECK_SCRIPT)
    else:
        # 提取失败项
        fails = re.findall(r'❌\s*([^\n]+)', out)
        msg = '检查失败'
        if fails:
            msg += '：' + '；'.join(f.strip() for f in fails[:3])
        rep.add('CRIT', '数据库', msg)

    # 安全项：端口必须只绑本机
    if binding and '0.0.0.0' in binding:
        rep.add('CRIT', '数据库', '端口暴露到公网（%s），必须修正' % binding)

    # 备份时效
    if os.path.isdir(DB_BACKUP_DIR):
        backups = [os.path.join(DB_BACKUP_DIR, f)
                   for f in os.listdir(DB_BACKUP_DIR) if f.endswith('.sql')]
        if not backups:
            rep.add('WARN', '数据库备份', '从未备份')
        else:
            latest = max(backups, key=os.path.getmtime)
            age_h = (time.time() - os.path.getmtime(latest)) / 3600
            size_kb = os.path.getsize(latest) / 1024
            if age_h > DB_BACKUP_MAX_AGE_H:
                rep.add('WARN', '数据库备份', '最近备份已 %.0f 小时（阈值 %d 小时）' % (
                    age_h, DB_BACKUP_MAX_AGE_H))
            else:
                rep.add('OK', '数据库备份', '%.0f 小时前（%.0f KB）' % (age_h, size_kb))
    else:
        rep.add('WARN', '数据库备份', '备份目录不存在')


# ==============================================================================
# 输出与归档
# ==============================================================================

def format_report(rep, verbose=False):
    """生成人类可读的报告"""
    now = datetime.now()
    lines = []
    lines.append('=' * 58)
    lines.append(' 系统巡检  %s' % now.strftime('%Y-%m-%d %H:%M:%S'))
    lines.append('=' * 58)
    lines.append('')

    marks = {'OK': '✅', 'WARN': '⚠️ ', 'CRIT': '❌'}

    # 正常的检查项：verbose 时才显示
    for level, name, msg in rep.items:
        if level == 'OK' and not verbose:
            continue
        lines.append('  %s %-6s %s' % (marks[level], name, msg))

    if not verbose and rep.warn == 0 and rep.crit == 0:
        lines.append('  所有检查项正常')
        lines.append('')
        for level, name, msg in rep.items:
            lines.append('    · %-6s %s' % (name, msg))

    lines.append('')
    lines.append('-' * 58)
    lines.append(' 结果: %s（警告 %d，严重 %d）' % (
        rep.level(), rep.warn, rep.crit))
    lines.append('-' * 58)

    return '\n'.join(lines)


def save_log(text):
    """写入日志文件，并做体积控制"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, datetime.now().strftime('%Y-%m-%d') + '.log')
        with open(path, 'a', encoding='utf-8') as f:
            f.write(text + '\n\n')

        # 同时维护一个 latest.txt 便于快速查看
        latest = os.path.join(LOG_DIR, 'latest.txt')
        with open(latest, 'w', encoding='utf-8') as f:
            f.write(text + '\n')

        # 主日志体积控制
        with open(latest, encoding='utf-8') as f:
            n = sum(1 for _ in f)
        if n > LOG_KEEP_LINES:
            with open(latest, encoding='utf-8') as f:
                content = f.readlines()
            with open(latest, 'w', encoding='utf-8') as f:
                f.writelines(content[-LOG_KEEP_LINES:])

        return path
    except (IOError, OSError) as e:
        sys.stderr.write('日志写入失败: %s\n' % e)
        return None


def save_alert(rep, text):
    """写告警文件"""
    try:
        os.makedirs(ALERT_DIR, exist_ok=True)
        fname = 'alert-%s-%s.txt' % (
            rep.level(), datetime.now().strftime('%Y%m%d-%H%M%S'))
        path = os.path.join(ALERT_DIR, fname)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text + '\n')

        # 清理过期告警
        cutoff = time.time() - ALERT_KEEP_DAYS * 86400
        for name in os.listdir(ALERT_DIR):
            p = os.path.join(ALERT_DIR, name)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.unlink(p)
            except OSError:
                pass

        return path
    except (IOError, OSError) as e:
        sys.stderr.write('告警写入失败: %s\n' % e)
        return None


def load_last_level():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            return f.read().strip()
    except (IOError, OSError):
        return None


def save_level(level):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            f.write(level)
    except (IOError, OSError):
        pass


# ==============================================================================
# 主流程
# ==============================================================================

def main():
    args = sys.argv[1:]
    no_mail = '--no-mail' in args
    verbose = '--verbose' in args or '-v' in args

    # 测试邮件配置
    if '--test-mail' in args:
        if notify is None:
            print('❌ 无法导入 notify 模块')
            return 1
        ok, info = notify.send(
            '[测试] 运维巡检邮件通知',
            '这是一条测试消息。\n\n如果你收到它，说明告警邮件的配置是正确的。\n'
            '发送时间: %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        print(('✅ ' if ok else '❌ ') + info)
        return 0 if ok else 1

    rep = Report()
    check_disk(rep)
    check_inode(rep)
    check_services(rep)
    check_heartbeat(rep)
    check_memory(rep)
    check_load(rep)
    check_traffic(rep)
    check_mariadb(rep)
    check_disk_detail(rep)

    text = format_report(rep, verbose=verbose)
    print(text)

    # 写日志
    log_path = save_log(text)
    if log_path:
        print('\n日志: %s' % log_path)

    # 写告警 + 发邮件
    if rep.level() != 'OK':
        alert_path = save_alert(rep, text)
        if alert_path:
            print('告警: %s' % alert_path)

        # 只在等级变化或首次告警时发邮件（避免每天重复轰炸）
        last = load_last_level()
        should_mail = (last != rep.level())

        if no_mail:
            print('（--no-mail）未发送邮件')
        elif notify is None:
            print('（无法导入 notify 模块）未发送邮件')
        elif not should_mail and last is not None:
            print('（上次已是 %s，不重复发送）' % last)
        else:
            subject = '[%s] 服务器巡检告警 (%s)' % (rep.level(), datetime.now().strftime('%m-%d %H:%M'))
            body = text + '\n\n--\n来自阿里云 ECS 日常巡检\n'
            ok, info = notify.send(subject, body)
            print(('邮件: ✅ %s' % info) if ok else ('邮件: ❌ %s' % info))

    save_level(rep.level())
    return rep.code()


if __name__ == '__main__':
    sys.exit(main())
