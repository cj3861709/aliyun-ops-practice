#!/bin/bash
# ==============================================================================
# MariaDB 恢复脚本
#
# 用法：
#   restore-mariadb.sh                       用最新备份
#   restore-mariadb.sh <文件>                 用指定文件
#   restore-mariadb.sh <文件> --dry-run       只验证不执行
#   restore-mariadb.sh <文件> --yes           跳过二次确认
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

BACKUP_DIR="/root/ops-lab/logs/db-backup"

DRY_RUN=0
CONFIRM=0
BACKUP_FILE=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --yes|-y)  CONFIRM=1 ;;
        *)         BACKUP_FILE="$arg" ;;
    esac
done

echo
echo "════════════════════════════════════════════════════════════"
echo " MariaDB 恢复"
echo "════════════════════════════════════════════════════════════"

if ! load_db_config; then
    exit 1
fi

if ! check_container; then
    exit 1
fi

# 默认用最新备份
if [ -z "$BACKUP_FILE" ]; then
    BACKUP_FILE=$(ls -t "$BACKUP_DIR"/*.sql 2>/dev/null | head -1)
    if [ -z "$BACKUP_FILE" ]; then
        echo "${C_BAD}❌ 没有可用备份${C_RST}"
        exit 1
    fi
    echo "使用最新备份"
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "${C_BAD}❌ 文件不存在: $BACKUP_FILE${C_RST}"
    exit 1
fi

echo
echo "备份文件: $(basename "$BACKUP_FILE")"
echo "大小:     $(du -h "$BACKUP_FILE" | cut -f1)"
echo "时间:     $(stat -c %y "$BACKUP_FILE" | cut -d. -f1)"

# ── 校验和验证 ──
if [ -f "${BACKUP_FILE}.sha256" ]; then
    echo
    echo "验证校验和..."
    if (cd "$(dirname "$BACKUP_FILE")" && sha256sum -c "$(basename "$BACKUP_FILE").sha256" >/dev/null 2>&1); then
        echo "  ${C_OK}✅ 校验通过${C_RST}"
    else
        echo "  ${C_BAD}❌ 校验失败！文件可能损坏${C_RST}"
        exit 1
    fi
else
    echo "  ${C_WARN}（无校验和文件，跳过）${C_RST}"
fi

# ── dry-run ──
if [ "$DRY_RUN" -eq 1 ]; then
    echo
    echo "${C_WARN}--dry-run 模式，不执行恢复${C_RST}"
    echo
    echo "备份内含的表:"
    grep -oE 'CREATE TABLE `[^`]+`' "$BACKUP_FILE" 2>/dev/null | sed 's/^/  /' || echo "  （未找到）"
    exit 0
fi

# ── 二次确认 ──
if [ "$CONFIRM" -eq 0 ]; then
    echo
    echo "${C_WARN}⚠️  这会覆盖当前数据库内容！${C_RST}"
    read -r -p "确认恢复？输入 yes 继续: " answer
    [ "$answer" = "yes" ] || { echo "已取消"; exit 0; }
fi

# ── 恢复前快照 ──
echo
echo "【1】恢复前快照"
SAFETY="$BACKUP_DIR/pre-restore-$(date +%Y%m%d-%H%M%S).sql"
if docker exec "${DB_CONTAINER}" mariadb-dump \
    -uroot -p"${DB_ROOT_PASS}" \
    --single-transaction --all-databases > "$SAFETY" 2>/dev/null; then
    echo "  ${C_OK}✅ $(basename "$SAFETY") ($(du -h "$SAFETY" | cut -f1))${C_RST}"
else
    echo "  ${C_BAD}❌ 快照失败，中止恢复${C_RST}"
    rm -f "$SAFETY"
    exit 1
fi

# ── 执行恢复 ──
echo
echo "【2】执行恢复"
if docker exec -i "${DB_CONTAINER}" mariadb -uroot -p"${DB_ROOT_PASS}" < "$BACKUP_FILE" 2>&1 \
   | grep -v "Using a password on the command line"; then
    echo "  ${C_OK}✅ 导入完成${C_RST}"
else
    echo "  ${C_BAD}❌ 导入失败${C_RST}"
    echo "  可用快照回滚: $SAFETY"
    exit 1
fi

# ── 验证 ──
echo
echo "【3】验证"
db_root -e "
SELECT SCHEMA_NAME AS db_name FROM information_schema.SCHEMATA
WHERE SCHEMA_NAME NOT IN ('information_schema','mysql','performance_schema','sys');
" 2>/dev/null | sed 's/^/  /'

echo
echo "${C_OK}✅ 恢复完成${C_RST}"
echo "   快照备份: $SAFETY"
echo
