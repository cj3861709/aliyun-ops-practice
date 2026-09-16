#!/bin/bash
# 检查已知偏差是否仍符合登记。
#
# 设计要点（吸取上一版的教训）：
#   1. 不用 printf 无换行 + echo 的拼接方式，避免值里的换行污染输出
#   2. 用 tr -d '\n' 清理命令输出中的换行
#   3. 每一项单独成行输出，格式固定，便于日志解析
#   4. 重复检查同一项时，只在一行内给出结论，不拆成两行
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

DOC=/root/ops-lab/docs/KNOWN-DEVIATIONS.md
ISSUES=0

# 辅助函数：清理命令输出（去换行、去首尾空格）
clean() {
  printf '%s' "$1" | tr -d '\n\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

check_item() {
  # $1=编号 $2=描述 $3=期望值 $4=实际值
  local id="$1" desc="$2" want="$3" got="$4"
  if [ "$got" = "$want" ]; then
    printf "  %-8s %-34s 期望=%-12s 实际=%-12s ✅\n" "$id" "$desc" "$want" "$got"
  else
    printf "  %-8s %-34s 期望=%-12s 实际=%-12s ⚠️\n" "$id" "$desc" "$want" "$got"
    ISSUES=$((ISSUES + 1))
  fi
}

echo "── 已知偏差检查 ──"
echo

# DEV-001: kdump 应为 disabled + inactive
KS=$(clean "$(systemctl is-enabled kdump 2>/dev/null)"); [ -z "$KS" ] && KS="unknown"
KA=$(clean "$(systemctl is-active kdump 2>/dev/null)"); [ -z "$KA" ] && KA="unknown"
check_item "DEV-001" "kdump 自启状态" "disabled" "$KS"
check_item "DEV-001" "kdump 运行状态" "inactive" "$KA"

# DEV-002: rp_filter 应为 0（经评估接受）
RF=$(clean "$(sysctl -n net.ipv4.conf.all.rp_filter 2>/dev/null)")
check_item "DEV-002" "rp_filter（经评估接受）" "0" "$RF"

# 关键保护：ip_forward 必须为 1
IF=$(clean "$(sysctl -n net.ipv4.ip_forward 2>/dev/null)")
check_item "SAFETY" "ip_forward（Docker 依赖）" "1" "$IF"

# DEV-003: INPUT 策略必须保持 ACCEPT（fail2ban 只做 IP 级封禁）
INPUT_POLICY=$(printf '%s' "$(iptables -S INPUT 2>/dev/null | head -1)" | tr -d '\n')
if echo "$INPUT_POLICY" | grep -q 'ACCEPT'; then
  printf "  %-8s %-34s %s\n" "DEV-003" "INPUT 策略（应为 ACCEPT）" "✅"
else
  printf "  %-8s %-34s 实际=%s ⚠️\n" "DEV-003" "INPUT 策略（应为 ACCEPT）" "$INPUT_POLICY"
  echo "           防火墙策略被改动了！这可能影响连接，请确认是否有人有意为之"
  ISSUES=$((ISSUES + 1))
fi

# DEV-004: fail2ban jail 白名单必须含本机回环
if systemctl is-active --quiet fail2ban 2>/dev/null; then
  F2B_WL_OK=1
  for j in $(fail2ban-client status 2>/dev/null | grep 'Jail list' | sed 's/.*:\s*//' | tr ',' ' '); do
    [ -z "$j" ] && continue
    fail2ban-client get "$j" ignoreip 2>/dev/null | grep -qE '127\.0\.0\.(0/8|1)' || F2B_WL_OK=0
  done
  if [ "$F2B_WL_OK" -eq 1 ]; then
    printf "  %-8s %-34s %s\n" "DEV-004" "fail2ban 白名单含回环" "✅"
  else
    printf "  %-8s %-34s ⚠️\n" "DEV-004" "fail2ban 白名单含回环"
    echo "           有 jail 的白名单缺失本机回环，存在自锁风险"
    ISSUES=$((ISSUES + 1))
  fi
fi

echo
if [ "$ISSUES" -eq 0 ]; then
  echo "  ✅ 所有已知偏差均符合登记，无意外变化"
  exit 0
else
  echo "  ⚠️ $ISSUES 项与登记不符，请人工确认"
  echo "     详见: $DOC"
  echo "     注意：不符【不一定】是问题——可能是有人有意改进了配置，"
  echo "           那就应该更新登记文件，而不是改回配置。"
  exit 1
fi
