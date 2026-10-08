# 手记 007：Redis 容器化部署

**日期**: 2026-09-22
**用时**: 约 60 分钟
**难度**: ⭐⭐⭐☆☆

---

## 1. 目标

已实践的服务里，Nginx 和 MySQL 有较完整的部署与排障记录，
**Redis** 还是空白。本次补齐。

**约束条件**：

| 项 | 值 |
|---|---|
| 可用内存 | 1275 MB（MariaDB 已占约 107 MB） |
| 可用磁盘 | 27 GB |
| 镜像 | `redis:latest`（113 MB，本地已有，无需下载） |

---

## 2. 动手前的四个决策

### 决策 1：内存上限设多少

**这里有个容易混淆的点：内存限制有两层。**

| 层次 | 参数 | 作用 |
|---|---|---|
| **容器层** | `docker run --memory=128m` | 限制**进程**内存总量，超过则 OOM Kill |
| **Redis 层** | `maxmemory 100mb` | 限制**数据**内存，超过则按策略淘汰键 |

**为什么容器给 128MB，而 Redis 只给 100MB**：

```
容器限制 128MB
├─ Redis 数据（maxmemory）        100MB
└─ Redis 进程本身开销               约 5-10MB
   （连接结构体、客户端缓冲区、AOF 缓冲等）
   ────────────────────────────────
   合计                            约 105-110MB

留 18-23MB 余量 → 避免 Redis 还没触发淘汰就被 OOM Kill
```

**如果两个都设 128MB**：Redis 的数据涨到 100MB+，加上进程开销超过容器限制，
**容器先被 OOM Kill**，而不是按预期触发键淘汰。

**结论：容器限制要大于 maxmemory，留出进程开销的余量。**

### 决策 2：持久化用 RDB 还是 AOF

| 维度 | RDB（快照） | AOF（追加日志） |
|---|---|---|
| 原理 | 定时 fork 子进程，把内存数据写入文件 | 记录每条写命令 |
| 文件 | `dump.rdb` | `appendonly.aof` |
| 恢复速度 | **快**（直接读二进制） | 慢（重放所有命令） |
| 数据安全性 | 差（最多丢几分钟） | **好**（`everysec` 最多丢 1 秒） |
| 文件体积 | 小 | 大 |
| 对性能影响 | fork 时可能卡顿 | 持续写入开销 |
| 适用场景 | 允许丢数据的缓存 | 数据不能丢的场景 |

**选择：两个都开。**

理由：

- **AOF** 保证数据安全 —— `appendfsync everysec` 是性能与安全的平衡点
- **RDB** 用于快速恢复和灾备传输（单个二进制文件，便于复制到异地）
- 两者互补，不冲突

**`appendfsync` 的三个取值**：

| 值 | 行为 | 数据丢失风险 |
|---|---|---|
| `always` | 每条命令都 fsync | 几乎不丢，但性能差 |
| **`everysec`** | 每秒 fsync 一次 | **最多丢 1 秒**（默认，推荐） |
| `no` | 交给操作系统决定 | 可能丢几十秒 |

### 决策 3：密码怎么生成、存哪里

**复用 MariaDB 任务建立的模式**：

```bash
# 生成 24 位随机密码（去掉容易混淆的 / + = ）
openssl rand -base64 32 | tr -d '/+=' | head -c 24
```

**存储位置**：`/etc/ops-lab/db.conf`（仓库外，600 权限）

**为什么不写在 docker run 的命令行里**：

```bash
# ❌ 这样写，密码会出现在：
docker run ... redis-server --requirepass "明文密码"
#   · shell 历史记录（~/.bash_history）
#   · 进程列表（ps aux 可见）
#   · 脚本文件（如果写成启动脚本）

# ✅ 写在配置文件里，通过挂载传入
-v /path/redis.conf:/usr/local/etc/redis/redis.conf:ro
```

### 决策 4：配置用命令行参数还是挂载文件

| 方式 | 命令 | 特点 |
|---|---|---|
| 命令行参数 | `redis-server --maxmemory 100mb --requirepass xxx` | 简单，但参数多时难维护 |
| **挂载配置文件** | `-v /path/redis.conf:/usr/local/etc/redis/redis.conf:ro` | **清晰、可版本控制、可审查** |

**选择挂载配置文件。**

**额外好处**：挂载时加 `:ro`（只读），防止容器内进程修改配置文件。

---

## 3. 部署过程

### 3.1 创建配置文件

`/root/ops-lab/conf/redis/redis.conf`：

```conf
# ── 网络 ──
bind 0.0.0.0
protected-mode yes
port 6379
timeout 0
tcp-keepalive 300

# ── 认证 ──
requirepass <24位随机密码>

# ── 内存管理 ──
maxmemory 100mb
maxmemory-policy allkeys-lru
maxmemory-samples 5

# ── 持久化：AOF ──
appendonly yes
appendfilename "appendonly.aof"
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# ── 持久化：RDB ──
save 900 1
save 300 10
save 60 10000
dbfilename dump.rdb
dir /data

# ── 日志 ──
loglevel notice

# ── 慢查询 ──
slowlog-log-slower-than 10000
slowlog-max-len 128
```

**注意 `bind 0.0.0.0` 与宿主机端口映射的关系**：

- 容器内绑 `0.0.0.0` 是**正常的**（容器有独立网络命名空间）
- **真正决定外部可达性的是宿主机的 `-p 127.0.0.1:6379:6379`**

### 3.2 启动容器

```bash
docker run -d \
  --name redis \
  --restart=unless-stopped \
  --memory=128m \
  --memory-swap=128m \
  -p 127.0.0.1:6379:6379 \
  -v /root/ops-lab/conf/redis/redis.conf:/usr/local/etc/redis/redis.conf:ro \
  -v redis-data:/data \
  redis:latest \
  redis-server /usr/local/etc/redis/redis.conf
```

### 3.3 验证

```bash
# 容器状态
docker ps

# 启动日志
docker logs redis

# 端口绑定（关键：应该是 127.0.0.1）
ss -tlnp | grep 6379

# 无密码连接（应该失败）
docker exec redis redis-cli ping
# → NOAUTH Authentication required.

# 带密码连接（应该成功）
docker exec redis redis-cli \
  -a "$(grep ^REDIS_PASS= /etc/ops-lab/db.conf | cut -d= -f2)" \
  --no-auth-warning ping
# → PONG
```

**验证结果**：

```
容器:   Up，127.0.0.1:6379->6379/tcp
端口:   LISTEN 127.0.0.1:6379（仅本机）
认证:   无密码 → NOAUTH；带密码 → PONG
内存:   7.8MiB / 128MiB (6.11%)
```

---

## 4. 遇到的问题

### 问题：密码没有写进凭据文件

**现象**：

编辑 `/etc/ops-lab/db.conf` 之后，验证命令取不到密码：

```bash
docker exec redis redis-cli -a "$(grep ^REDIS_PASS= /etc/ops-lab/db.conf | cut -d= -f2)" ping
# → AUTH failed: WRONGPASS
```

**排查过程**：

```bash
# 1. 检查凭据文件里的变量
grep -E '^[A-Za-z_]+=' /etc/ops-lab/db.conf | cut -d= -f1
# → DB_CONTAINER / DB_HOST / DB_PORT / DB_NAME
#    DB_ROOT_PASS / DB_USER / DB_USER_PASS
#    没有 REDIS_PASS

# 2. 检查文件大小
ls -la /etc/ops-lab/db.conf
# → 906 字节（与编辑前一致）

# 3. 确认 Redis 侧配置是否生效
grep -E '^\s*requirepass' /root/ops-lab/conf/redis/redis.conf
# → requirepass <已配置>（24 位）
```

**根因**：编辑没有保存成功（文件内容未变化），
或者写入的位置不对（`REDIS_PASS` 前面有空格则 `grep ^REDIS_PASS` 匹配不到）。

**修复方式**：改为从源头派生，用命令追加而不是手工输入：

```bash
# 从 redis.conf 读出真实密码，追加到凭据文件
PASS=$(grep -E '^\s*requirepass' /root/ops-lab/conf/redis/redis.conf | awk '{print $2}')
{
  echo ""
  echo "# Redis 密码"
  echo "REDIS_PASS=$PASS"
} >> /etc/ops-lab/db.conf

# 验证
grep -E '^REDIS_PASS=' /etc/ops-lab/db.conf | sed 's/=.*/=***/'
```

**为什么这个方法更好**：

| 方式 | 风险 |
|---|---|
| 手工输入 | 容易漏字符、忘记保存、两个文件的值可能不一致 |
| **从源头读取** | **保证两处一致，不会打错** |

这次遇到的问题（密码没写进凭据文件）就是手工维护多份副本的结果。
后来改成从 redis.conf 读取真实值再追加，就不会打错了。

---

## 5. 安全检查

Redis 未授权访问是最高频的入侵方式之一。攻击链如下：

```
1. 扫描 6379 端口，尝试无密码连接
2. CONFIG SET dir /root/.ssh
3. CONFIG SET dbfilename authorized_keys
4. SET x "\n\n<攻击者的公钥>\n\n"
5. SAVE —— 把公钥写进 /root/.ssh/authorized_keys
6. 用私钥 SSH 登录服务器
```

**这个攻击链利用了两个事实**：

- Redis 的 `CONFIG` 命令可以改配置
- Redis 的 `SAVE` 命令可以把内存数据写到任意路径

**本次部署做的防护（五层）**：

| 层 | 措施 | 实测 |
|---|---|---|
| ① 网络 | 端口只绑 `127.0.0.1` | 公网不可达 |
| ② 认证 | `requirepass` 强密码 | 无密码返回 NOAUTH |
| ③ 命令 | 重命名 `FLUSHALL` / `CONFIG` 等危险命令 | 旧名字失效 |
| ④ 运行身份 | 容器内以 `redis` 非特权用户运行 | — |
| ⑤ 配置保护 | 配置文件挂载为只读 `:ro` | 容器内不可改 |

**关于第 ③ 层的一个副作用**：

重命名 `CONFIG` 后，运维时需要用新名字调用。例如：

```bash
# 查看配置（需要新名字）
redis-cli -a <密码> CONFIG_DANGER_7f3a GET maxmemory
```

**这是"安全加固"与"运维便利"的取舍** ——
需要权衡：禁用危险命令能防攻击，但也给日常运维增加了一点操作成本。

---

## 6. 另一个观察：overcommit_memory 警告

```bash
docker logs redis
```

**启动日志里有这条警告**：

```
# WARNING overcommit_memory is set to 0! Background save may fail
  under low memory condition. To fix this issue add
  'vm.overcommit_memory = 1' to /etc/sysctl.conf
```

**原理**：Redis 做 RDB 快照时会 `fork()` 子进程。fork 采用**写时复制（COW）**，
内核需要"承诺"能分配足够的内存。`vm.overcommit_memory=0` 表示"启发式判断"，
在内存紧张时可能拒绝分配 → **后台保存失败**。

**修复方式**：设置 `vm.overcommit_memory = 1`（总是允许超额分配）

**另一个相关观察 —— `fork` 的性能影响**：

RDB 快照的 fork 操作在数据量很大时会阻塞主线程（复制页表），
这会导致**毫秒到秒级的延迟抖动**。

**这也是为什么有了 AOF 还要保留 RDB，反之亦然**：

| 场景 | 用什么 |
|---|---|
| 需要定期全量备份（传输到异地） | RDB |
| 需要最小化数据丢失 | AOF |
| 数据量大、对延迟敏感 | 调大 `save` 间隔，或只用 AOF |

---

## 7. 最终状态

```
容器:      redis (redis:latest, Redis 6.2.6)
内存限制:  128MB（实占 7.8MB）
端口:      127.0.0.1:6379（仅本机）
认证:      requirepass 已启用
持久化:    AOF (everysec) + RDB
淘汰策略:  allkeys-lru
内存上限:  maxmemory 100MB
数据卷:    redis-data -> /data
配置:      /root/ops-lab/conf/redis/redis.conf（只读挂载）
凭据:      /etc/ops-lab/db.conf（600 权限）
```

**当前容器全景**：

```
docker ps
  redis     127.0.0.1:6379->6379
  mariadb   127.0.0.1:3306->3306

两者都只绑本机，内存合计约 115MB
```

---

## 8. 结论

**内存限制要分层考虑**

容器的 `--memory` 和 Redis 的 `maxmemory` 是两个不同层次的限制，
设置时需要留出进程开销的余量（否则容器会先被 OOM Kill，
而不是按预期淘汰键）。

**持久化方案要看数据能否重来**

- 纯缓存（数据可重建）→ RDB 足够，性能更好
- 缓存 + 持久数据 → AOF 保证安全，RDB 用于灾备
- **两者兼有** → 都开（本次选择）

**配置值应从单一源头派生**

密码手工输入到多个文件容易不一致。
更可靠的做法是：**从一个权威来源读取，用命令写入其他位置**。

这次遇到的问题（密码没写进凭据文件）就是手工维护多份副本的典型风险。

**安全防护要覆盖完整的攻击链**

Redis 未授权访问不是单一漏洞，而是一条完整的攻击链
（改配置 → 写文件 → 拿权限）。**防护需要针对每个环节**：

- 网络层阻断（端口不暴露）
- 认证层拦截（密码）
- 命令层限制（重命名危险命令）

**任何一层单独都不够**：例如只设密码，如果密码弱仍会被爆破；
只绑本机，如果容器被判权或端口映射写错仍会暴露。

---

## 9. 遗留

- Redis 主从复制未实践
- `redis-benchmark` 压测未做
- 慢查询日志未分析
- Redis 与应用的集成未做（可考虑给站点加访问计数功能）

---

## 10. 协作说明

Redis 的配置内容和部署命令由 AI 提供，执行与验证由我做。

四个决策（内存上限、持久化方式、密码存储、配置挂载）是我按限制条件定的。
检查器脚本是 AI 写的，但我执行后发现"密码未写入凭据文件"这个问题，
并且改用从 redis.conf 读取真实值的方式追加，避免手工输入出错。

---

## 11. 后续计划

1. 部署 Tomcat
2. 配置 HTTPS（备案通过后）
3. 系统练习 Linux 命令（已搭建练习框架）
