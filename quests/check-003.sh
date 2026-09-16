#!/bin/bash
# 任务 003 检查器：只验证最终状态
set -uo pipefail

C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
PASS=0; FAIL=0
ok()  { printf "  ${C_OK}✅${C_RST} %s\n" "$1"; PASS=$((PASS+1)); }
bad() { printf "  ${C_BAD}❌${C_RST} %s\n" "$1"; FAIL=$((FAIL+1)); }
dim() { printf "  ${C_DIM}   %s${C_RST}\n" "$1"; }

LOG=/var/log/quest02.log
LR=/etc/logrotate.d/quest02

echo
echo "── 任务 003 检查 ──"
echo

echo "【1】日志文件权限"
OWNER=$(stat -c '%U:%G' "$LOG" 2>/dev/null || echo "不存在")
if [ "$OWNER" = "chenjin:chenjin" ]; then
  ok "日志属主正确（chenjin:chenjin）"
else
  bad "日志属主是 $OWNER（期望 chenjin:chenjin）"
fi

if sudo -u chenjin test -w "$LOG" 2>/dev/null; then
  ok "chenjin 用户可写日志"
else
  bad "chenjin 用户无法写入日志"
fi

echo
echo "【2】logrotate 配置"
if grep -qE '^\s*create\b' "$LR" 2>/dev/null; then
  ok "配置包含 create 指令"
  LINE=$(grep -E '^\s*create\b' "$LR" | head -1)
  if echo "$LINE" | grep -q 'chenjin'; then
    ok "create 指令指定了 chenjin 属主"
  else
    bad "create 未指定 chenjin：$LINE"
    dim "标准格式：create 0644 chenjin chenjin"
  fi
else
  bad "配置缺少 create 指令（轮转后新文件会归 root）"
fi

echo
echo "【3】服务状态"
if systemctl is-active --quiet quest02; then
  ok "服务处于 active"
else
  bad "服务未运行"
fi

echo
echo "【4】功能性验证（关键）"
dim "当前行数: $(wc -l < "$LOG" 2>/dev/null || echo 0)"
dim "等待 70 秒，观察日志是否增长..."
LINES_BEFORE=$(wc -l < "$LOG" 2>/dev/null || echo 0)
sleep 70
LINES_AFTER=$(wc -l < "$LOG" 2>/dev/null || echo 0)

if [ "$LINES_AFTER" -gt "$LINES_BEFORE" ]; then
  ok "日志正在增长（$LINES_BEFORE → $LINES_AFTER 行）"
  dim "最后一行: $(tail -1 "$LOG")"
else
  bad "日志没有增长（仍为 $LINES_BEFORE 行）"
  dim "服务状态正常但功能未恢复"
  echo
  dim "可能原因："
  dim "  · 权限已修但服务没重启（进程持有旧的 fd）"
  dim "    → 试 systemctl restart quest02"
  dim "  · 服务进程的身份不是 chenjin"
  dim "    → 查 ps -o user= -p \$(systemctl show quest02 -p MainPID --value)"
  dim "  · 日志路径不对"
fi

echo
echo "【5】生产站点未受影响"
for p in / /postmortem.html /journal.html /ops.html; do
  C=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1$p")
  if [ "$C" = "200" ]; then
    printf "  ${C_OK}✅${C_RST} %-18s HTTP %s\n" "$p" "$C"; PASS=$((PASS+1))
  else
    printf "  ${C_BAD}❌${C_RST} %-18s HTTP %s\n" "$p" "$C"; FAIL=$((FAIL+1))
  fi
  sleep 0.3
done

echo
if [ "$FAIL" -eq 0 ]; then
  printf "  ${C_OK}🎉 全部通过（%d/%d）${C_RST}\n" "$PASS" "$((PASS+FAIL))"
  echo
  echo "  下一步：写手记 003"
  echo "    重点：你的排查路径 + 你怎么验证"修好了"（不只是改动完成）"
else
  printf "  通过 %d / 共 %d\n" "$PASS" "$((PASS+FAIL))"
fi
echo
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
