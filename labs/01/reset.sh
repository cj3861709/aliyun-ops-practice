#!/bin/bash
# 练习 01 重置：清理该练习产生的所有内容，回到起点
#
# 注意：只清理【本练习相关】的东西，不动生产站点的配置。

SITE_ROOT=/srv/www/lab01
BACKUP_SCRIPT=/root/ops-lab/bin/lab01-backup.sh
LAB_CONF=/etc/nginx/conf.d/lab01.conf

echo "  ── 清理站点文件 ──"
if [ -d "$SITE_ROOT" ]; then
  rm -rf "$SITE_ROOT"
  echo "    已删除 $SITE_ROOT"
else
  echo "    $SITE_ROOT 不存在（无需清理）"
fi

echo
echo "  ── 清理 nginx 配置 ──"
FOUND=0
for f in /etc/nginx/conf.d/*.conf; do
  [ -f "$f" ] || continue
  case "$(basename "$f")" in
    personal-site.conf|00-limit-zones.conf) continue ;;   # 生产配置，不动
  esac
  if grep -qE 'lab01|srv/www/lab01' "$f" 2>/dev/null; then
    rm -f "$f"
    echo "    已删除 $f"
    FOUND=1
  fi
done
[ "$FOUND" -eq 0 ] && echo "    未发现 lab01 相关配置"

echo
echo "  ── 清理备份脚本 ──"
if [ -f "$BACKUP_SCRIPT" ]; then
  rm -f "$BACKUP_SCRIPT"
  echo "    已删除 $BACKUP_SCRIPT"
else
  echo "    $BACKUP_SCRIPT 不存在（无需清理）"
fi

echo
echo "  ── 恢复 nginx（如果配置被改坏）──"
if nginx -t >/dev/null 2>&1; then
  echo "    配置语法正常"
  systemctl reload nginx 2>/dev/null
  echo "    已 reload"
else
  echo "    ⚠️ 配置有语法错误，尝试从最近备份恢复"
  LATEST=$(ls -1t /root/ops-lab/backups/*.tar.gz 2>/dev/null | head -1)
  if [ -n "$LATEST" ]; then
    TMPD=$(mktemp -d)
    tar -xzf "$LATEST" -C "$TMPD" 2>/dev/null
    if [ -d "$TMPD/etc/nginx" ]; then
      cp -a "$TMPD/etc/nginx/." /etc/nginx/
      echo "    已从 $(basename "$LATEST") 恢复 /etc/nginx"
    fi
    rm -rf "$TMPD"
    if nginx -t >/dev/null 2>&1; then
      systemctl reload nginx
      echo "    恢复后语法正常，已 reload"
    else
      echo "    ❌ 恢复后仍有错，需要人工处理"
    fi
  fi
fi

echo
echo "  ── 确认生产站点未受影响 ──"
printf "    生产站点: HTTP %s\n" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ 2>/dev/null)"
printf "    nginx: %s\n" "$(systemctl is-active nginx)"

echo
echo "  ── 清理提示记录 ──"
rm -f /root/ops-lab/labs/.hint-state-01
echo "    已清理"

echo
echo "  ✅ 练习 01 已重置，可以重新开始"
