#!/bin/bash
# ==============================================================================
# 练习 02 环境准备：制造一个真实的、可排查的故障
#
# ⚠️ 这个脚本由「练习框架」在用户执行 lab reset 02 时调用，
#    用来【注入故障】。故障设计要满足：
#      1. 服务显示 active（不能一眼看出）
#      2. 但端口访问失败
#      3. 有明确、可查证的根因
#      4. 有多种排查路径（日志/端口/配置/进程）
#
# 故障设计：Nginx 配置监听了 9090，但 root 指向一个 nginx 用户无权读取的目录
#           → 服务正常启动（配置语法没错）
#           → 端口正常监听
#           → 但访问返回 403 Forbidden
#
# 为什么这样设计：
#   - 比"服务起不来"更贴近真实（真实故障往往是"起来了但不对"）
#   - 排查路径清晰：curl 看状态码 → error.log 看原因 → 检查权限 → 修复
#   - 涉及权限这个运维最高频的坑
# ==============================================================================

set -uo pipefail

LAB02_ROOT=/srv/www/lab02
LAB02_SITE=/etc/nginx/conf.d/lab02.conf
LAB02_STATE=/root/ops-lab/labs/02

echo "  ── 准备练习 02 的故障环境 ──"
echo

# --- 清理旧状态 ---
rm -rf "$LAB02_ROOT"
rm -f "$LAB02_SITE" /root/ops-lab/labs/.hint-state-02

# --- 创建站点目录，但【故意设置错误权限】---
mkdir -p "$LAB02_ROOT"
cat > "$LAB02_ROOT/index.html" <<'HTML'
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Lab 02</title></head>
<body>
<h1>LAB02-OK</h1>
<p>如果你看到这个页面，说明故障已修复。</p>
</body>
</html>
HTML

# 关键：把目录设为 700 root:root
# nginx worker 以 nginx 用户运行，无法进入该目录 → 403
chmod 700 "$LAB02_ROOT"
chown root:root "$LAB02_ROOT"
chmod 600 "$LAB02_ROOT/index.html"

echo "    已创建 $LAB02_ROOT（权限 700，属主 root）"
echo "    → nginx 用户无法进入这个目录"

# --- 创建 nginx 配置（语法正确，端口 9090）---
cat > "$LAB02_SITE" <<'CONF'
# 练习 02 的故障站点
#
# 提示：这个配置本身是【正确的】。
#       问题不在配置语法，在别处。
server {
    listen       9090;
    listen       [::]:9090;
    server_name  _;

    root  /srv/www/lab02;
    index index.html;

    access_log /var/log/nginx/lab02.access.log;
    error_log  /var/log/nginx/lab02.error.log warn;

    location / {
        try_files $uri $uri/ =404;
    }
}
CONF

echo "    已创建 $LAB02_SITE（监听 9090，配置语法正确）"

# --- 校验并加载配置 ---
if ! nginx -t 2>&1 | grep -qi 'successful'; then
  echo "    ❌ 配置语法有误，这不该发生！"
  nginx -t 2>&1 | sed 's/^/      /'
  exit 1
fi
echo "    ✅ nginx 配置语法正确（故障不在语法层面）"

systemctl reload nginx
sleep 1

# --- 确认故障已生效 ---
echo
echo "  ── 确认故障已注入 ──"
PORT_OK=0
ss -tln 2>/dev/null | grep -qE ':9090\b' && PORT_OK=1
if [ "$PORT_OK" -eq 1 ]; then
  echo "    ✅ 9090 端口已在监听"
else
  echo "    ⚠️ 9090 端口未监听"
fi

CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:9090/ 2>/dev/null || echo "000")
printf "    访问结果: HTTP %s" "$CODE"
case "$CODE" in
  403) echo " （✅ 符合预期的故障状态：权限拒绝）" ;;
  200) echo " （⚠️ 没注入成功？居然可访问）" ;;
  000) echo " （⚠️ 连不上，可能是端口问题）" ;;
  *)   echo " （状态码 $CODE）" ;;
esac

echo
echo "  ── 提示学生可能用到的排查入口（已存在证据）──"
echo "    · error.log: /var/log/nginx/lab02.error.log"
if [ -s /var/log/nginx/lab02.error.log ]; then
  echo "      （已有内容，共 $(wc -l < /var/log/nginx/lab02.error.log) 行）"
else
  # 触发一次错误让日志有内容
  curl -s http://127.0.0.1:9090/ >/dev/null 2>&1
  sleep 0.5
  if [ -s /var/log/nginx/lab02.error.log ]; then
    echo "      （已触发，共 $(wc -l < /var/log/nginx/lab02.error.log) 行）"
  else
    echo "      （暂无内容，学生访问后会产生）"
  fi
fi
echo "    · 服务状态: systemctl status nginx（会显示 active —— 这是干扰项）"
echo "    · 监听端口: ss -tlnp（会显示 9090 正常监听 —— 这也是干扰项）"
echo

# --- 创建 findings.md 模板 ---
mkdir -p "$LAB02_STATE"
if [ ! -f "$LAB02_STATE/findings.md" ]; then
  cat > "$LAB02_STATE/findings.md" <<'MD'
# 练习 02 排查记录

> 把你的排查过程写在这里。判分会检查这四个章节是否存在。
> 不要求写得多好，要求真的去查过。

## 现象

（你看到了什么？附上具体的命令和输出）


## 排查过程

（你依次检查了什么？每一步的结论是什么？哪些假设被推翻了？）


## 根因

（最终确定的原因。支持这个结论的证据是什么？）


## 修复

（你改了什么？为什么这么改？怎么确认修好了？）

MD
  echo "    已创建排查记录模板: $LAB02_STATE/findings.md"
else
  echo "    排查记录已存在: $LAB02_STATE/findings.md"
fi

echo
echo "  ✅ 练习 02 环境就绪"
echo
echo "  现在开始排查："
echo "    curl http://127.0.0.1:9090/     # 先复现问题"
echo "    lab show 02                  # 看要求"
echo "    lab hint 02                  # 卡住了看提示"
echo "    lab check 02                 # 判分"
