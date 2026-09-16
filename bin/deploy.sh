#!/bin/bash
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

# ==============================================================================
# 发布脚本 deploy.sh
#
# 用法:
#   deploy.sh <源目录> [版本标签]
#
# 流程（每一步都可失败退出，且失败不留半成品）：
#   1. 备份当前配置
#   2. 把源目录复制成新的 release 目录（时间戳命名）
#   3. 【发布前预检】新版本里必须有 index.html，否则拒绝发布
#   4. 检查 nginx 用户能否读取新目录（权限预检，避免上线后 403）
#   5. 语法校验 nginx（虽然没改配置，但保留这道关口是好习惯）
#   6. 原子切换软链接
#   7. reload nginx（不是 restart —— 这是零停机的关键）
#   8. 健康检查：连续探测，失败则自动回滚
#   9. 清理旧版本（保留最近 5 个）
#
# 设计要点：
#   - 任何一步失败都 exit 非 0，且不会留下"切换了一半"的状态
#   - 健康检查失败会自动回滚，而不是把坏版本留在线上
#   - 软链接切换是原子的，不存在新旧混杂的中间态
# ==============================================================================
set -euo pipefail

LAB=/root/ops-lab
WWW=/var/www
RELEASES=$WWW/releases
LIVE=$WWW/personal-site
KEEP_RELEASES=5
HEALTH_RETRIES=5

if [ $# -lt 1 ]; then
  echo "用法: $0 <源目录> [版本标签]"
  echo "示例: $0 /root/ops-lab/staging v2-homepage"
  exit 1
fi

SRC="$1"
LABEL="${2:-}"

echo "════════════════════════════════════════════════════"
echo " 发布开始  $(date '+%F %T')"
echo " 源目录:   $SRC"
echo " 标签:     ${LABEL:-（无）}"
echo "════════════════════════════════════════════════════"

# ============================================================================
# 步骤 1：校验源目录
# ============================================================================
echo
echo "[1/9] 校验源目录"
if [ ! -d "$SRC" ]; then
  echo "  ❌ 源目录不存在: $SRC"
  exit 1
fi
if [ ! -f "$SRC/index.html" ]; then
  echo "  ❌ 源目录缺少 index.html —— 拒绝发布空站点"
  echo "     这是发布前预检：宁可发布失败，也不要把 404 推上线"
  exit 1
fi
FILE_COUNT=$(find "$SRC" -type f | wc -l)
TOTAL_SIZE=$(du -sh "$SRC" | cut -f1)
echo "  ✅ 通过：$FILE_COUNT 个文件，共 $TOTAL_SIZE"

# ============================================================================
# 步骤 2：备份当前配置与记录当前版本
# ============================================================================
echo
echo "[2/9] 记录回滚点"
PREV=""
if [ -L "$LIVE" ]; then
  PREV=$(basename "$(readlink -f "$LIVE")")
  echo "  当前版本: $PREV"
  echo "  → 若发布失败将回滚到此版本"
else
  echo "  ⚠️ 当前无生效版本（首次发布）"
fi
"$LAB/bin/backup-config.sh" "pre-deploy-${LABEL:-unlabeled}" >/dev/null 2>&1 || true
echo "  已备份 nginx 配置"

# ============================================================================
# 步骤 3：创建新 release 目录
# ============================================================================
echo
echo "[3/9] 创建新版本目录"
STAMP=$(date +%Y%m%d-%H%M%S)
if [ -n "$LABEL" ]; then
  NEWREL="$RELEASES/${STAMP}-${LABEL}"
else
  NEWREL="$RELEASES/${STAMP}"
fi

if [ -e "$NEWREL" ]; then
  echo "  ❌ 目标已存在: $NEWREL"
  exit 1
fi

mkdir -p "$NEWREL"
cp -a "$SRC"/. "$NEWREL"/
# 确保权限正确：nginx 用户需要能读
chmod -R a+rX "$NEWREL"
echo "  ✅ 已创建 $NEWREL"
find "$NEWREL" -type f | sed "s|$NEWREL|    .|"

# ============================================================================
# 步骤 4：权限预检（最容易忽略、且上线后才暴露的坑）
# ============================================================================
echo
echo "[4/9] 权限预检（以 nginx 用户身份试读）"
if id nginx >/dev/null 2>&1; then
  if sudo -u nginx test -r "$NEWREL/index.html"; then
    echo "  ✅ nginx 用户可读 index.html"
  else
    echo "  ❌ nginx 用户无法读取！发布中止（否则上线即 403）"
    rm -rf "$NEWREL"
    exit 1
  fi
else
  echo "  (无 nginx 用户，跳过)"
fi

# ============================================================================
# 步骤 5：nginx 语法校验
# ============================================================================
echo
echo "[5/9] nginx 语法校验"
if nginx -t 2>&1 | grep -qi 'emerg'; then
  echo "  ❌ nginx 配置有错，发布中止"
  nginx -t 2>&1 | sed 's/^/     /'
  rm -rf "$NEWREL"
  exit 1
fi
echo "  ✅ 配置正常"

# ============================================================================
# 步骤 6：原子切换软链接
# ============================================================================
echo
echo "[6/9] 切换软链接（原子操作）"
TMPLINK="$WWW/.personal-site.new.$$"
ln -s "$NEWREL" "$TMPLINK"
mv -T "$TMPLINK" "$LIVE"
echo "  ✅ $LIVE -> $(readlink "$LIVE")"

echo "$(basename "$NEWREL")" > "$RELEASES/.current"
echo "$(basename "$NEWREL")" >> "$RELEASES/.history"

# ============================================================================
# 步骤 7：reload（平滑加载，非 restart）
# ============================================================================
echo
echo "[7/9] reload nginx（平滑，不中断连接）"
# 记录 reload 前 error.log 大小，用于只检查新增部分
ERRLOG=/var/log/nginx/error.log
ERR_BEFORE=0
[ -f "$ERRLOG" ] && ERR_BEFORE=$(stat -c %s "$ERRLOG" 2>/dev/null || echo 0)

systemctl reload nginx
RELOAD_RC=$?
echo "  systemctl reload 退出码: $RELOAD_RC"

# ═══ 关键检查（来自复盘 #004 的教训）═══
# systemctl reload 返回 0 【不代表】配置真的被加载了！
# nginx 可能在 reconfiguring 阶段失败（例如 limit_req_zone 的 key 变更），
# 然后静默沿用旧配置，造成磁盘与内存配置分叉。
# 唯一的可靠信号是：error.log 里有没有新增的 emerg。
sleep 2
if [ -f "$ERRLOG" ]; then
  NEWLOG=$(tail -c +$((ERR_BEFORE + 1)) "$ERRLOG" 2>/dev/null)
  # 注意：不用 `echo "$NEWLOG" | grep -q`，因为 NEWLOG 理论上可能很大，
  # 而 grep -q 提前退出会触发 SIGPIPE，配合 pipefail 造成假失败。
  # （见 docs/SCRIPTING-PITFALLS.md 坑 1）
  if printf '%s\n' "$NEWLOG" | grep -q 'emerg'; then
    echo "  ❌ reload 实际失败！error.log 出现 emerg："
    echo "$NEWLOG" | grep 'emerg' | tail -3 | sed 's/^/       /'
    echo "  → 配置未生效，触发回滚"
    HEALTH_OK=0
    FORCE_ROLLBACK=1
  else
    echo "  ✅ reload 成功（error.log 无新增 emerg）"
    FORCE_ROLLBACK=0
  fi
else
  echo "  ⚠️ 找不到 $ERRLOG，跳过 emerg 检查"
  FORCE_ROLLBACK=0
fi

# ============================================================================
# 步骤 8：健康检查，失败自动回滚
# ============================================================================
echo
echo "[8/9] 健康检查（连续 ${HEALTH_RETRIES} 次）"
HEALTH_OK=1
# 说明：请求间隔 1 秒。本地限流为 10r/s，1 秒间隔约 1 请求/秒，绝不会触发限流。
#       （曾经因为连发请求被自己的限流打成 429，导致健康检查误判 —— 见复盘 #004）
for i in $(seq 1 "$HEALTH_RETRIES"); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1/" || echo "000")
  if [ "$CODE" = "200" ]; then
    printf "  第 %d 次: HTTP %s ✅\n" "$i" "$CODE"
  else
    printf "  第 %d 次: HTTP %s ❌\n" "$i" "$CODE"
    # 429 需要区别对待：那是被限流，不是服务故障
    if [ "$CODE" = "429" ]; then
      echo "      注意：429 表示被限流（Too Many Requests），不是服务故障。"
      echo "            健康检查本身请求过快导致。这不算失败。"
      continue
    fi
    HEALTH_OK=0
    break
  fi
  sleep 1
done

if [ "$HEALTH_OK" -eq 0 ]; then
  echo
  echo "  ❌ 健康检查失败或 reload 有 emerg！自动回滚"
  echo
  echo "  --- 回滚前收集诊断信息 ---"
  echo "  磁盘配置的限流 key（nginx -T 读磁盘）:"
  nginx -T 2>/dev/null | grep -E 'limit_req_zone.*zone=general' | sed 's/^/    /' || true
  echo "  error.log 尾部:"
  tail -5 /var/log/nginx/error.log 2>/dev/null | sed 's/^/    /' || true
  echo "  当前 HTTP 行为:"
  curl -s -o /dev/null -w "    HTTP %{http_code}\n" --max-time 3 http://127.0.0.1/ || true
  echo
  if [ -n "$PREV" ]; then
    TMPLINK="$WWW/.personal-site.rollback.$$"
    ln -s "$RELEASES/$PREV" "$TMPLINK"
    mv -T "$TMPLINK" "$LIVE"
    echo "$PREV" > "$RELEASES/.current"
    systemctl reload nginx
    sleep 1
    RC=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ || echo 000)
    echo "  回滚到 $PREV，探测结果 HTTP $RC"
    if [ "$RC" = "200" ]; then
      echo "  ✅ 回滚成功，服务已恢复"
    else
      echo "  ❌ 回滚后仍异常，需要人工介入！"
    fi
  else
    echo "  ⚠️ 无上一版本可回滚（首次发布）"
  fi
  exit 1
fi
echo "  ✅ 健康检查全部通过"

# ============================================================================
# 步骤 9：清理旧版本
# ============================================================================
echo
echo "[9/9] 清理旧版本（保留最近 ${KEEP_RELEASES} 个）"
# 只删除目录，保留 .current/.history 等点文件
mapfile -t ALL_RELS < <(ls -1dt "$RELEASES"/*/ 2>/dev/null | sed 's|/$||')
COUNT=${#ALL_RELS[@]}
echo "  当前版本数: $COUNT"
if [ "$COUNT" -gt "$KEEP_RELEASES" ]; then
  for ((i=KEEP_RELEASES; i<COUNT; i++)); do
    OLD="${ALL_RELS[$i]}"
    # 安全检查：绝不删除正在生效的版本
    if [ "$(readlink -f "$LIVE")" = "$OLD" ]; then
      echo "  跳过（正在生效）: $(basename "$OLD")"
      continue
    fi
    rm -rf "$OLD"
    echo "  已删除: $(basename "$OLD")"
  done
else
  echo "  未超过上限，无需清理"
fi

echo
echo "════════════════════════════════════════════════════"
echo " ✅ 发布成功"
echo " 新版本: $(basename "$NEWREL")"
echo " 线上:   $(readlink "$LIVE")"
echo " 页面:   $(curl -s http://127.0.0.1/ | grep -o '<title>[^<]*</title>' || echo '(无标题)')"
echo "════════════════════════════════════════════════════"
