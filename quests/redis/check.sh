#!/bin/bash
# ==============================================================================
# Redis 部署任务检查器
# ==============================================================================
set -uo pipefail

C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_WARN=$'\033[33m'
C_DIM=$'\033[2m'; C_RST=$'\033[0m'

PASS=0; FAIL=0
ok()   { printf "  ${C_OK}✅${C_RST} %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  ${C_BAD}❌${C_RST} %s\n" "$1"; FAIL=$((FAIL+1)); }
warn() { printf "  ${C_WARN}⚠️${C_RST}  %s\n" "$1"; }
info() { printf "  ${C_DIM}   %s${C_RST}\n" "$1"; }

CONF=/etc/ops-lab/db.conf

echo
echo "── Redis 部署检查 ──"
echo

# ── 1. 容器存在且运行 ──
echo "【1】容器状态"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^redis$'; then
    ok "容器 redis 运行中"
    STATUS=$(docker inspect --format '{{.State.Status}}' redis 2>/dev/null)
    info "状态: $STATUS"

    # 重启策略
    RESTART=$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' redis 2>/dev/null)
    if [ "$RESTART" = "always" ] || [ "$RESTART" = "unless-stopped" ]; then
        ok "配置了自动重启（$RESTART）"
    else
        warn "重启策略为 $RESTART（建议 always 或 unless-stopped）"
    fi
elif docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^redis$'; then
    bad "容器 redis 存在但未运行"
    info "用 docker logs redis 看原因"
    exit 1
else
    bad "容器 redis 不存在"
    exit 1
fi

# ── 2. 内存限制 ──
echo
echo "【2】内存限制"
MEM_LIMIT=$(docker inspect --format '{{.HostConfig.Memory}}' redis 2>/dev/null)
if [ -n "$MEM_LIMIT" ] && [ "$MEM_LIMIT" -gt 0 ]; then
    MEM_MB=$((MEM_LIMIT / 1024 / 1024))
    if [ "$MEM_MB" -le 128 ]; then
        ok "内存限制 ${MEM_MB}MB（≤128MB）"
    else
        bad "内存限制 ${MEM_MB}MB，超过 128MB"
    fi
else
    bad "未设置内存限制"
    info "启动时加 --memory=128m"
fi

echo
echo "  ── 实际占用 ──"
docker stats redis --no-stream --format "    {{.MemUsage}} ({{.MemPerc}})" 2>/dev/null

# ── 3. 端口绑定 ──
echo
echo "【3】端口绑定（安全）"
BIND=$(ss -tln 2>/dev/null | grep ':6379' | awk '{print $4}' | head -1)
if [ -z "$BIND" ]; then
    bad "6379 端口未监听"
elif echo "$BIND" | grep -q '^127.0.0.1:6379$'; then
    ok "仅绑定 127.0.0.1（公网不可达）"
elif echo "$BIND" | grep -q '0.0.0.0:6379'; then
    bad "绑定到 0.0.0.0 —— 暴露到公网，必须改为 127.0.0.1"
    info "Redis 未授权访问是最高频的入侵方式之一"
else
    warn "绑定地址: $BIND"
fi

# ── 4. 密码认证 ──
echo
echo "【4】认证配置"
NOPASS=$(docker exec redis redis-cli PING 2>&1 | head -1)
if echo "$NOPASS" | grep -q 'NOAUTH'; then
    ok "已设置密码（无密码连接返回 NOAUTH）"
    HAS_AUTH=1
elif echo "$NOPASS" | grep -q 'PONG'; then
    bad "未设置密码 —— 任何人都能连接"
    HAS_AUTH=0
else
    warn "无法判断认证状态: $NOPASS"
    HAS_AUTH=0
fi

# ── 5. 持久化 ──
echo
echo "【5】持久化配置"
PASS_VAL=$(grep '^REDIS_PASS=' "$CONF" 2>/dev/null | cut -d= -f2)
if [ -n "$PASS_VAL" ]; then
    AOF=$(docker exec redis redis-cli -a "$PASS_VAL" --no-auth-warning CONFIG GET appendonly 2>/dev/null | tail -1 | tr -d '\r')
    SAVE=$(docker exec redis redis-cli -a "$PASS_VAL" --no-auth-warning CONFIG GET save 2>/dev/null | tail -1 | tr -d '\r')
    if [ "$AOF" = "yes" ]; then
        ok "AOF 已启用"
    else
        info "AOF 未启用（appendonly=$AOF）"
    fi
    if [ -n "$SAVE" ]; then
        ok "RDB 快照已配置（save=$SAVE）"
    else
        info "RDB 快照未配置"
    fi
    if [ "$AOF" != "yes" ] && [ -z "$SAVE" ]; then
        bad "既没开 AOF 也没开 RDB —— 重启后数据全丢"
    fi
else
    warn "无法验证（凭据文件里没有 REDIS_PASS）"
fi

echo
echo "  ── 数据文件 ──"
docker exec redis ls -lh /data/ 2>/dev/null | grep -vE '^total' | sed 's/^/    /' || echo "    （无法读取）"

# ── 6. 数据卷 ──
echo
echo "【6】数据卷"
VOLS=$(docker inspect --format '{{range .Mounts}}{{.Type}}:{{.Name}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' redis 2>/dev/null)
if echo "$VOLS" | grep -q '/data'; then
    ok "数据目录已挂载"
    echo "$VOLS" | grep '/data' | sed 's/^/    /'
else
    bad "数据目录未挂载 —— 容器删除后数据丢失"
fi

echo
# 注意：判断只读挂载要用 docker inspect 的 .RW 字段
# （.RW=false 表示只读；直接匹配输出文本里的 "ro" 会误判）
CONF_MOUNT=$(docker inspect redis \
    --format '{{range .Mounts}}{{if eq .Destination "/usr/local/etc/redis/redis.conf"}}{{.RW}}{{end}}{{end}}' 2>/dev/null)

if [ -n "$CONF_MOUNT" ]; then
    if [ "$CONF_MOUNT" = "false" ]; then
        ok "配置文件已挂载（只读 :ro）"
    else
        warn "配置文件已挂载但可写（建议加 :ro 防止容器内被篡改）"
    fi
else
    warn "未挂载配置文件（用命令行参数也能工作）"
fi

# ── 7. 凭据管理 ──
echo
echo "【7】凭据管理（安全）"
if [ -f "$CONF" ]; then
    PERM=$(stat -c '%a' "$CONF")
    if [ "$PERM" = "600" ]; then
        ok "凭据文件权限 600"
    else
        bad "凭据文件权限 $PERM（应为 600）"
    fi

    if grep -q '^REDIS_PASS=' "$CONF" 2>/dev/null; then
        ok "REDIS_PASS 已存入凭据文件"
    else
        bad "凭据文件里没有 REDIS_PASS"
        info "密码应存在 /etc/ops-lab/db.conf，而不是命令行或脚本里"
    fi
else
    bad "凭据文件不存在: $CONF"
fi

# ── 8. 功能验证 ──
echo
echo "【8】功能验证"
if [ -n "$PASS_VAL" ] && [ "${HAS_AUTH:-0}" -eq 1 ]; then
    docker exec redis redis-cli -a "$PASS_VAL" --no-auth-warning SET quest:test "部署验证" > /dev/null 2>&1
    RESULT=$(docker exec redis redis-cli -a "$PASS_VAL" --no-auth-warning GET quest:test 2>/dev/null | tr -d '\r')
    if [ "$RESULT" = "部署验证" ]; then
        ok "读写正常（SET/GET 验证通过）"
    else
        bad "读写异常（GET 返回: $RESULT）"
    fi
    docker exec redis redis-cli -a "$PASS_VAL" --no-auth-warning DEL quest:test > /dev/null 2>&1
fi

echo
echo "  ── 运行状态 ──"
if [ -n "$PASS_VAL" ]; then
    docker exec redis redis-cli -a "$PASS_VAL" --no-auth-warning INFO server 2>/dev/null | \
      grep -E 'redis_version|uptime_in_seconds|tcp_port' | sed 's/^/    /'
    docker exec redis redis-cli -a "$PASS_VAL" --no-auth-warning INFO memory 2>/dev/null | \
      grep -E 'used_memory_human|maxmemory_human|maxmemory_policy' | sed 's/^/    /'
fi

# ── 汇总 ──
echo
echo "  ──────────────────────────────────────────"
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
    printf "  ${C_OK}🎉 全部通过（%d/%d）${C_RST}\n" "$PASS" "$TOTAL"
    echo
    echo "  下一步：写手记"
    echo "    重点写四个决策的理由：内存上限、持久化方式、密码生成、配置方式"
else
    printf "  通过 %d / 共 %d\n" "$PASS" "$TOTAL"
    echo
    echo "  参考排查方向："
    echo "    docker logs redis           # 启动失败的原因"
    echo "    docker inspect redis        # 检查启动参数"
    echo "    ss -tlnp | grep 6379        # 端口绑定"
fi
echo
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
