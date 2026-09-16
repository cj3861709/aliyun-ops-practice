#!/bin/bash
# ==============================================================================
# daily-check.sh —— 日常巡检脚本（框架版）
#
# 【这是 B 阶段的练习：你需要填 5 处 ____ 】
#
# 设计说明（对应 C 阶段讲的 7 个要点）：
#   1. 输出分层：完整结果写日志文件，问题摘要输出到控制台
#   2. 退出码语义：0=正常 1=警告 2=严重
#   3. 阈值有依据：见下方注释
#   4. 告警去重：状态文件记录上次告警等级
#   5. 自包含：绝对路径、判断 TTY、无交互
#   6. 按问题分组：每组回答一个问题
#   7. 无副作用：只读操作，有超时保护
# ==============================================================================
set -uo pipefail

# ------------------------------------------------------------------------------
# 配置区
# ------------------------------------------------------------------------------
LOG_FILE="/var/log/daily-check.log"
ALERT_DIR="/var/log/daily-check-alerts"
STATE_FILE="/var/lib/daily-check.state"

# 阈值设定依据：
#   磁盘 80% —— 留 20% 空间给系统响应（日志暴涨、临时文件）
#              超过 80% 后 ext4 的写入性能开始下降
#   inode 80% —— inode 耗尽会导致"磁盘有空间但无法创建文件"
#               这个指标比磁盘使用率更隐蔽，必须单独检查
DISK_THRESHOLD=80
INODE_THRESHOLD=80

# 需要监控的关键服务
WATCHED_SERVICES="nginx sshd fail2ban crond quest02"

# quest02 日志（用于检查"日志是否在增长"）
QUEST02_LOG="/var/log/quest02.log"

# 颜色（仅交互终端启用）
if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_BAD=$'\033[31m'
  C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_BAD=""; C_DIM=""; C_RST=""
fi

# ------------------------------------------------------------------------------
# 结果收集
# ------------------------------------------------------------------------------
WARN_COUNT=0
CRIT_COUNT=0
RESULT_LINES=""

# 记录一行结果
# 用法：record <级别> <消息>
#   级别：OK / WARN / CRIT
record() {
  local level="$1"; shift
  local msg="$*"
  local mark
  case "$level" in
    OK)   mark="${C_OK}✅${C_RST}";;
    WARN) mark="${C_WARN}⚠️${C_RST}"; WARN_COUNT=$((WARN_COUNT+1));;
    CRIT) mark="${C_BAD}❌${C_RST}"; CRIT_COUNT=$((CRIT_COUNT+1));;
    *)    mark="  ";;
  esac
  printf "  %s %s\n" "$mark" "$msg"
  RESULT_LINES="${RESULT_LINES}${level}|${msg}"$'\n'
}

# ==============================================================================
# 检查组 1：磁盘空间
# 回答的问题：磁盘还够用吗？
# ==============================================================================
check_disk() {
  echo "【1】磁盘空间"

  local usage
  # ____ 空 1：取出根分区的使用率数字（不含 % 号）
  # 提示：df -h / | tail -1 | awk '{print $5}' | tr -d '%'
  #       或者用 df --output=pcent / | tail -1 | tr -d ' %'
  usage=____

  if [ -z "$usage" ]; then
    record WARN "无法获取磁盘使用率"
    return
  fi

  if [ "$usage" -ge "$DISK_THRESHOLD" ]; then
    local avail
    avail=$(df -h / | tail -1 | awk '{print $4}')
    record WARN "根分区使用率 ${usage}%（阈值 ${DISK_THRESHOLD}%），剩余 ${avail}"
  else
    record OK "根分区使用率 ${usage}%"
  fi
  echo
}

# ==============================================================================
# 检查组 2：inode 使用率
# 回答的问题：还能创建新文件吗？
#   这个指标容易被忽略：磁盘有空间但 inode 耗尽时，
#   创建文件会报 "No space left on device"，很难排查
# ==============================================================================
check_inode() {
  echo "【2】inode 使用率"

  local usage
  # ____ 空 2：取出根分区的 inode 使用率
  # 提示：df -i / | tail -1 | awk '{print $5}' | tr -d '%'
  usage=____

  if [ -z "$usage" ]; then
    record WARN "无法获取 inode 使用率"
    return
  fi

  if [ "$usage" -ge "$INODE_THRESHOLD" ]; then
    record WARN "inode 使用率 ${usage}%（阈值 ${INODE_THRESHOLD}%）"
  else
    record OK "inode 使用率 ${usage}%"
  fi
  echo
}

# ==============================================================================
# 检查组 3：关键服务状态
# 回答的问题：该跑的服务都在跑吗？
# ==============================================================================
check_services() {
  echo "【3】关键服务"

  local svc
  for svc in $WATCHED_SERVICES; do
    # ____ 空 3：判断服务是否在运行
    # 提示：用 systemctl is-active --quiet "$svc"
    #       成功（运行中）就 record OK，失败就 record CRIT
    #       注意：这里要用 if 语句，不要写成一整行
    if ____; then
      record OK "$svc 运行中"
    else
      record CRIT "$svc 未运行"
    fi
  done
  echo
}

# ==============================================================================
# 检查组 4：日志是否在增长
# 回答的问题：服务在"活着"还是在"工作"？
#   这是任务 003 学到的：systemctl is-active 说 active，
#   不代表服务真的在产出内容
# ==============================================================================
check_log_growth() {
  echo "【4】日志增长检查"

  if [ ! -f "$QUEST02_LOG" ]; then
    record WARN "日志文件不存在：$QUEST02_LOG"
    echo
    return
  fi

  local mtime now age
  # ____ 空 4：获取日志文件的最后修改时间（Unix 时间戳）
  # 提示：stat -c %Y "$QUEST02_LOG"
  mtime=____
  now=$(date +%s)
  age=$(( now - mtime ))

  local owner
  owner=$(stat -c '%U:%G' "$QUEST02_LOG")

  # 心跳服务每分钟写一次，超过 180 秒没更新就算异常
  # 阈值依据：正常间隔 60 秒，留 3 倍余量避免误报
  if [ "$age" -gt 180 ]; then
    record CRIT "日志已 ${age} 秒未更新（属主 ${owner}）"
    echo "  ${C_DIM}   → 检查：journalctl -u quest02 -n 20"
    echo "  ${C_DIM}   → 检查：sudo -u chenjin test -w $QUEST02_LOG${C_RST}"
  else
    record OK "日志 ${age} 秒前更新过（属主 ${owner}）"
  fi
  echo
}

# ==============================================================================
# 检查组 5：系统资源
# 回答的问题：资源是否紧张？
# ==============================================================================
check_resources() {
  echo "【5】系统资源"

  local load mem_avail
  load=$(awk '{print $1}' /proc/loadavg)
  mem_avail=$(awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo)

  # 负载阈值：本机 2 核，负载 > 4 表示明显过载
  # 依据：经验值，负载 > 核心数×2 时会感到明显卡顿
  if awk -v l="$load" 'BEGIN{exit !(l > 4)}'; then
    record WARN "系统负载 ${load}（2 核机器，>4 表示过载）"
  else
    record OK "系统负载 ${load}"
  fi

  # 内存阈值：可用 < 200MB 时进程可能被 OOM Killer 杀掉
  if [ "${mem_avail:-9999}" -lt 200 ]; then
    record WARN "可用内存仅 ${mem_avail}MB"
  else
    record OK "可用内存 ${mem_avail}MB"
  fi
  echo
}

# ==============================================================================
# 主流程
# ==============================================================================
main() {
  # 确保目录存在
  mkdir -p "$(dirname "$LOG_FILE")" "$ALERT_DIR" "$(dirname "$STATE_FILE")"

  local stamp
  stamp=$(date '+%F %T')
  local header="巡检 ${stamp}"

  echo
  echo "════════════════════════════════════════════════════════════"
  echo " ${header}"
  echo "════════════════════════════════════════════════════════════"
  echo

  check_disk
  check_inode
  check_services
  check_log_growth
  check_resources

  # ---- 汇总 ----
  echo "════════════════════════════════════════════════════════════"
  local level="OK" exit_code=0
  if [ "$CRIT_COUNT" -gt 0 ]; then
    level="CRITICAL"; exit_code=2
  elif [ "$WARN_COUNT" -gt 0 ]; then
    level="WARNING"; exit_code=1
  fi
  printf " 结果: %s（警告 %d，严重 %d）\n" "$level" "$WARN_COUNT" "$CRIT_COUNT"
  echo "════════════════════════════════════════════════════════════"
  echo

  # ---- 写日志（完整结果） ----
  {
    echo "########## ${header} ##########"
    printf '%s' "$RESULT_LINES"
    echo "LEVEL=${level} WARN=${WARN_COUNT} CRIT=${CRIT_COUNT}"
    echo
  } >> "$LOG_FILE"

  # ---- 日志自身轮转（防止巡检脚本把磁盘占满） ----
  # 这是设计要点 7：巡检脚本自己也要控制体积
  if [ "$(wc -l < "$LOG_FILE" 2>/dev/null || echo 0)" -gt 2000 ]; then
    tail -1000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
  fi

  # ---- 告警文件（只在有问题时生成） ----
  if [ "$level" != "OK" ]; then
    local alert_file="${ALERT_DIR}/alert-${level}-$(date +%Y%m%d-%H%M%S).txt"
    {
      echo "巡检告警 [${level}] ${stamp}"
      echo "警告 ${WARN_COUNT} 项，严重 ${CRIT_COUNT} 项"
      echo
      printf '%s' "$RESULT_LINES" | grep -E '^(WARN|CRIT)\|' | sed 's/^[A-Z]*|//'
      echo
      echo "完整日志: ${LOG_FILE}"
    } > "$alert_file"
    echo " 告警已写入: $alert_file"
    echo

    # 记录到 syslog（便于 journalctl 统一检索）
    logger -t daily-check "level=${level} warn=${WARN_COUNT} crit=${CRIT_COUNT}"
  fi

  # ---- 状态文件（用于告警去重） ----
  # ____ 空 5：把本次的告警等级写入状态文件
  # 提示：echo "$level" > "$STATE_FILE"
  #       这个文件的用途是让下次运行能知道"上次是什么等级"，
  #       从而实现"等级变化才告警"的去重逻辑
  ____

  exit "$exit_code"
}

main "$@"
