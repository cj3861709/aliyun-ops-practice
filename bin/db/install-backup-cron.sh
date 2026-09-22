#!/bin/bash
# ==============================================================================
# 安装/卸载数据库自动备份定时任务
#
# 用法：
#   install-backup-cron.sh           安装（每天 03:30）
#   install-backup-cron.sh remove    卸载
#   install-backup-cron.sh status    查看状态
# ==============================================================================
set -uo pipefail

MARKER="# ops-lab db-backup"
SCRIPT="/root/ops-lab/bin/db/backup-mariadb.sh"
LOGDIR="/root/ops-lab/logs/db-backup"
# 03:30 执行 —— 选择理由：避开 08:00 的巡检，且是凌晨低峰期
CRON_LINE="30 3 * * * $SCRIPT >> $LOGDIR/cron.log 2>&1  $MARKER"

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_RST=$'\033[0m'

case "${1:-install}" in
    status)
        echo "当前数据库备份定时任务:"
        crontab -l 2>/dev/null | grep -F "$MARKER" | sed 's/^/  /' || echo "  （未安装）"
        echo
        echo "已有备份:"
        ls -lht "$LOGDIR"/*.sql 2>/dev/null | head -5 | awk '{printf "  %-8s %s\n", $5, $9}' || echo "  （无）"
        ;;

    remove)
        crontab -l 2>/dev/null | grep -v "$MARKER" | crontab -
        echo "已移除"
        crontab -l 2>/dev/null | sed 's/^/  /'
        ;;

    install|*)
        mkdir -p "$LOGDIR"

        if crontab -l 2>/dev/null | grep -qF "$MARKER"; then
            echo "${C_WARN}已存在，跳过${C_RST}"
        else
            (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
            echo "${C_OK}✅ 已安装${C_RST}"
        fi

        echo
        echo "当前 crontab:"
        crontab -l 2>/dev/null | sed 's/^/  /'
        echo
        echo "下次执行: 每天 03:30"
        ;;
esac
