# 手记 006：Docker 与 MariaDB 容器化部署

**日期**: 2026-09-21
**用时**: 约 150 分钟
**难度**: ⭐⭐⭐⭐☆

---

## 1. 目标

在 2 核 1.87 GB 的服务器上，学习并部署：

1. Docker 基础（容器生命周期、隔离机制）
2. MariaDB 容器化部署（内存调优、数据持久化）

**内存是主要约束** —— 系统与阿里云组件已占约 480 MB，可用约 1390 MB。

---

## 2. Docker 基础：容器就是被隔离的进程

### 2.1 最直观的一组对比

```bash
# 宿主机上的 PID 1
ps -p 1 -o pid,comm
    PID COMMAND
      1 systemd           ← 系统初始化进程

# 容器内的 PID 1
docker exec test1 ps aux
PID   USER     TIME  COMMAND
    1 root      0:00 sleep 300     ← 我启动的进程
    7 root      0:00 ps aux
```

**这个对比说明容器的本质**：

> 容器不是虚拟机，而是**一个被隔离的普通进程**。
> 容器内的 PID 1 就是你启动的那个进程，不是 systemd、不是 init。

### 2.2 由此推导出的生命周期规则

因为容器的 PID 1 就是主进程，所以：

```
容器的主进程退出  →  容器结束
```

实测验证：

```bash
$ docker run alpine echo "hello from container"
hello from container

$ docker ps          # 看不到它
$ docker ps -a       # 才能看到
b0dc28faab37   alpine   "echo 'hello from co…"   Exited (0)
```

**`echo` 执行完就退出 → 容器立即结束。**

**这和之前学的 systemd 完全一致**：

| 场景 | 结果 |
|---|---|
| `Type=simple` 服务主进程退出 | systemd 认为服务结束 |
| 容器主进程退出 | 容器变为 Exited |

**也解释了为什么长期运行的服务脚本要写 `while true` 循环** ——
这在任务 002 写 `quest02.sh` 时已经实践过。

### 2.3 隔离性的验证

```bash
$ docker exec test1 hostname
0971f9c06127          # 容器 ID，不是宿主机名

$ docker exec test1 ls /
bin  dev  etc  home  lib  ...    # 容器自己的文件系统

$ docker exec test1 ps aux
PID   USER     TIME  COMMAND
    1 root      0:00 sleep 300   # 看不到宿主机的任何进程
    24 root     0:00 ps aux
```

**三个隔离维度**：

| 命令 | 验证的隔离 | 对应的命名空间 |
|---|---|---|
| `hostname` | 主机名独立 | UTS |
| `ls /` | 根文件系统独立 | Mount |
| `ps aux` | 进程空间独立 | PID |

### 2.4 与 systemd 的概念对应

之前学的 systemd 知识可以直接迁移：

| systemd | Docker |
|---|---|
| `systemctl start` | `docker run` / `docker start` |
| `systemctl stop` | `docker stop`（发 SIGTERM，10 秒后 SIGKILL） |
| `systemctl status` | `docker ps` / `docker inspect` |
| `Restart=always` | `docker run --restart=always` |
| `journalctl -u <服务>` | `docker logs <容器>` |
| unit 文件 | Dockerfile |
| `User=chenjin` | `USER chenjin` |
| **CGroup = 服务的进程边界** | **容器 = 一个 CGroup** |

### 2.5 退出码的含义

```bash
$ docker kill --signal=KILL mariadb
$ docker ps -a
容器: mariadb  状态: Exited (137)
                          └─┬─┘
                    137 = 128 + 9（SIGKILL）
```

| 退出码 | 含义 |
|---|---|
| `0` | 正常退出 |
| `1` | 应用错误 |
| `137` | SIGKILL（`docker kill -s KILL` 或 OOM Kill） |
| `143` | SIGTERM（`docker stop` 超时后的结果） |

---

## 3. 镜像拉取问题的排查

### 3.1 现象

```bash
$ docker pull mariadb:11
Error response from daemon: Get "https://registry-1.docker.io/v2/":
net/http: request canceled while waiting for connection
(Client.Timeout exceeded while awaiting headers)
```

**注意错误指向的是 `registry-1.docker.io`（官方仓库），而不是配置的镜像源。**

### 3.2 逐层排查

```bash
# 步骤 1：查看配置
$ cat /etc/docker/daemon.json
{ "registry-mirrors": ["https://6lhfyd5x.mirror.aliyuncs.com"] }

# 步骤 2：测试镜像源是否可达
$ curl -o /dev/null -w '%{http_code}' https://6lhfyd5x.mirror.aliyuncs.com/v2/
200                                    ← 服务活着

# 步骤 3：测试源里有没有目标镜像
$ curl -o /dev/null -w '%{http_code}' \
    https://6lhfyd5x.mirror.aliyuncs.com/v2/library/mariadb/manifests/11
404                                    ← 没有缓存！

# 步骤 4：对照测试（拉一个必定存在的镜像）
$ docker pull alpine:3.19
Error: registry-1.docker.io 超时           ← 也失败！
```

### 3.3 根因

**`registry-mirrors` 是缓存代理，不是完整的镜像仓库副本。**

```
Docker 拉取流程：
  1. 向镜像源请求 manifest
     · 源有缓存 → 从源下载         ✅
     · 源无缓存 → 源返回 404
  2. 任一源失败 → 回退到 registry-1.docker.io
  3. 官方仓库直连超时 → 整体失败
```

**这个镜像源虽然响应 200，但缓存是空的** —— 它是个已失效的旧账号专属地址。

### 3.4 解决

```bash
# 改用 DaoCloud 镜像源
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://6lhfyd5x.mirror.aliyuncs.com"
  ]
}
EOF

systemctl restart docker     # 必须 restart，镜像源配置不支持 reload

# 验证
docker info | grep -A3 "Registry Mirrors"

# 拉取成功（5 秒）
docker pull alpine:3.19
```

### 3.5 排查方法的通用性

```
现象：拉取超时
  ↓
诊断 1：服务是否可达（/v2/ 端点）     → 200（活着）
  ↓
诊断 2：具体资源是否存在（manifest）  → 404（没有）
  ↓
诊断 3：对照实验（拉已知存在的镜像）   → 也失败（确认整体不可用）
  ↓
结论：源是"空壳"
```

**关键点：不要因为"服务返回 200"就认为它能用。**

**这与任务 003 的教训一致**：

| 任务 003 | 本次 |
|---|---|
| `is-active` 说 active，不代表服务在干活 | 源返回 200，不代表有缓存 |
| 验证要看功能产出（日志增长） | 验证要看能否真的拉到镜像 |
| 用对照实验确认 | 用已知存在的镜像做对照 |

---

## 4. MariaDB 部署的三个设计决策

### 4.1 数据存哪：命名卷 vs 绑定挂载

| 方案 | 命令 | 特点 |
|---|---|---|
| 匿名卷 | `docker run mariadb` | 名字是随机 hash，无法管理 |
| **命名卷** | `-v mariadb-data:/var/lib/mysql` | **名字清晰，Docker 管理权限** |
| 绑定挂载 | `-v /opt/mysql:/var/lib/mysql` | 路径直观，但权限要对齐 |

**选择命名卷。** 理由：

MariaDB 镜像的 Dockerfile 里有 `VOLUME [/var/lib/mysql]` 声明，
所以不指定 `-v` 时会自动创建**匿名卷** —— 数据不会丢，但难以管理。

**绑定挂载的权限陷阱**：

```
容器内:  mysql 用户，UID 999
宿主机:  /opt/mysql 属主是 root:root
    ↓
容器内 mysql 用户无法写入 → 启动失败
    ↓
需要 chown -R 999:999 /opt/mysql
```

**但 999 这个 UID 是特定镜像的设定** —— 换个镜像可能不同，这是绑定挂载的脆弱之处。

### 4.2 端口怎么暴露

```bash
-p 3306:3306              # ❌ 绑定 0.0.0.0，公网可达
-p 127.0.0.1:3306:3306    # ✅ 只本机可连
```

**选择只绑本机。** 服务器有公网 IP，MySQL 是高频攻击目标。

**实测验证**：

```bash
$ ss -tlnp | grep 3306
LISTEN 0 4096 127.0.0.1:3306 0.0.0.0:* users:(("docker-proxy",pid=104065))
              └────┬────┘
        只有本机，没有 0.0.0.0

$ # 从公网 IP 测试
$ timeout 5 bash -c 'cat < /dev/null > /dev/tcp/<你的公网IP>/3306' 2>/dev/null
$ echo $?
1                          ← 连接失败（符合预期）
```

**注意一个容易混淆的点**：

```
容器日志显示:  Server socket created on IP: '0.0.0.0', port: '3306'
```

**这是容器【内部】的监听地址** —— 容器的网络是隔离的，
真正决定可达性的是**宿主机的端口映射**。

**纵深防御**：网络层（只绑本机）+ 认证层（强密码）= 两层保护。

### 4.3 内存限制

```bash
--memory=400m           # 硬限制，超过则 OOM Kill
--memory-swap=400m      # 禁用 swap
```

**为什么数据库要禁用 swap**：

```
内存不足 → 数据换到 swap（磁盘）
    ↓
磁盘比内存慢几个数量级
    ↓
查询从毫秒变秒级，甚至触发 swap thrashing 导致整机卡死
    ↓
宁可被 OOM Kill 后重启，也不要 swap 导致的性能崩塌
```

**启动命令**：

```bash
docker run -d \
  --name mariadb \
  --restart=unless-stopped \
  --memory=400m --memory-swap=400m \
  -p 127.0.0.1:3306:3306 \
  -v mariadb-data:/var/lib/mysql \
  -e MARIADB_ROOT_PASSWORD='<从凭据文件读取>' \
  -e MARIADB_DATABASE=opslab \
  -e MARIADB_USER=opsuser \
  -e MARIADB_PASSWORD='<从凭据文件读取>' \
  mariadb:11 \
  --character-set-server=utf8mb4 \
  --collation-server=utf8mb4_unicode_ci \
  --innodb-buffer-pool-size=128M
```

**实测资源占用**：

```
内存:  97.6 MiB / 400 MiB (24.4%)
镜像:  329 MB
数据卷: 162 MB
```

---

## 5. 三个验证实验

### 5.1 实验一：数据持久化

**验证问题**：容器删除后，数据还在吗？

```bash
# ① 写入数据
docker exec mariadb mariadb -uopsuser -p'<密码>' opslab -e "
INSERT INTO uptime_log (note) VALUES ('容器删除前写入的数据');
"

# ② 删除容器（不加 -v，数据卷保留）
docker rm -f mariadb

# ③ 确认数据卷还在
docker volume ls | grep mariadb-data
local     mariadb-data

# ④ 用同一个数据卷重建容器（初始化参数不需要了）
docker run -d --name mariadb \
  -v mariadb-data:/var/lib/mysql \
  -e MARIADB_ROOT_PASSWORD='***' \
  mariadb:11 ...

# ⑤ 验证
docker exec mariadb mariadb -uopsuser -p'<密码>' opslab -e "SELECT * FROM uptime_log;"
id  note                    created_at
1   容器删除前写入的数据      2026-09-21 06:34:15     ← 数据还在！
```

**结论**：

```
容器（无状态）              数据卷（有状态）
     ↓                          ↓
docker rm -f 删除              不受影响
     ↓                          ↓
重建容器（新 ID）            重新挂载同一卷
874a7573923d                    ↓
                            数据完好
```

**参数对比**：

| 参数 | 首次启动 | 重建时 | 原因 |
|---|---|---|---|
| `-e MARIADB_DATABASE` | 需要 | 不需要 | 库已存在 |
| `-e MARIADB_USER` | 需要 | 不需要 | 用户已存在 |
| `MARIADB_ROOT_PASSWORD` | 需要 | **仍需要** | 否则容器拒绝启动 |
| `-v mariadb-data:/var/lib/mysql` | **必须** | **必须** | 数据来源 |

**初始化参数只在"数据目录为空"时生效** —— 这是官方镜像 entrypoint 的逻辑。

### 5.2 实验二：备份与恢复

**验证问题**：误删数据后能否恢复？

```bash
# ① 备份（备份文件写【宿主机】，不在容器内）
docker exec mariadb mariadb-dump \
  -uroot -p'<密码>' --single-transaction --routines --triggers \
  opslab > /root/ops-lab/logs/db-backup/opslab-$(date +%Y%m%d-%H%M%S).sql

# ② 模拟误删
docker exec mariadb mariadb -uroot -p'<密码>' opslab -e "DROP TABLE uptime_log;"

# ③ 确认损坏
docker exec mariadb mariadb -uopsuser -p'<密码>' opslab -e "SELECT * FROM uptime_log;"
ERROR 1146 (42S02): Table 'opslab.uptime_log' doesn't exist

# ④ 恢复
docker exec -i mariadb mariadb -uroot -p'<密码>' opslab < backup.sql

# ⑤ 验证
docker exec mariadb mariadb -uopsuser -p'<密码>' opslab -e "SELECT * FROM uptime_log;"
id  note                    created_at
1   容器删除前写入的数据      2026-09-21 06:34:15
2   第二条测试数据            06:40:07
3   第三条测试数据            06:40:07
```

**关键参数**：

```bash
--single-transaction
```

| 不用它 | 用它 |
|---|---|
| 备份时锁表 → 业务阻塞 | 用事务快照 → 不阻塞 |
| 备份期间写入可能导致不一致 | 保证一致性 |

**原理**：InnoDB 的 MVCC 能在事务开始时冻结数据快照。
**注意**：只对 InnoDB 表有效，MyISAM 仍会锁表。

**一个容易忽略的点**：

```bash
# ✅ 正确：重定向在宿主机执行，备份文件写在宿主机
docker exec mariadb mariadb-dump ... > /host/path/backup.sql

# ❌ 错误：备份文件在容器里，容器删除就丢了
docker exec mariadb bash -c "mariadb-dump ... > /tmp/backup.sql"
```

**备份文件不能和数据放在同一个失败域里。**

### 5.3 实验三：崩溃恢复

**验证问题**：数据库被强制杀死后，已提交的数据会丢吗？

```bash
# ① 写入数据并确认
docker exec mariadb mariadb -uroot -p'<密码>' opslab -e "
INSERT INTO uptime_log (note) VALUES ('崩溃测试 - 第4条');
SELECT COUNT(*) FROM uptime_log;
"
4

# ② 强制杀死（模拟断电）
docker kill --signal=KILL mariadb
docker ps -a
容器: mariadb  状态: Exited (137)

# ③ 重启
docker start mariadb
sleep 15

# ④ 【关键】观察恢复日志
docker logs mariadb | grep -E "crash recovery|recover|LSN"
2026-09-21 6:48:11 [Note] InnoDB: Starting crash recovery from checkpoint LSN=45568
2026-09-21 6:48:11 [Note] InnoDB: End of log at LSN=55529
2026-09-21 6:48:11 [Note] InnoDB: To recover: 31 pages

# ⑤ 验证数据
docker exec mariadb mariadb -uopsuser -p'<密码>' opslab -e "SELECT COUNT(*) FROM uptime_log;"
4                        ← 4 条全部完好，包括崩溃前插入的那条
```

**日志解读**：

```
Starting crash recovery from checkpoint LSN=45568
                              └────┬────┘
                    最后一次"已安全落盘"的位置

End of log at LSN=55529
                   └────┬────┘
            redo log 的末尾（崩溃前的最后操作）

To recover: 31 pages
            └──┬──┘
      两点之间需要重放的页数
```

**LSN（Log Sequence Number）** 是 InnoDB 的日志序列号，单调递增，标记每次数据变更。

**验证的原理：WAL（Write-Ahead Logging）**

```
数据写入流程：
  1. 修改内存中的 buffer pool（脏页）
  2. 【先】写 redo log（ib_logfile0）—— 顺序写，快
  3. 【后】异步刷脏页到 ibdata1 —— 随机写，慢

崩溃时：
  · 内存中的脏页丢失
  · 但 redo log 里有完整的操作记录
  · 重启时重放 redo log → 恢复未落盘的数据
```

**数据文件对照**：

```
ibdata1        12 MB    InnoDB 系统表空间
ib_logfile0   100 MB    redo log（崩溃恢复的关键）
aria_log.*      5 MB    Aria 引擎日志
ibtmp1         12 MB    临时表空间
```

**"先写日志"是持久化系统的通用模式**：

| 系统 | 日志机制 | 作用 |
|---|---|---|
| MariaDB | redo log | 崩溃恢复 |
| ext4 文件系统 | journal | 文件系统一致性 |
| Redis | AOF | 持久化 |

---

## 6. 凭据管理

### 6.1 问题

数据库密码硬编码在脚本里，脚本被提交到 Git：

```bash
# 脚本里的密码
ROOT_PASS="<数据库密码>"
```

**风险**：

| 因素 | 评估 |
|---|---|
| 仓库是 Private | 降低风险，但密码仍在远程服务器上 |
| Git 历史永久保留 | 即使删除文件，`git log -p` 仍能翻出来 |
| **维护性问题** | 换密码需改代码并重新提交，而配置分离只改一处 |

### 6.2 正确做法：凭据与代码分离

```bash
# 配置文件（仓库外，600 权限）
/etc/ops-lab/db.conf
  DB_ROOT_PASS=<24位随机密码>
  DB_USER_PASS=<24位随机密码>

# 脚本里只引用路径
source /etc/ops-lab/db.conf
```

**生成强密码**：

```bash
openssl rand -base64 32 | tr -d '/+=' | head -c 24
```

**共享模块 `_common.sh`**：

```bash
load_db_config() {
    # 1. 检查文件存在
    # 2. 【关键】检查权限必须是 600
    if [ "$perm" != "600" ]; then
        echo "❌ 凭据文件权限不安全: $perm（应为 600）"
        return 1
    fi
    # 3. source 加载
    # 4. 检查必需变量是否存在
}
```

**权限检查的意义**：如果凭据文件权限被改宽，脚本拒绝运行，
避免"密码可能已泄漏却继续使用"。

### 6.3 密码轮换

```bash
# 重置数据库密码
docker exec mariadb mariadb -uroot -p'<旧密码>' -e "
ALTER USER 'root'@'localhost' IDENTIFIED BY '<新密码>';
ALTER USER 'root'@'%' IDENTIFIED BY '<新密码>';
ALTER USER 'opsuser'@'%' IDENTIFIED BY '<新密码>';
FLUSH PRIVILEGES;
"

# 验证：新密码可用 + 旧密码失效
```

**注意到 `root@'%'` 存在** —— 意味着如果端口暴露到公网，
root 可以从任何地方登录。**这是端口只绑本机的另一个理由。**

### 6.4 从 Git 历史清除密码

**关键认知：仅删除文件不够，必须重写历史。**

即使后续提交删除了含密码的文件，`git log -p` 仍能翻出旧内容 ——
密码存在于历史提交的对象里。

**完整流程**：

```bash
# 1. 建备份分支（安全网，出错可回滚）
git branch backup-before-rewrite

# 2. 重置到目标提交
git reset --soft <clean-commit>

# 3. 显式暂存改写后的文件
#    注意：--soft 只移动 HEAD，不改变暂存区
#    这一步不能省，否则提交的是旧内容
git add <改写的文件>

# 4. 用 --amend 替换提交（而非新建提交）
git commit --amend --no-edit

# 5. 强推
git push --force-with-lease origin main
```

**`reset --soft` / `--mixed` / `--hard` 的行为差异**：

| 参数 | HEAD | 暂存区 | 工作区 |
|---|---|---|---|
| `--soft` | 移动 | **不变** | 不变 |
| `--mixed`（默认） | 移动 | 重置 | 不变 |
| `--hard` | 移动 | 重置 | **重置（会丢改动）** |

**验证方法**（用独立手段，避免只看差异）：

```bash
# 方法 1：直接搜索提交对象的内容
git grep -q "<密码模式>" HEAD
git grep -q "<密码模式>" origin/main

# 方法 2：搜索完整历史（最全面）
git log origin/main -p | grep -c "<密码模式>"
```

**为什么用 `git grep` 而不是 `git diff | grep`**：

| 方法 | 检查对象 | 特点 |
|---|---|---|
| `git diff --cached \| grep` | 差异内容 | 依赖 diff 的语义，边界情况易误判 |
| **`git grep <模式> <提交>`** | **提交对象的实际内容** | **直接读取，结果确定** |
| `git log -p \| grep -c` | 完整历史 | 覆盖所有可达提交 |

**强推后必须重新 fetch 再验证**：

```bash
git push --force-with-lease origin main
git fetch origin              # ← 刷新远程引用
git grep origin/main          # ← 检查【远程的】内容，而非本地
```

只检查本地不能证明远程已更新 ——
推送可能部分失败、可能有多个分支未同步、本地引用可能是缓存的。

---

## 7. 最终架构

```
阿里云 ECS（2C2G, 1.87GB 内存, 28G 磁盘）
│
├─ 宿主机
│   ├─ nginx 1.24（静态站点，端口 80/8080）
│   ├─ systemd（quest02 心跳服务）
│   ├─ fail2ban（3 个 jail）
│   ├─ cron（流量监控 + 日常巡检 daily-check.py）
│   ├─ Docker 26.1.3
│   └─ 凭据文件
│       ├─ /etc/ops-lab/mail.conf（600）
│       └─ /etc/ops-lab/db.conf（600）
│
└─ Docker 容器
    └─ mariadb:11
        ├─ 内存限制 400MB（实占 ~98MB）
        ├─ 端口仅绑定 127.0.0.1:3306
        └─ 数据卷 mariadb-data（162MB）
```

---

## 8. 结论

**容器是进程的隔离，不是虚拟机的替代**

容器内的 PID 1 就是启动的那个进程，这决定了两件事：

- **生命周期**：主进程退出，容器就结束
- **信号处理**：`docker stop` 发 SIGTERM 给 PID 1，主进程应正确处理

**这和 systemd 的设计逻辑一致** —— 服务管理器只跟踪主进程状态。

**镜像源是缓存代理，不是完整仓库副本**

`registry-mirrors` 失败时会回退到官方仓库。
如果源本身失效（服务可达但缓存为空），排查需要：

1. 测服务可达性（`/v2/` 端点）
2. 测目标资源存在性（manifest）
3. **对照实验**（拉一个必定存在的镜像）

**不能因为"服务返回 200"就认为能用。**

**容器运维的核心是"无状态容器 + 有状态数据卷"**

三个实验共同验证了这个模式：

| 实验 | 验证的内容 |
|---|---|
| 数据持久化 | 容器删除重建，数据完好 |
| 备份恢复 | 误删后能还原 |
| 崩溃恢复 | 强杀后已提交数据不丢 |

这三个实验做下来，对「容器无状态 + 数据有状态」这个模式有了具体的认识。

**凭据必须与代码分离**

密码硬编码在脚本里会进入 Git 历史，即使后续删除也难以彻底清除。
正确做法是配置文件外部化（`/etc/` + 600 权限），
并且脚本在读取前校验文件权限。

**验证清除是否成功时，必须用可靠方法**：
`git grep <模式> <提交>` 直接读取内容，比分析 diff 可靠。

---

## 9. 协作说明

本文的命令和参数说明来自 AI，执行和判断由我做。

具体来说：容器参数（内存限制、端口绑定、数据卷）、三个验证实验的操作、
镜像拉取失败的排查、凭据文件的创建与密码轮换，都是我实际执行的。
AI 提供的是命令形式、概念解释（容器与 systemd 的对应、WAL 机制）和排查方向。

有两处需要自己拿主意：

一是镜像拉取超时的排查。表面看是"换个源"，但要定位到根因需要分层验证 ——
源可达性、镜像是否存在、对照实验，每一步都要设计验证方法。

二是崩溃恢复实验。`docker kill --signal=KILL` 是破坏性操作，
动手前要想清楚数据会不会丢、丢了怎么回滚。

---

## 10. 遗留

- MariaDB 主从复制未实践（单机内存受限）
- 慢查询分析未做
- 备份尚未配置定时任务
- 数据库未与网站集成（可考虑给站点加访问统计功能）

---

## 11. 后续计划

1. 将 MariaDB 健康检查接入日常巡检脚本
2. 配置数据库自动备份的 cron 任务
3. 编写项目 README
