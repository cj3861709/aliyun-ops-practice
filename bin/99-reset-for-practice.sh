#!/bin/bash
# ==============================================================================
# 练习重置 / 参考实现移除
#
# 用途：老师先做了一套"参考实现"并验证通过。如果你要亲手重做一遍，
#       用这个脚本把它清干净，从零开始练。
#
# 用法：
#   ./99-reset-for-practice.sh status     # 只看当前状态，不动任何东西
#   ./99-reset-for-practice.sh soft       # 只删站点配置和文件，保留 nginx 包
#   ./99-reset-for-practice.sh hard       # 连 nginx 包一起卸载，完全回到起点
#
# 安全设计：
#   - 执行前自动备份
#   - 默认需要输入 yes 确认
#   - 会保留 /root/ops-lab 里的脚本和备份（那些是你的工具，不是练习对象）
# ==============================================================================
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

MODE="${1:-status}"
LAB=/root/ops-lab
SITE_ROOT=/var/www/personal-site
CONF=/etc/nginx/conf.d/personal-site.conf
DROPIN=/etc/systemd/system/nginx.service.d/override.conf

show_status() {
  echo "############ 当前状态 ############"
  echo
  echo "--- nginx 包 ---"
  rpm -q nginx 2>/dev/null || echo "  未安装"
  echo
  echo "--- 服务状态 ---"
  if systemctl list-unit-files 2>/dev/null | grep -q '^nginx.service'; then
    printf "  运行状态: %s\n" "$(systemctl is-active nginx 2>/dev/null)"
    printf "  开机自启: %s\n" "$(systemctl is-enabled nginx 2>/dev/null)"
    printf "  Restart策略: %s\n" "$(systemctl show nginx -p Restart --value 2>/dev/null)"
  else
    echo "  nginx.service 不存在"
  fi
  echo
  echo "--- 站点文件 ---"
  [ -d "$SITE_ROOT" ] && find "$SITE_ROOT" -type f -exec ls -l {} \; | awk '{printf "  %s  %s\n", $5, $9}' || echo "  无"
  echo
  echo "--- 站点配置 ---"
  [ -f "$CONF" ] && echo "  存在: $CONF" || echo "  无"
  echo
  echo "--- drop-in 覆盖 ---"
  [ -f "$DROPIN" ] && echo "  存在: $DROPIN" || echo "  无"
  echo
  echo "--- 监听端口 ---"
  ss -tlnp 2>/dev/null | grep -E ':(80|8080)\b' || echo "  80/8080 无监听"
  echo
  echo "--- 你的工具（不会被删除）---"
  ls -1 "$LAB/bin/" 2>/dev/null | sed 's/^/  /'
  echo "  备份: $(ls -1 "$LAB"/backups/*.tar.gz 2>/dev/null | wc -l) 份"
}

confirm() {
  echo
  echo "⚠️  即将执行【$1】"
  echo
  read -r -p "确认请输入 yes：" ans
  if [ "$ans" != "yes" ]; then
    echo "已取消。"
    exit 0
  fi
}

case "$MODE" in
  status)
    show_status
    ;;

  soft)
    show_status
    confirm "软重置：删除站点配置与文件，保留 nginx 包"
    echo
    echo "=== 1. 备份 ==="
    "$LAB/bin/backup-config.sh" pre-reset-soft

    echo
    echo "=== 2. 停止并禁用服务 ==="
    systemctl stop nginx 2>/dev/null && echo "  已停止" || echo "  (未运行)"
    systemctl disable nginx 2>/dev/null && echo "  已禁用自启" || echo "  (已禁用)"

    echo
    echo "=== 3. 删除 drop-in ==="
    rm -f "$DROPIN"
    rmdir /etc/systemd/system/nginx.service.d 2>/dev/null
    systemctl daemon-reload
    echo "  已删除并 daemon-reload"

    echo
    echo "=== 4. 删除站点配置与文件 ==="
    rm -f "$CONF"
    rm -rf "$SITE_ROOT"
    echo "  已删除 $CONF 和 $SITE_ROOT"

    echo
    echo "=== 5. 结果 ==="
    rpm -q nginx
    echo "  nginx 包保留（可用 systemctl start nginx 看默认页面）"
    echo
    echo "✅ 软重置完成。现在你可以自己动手了。"
    show_status
    ;;

  hard)
    show_status
    confirm "硬重置：卸载 nginx 包，完全回到未安装状态"
    echo
    echo "=== 1. 备份 ==="
    "$LAB/bin/backup-config.sh" pre-reset-hard

    echo
    echo "=== 2. 停止禁用删除服务 ==="
    systemctl stop nginx 2>/dev/null || true
    systemctl disable nginx 2>/dev/null || true
    rm -f "$DROPIN"
    rmdir /etc/systemd/system/nginx.service.d 2>/dev/null
    systemctl daemon-reload
    echo "  完成"

    echo
    echo "=== 3. 删除站点内容 ==="
    rm -f "$CONF"
    rm -rf "$SITE_ROOT"
    echo "  完成"

    echo
    echo "=== 4. 卸载 nginx 包 ==="
    yum remove -y nginx >/tmp/nginx-remove.log 2>&1
    echo "  卸载结果: $(rpm -q nginx 2>&1)"

    echo
    echo "=== 5. 清理残留日志 ==="
    rm -f /var/log/nginx/personal-site.*.log
    echo "  完成"

    echo
    echo "=== 6. 验证已回到起点 ==="
    command -v nginx >/dev/null && echo "  ⚠️ nginx 命令仍存在" || echo "  ✅ nginx 命令已消失"
    _SL=$(ss -tlnp 2>/dev/null)
    if printf '%s\n' "$_SL" | grep -qE ':(80|8080)'; then
      echo "  ⚠️ 仍有端口监听"
    else
      echo "  ✅ 无端口监听"
    fi
    [ -d /var/www/personal-site ] && echo "  ⚠️ 站点目录仍在" || echo "  ✅ 站点目录已删"
    echo
    echo "✅ 硬重置完成。环境已回到 2026-09-13 初始状态。"
    echo
    echo "  你的工具仍然保留："
    ls -1 "$LAB/bin/" | sed 's/^/    /'
    echo "  备份: $(ls -1 "$LAB"/backups/*.tar.gz | wc -l) 份"
    ;;

  *)
    echo "用法: $0 {status|soft|hard}"
    exit 1
    ;;
esac
