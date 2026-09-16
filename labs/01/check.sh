#!/bin/bash
# ==============================================================================
# 练习 01 判分
#
# 原则：检查【系统状态】，不检查【命令历史】
# ==============================================================================

# ---------------------------------------------------------------------------
# 自包含引导：若由 lab.sh 调用，这些函数已存在；若直接运行，则在此定义。
# 这样 check.sh 既能被 lab.sh source，也能独立 bash 执行。
# ---------------------------------------------------------------------------
if ! declare -F check_ok >/dev/null 2>&1; then
  if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_WARN=$'\033[33m'
    C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_RST=$'\033[0m'
  else
    C_OK=""; C_BAD=""; C_WARN=""; C_DIM=""; C_BOLD=""; C_RST=""
  fi
  PASS=0
  FAIL=0

  check_ok() {
    local desc="$1"; shift
    if eval "$@" >/dev/null 2>&1; then
      printf "  ${C_OK}✅${C_RST} %s\n" "$desc"
      PASS=$((PASS + 1))
    else
      printf "  ${C_BAD}❌${C_RST} %s\n" "$desc"
      FAIL=$((FAIL + 1))
    fi
  }

  check_note() {
    printf "  ${C_WARN}📝${C_RST} %s\n" "$1"
  }

  show() {
    local desc="$1"; shift
    local val
    val=$(eval "$@" 2>/dev/null | head -3 | tr '\n' '|' | sed 's/|$//')
    printf "  ${C_DIM}   %s: %s${C_RST}\n" "$desc" "${val:-（空）}"
  }
fi

SITE_ROOT=/srv/www/lab01
BACKUP_SCRIPT=/root/ops-lab/bin/lab01-backup.sh
TARGET_PORT=8081

echo "【1】站点文件"
check_ok "目录 $SITE_ROOT 存在" "[ -d '$SITE_ROOT' ]"
check_ok "首页文件存在且非空" "[ -s '$SITE_ROOT/index.html' ]"
if [ -f "$SITE_ROOT/index.html" ]; then
  check_ok "首页包含 LAB01-OK" "grep -q 'LAB01-OK' '$SITE_ROOT/index.html'"
else
  printf "  ${C_BAD}❌${C_RST} 首页包含 LAB01-OK（文件不存在）\n"; FAIL=$((FAIL+1))
fi

echo
echo "【2】nginx 配置"
check_ok "存在 lab01 的站点配置" \
  "grep -rlq 'srv/www/lab01' /etc/nginx/conf.d/ 2>/dev/null"
check_ok "nginx 配置语法正确" "nginx -t"
check_ok "配置中声明了 listen 8081" \
  "grep -rqE 'listen\s+8081' /etc/nginx/conf.d/ 2>/dev/null"

# ---- 冲突检测：学生是否误用了 8080 ----
echo
echo "【2.5】端口冲突检查（本练习的关键考点）"
# 正确逻辑：只检查【未注释的 listen 指令】，避免注释中的 8080 造成假警报。
# （本项目已有三次"验证逻辑写错导致假警报"的教训，见 docs/SCRIPTING-PITFALLS.md）
LAB01_LISTENS=""
for _cf in $(grep -rl 'srv/www/lab01' /etc/nginx/conf.d/ 2>/dev/null); do
  # 去掉行内注释，再提取行首的 listen 指令
  _l=$(sed 's/#.*//' "$_cf" 2>/dev/null | grep -oE '^[[:space:]]*listen[[:space:]]+[0-9]+' | grep -oE '[0-9]+$' | tr '\n' ' ')
  LAB01_LISTENS="$LAB01_LISTENS $_l"
done
LAB01_LISTENS=$(printf '%s' "$LAB01_LISTENS" | tr -s ' ')

if printf ' %s ' "$LAB01_LISTENS" | grep -q ' 8080 '; then
  printf "  ${C_BAD}❌${C_RST} lab01 配置使用了 8080（已被生产站点占用）\n"
  echo "  ${C_DIM}   → 你的配置会被 nginx 静默忽略。"
  echo "   → 验证: curl http://127.0.0.1:8080/ 看返回的是不是生产站点。"
  echo "   → 正确做法: 改用 8081。${C_RST}"
  FAIL=$((FAIL+1))
else
  printf "  ${C_OK}✅${C_RST} 未使用冲突端口 8080（实际 listen: %s）\n" "${LAB01_LISTENS:-未检测到}"
  PASS=$((PASS+1))
fi

# 检查 nginx 是否报了 conflicting server name
if nginx -t 2>&1 | grep -qi 'conflicting server name'; then
  printf "  ${C_BAD}❌${C_RST} nginx 报告了 conflicting server name\n"
  nginx -t 2>&1 | grep -i conflict | head -2 | sed 's/^/       /'
  echo "  ${C_DIM}   → 这说明有 server 块被忽略了。检查你的 server_name。${C_RST}"
  FAIL=$((FAIL+1))
else
  printf "  ${C_OK}✅${C_RST} nginx 无 conflicting server name 警告\n"; PASS=$((PASS+1))
fi

echo
echo "【3】服务可达性"
check_ok "8081 端口正在监听" "ss -tln | grep -qE ':8081[[:space:]]'"
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${TARGET_PORT}/" 2>/dev/null || echo "000")
if [ "$CODE" = "200" ]; then
  printf "  ${C_OK}✅${C_RST} HTTP 返回 200\n"; PASS=$((PASS+1))
else
  printf "  ${C_BAD}❌${C_RST} HTTP 返回 200（实际 $CODE）\n"; FAIL=$((FAIL+1))
fi
BODY=$(curl -s --max-time 3 "http://127.0.0.1:${TARGET_PORT}/" 2>/dev/null || true)
if printf '%s' "$BODY" | grep -q 'LAB01-OK'; then
  printf "  ${C_OK}✅${C_RST} 通过 HTTP 能取到 LAB01-OK\n"; PASS=$((PASS+1))
else
  printf "  ${C_BAD}❌${C_RST} 通过 HTTP 能取到 LAB01-OK\n"; FAIL=$((FAIL+1))
  show "实际内容" "printf '%s' \"\$BODY\" | head -c 120"
fi

echo
echo "【4】服务管理配置"
check_ok "nginx 服务处于 active" "systemctl is-active nginx"
check_ok "nginx 开机自启" "systemctl is-enabled nginx"
RESTART=$(systemctl show nginx -p Restart --value 2>/dev/null)
if [ "$RESTART" = "always" ]; then
  printf "  ${C_OK}✅${C_RST} Restart=always\n"; PASS=$((PASS+1))
else
  printf "  ${C_BAD}❌${C_RST} Restart=always（实际: %s）\n" "${RESTART:-未设置}"
  FAIL=$((FAIL+1))
  show "drop-in 目录" "ls /etc/systemd/system/nginx.service.d/ 2>/dev/null"
fi

echo
echo "【5】备份脚本"
if [ -x "$BACKUP_SCRIPT" ]; then
  printf "  ${C_OK}✅${C_RST} $BACKUP_SCRIPT 存在且可执行\n"; PASS=$((PASS+1))
  check_ok "脚本引用了 /etc/nginx" "grep -q '/etc/nginx' '$BACKUP_SCRIPT'"
  check_ok "脚本引用了 backups 目录" "grep -q 'backups' '$BACKUP_SCRIPT'"
else
  printf "  ${C_BAD}❌${C_RST} $BACKUP_SCRIPT 存在且可执行\n"; FAIL=$((FAIL+1))
fi

echo
echo "【6】实际自愈能力测试（关键！）"
echo "  ${C_DIM}  会真的 kill 掉 nginx 主进程，验证能否自动恢复${C_RST}"
OLD_PID=$(cat /run/nginx.pid 2>/dev/null || echo "")
if [ -n "$OLD_PID" ]; then
  kill -KILL "$OLD_PID" 2>/dev/null || true
  sleep 4
  NEW_PID=$(cat /run/nginx.pid 2>/dev/null || echo "")
  if [ -n "$NEW_PID" ] && [ "$NEW_PID" != "$OLD_PID" ]; then
    printf "  ${C_OK}✅${C_RST} kill 后自动恢复（PID %s → %s）\n" "$OLD_PID" "$NEW_PID"
    PASS=$((PASS+1))
  else
    printf "  ${C_BAD}❌${C_RST} kill 后未自动恢复\n"; FAIL=$((FAIL+1))
    echo "  ${C_DIM}   → 这就是 Restart=always 的意义。配置写了不等于生效。${C_RST}"
  fi
  sleep 1
  C2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${TARGET_PORT}/" 2>/dev/null || echo 000)
  if [ "$C2" = "200" ]; then
    printf "  ${C_OK}✅${C_RST} 恢复后站点仍可访问\n"; PASS=$((PASS+1))
  else
    printf "  ${C_BAD}❌${C_RST} 恢复后站点仍可访问（HTTP $C2）\n"; FAIL=$((FAIL+1))
  fi
else
  printf "  ${C_BAD}❌${C_RST} 无法读取 nginx PID，跳过自愈测试\n"; FAIL=$((FAIL+1))
fi

echo
echo "【附】当前状态"
show "nginx 状态" "systemctl is-active nginx"
show "监听端口" "ss -tln | grep -E ':(80|8080|8081)' | awk '{print \$4}'"
show "站点文件" "ls -la $SITE_ROOT 2>/dev/null"
