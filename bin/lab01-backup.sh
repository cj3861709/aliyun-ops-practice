#!/bin/bash
# 备份 nginx 配置到 ops-lab/backups
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

set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
DEST_DIR=/root/ops-lab/backups
DEST="$DEST_DIR/${STAMP}-lab01-nginx.tar.gz"
mkdir -p "$DEST_DIR"
tar -czf "$DEST" /etc/nginx
echo "已备份 -> $DEST ($(du -h "$DEST" | cut -f1))"
