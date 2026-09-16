#!/bin/bash
# 冷启动测试的死人开关：若服务未能恢复，强制拉起
LOG=/root/ops-lab/logs/coldstart-deadman.log
mkdir -p /root/ops-lab/logs
{
  echo "=== 死人开关触发 $(date '+%F %T') ==="
  for s in nginx fail2ban sshd; do
    A=$(systemctl is-active "$s" 2>/dev/null)
    echo "  $s: $A"
    if [ "$A" != "active" ]; then
      echo "    → 尝试启动"
      systemctl start "$s" 2>&1 | sed 's/^/      /'
      sleep 2
      echo "    → 启动后: $(systemctl is-active "$s" 2>/dev/null)"
    fi
  done
  echo "  站点: HTTP $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/ 2>/dev/null || echo 000)"
} >> "$LOG" 2>&1
