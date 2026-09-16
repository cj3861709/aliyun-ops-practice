#!/bin/bash
# ==============================================================================
# 任务 003 标准修复流程
#
# 学生做完后对照这个脚本，检查自己是否漏了步骤。
# ==============================================================================
set -uo pipefail

LR=/etc/logrotate.d/quest02
LOG=/var/log/quest02.log

echo "════════════════════════════════════════════════════════════"
echo " 任务 003 标准修复  $(date '+%F %T')"
echo "════════════════════════════════════════════════════════════"

echo
echo "【现象回顾】"
printf "  之前: 服务 %s，但日志属主 %s，chenjin 无法写入\n" \
  "$(systemctl is-active quest02)" "$(stat -c '%U:%G' "$LOG" 2>/dev/null)"

echo
echo "【步骤 1】立即恢复写入能力：修复文件属主"
echo
chown chenjin:chenjin "$LOG"
printf "  %s\n" "$(stat -c '%A %U:%G' "$LOG")"
echo "  → 这一步让服务【现在】就能写入"

echo
echo "【步骤 2】修复根因：改正 logrotate 配置"
echo
echo "  为什么必须改配置："
echo "    只改属主文件的话，下一次轮转又会把属主变回 root。"
echo "    必须从源头修 —— 让 logrotate 创建文件时就设对属主。"
echo
cat > "$LR" <<'EOF'
/var/log/quest02.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 chenjin chenjin
}
EOF
grep -E '^\s*create' "$LR" | sed 's/^/  正确配置: /'

echo
echo "【步骤 3】刷新服务打开的文件"
echo
echo "  改完属主后，服务能否立即写入，取决于它打开文件的方式："
echo "    · 每次 >> 打开 → 下一个写入周期自动成功"
echo "    · 持有 fd      → 需要重启才能打开新文件"
echo
echo "  本服务的脚本用 >> （每次重开），理论上不重启也能恢复。"
echo "  但为了确保状态干净，标准做法仍是重启一次。"
echo
systemctl restart quest02
sleep 3
printf "  服务: %s   新主进程: %s\n" \
  "$(systemctl is-active quest02)" \
  "$(systemctl show quest02 -p MainPID --value)"

echo
echo "【步骤 4】验证（关键：验证功能，不只是验证命令成功）"
echo
BEFORE=$(wc -l < "$LOG")
echo "  当前行数: $BEFORE，等待 70 秒..."
sleep 70
AFTER=$(wc -l < "$LOG")
echo "  之后行数: $AFTER"
echo
if [ "$AFTER" -gt "$BEFORE" ]; then
  echo "  ✅ 日志在增长 —— 功能已真正恢复"
  tail -2 "$LOG" | sed 's/^/    /'
else
  echo "  ❌ 日志未增长，需要进一步排查"
fi

echo
echo "【步骤 5】预防：验证轮转后属主正确"
echo
echo "  光修复当前问题不够，还要确认【下次轮转】不会再犯。"
echo "  方法：dry-run 看 logrotate 会创建什么属主的文件。"
echo
OUT=$(logrotate -d -f "$LR" 2>&1)
LINE=$(echo "$OUT" | grep 'creating new /var/log/quest02.log')
if [ -n "$LINE" ]; then
  echo "  $LINE"
  if echo "$LINE" | grep -q 'uid = 1000'; then
    echo "  ✅ 下次轮转新文件会归 chenjin（uid 1000）"
  else
    echo "  ⚠️ 属主可能不对，检查 create 指令"
  fi
else
  echo "  （dry-run 未显示 creating new 行）"
fi

echo
echo "════════════════════════════════════════════════════════════"
echo " 修复完成"
echo "════════════════════════════════════════════════════════════"
