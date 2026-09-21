#!/bin/bash
# ==============================================================================
# 安装 daily-check 定时任务
#
# 用法：
#   install-daily-check-cron.sh          安装（每天 08:00）
#   install-daily-check-cron.sh remove   卸载
# ==============================================================================
set -uo pipefail

MARKER="# ops-lab daily-check"
CRON_LINE="0 8 * * * /usr/bin/python3 /root/ops-lab/lib/daily-check.py >> /root/ops-lab/logs/daily-check/cron.log 2>&1  $MARKER"

if [ "${1:-install}" = "remove" ]; then
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab -
    echo "已移除 daily-check 定时任务"
    crontab -l 2>/dev/null | sed 's/^/  /'
    exit 0
fi

# 防重复安装
if crontab -l 2>/dev/null | grep -qF "$MARKER"; then
    echo "已存在，跳过安装"
    crontab -l 2>/dev/null | grep -F "$MARKER" | sed 's/^/  /'
    exit 0
fi

# 追加
(crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -

echo "已安装 daily-check 定时任务"
echo
echo "当前 crontab:"
crontab -l 2>/dev/null | sed 's/^/  /'
echo
echo "下次执行时间:"
systemctl list-timers 2>/dev/null | grep -i cron | head -2 | sed 's/^/  /' || echo "  （每天 08:00）"
