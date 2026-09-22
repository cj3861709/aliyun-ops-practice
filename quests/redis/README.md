# 任务：部署并运维 Redis

**难度**: ⭐⭐⭐☆☆
**预计时间**: 60-90 分钟

---

## 背景

JD 要求「熟悉 Nginx、MySQL、Redis、Tomcat 等常用服务的安装、部署、调优及日常维护」。

Nginx 和 MySQL 你已经有实操，**Redis 还没有**。
本地已有 `redis:latest` 镜像（113MB），不需要重新下载。

---

## 任务目标

在这台服务器上部署 Redis，满足以下要求：

| # | 要求 | 说明 |
|---|---|---|
| 1 | **容器化部署** | 用 Docker 跑 redis:latest |
| 2 | **内存限制** | 限制在 128MB 以内 |
| 3 | **端口只绑本机** | 不能暴露到公网 |
| 4 | **设置认证密码** | 不能用无密码的默认配置 |
| 5 | **数据持久化** | 容器删除后数据不丢 |
| 6 | **密码存凭据文件** | 不能硬编码在脚本里 |

---

## 检查器

```bash
bash /root/ops-lab/quests/redis/check.sh
```

检查器验证的是**最终状态**，不看你用了什么命令。

---

## 关键决策（动手前想清楚）

### 决策 1：内存上限设多少？为什么？

可用内存 1275 MB，MariaDB 已占约 107 MB。

**提示**：Redis 的 `maxmemory` 配置和容器的 `--memory` 限制是两个不同的东西 ——
一个管"键的淘汰"，一个管"进程的存活"。

### 决策 2：持久化用 RDB 还是 AOF？

| | RDB | AOF |
|---|---|---|
| 原理 | 定时快照 | 记录每条写命令 |
| 数据安全 | 最多丢几分钟 | 最多丢 1 秒 |
| 恢复速度 | 快 | 慢 |
| 文件大小 | 小 | 大 |

**两个都开是可以的**（生产常见做法）。你选哪个？

### 决策 3：密码怎么生成、存哪里？

参考你之前 MariaDB 的做法：

```bash
# 生成随机密码
openssl rand -base64 32 | tr -d '/+=' | head -c 24

# 存到凭据文件（仓库外，600 权限）
/etc/ops-lab/db.conf
```

**提示**：`/etc/ops-lab/db.conf` 已经存在，包含 MariaDB 的凭据。
你需要往里面**追加** Redis 的配置。

### 决策 4：配置文件挂载还是用命令行参数？

| 方式 | 命令 | 特点 |
|---|---|---|
| 命令行参数 | `redis-server --maxmemory 100mb --requirepass xxx` | 简单，但参数多时难维护 |
| **挂载配置文件** | `-v /path/redis.conf:/usr/local/etc/redis/redis.conf` | **清晰，可版本控制** |

**推荐挂载配置文件** —— 因为面试会问"你的 Redis 配置在哪"，能拿出一个文件比背参数强。

---

## 命令参考

**你对 Docker 已经熟悉了，这里只列关键点：**

```bash
# 查看现有容器（避免命名冲突）
docker ps -a

# 启动容器（参考 MariaDB 的参数）
docker run -d \
  --name redis \
  --restart=unless-stopped \
  --memory=128m \
  -p 127.0.0.1:6379:6379 \
  -v <你的数据卷>:/data \
  -v <你的配置文件>:/usr/local/etc/redis/redis.conf:ro \
  redis:latest \
  redis-server /usr/local/etc/redis/redis.conf

# 验证
docker ps
docker logs redis
ss -tlnp | grep 6379
docker exec redis redis-cli -a '<密码>' ping
```

**注意 Redis 6.2 的一个行为**：
`redis-cli -a` 会输出密码警告，加 `--no-auth-warning` 可以抑制。

---

## 检查项

检查器会验证：

- [ ] 容器 `redis` 在运行
- [ ] 内存限制 ≤ 128MB
- [ ] 端口只绑定 127.0.0.1
- [ ] 有密码认证（无密码连接应失败）
- [ ] 配置了持久化（AOF 或 RDB）
- [ ] 有数据卷挂载
- [ ] 凭据在 `/etc/ops-lab/db.conf`（不在命令行明文）
- [ ] 写了 3 条测试数据后能读回

---

## 完成后

写一份手记，重点写：

1. **四个决策你怎么想的**（内存、持久化、密码、配置方式）
2. **遇到的问题**（如果有）
3. **Redis 的哪些特性你觉得值得记**

---

## 卡住了

**把命令和报错贴给老师**，不要只问"怎么办"。

**排查思路参考**：
- 容器起不来 → `docker logs redis`
- 连不上 → `ss -tlnp | grep 6379`、`docker ps`
- 认证失败 → 检查密码，`docker exec redis redis-cli CONFIG GET requirepass`
- 配置不生效 → 检查挂载路径、`docker exec redis cat /usr/local/etc/redis/redis.conf`
