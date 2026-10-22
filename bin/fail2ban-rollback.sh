#!/bin/bash
# fail2ban 完全回滚：卸载服务、清空所有封禁、移除包
# 用途：如果 fail2ban 导致任何问题，运行此脚本即可完全恢复。
#
# ═══ 本项目 Shell 脚本规范（详见 docs/SHELL-CONVENTIONS.md）═══
#   0. 脚本自带 PATH —— cron 与 at 的 PATH 都不含 /usr/sbin，
#      iptables、ipset 恰好在 /usr/sbin（手记 010；规范「脚本必须自带 PATH」）
#   1. 默认用 `set -uo pipefail`，谨慎使用 `-e`
#      原因：systemctl is-active / grep / diff 等【查询类命令】
#            在"否定结果"时返回非 0，配合 -e 会导致脚本意外中断
#            （真实事故见 docs/postmortem/008-set-e-systemctl-is-active.md）
#   2. 若用了 -e，所有查询类命令必须加 `|| true` 保护
#   3. 破坏性脚本必须用 `trap cleanup EXIT` 保证环境恢复
#   4. 不用 bc，改用整数运算（依赖更少）
#   5. 管道 + grep -q 要防 SIGPIPE（复盘 #007）
#   6. 统计类输出必须区分「0 条」和「量不出来」——两者含义相反
# ═══════════════════════════════════════════════════════════════

set -uo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

# ── 预检 ──
# iptables / ipset 是用来清空防火墙规则的。缺了它们就清不了，
# 原来的版本会一路走完并打印「剩余 f2b 相关规则: 0」——把「量不出来」
# 显示成「已经干净」，是回滚脚本里最坏的一种谎。
PRE_MISSING=""
for c in iptables ipset systemctl; do
  command -v "$c" >/dev/null 2>&1 || PRE_MISSING="$PRE_MISSING $c"
done
if [ -n "$PRE_MISSING" ]; then
  echo "❌ 缺少命令:$PRE_MISSING"
  echo "   PATH=$PATH"
  echo "   这些命令用于清空防火墙规则，缺了就无法确认回滚完成，已中止。"
  exit 78
fi

FAILED=0

# 统计 f2b 相关规则条数。
# 关键：grep -c 没匹配时「打印 0 并返回 1」，不能拿它的返回码判断失败，
# 否则「规则已经清空」会被误报成「量不出来」。先取 iptables 输出，取不到才算失败。
f2b_rule_count() {
  local out n
  if ! out=$(iptables -S 2>/dev/null); then
    echo "无法统计（iptables 调用失败）"
    return
  fi
  n=$(printf '%s\n' "$out" | grep -c f2b || true)
  echo "${n:-0}"
}

# 计数并把「量不出来」记为失败，避免最后仍然打印回滚完成
count_and_judge() {
  local label cnt
  label="$1"
  cnt=$(f2b_rule_count)
  echo "  $label: $cnt"
  case "$cnt" in
    *无法统计*) FAILED=$((FAILED+1)) ;;
  esac
}

echo "════════════════════════════════════════════════════════════"
echo " fail2ban 回滚  $(date '+%F %T')"
echo "════════════════════════════════════════════════════════════"
echo

echo "[1/4] 停止并禁用服务"
systemctl stop fail2ban 2>/dev/null && echo "  已停止" || echo "  (未运行)"
systemctl disable fail2ban 2>/dev/null && echo "  已禁用自启" || echo "  (已禁用)"

echo
echo "[2/4] 清空所有封禁规则（关键：先解封再卸载）"
if command -v fail2ban-client >/dev/null 2>&1; then
  for jail in $(fail2ban-client status 2>/dev/null | grep 'Jail list' | sed 's/.*:\s*//' | tr ',' ' '); do
    fail2ban-client unban --all 2>/dev/null && echo "  已清空 $jail 的封禁"
  done
else
  echo "  (fail2ban-client 不存在，跳过解封)"
fi

echo
echo "[3/4] 清理残留的 iptables 链和 ipset"
for chain in $(iptables -S 2>/dev/null | awk '/^-N f2b/{print $2}'); do
  iptables -F "$chain" 2>/dev/null
  # 从 INPUT 中移除对该链的引用。
  # 原来写成：while iptables -S INPUT | grep -q -- "-j $chain"
  # grep -q 一命中就退出，上游 iptables 收 SIGPIPE，pipefail 下整条管道
  # 返回非 0，循环会提前结束（复盘 #007 同一个坑）。这里改成先取输出再判断。
  while :; do
    RULES=$(iptables -S INPUT 2>/dev/null || true)
    case "$RULES" in
      *"-j $chain"*) : ;;
      *) break ;;
    esac
    iptables -D INPUT -j "$chain" 2>/dev/null || break
  done
  if iptables -X "$chain" 2>/dev/null; then
    echo "  已删除链 $chain"
  else
    echo "  ⚠️ 链 $chain 删除失败"
    FAILED=$((FAILED+1))
  fi
done
for set in $(ipset list -n 2>/dev/null | grep -i f2b); do
  if ipset destroy "$set" 2>/dev/null; then
    echo "  已删除 ipset $set"
  else
    echo "  ⚠️ ipset $set 删除失败"
    FAILED=$((FAILED+1))
  fi
done
count_and_judge "剩余 f2b 相关规则"

echo
echo "[4/4] 卸载软件包与配置"
if yum remove -y fail2ban fail2ban-server fail2ban-firewalld 2>&1 | tail -3 | sed 's/^/  /'; then
  :
else
  echo "  ⚠️ yum 卸载返回非 0，请人工确认包状态"
  FAILED=$((FAILED+1))
fi
rm -rf /etc/fail2ban

echo
echo "  验证:"
command -v fail2ban-client >/dev/null 2>&1 && echo "  ⚠️ 命令仍在" || echo "  ✅ fail2ban-client 已移除"
UNIT_CNT=$(systemctl list-unit-files 2>/dev/null | grep -c '^fail2ban' || true)
if [ "${UNIT_CNT:-0}" -gt 0 ]; then
  echo "  ⚠️ 服务单元仍在（$UNIT_CNT 个）"
else
  echo "  ✅ 服务单元已移除"
fi
count_and_judge "iptables f2b 规则数"

echo
echo "  服务状态确认:"
printf "    sshd=%s  nginx=%s  docker=%s\n" \
  "$(systemctl is-active sshd 2>/dev/null || true)" \
  "$(systemctl is-active nginx 2>/dev/null || true)" \
  "$(systemctl is-active docker 2>/dev/null || true)"
echo "    站点: HTTP $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/ || echo 000)"
echo
if [ "$FAILED" -eq 0 ]; then
  echo "✅ 回滚完成"
else
  echo "⚠️ 回滚结束，但有 $FAILED 项未完成，见上面的 ⚠️"
  exit 1
fi
