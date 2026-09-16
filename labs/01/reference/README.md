# 练习 01 参考实现

这是老师试做练习 01 时产出的实现，**仅供对照**。

> ⚠️ 强烈建议先自己做完再看这里。
> 直接抄答案学不到东西 —— 面试时手要放在键盘上。

## 关键决策点

| 决策 | 选择 | 理由 |
|---|---|---|
| 端口 | 8081（不是 8080） | 8080 被生产站点占用，同 server_name 会冲突 |
| 目录属主 | nginx:nginx | nginx worker 以 nginx 用户运行 |
| 目录权限 | 755 | 需要 x 权限才能进入目录 |
| 文件权限 | 644 | 需要 r 权限才能读取 |
| 配置方式 | conf.d 下独立文件 | 不污染主配置，便于管理 |
| 重载方式 | reload（非 restart） | 平滑加载，不中断连接 |
| 自愈配置 | systemd drop-in | 不改厂商文件，升级不丢失 |

## 那份配置里的注释

`lab01.conf` 头部有段注释解释了为什么用 8081：

```nginx
# 端口选择说明：
#   8080 已被生产站点占用。若也用 8080 且 server_name 相同（_），
#   nginx 会报 "conflicting server name" 并【静默忽略】本配置。
#   因此选用空闲的 8081。
```

**写这种"为什么"的注释是运维的好习惯** ——
半年后接手的人（可能是你自己）能立刻明白，而不用重新排查一遍。

## 试做时的完整步骤

```bash
# 1. 先看现状（不要急着改）
ss -tlnp | grep -E ':(80|8080|8081)'
ls -la /etc/nginx/conf.d/

# 2. 改配置前备份
/root/ops-lab/bin/backup-config.sh pre-lab01

# 3. 建目录和文件
mkdir -p /srv/www/lab01
cat > /srv/www/lab01/index.html <<'HTML'
<h1>LAB01-OK</h1>
HTML

# 4. 修权限（最容易忘的一步）
chown -R nginx:nginx /srv/www/lab01
chmod 755 /srv/www/lab01
chmod 644 /srv/www/lab01/index.html
# 验证 nginx 用户真的能读
sudo -u nginx test -r /srv/www/lab01/index.html && echo OK

# 5. 写配置
cat > /etc/nginx/conf.d/lab01.conf <<'CONF'
server {
    listen       8081;
    listen       [::]:8081;
    server_name  _;
    root  /srv/www/lab01;
    index index.html;
    location / { try_files $uri $uri/ =404; }
}
CONF

# 6. 校验（必做）
nginx -t
# ⚠️ 观察有没有 warning，不只是看最后一行

# 7. reload
systemctl reload nginx

# 8. 验证行为（不是看命令有没有报错）
curl -s http://127.0.0.1:8081/ | grep LAB01-OK

# 9. 自愈配置
mkdir -p /etc/systemd/system/nginx.service.d
cat > /etc/systemd/system/nginx.service.d/override.conf <<'EOF'
[Service]
Restart=always
RestartSec=2s
EOF
systemctl daemon-reload
systemctl show nginx -p Restart --value   # 应为 always

# 10. 备份脚本
cat > /root/ops-lab/bin/lab01-backup.sh <<'EOF'
#!/bin/bash
set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
DEST="/root/ops-lab/backups/${STAMP}-lab01-nginx.tar.gz"
mkdir -p "$(dirname "$DEST")"
tar -czf "$DEST" /etc/nginx
echo "已备份 -> $DEST"
EOF
chmod +x /root/ops-lab/bin/lab01-backup.sh

# 11. 判分
lab check 01
```

## 老师试做时的结果

```
【2.5】端口冲突检查（本练习的关键考点）
  ✅ 未使用冲突端口 8080（实际 listen: 8081）
  ✅ nginx 无 conflicting server name 警告

【6】实际自愈能力测试（关键！）
  ✅ kill 后自动恢复（PID 29545 → 29668）
  ✅ 恢复后站点仍可访问

退出码: 0   ✅ 全部通过
```
