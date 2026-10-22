#!/bin/bash
# 死人开关：若被触发，说明加固后验证失败，自动恢复原始参数。
#
# 由 at 排程执行。正常情况下应在执行前被手动取消。
#
# ═══ 本项目 Shell 脚本规范（详见 docs/SHELL-CONVENTIONS.md）═══
#   0. 脚本自带 PATH —— at 与 cron 的 PATH 都不含 /usr/sbin，
#      而 sysctl、ss 恰好在 /usr/sbin（手记 010；规范「脚本必须自带 PATH」）
#   1. 默认用 `set -uo pipefail`，谨慎使用 `-e`
#      原因：systemctl is-active / grep / diff 等【查询类命令】
#            在"否定结果"时返回非 0，配合 -e 会导致脚本意外中断
#            （真实事故见 docs/postmortem/008-set-e-systemctl-is-active.md）
#   2. 若用了 -e，所有查询类命令必须加 `|| true` 保护
#   3. 破坏性脚本必须用 `trap cleanup EXIT` 保证环境恢复
#   4. 不用 bc，改用整数运算（依赖更少）
#   5. 管道 + grep -q 要防 SIGPIPE（复盘 #007）
#   6. 恢复动作失败绝不打印「已恢复」——只报真实结果
# ═══════════════════════════════════════════════════════════════

set -uo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

LOG=/root/ops-lab/logs/deadman.log
mkdir -p /root/ops-lab/logs

# ── 预检 ──
# 缺命令就当场停下并写清楚原因。原来的版本在没有 sysctl 时会一路空转到
# 最后，打印「已恢复原始参数」，把「什么都没做」记成「回滚成功」。
MISSING=""
for c in sysctl ss systemctl date; do
  command -v "$c" >/dev/null 2>&1 || MISSING="$MISSING $c"
done
if [ -n "$MISSING" ]; then
  {
    echo "════════════════════════════════════════════════════════"
    echo "死人开关无法执行  $(date '+%F %T')"
    echo "════════════════════════════════════════════════════════"
    echo "缺少命令:$MISSING"
    echo "PATH=$PATH"
    echo "结论：未执行任何恢复动作，需要人工介入。"
  } >> "$LOG" 2>&1
  exit 78
fi

FAILED=0
FAILED_LIST=""

{
  echo "════════════════════════════════════════════════════════"
  echo "死人开关触发  $(date '+%F %T')"
  echo "════════════════════════════════════════════════════════"
  echo "说明：加固后未在期限内确认成功，执行自动恢复。"
  echo

  echo "--- 恢复前状态 ---"
  for p in kernel.dmesg_restrict net.ipv4.conf.all.log_martians \
           net.ipv4.conf.all.send_redirects net.ipv4.conf.all.rp_filter; do
    printf '  %-42s = %s\n' "$p" "$(sysctl -n "$p" 2>/dev/null)"
  done
  echo
  echo "  SSH 监听:"
  ss -tlnp 2>/dev/null | grep ':22 ' | sed 's/^/    /' || true
  echo "  sshd 服务: $(systemctl is-active sshd 2>/dev/null || true)"
  echo

  # 移除加固配置文件。用通配而不是写死文件名：加固配置从 60- 改名到 99-zz- 之后，
  # 写死 60-ops-lab-hardening.conf 的这本脚本就再也不会命中，等于这一步悄悄失效。
  HARDEN_FILES=$(ls -1 /etc/sysctl.d/*ops-lab-hardening*.conf 2>/dev/null || true)
  if [ -z "$HARDEN_FILES" ]; then
    echo "  未找到 /etc/sysctl.d/*ops-lab-hardening*.conf，跳过移除"
  else
    for f in $HARDEN_FILES; do
      if mv "$f" "$f.reverted" 2>/dev/null; then
        echo "  已移出加固配置: $(basename "$f") -> $(basename "$f").reverted"
      else
        echo "  ❌ 移出加固配置失败: $f"
        FAILED=$((FAILED+1))
        FAILED_LIST="$FAILED_LIST mv:$(basename "$f")"
      fi
    done
  fi
  echo

  # 恢复原值（宽松但安全，确保连通性优先）
  echo "--- 执行恢复 ---"
  for kv in kernel.dmesg_restrict=0 \
            net.ipv4.conf.all.log_martians=0 \
            net.ipv4.conf.all.send_redirects=1 \
            net.ipv4.conf.default.send_redirects=1 \
            net.ipv4.conf.all.rp_filter=0 \
            net.ipv4.conf.default.rp_filter=0; do
    if sysctl -w "$kv" >/dev/null 2>&1; then
      echo "  ✅ $kv"
    else
      echo "  ❌ $kv 失败"
      FAILED=$((FAILED+1))
      FAILED_LIST="$FAILED_LIST $kv"
    fi
  done

  if [ "$FAILED" -eq 0 ]; then
    echo "  已恢复原始参数（6 项全部成功）"
  else
    echo "  ⚠️ 共 $FAILED 项失败:$FAILED_LIST"
    echo "  ⚠️ 回滚未完成，需要人工介入"
  fi
  echo

  echo "--- 恢复后状态 ---"
  for p in kernel.dmesg_restrict net.ipv4.conf.all.rp_filter; do
    printf '  %-42s = %s\n' "$p" "$(sysctl -n "$p" 2>/dev/null)"
  done
  echo "  sshd 服务: $(systemctl is-active sshd 2>/dev/null || true)"
  echo
  if [ "$FAILED" -eq 0 ]; then
    echo "  请检查网络连通性后重新评估加固方案。"
  else
    echo "  请立刻人工检查：上面失败的项仍停在原值上。"
  fi
  echo "deadman triggered at $(date '+%F %T')"
} >> "$LOG" 2>&1

if [ "$FAILED" -ne 0 ]; then
  exit 1
fi
exit 0
