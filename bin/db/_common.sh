#!/bin/bash
# ==============================================================================
# 数据库运维脚本的公共部分
#
# 用途：被其他脚本 source，提供：
#   · 凭据加载（从 /etc/ops-lab/db.conf 读取）
#   · 配置校验
#   · 公共变量
#
# 设计原则：凭据与代码分离
#   · 代码里【不出现】任何密码
#   · 密码只存在于 /etc/ops-lab/db.conf（600 权限，仓库外）
#   · 这样代码可以安全地提交到 Git
# ==============================================================================
# ── 统一 PATH ──
# cron 执行用户 crontab 时默认 PATH 是 /usr/bin:/bin，不含 /usr/sbin，
# 裸调 ss 会静默失败。脚本不应依赖调用方（登录 shell / cron / systemd）的 PATH。
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
DB_CONF="${DB_CONF:-/etc/ops-lab/db.conf}"

# ── 颜色 ──
if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_WARN=$'\033[33m'
    C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
    C_OK=""; C_BAD=""; C_WARN=""; C_DIM=""; C_RST=""
fi

# ── 加载凭据 ──
load_db_config() {
    if [ ! -f "$DB_CONF" ]; then
        echo "${C_BAD}❌ 凭据文件不存在: $DB_CONF${C_RST}" >&2
        echo "${C_DIM}   创建方法见 docs/db-credentials.md${C_RST}" >&2
        return 1
    fi

    # 权限检查：必须是 600，否则密码可能被其他用户读取
    local perm
    perm=$(stat -c '%a' "$DB_CONF" 2>/dev/null)
    if [ "$perm" != "600" ]; then
        echo "${C_BAD}❌ 凭据文件权限不安全: $perm（应为 600）${C_RST}" >&2
        echo "${C_DIM}   修复: chmod 600 $DB_CONF${C_RST}" >&2
        return 1
    fi

    # shellcheck disable=SC1090
    if ! source "$DB_CONF"; then
        echo "${C_BAD}❌ 凭据文件解析失败${C_RST}" >&2
        return 1
    fi

    # 必需变量检查
    local missing=""
    for var in DB_CONTAINER DB_ROOT_PASS DB_USER DB_USER_PASS DB_NAME; do
        if [ -z "${!var:-}" ]; then
            missing="$missing $var"
        fi
    done
    if [ -n "$missing" ]; then
        echo "${C_BAD}❌ 凭据文件缺少变量:$missing${C_RST}" >&2
        return 1
    fi

    return 0
}

# ── 检查容器是否运行 ──
check_container() {
    if ! docker ps --filter "name=${DB_CONTAINER}" --format '{{.Names}}' \
         | grep -q "^${DB_CONTAINER}$"; then
        echo "${C_BAD}❌ 容器 ${DB_CONTAINER} 未运行${C_RST}" >&2
        echo "${C_DIM}   启动: docker start ${DB_CONTAINER}${C_RST}" >&2
        return 1
    fi
    return 0
}

# ── 执行 SQL（用 root）──
db_root() {
    docker exec "${DB_CONTAINER}" mariadb \
        -uroot -p"${DB_ROOT_PASS}" "$@" 2>&1 | grep -v "Using a password on the command line"
}

# ── 执行 SQL（用应用用户）──
db_user() {
    docker exec "${DB_CONTAINER}" mariadb \
        -u"${DB_USER}" -p"${DB_USER_PASS}" "${DB_NAME}" "$@" 2>&1 \
        | grep -v "Using a password on the command line"
}
