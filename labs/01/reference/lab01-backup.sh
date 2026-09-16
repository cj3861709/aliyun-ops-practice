#!/bin/bash
# 备份 nginx 配置到 ops-lab/backups
set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
DEST_DIR=/root/ops-lab/backups
DEST="$DEST_DIR/${STAMP}-lab01-nginx.tar.gz"
mkdir -p "$DEST_DIR"
tar -czf "$DEST" /etc/nginx
echo "已备份 -> $DEST ($(du -h "$DEST" | cut -f1))"
