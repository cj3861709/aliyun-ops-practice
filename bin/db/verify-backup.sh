#!/bin/bash
# 备份可恢复性校验：每天在一次性容器里恢复最新 all-*.sql，校验它真能用
# v2 变更：就绪探测由 mariadb-admin ping（有坑，密码错也 rc=0）改为真实登录；
#          建库加重试；任何失败都打印 stderr 与容器日志，不再吞错误
set -uo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

OPS=/root/ops-lab
BACKUP_DIR="$OPS/logs/db-backup"
LOG="$BACKUP_DIR/verify.log"
CT=drill-verify                 # 一次性容器的名字，跑完必删
TARGET_DB=opslab                # 只恢复这一个库，避免碰 mysql 系统库
DRILL_PASS="drillverify-$RANDOM$RANDOM"
READY_TIMEOUT=90
ERR=$(mktemp)

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
cleanup() { docker rm -f "$CT" >/dev/null 2>&1 || true; rm -f "$ERR"; }
trap cleanup EXIT                # 无论成功失败、Ctrl-C，都删临时容器

# 失败统一从这儿出去：先报结论，再把「为什么」原样打出来
fail() {
  log "❌ $*"
  if [ -s "$ERR" ]; then
    log "   最后一条命令的 stderr（尾部 20 行）："
    tail -20 "$ERR" | sed 's/^/     /' >> "$LOG"
    tail -20 "$ERR" | sed 's/^/     /'
  fi
  log "   临时容器日志（尾部 15 行，初始化/认证问题都在这）："
  docker logs --tail 15 "$CT" 2>&1 | sed 's/^/   │ /' | tee -a "$LOG" >/dev/null
  docker logs --tail 15 "$CT" 2>&1 | sed 's/^/   │ /'
  exit 1
}

log "──── 备份可恢复性校验开始（v2）────"

# 第 0 步：配置（拿生产侧的连接信息，用于最后对比）
if [ -r /etc/ops-lab/db.conf ]; then
  # shellcheck disable=SC1091
  . /etc/ops-lab/db.conf
else
  log "⚠️ 读不到 /etc/ops-lab/db.conf，跳过与生产侧的 checksum 对比"
fi

# 第 1 步：找最新备份
LATEST=$(ls -1t "$BACKUP_DIR"/all-*.sql 2>/dev/null | head -1)
[ -n "$LATEST" ] || fail "备份目录里没有 all-*.sql：$BACKUP_DIR"
SZ=$(stat -c %s "$LATEST" 2>/dev/null || echo 0)
case "$SZ" in ''|*[!0-9]*) fail "量不出备份文件大小：$LATEST" ;; esac
[ "$SZ" -gt 0 ] || fail "备份文件是 0 字节：$LATEST"
log "备份文件: $(basename "$LATEST") ($SZ 字节)"

# 第 2 步：硬判据 1 —— sha256 与备份时一致
if [ -f "$LATEST.sha256" ]; then
  if ( cd "$BACKUP_DIR" && sha256sum -c "$(basename "$LATEST").sha256" ) >/dev/null 2>"$ERR"; then
    log "✅ 硬判据1 sha256 通过"
  else
    fail "sha256 校验不通过（备份文件在磁盘上坏了）"
  fi
else
  log "⚠️ 没有 .sha256 旁证文件，跳过校验"
fi
: > "$ERR"

# 第 3 步：起一次性容器（不映射端口、不用生产 volume）
docker run -d --name "$CT" -e MARIADB_ROOT_PASSWORD="$DRILL_PASS" mariadb:11 >/dev/null 2>"$ERR" \
  || fail "临时容器启动失败（名字冲突？先 docker rm -f $CT）"

# 第 4 步：就绪探测 —— 打一次真登录，不是 ping
# 〔为什么〕mariadb-admin ping 在密码错误时仍返回 0，会把「还没准备好」误判成「已就绪」
ready=0
for _ in $(seq 1 "$READY_TIMEOUT"); do
  if docker exec "$CT" mariadb -uroot -p"$DRILL_PASS" -e 'SELECT 1' >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  docker exec "$CT" mariadb -uroot -p"$DRILL_PASS" -e 'SELECT 1' 2>"$ERR"   # 再打一次，把真实报错留下来
  fail "临时容器 ${READY_TIMEOUT}s 内未就绪（探测用的是真实登录）"
fi
log "临时容器就绪"

# 第 5 步：建库（重试 30 次，防初始化末尾的瞬时拒绝）
ok=0
for _ in $(seq 1 30); do
  if docker exec "$CT" mariadb -uroot -p"$DRILL_PASS" \
       -e "CREATE DATABASE IF NOT EXISTS \`$TARGET_DB\`" 2>"$ERR"; then ok=1; break; fi
  sleep 1
done
[ "$ok" = 1 ] || fail "建库失败（重试 30 次）"
: > "$ERR"
log "建库 $TARGET_DB 完成"

# 第 6 步：导入（--one-database 只回放这一个库，mysql 系统库的 DROP 全部跳过）
if docker exec -i "$CT" mariadb -uroot -p"$DRILL_PASS" --one-database "$TARGET_DB" < "$LATEST" 2>"$ERR"; then
  log "导入完成"
else
  fail "导入失败（备份文件可能损坏，或与 MariaDB 版本不兼容）"
fi
: > "$ERR"

# 第 7 步：硬判据 2 —— 恢复出来的库里至少有表
TB=$(docker exec "$CT" mariadb -uroot -p"$DRILL_PASS" -N -B \
     -e "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='$TARGET_DB'" 2>"$ERR")
case "$TB" in ''|*[!0-9]*) fail "量不出恢复后的表数（输出: ${TB:-空}）" ;; esac
[ "$TB" -gt 0 ] || fail "恢复后一张表都没有"
log "✅ 硬判据2 恢复后表数: $TB"

# 第 8 步：硬判据 3 —— 心跳表有数据（空表 = 恢复了个壳）
ROWS=$(docker exec "$CT" mariadb -uroot -p"$DRILL_PASS" -N -B \
       -e "SELECT COUNT(*) FROM \`$TARGET_DB\`.uptime_log" 2>"$ERR")
case "$ROWS" in ''|*[!0-9]*) fail "量不出 uptime_log 行数（输出: ${ROWS:-空}）" ;; esac
[ "$ROWS" -gt 0 ] || fail "uptime_log 恢复后是空表"
log "✅ 硬判据3 uptime_log 行数: $ROWS"

# 第 9 步：软判据 —— 与生产侧比 checksum
# 〔为什么只记录不判死〕心跳表在备份之后还在继续写，两侧不一致是正常的；
#   行数判据的正确对象是「备份那一刻」，不是「现在」
if [ -n "${DB_ROOT_PASS:-}" ] && [ -n "${DB_CONTAINER:-}" ]; then
  NEW=$(docker exec "$CT" mariadb -uroot -p"$DRILL_PASS" -N -B \
        -e "CHECKSUM TABLE \`$TARGET_DB\`.uptime_log" 2>/dev/null | awk '{print $2}')
  PROD=$(docker exec "$DB_CONTAINER" mariadb -uroot -p"$DB_ROOT_PASS" -N -B \
         -e "CHECKSUM TABLE \`$TARGET_DB\`.uptime_log" 2>/dev/null | awk '{print $2}')
  if [ "$NEW" = "$PROD" ]; then
    log "ℹ️ 软判据 checksum 与生产侧一致: $NEW"
  else
    log "ℹ️ 软判据 checksum 不同（恢复侧 $NEW / 生产侧 $PROD）—— 备份后有心跳写入，属正常"
  fi
fi

log "✅ 校验通过：$(basename "$LATEST") 可以在干净实例上完整恢复"
exit 0
