#!/bin/bash
# 流量巡检 + 阈值告警。由 cron 每小时调用。
# 退出码: 0=正常  1=警告  2=严重
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

LAB=/root/ops-lab
MONITOR=$LAB/lib/traffic-monitor.py
OUTDIR=$LAB/logs/traffic
ALERTDIR=$LAB/logs/alerts
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$OUTDIR/traffic-$STAMP.txt"

mkdir -p "$OUTDIR" "$ALERTDIR"

# 运行监控
python3 "$MONITOR" > "$OUT" 2>&1
RC=$?

# 只保留最近 48 份报告，避免自己把磁盘吃了（这本身就是运维意识）
ls -1t "$OUTDIR"/traffic-*.txt 2>/dev/null | tail -n +49 | xargs -r rm -f

# 提取关键数字
TODAY_MB=$(grep -oE '站点出流量:  *[0-9.]+ (MB|GB)' "$OUT" | head -1 | grep -oE '[0-9.]+ (MB|GB)')
TODAY_PCT=$(grep -oE '占日均预算: *[0-9.]+%' "$OUT" | head -1 | grep -oE '[0-9.]+')

# 判定等级
LEVEL="OK"
if [ -n "${TODAY_PCT:-}" ]; then
  PCT_INT=${TODAY_PCT%.*}
  if [ "$PCT_INT" -ge 80 ]; then
    LEVEL="CRITICAL"
  elif [ "$PCT_INT" -ge 50 ]; then
    LEVEL="WARNING"
  fi
fi

# 记录到 syslog（便于 journalctl 统一检索）
logger -t traffic-check "level=$LEVEL today=${TODAY_MB:-?} budget_pct=${TODAY_PCT:-?}%"

# 告警
if [ "$LEVEL" != "OK" ]; then
  ALERT="$ALERTDIR/alert-$LEVEL-$STAMP.txt"
  {
    echo "流量告警 [$LEVEL] $(date '+%F %T')"
    echo "今日站点出流量: ${TODAY_MB:-未知}"
    echo "占日均预算: ${TODAY_PCT:-未知}%"
    echo ""
    echo "日均预算 = 20GB/月 ÷ 30 = 682.7 MB"
    echo ""
    echo "=== 异常访问 TOP ==="
    sed -n '/异常访问检测/,/^$/p' "$OUT"
    echo ""
    echo "完整报告: $OUT"
  } > "$ALERT"
  echo "[$LEVEL] 已写入告警文件: $ALERT"
  logger -t traffic-check "ALERT written: $ALERT"
fi

# 终端摘要
echo "[$LEVEL] $(date '+%F %T')  今日 ${TODAY_MB:-?}  预算占用 ${TODAY_PCT:-?}%  报告: $OUT"

case "$LEVEL" in
  OK)       exit 0 ;;
  WARNING)  exit 1 ;;
  CRITICAL) exit 2 ;;
esac
