#!/bin/bash
# ==============================================================================
# 练习 02 判分
#
# 检查两件事：
#   1. 结果：站点是否恢复正常
#   2. 过程：是否写了排查记录（这是本练习的核心价值）
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

FINDINGS=/root/ops-lab/labs/02/findings.md

echo "【1】故障是否已修复"
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:9090/ 2>/dev/null || echo "000")
if [ "$CODE" = "200" ]; then
  printf "  ${C_OK}✅${C_RST} HTTP 返回 200（实际 $CODE）\n"; PASS=$((PASS+1))
else
  printf "  ${C_BAD}❌${C_RST} HTTP 返回 200（实际 $CODE）\n"; FAIL=$((FAIL+1))
  case "$CODE" in
    403) echo "  ${C_DIM}   → 403 Forbidden：服务活着，但拒绝了访问。想想要检查什么。${C_RST}" ;;
    404) echo "  ${C_DIM}   → 404 Not Found：服务活着，但找不到文件。检查 root 指向。${C_RST}" ;;
    000) echo "  ${C_DIM}   → 连不上：检查端口监听和服务状态。${C_RST}" ;;
  esac
fi

BODY=$(curl -s --max-time 3 http://127.0.0.1:9090/ 2>/dev/null || true)
if printf '%s' "$BODY" | grep -q 'LAB02-OK'; then
  printf "  ${C_OK}✅${C_RST} 页面内容包含 LAB02-OK\n"; PASS=$((PASS+1))
else
  printf "  ${C_BAD}❌${C_RST} 页面内容包含 LAB02-OK\n"; FAIL=$((FAIL+1))
fi

echo
echo "【2】权限是否被正确修复"
if [ -d /srv/www/lab02 ]; then
  # 检查 nginx 用户能否读取 index.html
  if sudo -u nginx test -r /srv/www/lab02/index.html 2>/dev/null; then
    printf "  ${C_OK}✅${C_RST} nginx 用户可读取站点文件\n"; PASS=$((PASS+1))
  else
    printf "  ${C_BAD}❌${C_RST} nginx 用户无法读取站点文件\n"; FAIL=$((FAIL+1))
  fi

  # 检查目录是否可进入
  if sudo -u nginx test -x /srv/www/lab02 2>/dev/null; then
    printf "  ${C_OK}✅${C_RST} nginx 用户可进入站点目录\n"; PASS=$((PASS+1))
  else
    printf "  ${C_BAD}❌${C_RST} nginx 用户无法进入站点目录（缺 x 权限）\n"; FAIL=$((FAIL+1))
    show "当前权限" "stat -c '%A %U:%G' /srv/www/lab02"
  fi
else
  printf "  ${C_BAD}❌${C_RST} 站点目录不存在\n"; FAIL=$((FAIL+1))
fi

echo
echo "【3】排查记录（本练习的核心）"
if [ -f "$FINDINGS" ]; then
  printf "  ${C_OK}✅${C_RST} findings.md 存在\n"; PASS=$((PASS+1))

  for section in "## 现象" "## 排查过程" "## 根因" "## 修复"; do
    if grep -qF "$section" "$FINDINGS" 2>/dev/null; then
      printf "  ${C_OK}✅${C_RST} 包含章节: %s\n" "$section"; PASS=$((PASS+1))
    else
      printf "  ${C_BAD}❌${C_RST} 缺少章节: %s\n" "$section"; FAIL=$((FAIL+1))
    fi
  done

  # 检查内容是否"真的写了"（而非只留标题）
  LINES=$(wc -l < "$FINDINGS")
  printf "  ${C_DIM}   记录长度: %s 行${C_RST}\n" "$LINES"
  if [ "$LINES" -lt 20 ]; then
    printf "  ${C_WARN}📝${C_RST} 内容偏短。判分只看结构，但写得详细才有价值 ——\n"
    echo "  ${C_DIM}     面试时你能讲出多少细节，取决于你记录了多少。${C_RST}"
  fi

  echo
  echo "  ${C_DIM}   ── 你的记录内容摘要 ──${C_RST}"
  head -30 "$FINDINGS" | sed 's/^/     /'
else
  printf "  ${C_BAD}❌${C_RST} findings.md 不存在\n"; FAIL=$((FAIL+1))
  echo "  ${C_DIM}   请写到: $FINDINGS${C_RST}"
fi

echo
echo "【4】额外检查：是否留下了隐患"
echo "  ${C_DIM}  修复权限时容易过度授权（比如 chmod 777）。检查一下：${C_RST}"
if [ -d /srv/www/lab02 ]; then
  PERM=$(stat -c '%a' /srv/www/lab02)
  FILEPERM=$(stat -c '%a' /srv/www/lab02/index.html 2>/dev/null || echo "?")
  printf "  目录权限: %s   文件权限: %s\n" "$PERM" "$FILEPERM"
  # 777 是不必要的过度授权
  case "$PERM" in
    777|77*) printf "  ${C_WARN}📝${C_RST} 目录权限 %s 过宽。nginx 只需要 r-x，不需要 w。\n" "$PERM" ;;
    755|750|751|700|740) printf "  ${C_OK}✅${C_RST} 权限设置合理\n"; PASS=$((PASS+1)) ;;
    *) printf "  ${C_DIM}   权限 %s —— 只要能读就能工作${C_RST}\n" "$PERM" ;;
  esac
fi

echo
echo "【附】当前状态"
show "9090 监听" "ss -tln | grep ':9090' | awk '{print \$4}'"
show "目录权限" "stat -c '%A %U:%G' /srv/www/lab02 2>/dev/null"
show "error.log 尾部" "tail -3 /var/log/nginx/lab02.error.log 2>/dev/null"
show "nginx 状态" "systemctl is-active nginx"
