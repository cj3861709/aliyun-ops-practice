#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify.py —— 邮件通知模块

设计要点：

  1. 【凭据与代码分离】
     邮箱账号和授权码放在 /etc/ops-lab/mail.conf（600 权限），
     不进 Git 仓库。代码里只引用路径。

  2. 【端口选择】
     阿里云 ECS 默认封锁 25 端口（防垃圾邮件），必须用：
       · 465 —— SSL 直连（推荐）
       · 587 —— STARTTLS 升级
     不能用 25。

  3. 【失败不中断主流程】
     邮件发送失败不应该让巡检脚本本身报错退出。
     通知是"尽力而为"，巡检结果才是主要产出。

  4. 【超时保护】
     SMTP 连接可能挂起，必须设超时，否则巡检脚本会被卡住。

配置文件格式（/etc/ops-lab/mail.conf）：

    [smtp]
    host = smtp.qq.com
    port = 465
    user = your@qq.com
    password = 授权码（不是登录密码）
    from = your@qq.com
    to = 接收告警的邮箱

运行环境：Python 3.6+
依赖：仅标准库
"""

import os
import smtplib
import socket
import ssl
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

# Python 3.6 的 configparser 叫 ConfigParser
try:
    import configparser
except ImportError:                                     # pragma: no cover
    import ConfigParser as configparser

DEFAULT_CONF = "/etc/ops-lab/mail.conf"
SMTP_TIMEOUT = 20       # 秒


class MailConfigError(Exception):
    """配置文件问题"""
    pass


def load_config(path=DEFAULT_CONF):
    """读取 SMTP 配置"""
    if not os.path.isfile(path):
        raise MailConfigError("配置文件不存在: %s" % path)

    # 权限检查：包含凭据的文件不应被其他用户读取
    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:
        # 只警告，不中断（方便调试）
        sys.stderr.write(
            "[warn] %s 权限为 %o，建议改为 600（当前其他用户可读）\n"
            % (path, mode))

    cp = configparser.ConfigParser()
    try:
        cp.read(path, encoding='utf-8')
    except Exception as e:
        raise MailConfigError("配置文件解析失败: %s" % e)

    if not cp.has_section('smtp'):
        raise MailConfigError("配置文件缺少 [smtp] 段")

    cfg = {}
    for key in ('host', 'port', 'user', 'password', 'from', 'to'):
        if not cp.has_option('smtp', key):
            raise MailConfigError("配置缺少 [smtp] %s" % key)
        cfg[key] = cp.get('smtp', key).strip()

    try:
        cfg['port'] = int(cfg['port'])
    except ValueError:
        raise MailConfigError("port 必须是数字: %r" % cfg['port'])

    return cfg


def build_message(cfg, subject, body):
    """构造 MIMEText 邮件"""
    msg = MIMEText(body, 'plain', 'utf-8')
    # 主题含中文时必须用 Header 编码，否则会乱码
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr(('运维巡检', cfg['from']))
    msg['To'] = cfg['to']
    msg['Date'] = formatdate(localtime=True)
    return msg


def send(subject, body, conf_path=DEFAULT_CONF):
    """
    发送邮件。

    返回 (成功?, 错误信息)
    注意：不抛异常 —— 调用方不应该因为通知失败而中断。
    """
    try:
        cfg = load_config(conf_path)
    except MailConfigError as e:
        return False, "配置错误: %s" % e

    msg = build_message(cfg, subject, body)
    recipients = [x.strip() for x in cfg['to'].split(',') if x.strip()]

    try:
        if cfg['port'] == 465:
            # SSL 直连
            ctx = ssl.create_default_context()
            server = smtplib.SMTP_SSL(cfg['host'], cfg['port'],
                                      timeout=SMTP_TIMEOUT, context=ctx)
        else:
            # 587 或自定义端口 —— STARTTLS
            server = smtplib.SMTP(cfg['host'], cfg['port'],
                                  timeout=SMTP_TIMEOUT)
            server.ehlo()
            if server.has_extn('starttls'):
                server.starttls(context=ssl.create_default_context())
                server.ehlo()

        with server:
            server.login(cfg['user'], cfg['password'])
            server.sendmail(cfg['from'], recipients, msg.as_string())
        return True, "已发送至 %s" % ', '.join(recipients)

    except smtplib.SMTPAuthenticationError:
        return False, "认证失败 —— 检查是否用了【授权码】而不是登录密码"
    except smtplib.SMTPException as e:
        return False, "SMTP 错误: %s" % e
    except socket.timeout:
        return False, "连接超时（%d 秒）—— 检查端口是否被封锁" % SMTP_TIMEOUT
    except socket.gaierror as e:
        return False, "域名解析失败: %s" % e
    except OSError as e:
        # 常见于：阿里云封 25 端口 → Connection refused / timed out
        return False, "网络错误: %s（阿里云封 25 端口，请用 465/587）" % e
    except Exception as e:
        return False, "未知错误: %s: %s" % (type(e).__name__, e)


def main():
    """命令行测试：python3 notify.py 主题 正文"""
    if len(sys.argv) < 2:
        print("用法: %s <主题> [正文]" % sys.argv[0])
        print("      %s --check   仅检查配置" % sys.argv[0])
        return 1

    if sys.argv[1] == '--check':
        try:
            cfg = load_config()
        except MailConfigError as e:
            print("❌ %s" % e)
            return 1
        print("✅ 配置可读")
        print("   SMTP:    %s:%d" % (cfg['host'], cfg['port']))
        print("   发信人:  %s" % cfg['from'])
        print("   收信人:  %s" % cfg['to'])
        print("   授权码:  %s" % ('*' * len(cfg['password'])))
        return 0

    subject = sys.argv[1]
    body = sys.argv[2] if len(sys.argv) > 2 else '这是一条测试消息。'
    ok, info = send(subject, body)
    if ok:
        print("✅ %s" % info)
        return 0
    print("❌ %s" % info)
    return 1


if __name__ == '__main__':
    sys.exit(main())
