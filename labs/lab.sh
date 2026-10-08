#!/bin/bash
# ==============================================================================
# 运维练习实验室 —— 框架
#
# 设计理念：
#   1. 【判分基于系统状态，不是基于命令历史】
#      不会检查"你有没有敲过某条命令"，而是检查"系统是否处于正确状态"。
#      这样你可以用任何方法达成目标 —— 那才是真实运维。
#
#   2. 【每个检查项独立报告】
#      不用"全对/全错"，而是逐项列出通过/未通过，指出差在哪。
#
#   3. 【提供重置和提示】
#      reset 回到起点，hint 给提示（不直接给答案）。
#
# 用法:
#   lab list              列出所有练习
#   lab show <编号>       查看练习说明
#   lab check <编号>      运行判分
#   lab reset <编号>      重置该练习的环境
#   lab hint <编号>       获取提示（渐进式）
# ==============================================================================
set -uo pipefail

LAB_ROOT=/root/ops-lab/labs

# 颜色（仅在交互终端启用）
if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_WARN=$'\033[33m'
  C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_RST=$'\033[0m'
else
  C_OK=""; C_BAD=""; C_WARN=""; C_DIM=""; C_BOLD=""; C_RST=""
fi

PASS=0
FAIL=0

# 检查项：check_ok "描述" "命令" —— 命令返回 0 视为通过
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

# 需要人工阅读的检查（比如"解释为什么"）
check_note() {
  printf "  ${C_WARN}📝${C_RST} %s\n" "$1"
}

# 输出实际值，帮助定位问题
show() {
  local desc="$1"; shift
  local val
  val=$(eval "$@" 2>/dev/null | head -3 | tr '\n' '|' | sed 's/|$//')
  printf "  ${C_DIM}   %s: %s${C_RST}\n" "$desc" "${val:-（空）}"
}

# ==============================================================================
# 列出练习
# ==============================================================================
cmd_list() {
  echo
  echo "${C_BOLD}运维练习实验室${C_RST}"
  echo
  printf "  %-6s %-34s %s\n" "编号" "主题" "难度"
  printf "  %s\n" "──────────────────────────────────────────────────────────"
  for d in "$LAB_ROOT"/*/; do
    [ -d "$d" ] || continue
    local id name diff
    id=$(basename "$d")
    name=$(grep -m1 '^# ' "$d/README.md" 2>/dev/null | sed 's/^# //' || echo "(无说明)")
    diff=$(grep -m1 '^难度:' "$d/README.md" 2>/dev/null | sed 's/难度: *//' || echo "?")
    printf "  %-6s %-34s %s\n" "$id" "$name" "$diff"
  done
  echo
  echo "  ${C_DIM}用法: lab show <编号> | check <编号> | reset <编号> | hint <编号>${C_RST}"
  echo
}

# ==============================================================================
# 显示练习说明
# ==============================================================================
cmd_show() {
  local id="$1"
  local dir="$LAB_ROOT/$id"
  if [ ! -d "$dir" ]; then
    echo "❌ 找不到练习: $id"
    cmd_list
    return 1
  fi
  if [ -f "$dir/README.md" ]; then
    # 用 python 做简单 markdown 渲染（终端友好）
    python3 - "$dir/README.md" <<'PY'
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
BOLD = "\033[1m" if sys.stdout.isatty() else ""
RST = "\033[0m" if sys.stdout.isatty() else ""
DIM = "\033[2m" if sys.stdout.isatty() else ""
out = []
for line in t.split("\n"):
    if line.startswith("# "):
        out.append("\n" + BOLD + line[2:] + RST + "\n" + "═" * 60)
    elif line.startswith("## "):
        out.append("\n" + BOLD + "▸ " + line[3:] + RST)
    elif line.startswith("### "):
        out.append(BOLD + "  · " + line[4:] + RST)
    elif line.startswith("```"):
        continue
    elif line.strip().startswith("- [ ]"):
        out.append("  ☐ " + line.strip()[5:].strip())
    elif line.strip().startswith("- "):
        out.append("  • " + line.strip()[2:])
    elif re.match(r"^\d+\.", line.strip()):
        out.append("  " + line.strip())
    elif line.strip().startswith("> "):
        out.append(DIM + "  │ " + line.strip()[2:] + RST)
    else:
        out.append(line)
print("\n".join(out))
PY
  else
    echo "（该练习没有 README.md）"
  fi
  echo
}

# ==============================================================================
# 判分
# ==============================================================================
cmd_check() {
  local id="$1"
  local dir="$LAB_ROOT/$id"
  if [ ! -f "$dir/check.sh" ]; then
    echo "❌ 找不到判分脚本: $dir/check.sh"
    return 1
  fi

  echo
  echo "${C_BOLD}════════════════════════════════════════════════════════════${C_RST}"
  echo "${C_BOLD} 判分: $id${C_RST}"
  echo "${C_BOLD}════════════════════════════════════════════════════════════${C_RST}"
  echo

  PASS=0
  FAIL=0
  # shellcheck disable=SC1090
  source "$dir/check.sh"

  echo
  echo "${C_BOLD}────────────────────────────────────────────────────────────${C_RST}"
  local total=$((PASS + FAIL))
  if [ "$FAIL" -eq 0 ] && [ "$total" -gt 0 ]; then
    printf " ${C_OK}${C_BOLD}🎉 全部通过 (%d/%d)${C_RST}\n" "$PASS" "$total"
    echo
    echo " ${C_DIM}接下来："
    echo "   1. 试着解释每一步「为什么这样做」，而不只是「做了什么」"
    echo "   2. 用 lab reset $id 重置，不看说明再做一遍"
    echo "   3. 把过程中遇到的问题写成复盘（这是简历素材）${C_RST}"
  else
    printf " ${C_WARN}${C_BOLD}通过 %d / 共 %d${C_RST}\n" "$PASS" "$total"
    echo
    echo " ${C_DIM}提示：用 lab hint $id 获取渐进式提示${C_RST}"
  fi
  echo

  [ "$FAIL" -eq 0 ] && return 0 || return 1
}

# ==============================================================================
# 重置
# ==============================================================================
cmd_reset() {
  local id="$1"
  local dir="$LAB_ROOT/$id"
  if [ ! -f "$dir/reset.sh" ]; then
    echo "❌ 该练习没有重置脚本: $dir/reset.sh"
    return 1
  fi
  echo
  echo "${C_BOLD}重置练习 $id${C_RST}"
  echo
  read -r -p "  确认重置？会删除该练习相关的内容 (yes/no): " ans
  if [ "$ans" != "yes" ]; then
    echo "  已取消。"
    return 0
  fi
  echo
  # shellcheck disable=SC1090
  source "$dir/reset.sh"
}

# ==============================================================================
# 提示（渐进式）
# ==============================================================================
cmd_hint() {
  local id="$1"
  local dir="$LAB_ROOT/$id"
  local f="$dir/HINTS.md"
  if [ ! -f "$f" ]; then
    echo "❌ 该练习没有提示文件"
    return 1
  fi

  # 记录该练习已被查看的提示数
  local state="/root/ops-lab/labs/.hint-state-$id"
  local n=0
  [ -f "$state" ] && n=$(cat "$state")

  # 统计提示总数（以 "### 提示" 计数）
  local total
  total=$(grep -c '^### 提示' "$f" 2>/dev/null || echo 0)
  if [ "$total" -eq 0 ]; then
    cat "$f"
    return 0
  fi

  if [ "$n" -ge "$total" ]; then
    echo
    echo "${C_WARN}已显示全部 $total 条提示。${C_RST}"
    echo
    echo "${C_DIM}还是没有思路？那说明需要先补基础知识。"
    echo "建议：lab show $id 重读说明，或直接问我。${C_RST}"
    echo
    # 显示全部
    awk '/^### 提示/{p=1} p' "$f"
    return 0
  fi

  n=$((n + 1))
  echo "$n" > "$state"

  echo
  echo "${C_BOLD}提示 $n / $total${C_RST}"
  echo
  # 显示第 n 条提示及其内容
  awk -v want="$n" '
    /^### 提示/ { c++; if (c == want) { p = 1 } else { p = 0 } }
    p { print }
  ' "$f"
  echo
  echo "${C_DIM}再运行一次 lab hint $id 可获取下一条提示。${C_RST}"
  echo
}

# ==============================================================================
# 主入口
# ==============================================================================
case "${1:-list}" in
  list|ls)   cmd_list ;;
  show)      cmd_show "${2:?需要指定练习编号}" ;;
  check)     cmd_check "${2:?需要指定练习编号}" ;;
  reset)     cmd_reset "${2:?需要指定练习编号}" ;;
  hint)      cmd_hint "${2:?需要指定练习编号}" ;;
  *)
    echo "用法: lab {list|show|check|reset|hint} [编号]"
    exit 1
    ;;
esac
