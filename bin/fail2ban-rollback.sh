#!/bin/bash
# fail2ban 完全回滚：卸载服务、清空所有封禁、移除包
# 用途：如果 fail2ban 导致任何问题，运行此脚本即可完全恢复。
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
fi

echo
echo "[3/4] 清理残留的 iptables 链和 ipset"
for chain in $(iptables -S 2>/dev/null | awk '/^-N f2b/{print $2}'); do
  iptables -F "$chain" 2>/dev/null
  # 从 INPUT 中移除对该链的引用
  while iptables -S INPUT 2>/dev/null | grep -q -- "-j $chain"; do
    iptables -D INPUT -j "$chain" 2>/dev/null || break
  done
  iptables -X "$chain" 2>/dev/null && echo "  已删除链 $chain"
done
for set in $(ipset list -n 2>/dev/null | grep -i f2b); do
  ipset destroy "$set" 2>/dev/null && echo "  已删除 ipset $set"
done
echo "  剩余 f2b 相关规则: $(iptables -S 2>/dev/null | grep -c f2b | tr -d '\n')"

echo
echo "[4/4] 卸载软件包与配置"
yum remove -y fail2ban fail2ban-server fail2ban-firewalld 2>&1 | tail -3 | sed 's/^/  /'
rm -rf /etc/fail2ban

echo
echo "  验证:"
command -v fail2ban-client >/dev/null 2>&1 && echo "  ⚠️ 命令仍在" || echo "  ✅ fail2ban-client 已移除"
systemctl list-unit-files 2>/dev/null | grep -q '^fail2ban' && echo "  ⚠️ 服务单元仍在" || echo "  ✅ 服务单元已移除"
echo "  iptables f2b 规则数: $(iptables -S 2>/dev/null | grep -c f2b | tr -d '\n')"

echo
echo "  服务状态确认:"
printf "    sshd=%s  nginx=%s  docker=%s\n" \
  "$(systemctl is-active sshd)" "$(systemctl is-active nginx)" "$(systemctl is-active docker)"
echo "    站点: HTTP $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/)"
echo
echo "✅ 回滚完成"
