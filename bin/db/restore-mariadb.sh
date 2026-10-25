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
# ── 护栏：挡住「整实例」备份直接灌进生产 ──
# 为什么要这段：默认备份用 --all-databases 打的，含 mysql 系统库的 28 条 DROP TABLE。
# 下面「执行恢复」那段是不带 --one-database 的裸导入 —— 等于在生产的 MariaDB 上重灌
# mysql.user / mysql.db / mysql.global_priv（权限表），并重建备份时刻已存在的
# 其它库（blog / test / p / ps）。验证段只看库名列表，看不出来，所以必须在这里拦。
if [ "${ALLOW_FULL:-0}" != "1" ] && grep -q 'CREATE DATABASE .*`mysql`' "$BACKUP_FILE"; then
    echo
    echo "${C_BAD}❌ 这是一份「整实例」备份（含 mysql 系统库），拒绝直接恢复${C_RST}"
    echo "   直接导入会覆盖权限表并重建其它库，不是「回滚数据」而是「回滚整台实例」。"
    echo
    echo "   只恢复单个库（推荐）："
    echo "     docker exec -i \${DB_CONTAINER} mariadb -uroot -p\"\${DB_ROOT_PASS}\" \\"
    echo "       --one-database ${DB_NAME:-opslab} < $(basename "$BACKUP_FILE")"
    echo
    echo "   确实要整实例恢复（清楚后果再执行）："
    echo "     ALLOW_FULL=1 $0 $*"
    exit 78
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
OUT=$(docker exec -i "${DB_CONTAINER}" mariadb -uroot -p"${DB_ROOT_PASS}" < "$BACKUP_FILE" 2>&1)   # 先收完整输出
RC=$?                                                                                              # 退出码来自 mariadb，不是 grep
printf '%s\n' "$OUT" | grep -v "Using a password on the command line" | sed 's/^/  /'             # 只用来过滤显示
if [ "$RC" -eq 0 ]; then
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
