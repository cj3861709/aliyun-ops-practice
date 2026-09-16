#!/bin/bash
# 死人开关：若被触发，说明加固后验证失败，自动恢复原始参数。
#
# 由 at 排程执行。正常情况下应在执行前被手动取消。
LOG=/root/ops-lab/logs/deadman.log
mkdir -p /root/ops-lab/logs

{
  echo "════════════════════════════════════════════════════════"
  echo "死人开关触发  $(date '+%F %T')"
  echo "════════════════════════════════════════════════════════"
  echo "说明：加固后未在期限内确认成功，执行自动恢复。"
  echo

  echo "--- 恢复前状态 ---"
  for p in kernel.dmesg_restrict net.ipv4.conf.all.log_martians \
           net.ipv4.conf.all.send_redirects net.ipv4.conf.all.rp_filter; do
    printf '  %-42s = %s\n' "$p" "$(sysctl -n $p 2>/dev/null)"
  done
  echo
  echo "  SSH 监听:"
  ss -tlnp 2>/dev/null | grep ':22 ' | sed 's/^/    /'
  echo "  sshd 服务: $(systemctl is-active sshd)"
  echo

  # 移除加固配置文件
  if [ -f /etc/sysctl.d/60-ops-lab-hardening.conf ]; then
    mv /etc/sysctl.d/60-ops-lab-hardening.conf \
       /etc/sysctl.d/60-ops-lab-hardening.conf.reverted 2>/dev/null
    echo "  已移出加固配置: 60-ops-lab-hardening.conf -> .reverted"
  fi

  # 恢复原值（宽松但安全，确保连通性优先）
  sysctl -w kernel.dmesg_restrict=0 >/dev/null 2>&1
  sysctl -w net.ipv4.conf.all.log_martians=0 >/dev/null 2>&1
  sysctl -w net.ipv4.conf.all.send_redirects=1 >/dev/null 2>&1
  sysctl -w net.ipv4.conf.default.send_redirects=1 >/dev/null 2>&1
  sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null 2>&1
  sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null 2>&1
  echo "  已恢复原始参数"

  echo
  echo "--- 恢复后状态 ---"
  for p in kernel.dmesg_restrict net.ipv4.conf.all.rp_filter; do
    printf '  %-42s = %s\n' "$p" "$(sysctl -n $p 2>/dev/null)"
  done
  echo "  sshd 服务: $(systemctl is-active sshd)"
  echo
  echo "  请检查网络连通性后重新评估加固方案。"
} >> "$LOG" 2>&1

echo "deadman triggered at $(date '+%F %T')" >> "$LOG"
