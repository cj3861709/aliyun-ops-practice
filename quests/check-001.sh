#!/bin/bash
# 任务 001 检查器：检查 8090 端口是否正常提供你的页面
set -uo pipefail

PORT=8090
C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
PASS=0; FAIL=0

ok()   { printf "  ${C_OK}✅${C_RST} %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  ${C_BAD}❌${C_RST} %s\n" "$1"; FAIL=$((FAIL+1)); }
dim()  { printf "  ${C_DIM}   %s${C_RST}\n" "$1"; }

echo
echo "── 任务 001 检查 ──"
echo

# 1. 端口监听
if ss -tln 2>/dev/null | grep -qE ":${PORT}\b"; then
  ok "8090 端口正在监听"
else
  bad "8090 端口未监听"
  dim "想想：nginx 配置里写了 listen 8090 吗？reload 了吗？"
fi

# 2. HTTP 可达
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${PORT}/" 2>/dev/null || echo 000)
if [ "$CODE" = "200" ]; then
  ok "HTTP 返回 200"
else
  bad "HTTP 返回 $CODE（期望 200）"
  case "$CODE" in
    403) dim "403 = 权限问题。nginx 用户能读取你的文件吗？" ;;
    404) dim "404 = 找不到文件。配置里的 root 路径对吗？" ;;
    000) dim "连不上。检查端口监听和 nginx 状态。" ;;
    502) dim "502 = 后端无响应。" ;;
  esac
fi

# 3. 内容包含关键字
BODY=$(curl -s --max-time 3 "http://127.0.0.1:${PORT}/" 2>/dev/null || true)
if printf '%s' "$BODY" | grep -q 'Chen Jin 的运维练手区'; then
  ok "页面包含 'Chen Jin 的运维练手区'"
else
  bad "页面未包含目标文字"
  if [ -n "$BODY" ]; then
    dim "实际内容前 80 字："
    printf '%s' "$BODY" | head -c 80 | sed 's/^/       /'
    echo
  fi
fi

# 4. 标题含名字
if printf '%s' "$BODY" | grep -qiE '<title>[^<]*(Chen Jin|陈锦)'; then
  ok "页面标题包含你的名字"
else
  bad "页面标题未包含名字"
  T=$(printf '%s' "$BODY" | grep -o '<title>[^<]*</title>' | head -1)
  [ -n "$T" ] && dim "实际标题：$T"
fi

# 5. 不影响生产站点
echo
echo "── 顺带检查：没搞坏别的东西 ──"
for p in / /postmortem.html /ops.html; do
  C=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1$p")
  if [ "$C" = "200" ]; then
    printf "  ${C_OK}✅${C_RST} 生产站点 %-18s HTTP %s\n" "$p" "$C"
    PASS=$((PASS+1))
  else
    printf "  ${C_BAD}❌${C_RST} 生产站点 %-18s HTTP %s\n" "$p" "$C"
    FAIL=$((FAIL+1))
  fi
  sleep 0.3
done

echo
echo "── 结果 ──"
TOTAL=$((PASS+FAIL))
if [ "$FAIL" -eq 0 ]; then
  printf "  ${C_OK}🎉 全部通过（%d/%d）${C_RST}\n" "$PASS" "$TOTAL"
  echo
  echo "  下一步：写手记！"
  echo "    cp /root/ops-lab/my-journal/_TEMPLATE.md \\"
  echo "       '/root/ops-lab/my-journal/001-部署第一个页面.md'"
  echo "    然后编辑它，写你的真实过程。"
else
  printf "  通过 %d / 共 %d\n" "$PASS" "$TOTAL"
  echo
  echo "  卡住了？把你的命令和报错贴给老师。"
fi

echo
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
