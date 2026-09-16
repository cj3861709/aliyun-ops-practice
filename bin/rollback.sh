#!/bin/bash
# ==============================================================================
# 回滚脚本 rollback.sh
#
# 用法:
#   rollback.sh              回滚到上一个版本
#   rollback.sh <版本名>      回滚到指定版本
#   rollback.sh --list        列出所有可回滚的版本
#
# 回滚为什么快：
#   文件早已在 releases/ 里，回滚只是把软链接指回去 + reload，毫秒级完成。
#   不需要重新传输文件，不需要重新构建。
#   这就是"不可变发布物 + 符号链接切换"模式的价值。
# ==============================================================================
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

RELEASES=/var/www/releases
LIVE=/var/www/personal-site
CURRENT_FILE=$RELEASES/.current

list_releases() {
  echo "可用的版本（新的在上）:"
  local cur
  cur=$(cat "$CURRENT_FILE" 2>/dev/null || echo "")
  for d in $(ls -1dt "$RELEASES"/*/ 2>/dev/null | sed 's|/$||'); do
    local name size files
    name=$(basename "$d")
    size=$(du -sh "$d" 2>/dev/null | cut -f1)
    files=$(find "$d" -type f | wc -l)
    if [ "$name" = "$cur" ]; then
      printf "  * %-40s %6s  %2s 文件   <- 当前生效\n" "$name" "$size" "$files"
    else
      printf "    %-40s %6s  %2s 文件\n" "$name" "$size" "$files"
    fi
  done
}

if [ "${1:-}" = "--list" ] || [ "${1:-}" = "-l" ]; then
  list_releases
  exit 0
fi

echo "════════════════════════════════════════════════════"
echo " 回滚  $(date '+%F %T')"
echo "════════════════════════════════════════════════════"

CURRENT=$(cat "$CURRENT_FILE" 2>/dev/null || echo "")
echo
echo "当前版本: ${CURRENT:-（未知）}"

# ---------------------------------------------------------------------------
# 确定目标版本
# ---------------------------------------------------------------------------
TARGET="${1:-}"

if [ -z "$TARGET" ]; then
  # 找当前版本的"上一个"（.history 里倒数第二个）
  TARGET=$(grep -v "^${CURRENT}$" "$RELEASES/.history" 2>/dev/null | tail -1 || echo "")
  if [ -z "$TARGET" ]; then
    echo
    echo "❌ 没有可回滚的历史版本"
    echo
    list_releases
    exit 1
  fi
  echo "目标版本: $TARGET （上一个版本）"
else
  echo "目标版本: $TARGET （指定）"
fi

# ---------------------------------------------------------------------------
# 校验目标版本存在且完整
# ---------------------------------------------------------------------------
if [ ! -d "$RELEASES/$TARGET" ]; then
  echo
  echo "❌ 目标版本不存在: $RELEASES/$TARGET"
  echo
  list_releases
  exit 1
fi

if [ ! -f "$RELEASES/$TARGET/index.html" ]; then
  echo "❌ 目标版本缺少 index.html，拒绝回滚（否则会回滚到 404）"
  exit 1
fi
echo "✅ 目标版本存在且完整"

# ---------------------------------------------------------------------------
# 权限预检
# ---------------------------------------------------------------------------
if id nginx >/dev/null 2>&1 && ! sudo -u nginx test -r "$RELEASES/$TARGET/index.html"; then
  echo "❌ nginx 用户无法读取目标版本"
  exit 1
fi

# ---------------------------------------------------------------------------
# 原子切换
# ---------------------------------------------------------------------------
echo
echo "切换软链接..."
TMPLINK="/var/www/.personal-site.rollback.$$"
ln -s "$RELEASES/$TARGET" "$TMPLINK"
mv -T "$TMPLINK" "$LIVE"
echo "  $LIVE -> $(readlink "$LIVE")"

echo "$TARGET" > "$CURRENT_FILE"
echo "$TARGET" >> "$RELEASES/.history"

echo
echo "reload nginx..."
systemctl reload nginx

# ---------------------------------------------------------------------------
# 验证
# ---------------------------------------------------------------------------
echo
echo "健康检查:"
OK=1
for i in 1 2 3; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1/ || echo 000)
  printf "  第 %d 次: HTTP %s\n" "$i" "$CODE"
  [ "$CODE" = "200" ] || OK=0
  sleep 0.3
done

echo
if [ "$OK" -eq 1 ]; then
  echo "════════════════════════════════════════════════════"
  echo " ✅ 回滚成功"
  echo " 版本: $CURRENT  ->  $TARGET"
  echo " 页面: $(curl -s http://127.0.0.1/ | grep -o '<title>[^<]*</title>' || echo '(无标题)')"
  echo "════════════════════════════════════════════════════"
else
  echo " ❌ 回滚后健康检查仍失败，需要人工介入"
  exit 1
fi
