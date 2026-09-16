#!/bin/bash
# 一键巡检。快照存到 /root/ops-lab/logs/
#
# 重要：/proc/net/dev 的计数【包含内网流量】，不等于计费的公网流量。
#       阿里云 agent（云盾/日志/监控/备份）大量发往 100.64.0.0/10 内部服务网，
#       不出机房、不计费。真实计费流量请看控制台「云数据传输 CDT」或费用中心。
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

OUT=/root/ops-lab/logs/health-$(date +%Y%m%d-%H%M%S).txt
{
  echo "########## 巡检 $(date '+%F %T') ##########"
  echo "--- 负载 ---";      uptime
  echo "--- 内存 ---";      free -m
  echo "--- 磁盘 ---";      df -hT -x tmpfs -x devtmpfs
  echo "--- inode ---";     df -i -x tmpfs -x devtmpfs | head -3
  echo "--- 失败服务 ---";  systemctl --failed --no-pager
  echo "--- 监听端口 ---";  ss -tulnp
  echo "--- 内存TOP5 ---";  ps aux --sort=-%mem | head -6
  echo "--- CPU TOP5 ---";  ps aux --sort=-%cpu | head -6
  echo "--- eth0 累计流量（含内网，非计费口径）---"
  python3 - <<'PYEOF'
for line in open("/proc/net/dev"):
    if line.strip().startswith("eth0:"):
        n = line.split(":")[1].split()
        rx, tx = int(n[0]), int(n[8])
        print("  接收 %.3f GB / 发送 %.3f GB" % (rx/1073741824, tx/1073741824))
        break
PYEOF
  echo "--- fail2ban ---"
  /root/ops-lab/bin/f2b-status.sh 2>&1 | grep -E '服务|合计|白名单|INPUT' | head -5
  echo "--- 练习环境 ---"
  for _lp in 8081 9090; do
    if ss -tln 2>/dev/null | grep -qE ":$_lp[[:space:]]"; then
      _lc=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$_lp/" 2>/dev/null || echo 000)
      echo "  端口 $_lp: HTTP $_lc"
    fi
    sleep 0.3
  done
  if [ -x /root/ops-lab/labs/01/check.sh ]; then
    _l1=$(bash /root/ops-lab/labs/01/check.sh 2>/dev/null | grep -cE '✅' || true)
    _l1f=$(bash /root/ops-lab/labs/01/check.sh 2>/dev/null | grep -cE '❌' || true)
    echo "  练习01 判分: 通过 ${_l1:-0} 项，未通过 ${_l1f:-0} 项"
  fi
  echo "--- 已知偏差 ---"
  /root/ops-lab/bin/deviation-check.sh 2>&1 | grep -E 'DEV-|✅|⚠️|❌' | head -8
  echo "--- 备份数量 ---"
  echo "  $(ls -1 /root/ops-lab/backups/*.tar.gz 2>/dev/null | wc -l) 份"
} > "$OUT" 2>&1
echo "巡检快照 -> $OUT"
grep -A1 -e '负载' -e 'eth0 累计' "$OUT"
