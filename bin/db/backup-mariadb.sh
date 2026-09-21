#!/bin/bash
# ==============================================================================
# MariaDB 备份脚本
#
# 用法：
#   backup-mariadb.sh                备份所有数据库
#   backup-mariadb.sh opslab         备份指定数据库
#   backup-mariadb.sh --list         列出已有备份
#
# 凭据从 /etc/ops-lab/db.conf 读取（不在代码里）
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

BACKUP_DIR="/root/ops-lab/logs/db-backup"
KEEP_DAYS=14

echo
echo "════════════════════════════════════════════════════════════"
echo " MariaDB 备份  $(date '+%F %T')"
echo "════════════════════════════════════════════════════════════"
echo

mkdir -p "$BACKUP_DIR"

# ── 列出备份（不需要凭据）──
if [ "${1:-}" = "--list" ]; then
    echo "备份文件（$BACKUP_DIR）:"
    ls -lht "$BACKUP_DIR"/*.sql 2>/dev/null | sed 's/^/  /' || echo "  （无备份）"
    exit 0
fi

# ── 加载凭据 ──
if ! load_db_config; then
    exit 1
fi

# ── 检查容器 ──
if ! check_container; then
    exit 1
fi

# ── 确定备份对象 ──
if [ -n "${1:-}" ]; then
    TARGET="$1"
    TARGET_ARGS=("$TARGET")
    echo "备份目标: 数据库 $TARGET"
else
    TARGET="all"
    TARGET_ARGS=("--all-databases")
    echo "备份目标: 所有数据库"
fi

STAMP=$(date +%Y%m%d-%H%M%S)
OUTFILE="$BACKUP_DIR/${TARGET}-${STAMP}.sql"
echo "输出文件: $OUTFILE"
echo

# ── 执行备份 ──
START=$(date +%s)
ERRFILE=$(mktemp)

if docker exec "${DB_CONTAINER}" mariadb-dump \
    -uroot -p"${DB_ROOT_PASS}" \
    --single-transaction \
    --routines --triggers --events \
    "${TARGET_ARGS[@]}" > "$OUTFILE" 2>"$ERRFILE"; then

    END=$(date +%s)
    SIZE=$(stat -c %s "$OUTFILE")
    LINES=$(wc -l < "$OUTFILE")

    # 校验：文件不能太小（mysqldump 失败时可能产生空文件）
    if [ "$SIZE" -lt 100 ]; then
        echo "${C_BAD}❌ 备份文件异常小（$SIZE 字节）${C_RST}"
        sed 's/^/    /' "$ERRFILE"
        rm -f "$OUTFILE" "$ERRFILE"
        exit 1
    fi

    sha256sum "$OUTFILE" > "${OUTFILE}.sha256"
    rm -f "$ERRFILE"

    echo "${C_OK}✅ 备份成功${C_RST}"
    echo "   大小:   $(numfmt --to=iec "$SIZE" 2>/dev/null || echo "$SIZE 字节")"
    echo "   行数:   $LINES"
    echo "   耗时:   $((END - START)) 秒"
    echo "   校验和: $(cut -c1-16 "${OUTFILE}.sha256")"

    # ── 清理旧备份 ──
    DELETED=$(find "$BACKUP_DIR" -name '*.sql' -mtime +"$KEEP_DAYS" -print -delete 2>/dev/null | wc -l)
    find "$BACKUP_DIR" -name '*.sha256' -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
    if [ "$DELETED" -gt 0 ]; then
        echo "   已清理 ${DELETED} 个超过 ${KEEP_DAYS} 天的旧备份"
    fi

    echo
    echo "现有备份:"
    ls -lht "$BACKUP_DIR"/*.sql 2>/dev/null | head -5 | awk '{printf "  %-8s %s\n", $5, $9}'
else
    echo "${C_BAD}❌ 备份失败${C_RST}"
    sed 's/^/    /' "$ERRFILE"
    rm -f "$OUTFILE" "$ERRFILE"
    exit 1
fi
echo
