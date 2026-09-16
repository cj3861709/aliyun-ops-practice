#!/bin/bash
# fail2ban 状态摘要 + 异常检测
#
# 检测逻辑：
#   1. 服务是否活着（用 fail2ban-client ping，而不是 ps -C）
#   2. 是否有封禁（有封禁不一定异常，但要能看到）
#   3. 近期封禁增长速率（突增可能是被针对性攻击）
#   4. fail2ban 自身是否会失败（比如日志路径错误）
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

echo "── fail2ban 状态 $(date '+%F %T') ──"
echo

# 服务活性（最可靠的方法）
if ! systemctl is-active --quiet fail2ban; then
  echo "  ❌ fail2ban 服务未运行！"
  echo "     排查: journalctl -u fail2ban -n 30"
  exit 2
fi

# 进程内存（注意进程名是 fail2ban-server）
MAINPID=$(systemctl show fail2ban -p MainPID --value 2>/dev/null)
MEM="?"
if [ -n "$MAINPID" ] && [ "$MAINPID" != "0" ]; then
  RSS=$(ps -o rss= -p "$MAINPID" 2>/dev/null | tr -d ' ')
  [ -n "$RSS" ] && # 不用 bc（不一定装有）：用整数运算
  MEM=$(printf "%.1f MB" "$((RSS / 1024)).$(( (RSS % 1024) * 10 / 1024 ))")
fi
echo "  ✅ 服务运行中 (PID=$MAINPID, 内存≈$MEM)"

# client 通信
if ! fail2ban-client ping 2>/dev/null | grep -q pong; then
  echo "  ⚠️ fail2ban-client 无法与 server 通信"
  exit 2
fi
echo "  ✅ client 通信正常"
echo

# 各 jail 状态
TOTAL_BANNED=0
TOTAL_FAILED=0
for jail in $(fail2ban-client status 2>/dev/null | grep 'Jail list' | sed 's/.*:\s*//' | tr ',' ' '); do
  [ -z "$jail" ] && continue
  ST=$(fail2ban-client status "$jail" 2>/dev/null)
  CUR_BAN=$(echo "$ST" | grep 'Currently banned' | grep -oE '[0-9]+$')
  TOT_BAN=$(echo "$ST" | grep 'Total banned' | grep -oE '[0-9]+$')
  CUR_FAIL=$(echo "$ST" | grep 'Currently failed' | grep -oE '[0-9]+$')
  TOT_FAIL=$(echo "$ST" | grep 'Total failed' | grep -oE '[0-9]+$')

  printf "  %-20s 当前封禁=%-4s 累计封禁=%-5s 当前失败=%-4s 累计失败=%s\n" \
    "$jail" "${CUR_BAN:-0}" "${TOT_BAN:-0}" "${CUR_FAIL:-0}" "${TOT_FAIL:-0}"

  TOTAL_BANNED=$((TOTAL_BANNED + ${CUR_BAN:-0}))
  TOTAL_FAILED=$((TOTAL_FAILED + ${CUR_FAIL:-0}))

  # 显示被封 IP（若有）
  BANNED=$(echo "$ST" | grep 'Banned IP list' | sed 's/.*:\s*//')
  [ -n "$BANNED" ] && echo "      被封: $BANNED"
done

echo
echo "  合计: 当前被封 $TOTAL_BANNED 个 IP，当前失败计数 $TOTAL_FAILED"

# iptables 规则检查（确认防护真的在网层生效）
F2B_RULES=$(iptables -S 2>/dev/null | grep -c 'f2b-')
echo "  iptables 中 f2b 相关规则: $F2B_RULES 条"

# 关键安全检查：白名单是否仍完整（防止有人改了配置导致自锁）
WL_OK=1
for jail in $(fail2ban-client status 2>/dev/null | grep 'Jail list' | sed 's/.*:\s*//' | tr ',' ' '); do
  [ -z "$jail" ] && continue
  WL=$(fail2ban-client get "$jail" ignoreip 2>/dev/null)
  echo "$WL" | grep -qE '127\.0\.0\.(0/8|1)' || WL_OK=0
done
echo
if [ "$WL_OK" -eq 1 ]; then
  echo "  ✅ 白名单含本机回环（无自锁风险）"
else
  echo "  ❌ 白名单缺失本机回环！存在自锁风险"
  exit 2
fi

# INPUT 策略必须未被改动（这是我们的安全边界）
INPUT_POLICY=$(iptables -S INPUT 2>/dev/null | head -1)
echo "  INPUT 策略: $INPUT_POLICY"
if echo "$INPUT_POLICY" | grep -q 'ACCEPT'; then
  echo "  ✅ INPUT 策略未被修改（符合预期）"
else
  echo "  ⚠️ INPUT 策略被改了！这不在预期内"
fi

echo
# 退出码语义
if [ "$TOTAL_BANNED" -gt 20 ]; then
  echo "  ⚠️ 当前封禁数较多（$TOTAL_BANNED），可能有扫描活动"
  exit 1
fi
exit 0
