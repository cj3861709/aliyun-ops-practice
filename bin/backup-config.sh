#!/bin/bash
# 备份关键配置。用法: backup-config.sh [标签]
# 备份产物: /root/ops-lab/backups/<时间戳>-<标签>.tar.gz
#
# ═══ 本项目 Shell 脚本规范（详见 docs/SHELL-CONVENTIONS.md）═══
#   1. 默认用 `set -uo pipefail`，谨慎使用 `-e`
#      原因：systemctl is-active / grep / diff 等【查询类命令】
#            在"否定结果"时返回非 0，配合 -e 会导致脚本意外中断
#            （真实事故见 docs/postmortem/008-set-e-systemctl-is-active.md）
#   2. 若用了 -e，所有查询类命令必须加 `|| true` 保护
#   3. 破坏性脚本必须用 `trap cleanup EXIT` 保证环境恢复
#   4. 不用 bc，改用整数运算（依赖更少）
#   5. 管道 + grep -q 要防 SIGPIPE（复盘 #007）
# ═══════════════════════════════════════════════════════════════

set -uo pipefail

STAMP=$(date +%Y%m%d-%H%M%S)
LABEL="${1:-manual}"
DEST_DIR=/root/ops-lab/backups
DEST="$DEST_DIR/${STAMP}-${LABEL}.tar.gz"

mkdir -p "$DEST_DIR"

# 只备份存在的东西，收集清单
INCLUDE=()
# 收集列表（2026-09-13 演练后扩充：加入 fail2ban 与 sysctl）
for p in /etc/nginx /etc/ssh/sshd_config /etc/crontab /etc/cron.d \
         /etc/systemd/system /etc/hosts /etc/fstab /etc/docker \
         /root/.ssh/authorized_keys \
         /etc/fail2ban /etc/sysctl.conf /etc/sysctl.d /etc/security/limits.conf \
         /etc/logrotate.d /etc/logrotate.conf; do
  [ -e "$p" ] && INCLUDE+=("$p")
done

# crontab 单独导出（不在文件系统里）
CRON_TMP=""
if crontab -l >/dev/null 2>&1; then
  CRON_TMP=$(mktemp)
  crontab -l > "$CRON_TMP" 2>/dev/null || true
  INCLUDE+=("$CRON_TMP")
fi

# iptables 规则快照
IPT_TMP=""
if command -v iptables-save >/dev/null 2>&1; then
  IPT_TMP=$(mktemp)
  iptables-save > "$IPT_TMP" 2>/dev/null || true
  INCLUDE+=("$IPT_TMP")
fi

if [ ${#INCLUDE[@]} -eq 0 ]; then
  echo "没有任何可备份的路径，退出"
  exit 1
fi

tar -czf "$DEST" \
    --exclude="*.swp" --exclude="*.swo" --exclude="*~" \
    --exclude=".viminfo" --exclude="*.tmp" \
    "${INCLUDE[@]}" 2>/dev/null
[ -n "$CRON_TMP" ] && rm -f "$CRON_TMP"
[ -n "$IPT_TMP" ] && rm -f "$IPT_TMP"

SIZE=$(du -h "$DEST" | cut -f1)
echo "已备份 -> $DEST ($SIZE)"
echo "包含 ${#INCLUDE[@]} 个路径"

# 只保留最近 30 份，避免撑爆磁盘（这本身就是运维习惯）
COUNT=$(ls -1 "$DEST_DIR"/*.tar.gz 2>/dev/null | wc -l)
if [ "$COUNT" -gt 30 ]; then
  ls -1t "$DEST_DIR"/*.tar.gz | tail -n +31 | xargs -r rm -f
  echo "清理旧备份，保留最近 30 份"
fi
