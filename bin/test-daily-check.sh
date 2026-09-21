#!/bin/bash
# ==============================================================================
# 完整测试 daily-check（含邮件通知）
# ==============================================================================
set -uo pipefail

echo "═══ 测试 1：不发邮件，只看报告 ═══"
echo
python3 /root/ops-lab/lib/daily-check.py --no-mail --verbose
echo

echo "═══ 测试 2：检查邮件配置 ═══"
echo
python3 /root/ops-lab/lib/notify.py --check
echo

echo "═══ 测试 3：发送测试邮件 ═══"
echo
python3 /root/ops-lab/lib/daily-check.py --test-mail
echo

echo "═══ 测试 4：验证产物 ═══"
echo
echo "  ── 日志文件 ──"
ls -la /root/ops-lab/logs/daily-check/ | sed 's/^/    /'
echo
echo "  ── 最新报告内容 ──"
cat /root/ops-lab/logs/daily-check/latest.txt 2>/dev/null | sed 's/^/    /'
echo
echo "  ── 告警目录 ──"
ls -la /root/ops-lab/logs/alerts/ 2>/dev/null | sed 's/^/    /' || echo "    （无告警，正常）"
echo
echo "  ── 状态文件 ──"
cat /root/ops-lab/logs/daily-check.state 2>/dev/null | sed 's/^/    /' || echo "    （未创建）"
