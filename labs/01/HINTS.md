# 练习 01 —— 渐进式提示

> 规则：每次只给一条提示，从思路到具体操作递进。
> **先自己想，卡住了再看。** 直接看答案学不到东西。

### 提示 1：先搞清楚现状，再动手

不要急着写配置。先回答三个问题：

```bash
# 1. nginx 现在在监听哪些端口？
ss -tlnp | grep nginx

# 2. 现有的站点配置长什么样？（读懂了再模仿）
ls -la /etc/nginx/conf.d/
cat /etc/nginx/conf.d/personal-site.conf

# 3. nginx 的主配置 include 了哪些目录？
grep -n 'include' /etc/nginx/nginx.conf
```

**关键发现**：现有站点已经在用 8080 端口。你要么改端口，要么先处理冲突。

---

### 提示 2：为什么要用 8081（端口冲突）

**先自己查一下现状**：

```bash
ss -tlnp | grep -E ':(80|8080|8081)'
```

你会发现 80 和 8080 都已被现有站点监听。

**如果强行用 8080 会怎样？** 亲手试一下：

```bash
# 临时加一个用 8080 的配置（做完记得删）
cat > /etc/nginx/conf.d/test8080.conf <<'EOF'
server {
    listen 8080;
    server_name _;
    root /srv/www/lab01;
    index index.html;
}
EOF

nginx -t
# 会看到: [warn] conflicting server name "_" on 0.0.0.0:8080, ignored
# 但最后仍显示 "test is successful"

curl http://127.0.0.1:8080/
# 返回的是【生产站点】的页面，不是你的 lab01

rm -f /etc/nginx/conf.d/test8080.conf
```

**这个实验说明**：
- `nginx -t` 的 warning 不能忽略 —— 它可能意味着配置被忽略
- "test is successful" 只保证语法正确，不保证行为符合预期

**结论**：用 8081。已实测空闲。


### 提示 3：目录和文件要这样建

```bash
mkdir -p /srv/www/lab01
cat > /srv/www/lab01/index.html <<'HTML'
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Lab 01</title></head>
<body><h1>LAB01-OK</h1></body></html>
HTML
```

⚠️ 注意权限：nginx 的 worker 以 `nginx` 用户运行，它必须能读到这些文件。

```bash
# 验证 nginx 用户能否读取（这是最容易忽略的一步）
sudo -u nginx test -r /srv/www/lab01/index.html && echo "可读" || echo "不可读！"
```

---

### 提示 4：站点配置文件的结构

在 `/etc/nginx/conf.d/` 下新建一个 `.conf` 文件，结构参考现有站点：

```nginx
server {
    listen       8080;
    server_name  _;
    root         /srv/www/lab01;
    index        index.html;

    location / {
        try_files $uri $uri/ =404;
    }
}
```

**改完必须做**：

```bash
nginx -t              # 语法校验，错了好修
systemctl reload nginx   # 平滑加载（不是 restart）
curl http://127.0.0.1:8080/   # 验证
```

---

### 提示 5：Restart=always 怎么配

**不要直接改 `/usr/lib/systemd/system/nginx.service`** —— 包升级会被覆盖。

用 **drop-in**：

```bash
mkdir -p /etc/systemd/system/nginx.service.d
cat > /etc/systemd/system/nginx.service.d/override.conf <<'EOF'
[Service]
Restart=always
RestartSec=2s
EOF

systemctl daemon-reload        # 必须！否则不生效
systemctl show nginx -p Restart --value    # 验证
```

**这个文件项目里已经有了**（`/etc/systemd/system/nginx.service.d/override.conf`）——
先 `cat` 看看，理解每行的作用。

---

### 提示 6：备份脚本怎么写

参考项目里已有的 `/root/ops-lab/bin/backup-config.sh`：

```bash
#!/bin/bash
set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
DEST="/root/ops-lab/backups/${STAMP}-lab01-nginx.tar.gz"
mkdir -p "$(dirname "$DEST")"
tar -czf "$DEST" /etc/nginx
echo "已备份 -> $DEST"
```

```bash
chmod +x /root/ops-lab/bin/lab01-backup.sh
/root/ops-lab/bin/lab01-backup.sh    # 实测一次
ls -la /root/ops-lab/backups/ | tail -3
```

**注意**：判分只检查脚本存在、可执行、内容引用了正确的路径。
但你应该真的运行一次，确认它能产出文件。

---

### 提示 7：为什么判分要真的 kill 进程

判分脚本里有这一步：

```bash
kill -KILL $(cat /run/nginx.pid)
sleep 4
# 检查 PID 是否变化
```

**这是本练习最重要的部分。** 因为：

- `systemctl show nginx -p Restart` 输出 `always` 只说明**配置写对了**
- 只有真的 kill 掉，才能证明**行为符合预期**

这正是项目文档里反复强调的原则：

> **配置"写进去"和"配置生效"是两件不同的事。**
> 唯一可靠的验证是观察实际行为。

（详见 `/root/ops-lab/docs/postmortem/001-nginx-restart-policy.md`）

---

### 还是没做出来？

那就直接问我。**但请先告诉我你卡在哪一步、试过什么、报什么错** ——
这样我能给你针对性的帮助，而不是重复这些提示。
