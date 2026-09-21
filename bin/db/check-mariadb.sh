#!/bin/bash
# ==============================================================================
# MariaDB 健康检查
#
# 检查：容器 / 连接 / 内存 / 数据卷 / 连接数 / 备份时效 / 网络暴露
# 退出码：0=正常 1=警告 2=异常
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

BACKUP_DIR="/root/ops-lab/logs/db-backup"

FAIL=0
WARN=0

echo
echo "════════════════════════════════════════════════════════════"
echo " MariaDB 健康检查  $(date '+%F %T')"
echo "════════════════════════════════════════════════════════════"

if ! load_db_config; then
    exit 2
fi

echo
echo "【1】容器状态"
if docker ps --filter "name=${DB_CONTAINER}" --format '{{.Names}}' \
   | grep -q "^${DB_CONTAINER}$"; then
    UPTIME=$(docker inspect --format '{{.State.StartedAt}}' "${DB_CONTAINER}" 2>/dev/null)
    echo "  ${C_OK}✅ 运行中${C_RST}  启动于: ${UPTIME}"
else
    echo "  ${C_BAD}❌ 未运行${C_RST}"
    FAIL=$((FAIL + 1))
fi

echo
echo "【2】数据库连接"
if docker exec "${DB_CONTAINER}" mariadb -uroot -p"${DB_ROOT_PASS}" \
   -e "SELECT 1" >/dev/null 2>&1; then
    VER=$(docker exec "${DB_CONTAINER}" mariadb -uroot -p"${DB_ROOT_PASS}" \
          -sN -e "SELECT VERSION()" 2>/dev/null)
    echo "  ${C_OK}✅ 可连接${C_RST}"
    echo "     版本: ${VER}"
else
    echo "  ${C_BAD}❌ 无法连接${C_RST}"
    FAIL=$((FAIL + 1))
fi

echo
echo "【3】内存占用"
STATS=$(docker stats "${DB_CONTAINER}" --no-stream --format '{{.MemUsage}}|{{.MemPerc}}' 2>/dev/null)
if [ -n "$STATS" ]; then
    MEM=$(echo "$STATS" | cut -d'|' -f1)
    PCT=$(echo "$STATS" | cut -d'|' -f2 | tr -d '%')
    echo "  ${MEM}  (${PCT}%)"
    if [ "${PCT%.*}" -ge 80 ]; then
        echo "  ${C_WARN}⚠️  接近内存限额${C_RST}"
        WARN=$((WARN + 1))
    fi
fi

echo
echo "【4】数据卷与磁盘"
VOLSIZE=$(du -sh /var/lib/docker/volumes/"${DB_NAME}"-data 2>/dev/null | cut -f1)
VOLSIZE=${VOLSIZE:-$(du -sh /var/lib/docker/volumes/mariadb-data 2>/dev/null | cut -f1)}
echo "  数据卷:   ${VOLSIZE:-未知}"
DISK_PCT=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
echo "  磁盘使用: ${DISK_PCT}%"
if [ "$DISK_PCT" -ge 80 ]; then
    echo "  ${C_WARN}⚠️  磁盘使用率偏高${C_RST}"
    WARN=$((WARN + 1))
fi

echo
echo "【5】连接与数据库统计"
db_root -e "
SELECT
  (SELECT COUNT(*) FROM information_schema.PROCESSLIST) AS conn,
  @@max_connections AS max_conn,
  (SELECT COUNT(*) FROM information_schema.SCHEMATA
     WHERE SCHEMA_NAME NOT IN
       ('information_schema','mysql','performance_schema','sys')) AS user_dbs;
" 2>/dev/null | sed 's/^/  /'

echo
echo "【6】最近备份"
LATEST=$(ls -t "$BACKUP_DIR"/*.sql 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
    AGE_H=$(( ($(date +%s) - $(stat -c %Y "$LATEST")) / 3600 ))
    echo "  $(basename "$LATEST")  ($(du -h "$LATEST" | cut -f1), ${AGE_H} 小时前)"
    if [ "$AGE_H" -gt 48 ]; then
        echo "  ${C_WARN}⚠️  超过 48 小时未备份${C_RST}"
        WARN=$((WARN + 1))
    else
        echo "  ${C_OK}✅ 备份较新${C_RST}"
    fi
else
    echo "  ${C_WARN}⚠️  从未备份${C_RST}"
    WARN=$((WARN + 1))
fi

echo
echo "【7】网络暴露（安全）"
BIND=$(ss -tlnp 2>/dev/null | grep ':3306' | awk '{print $4}' | head -1)
if echo "$BIND" | grep -q '127.0.0.1:3306'; then
    echo "  ${C_OK}✅ 仅绑定本机（$BIND）${C_RST}"
elif [ -z "$BIND" ]; then
    echo "  ${C_WARN}⚠️  未监听 3306${C_RST}"
    FAIL=$((FAIL + 1))
else
    echo "  ${C_BAD}❌ 绑定地址异常: $BIND（可能暴露到公网）${C_RST}"
    FAIL=$((FAIL + 1))
fi

echo
echo "【8】凭据文件权限（安全）"
PERM=$(stat -c '%a' "$DB_CONF" 2>/dev/null)
if [ "$PERM" = "600" ]; then
    echo "  ${C_OK}✅ $DB_CONF 权限 600${C_RST}"
else
    echo "  ${C_BAD}❌ $DB_CONF 权限 $PERM（应为 600）${C_RST}"
    FAIL=$((FAIL + 1))
fi

echo
echo "════════════════════════════════════════════════════════════"
if [ "$FAIL" -gt 0 ]; then
    echo " 结果: ${C_BAD}异常（$FAIL 项失败，$WARN 项警告）${C_RST}"
    exit 2
elif [ "$WARN" -gt 0 ]; then
    echo " 结果: ${C_WARN}警告（$WARN 项）${C_RST}"
    exit 1
else
    echo " 结果: ${C_OK}全部正常${C_RST}"
    exit 0
fi
