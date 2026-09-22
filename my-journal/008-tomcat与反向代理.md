# 手记 008：Tomcat 部署与 nginx 反向代理

**日期**: 2026-09-22
**用时**: 约 120 分钟
**难度**: ⭐⭐⭐⭐☆

---

## 1. 目标

已部署的服务里 Nginx / MySQL / Redis 都有实操记录，
**Tomcat** 还是空白。本次补齐，并实践 **nginx 反向代理**（两者配合的常见架构）。

---

## 2. JDK 安装与版本选择

### 2.1 环境

```bash
java -version
# -bash: java: command not found
```

### 2.2 可选的 JDK 发行版

查询软件源时发现两套选择：

```
java-11-openjdk-headless            标准 OpenJDK，约 180 MB
java-11-alibaba-dragonwell-headless 阿里云发行版，约 39 MB
java-17-*                           更新的 LTS 版本
java-1.8.0-*                        老项目兼容
```

**选择 Dragonwell**，理由：

| 理由 | 说明 |
|---|---|
| **体积小 4.6 倍** | 39MB vs 180MB（流量敏感场景重要） |
| **官方维护** | 阿里云在自家 ECS 上维护的发行版 |
| **完全兼容** | Dragonwell 就是 OpenJDK + 补丁 |

### 2.3 OpenJDK 的多个发行版

这是一个值得了解的知识点：

| 发行商 | 产品名 |
|---|---|
| Oracle | Oracle JDK |
| 社区 | OpenJDK |
| Eclipse 基金会 | Temurin（原 AdoptOpenJDK） |
| **阿里云** | **Dragonwell** ← 本次使用 |
| 亚马逊 | Corretto |
| Azul | Zulu |
| 腾讯 | Kona |
| 华为 | Bisheng JDK |

**差异点**：GC 调优、长期支持承诺、许可条款、特定场景优化。

**实践价值**：选择版本时了解发行版差异，能避免"默认用哪个就用哪个"的盲目性。

### 2.4 装完后发现的问题：JDK 被"抢"了

**装完 Dragonwell 后**：

```bash
java -version
# openjdk version "11.0.31.28"（Alibaba Dragonwell Extended Edition）
```

**安装 Tomcat 后，再查**：

```bash
java -version
# openjdk version "11.0.25" 2024-10-15 LTS
# OpenJDK Runtime Environment (Red_Hat-11.0.25.0.9-1)
```

**变回 Red Hat 的 OpenJDK 了。**

**原因**：Tomcat 的依赖自动拉入了 `java-11-openjdk-headless`，
并且 `alternatives` 系统的软链接被切走了：

```bash
alternatives --display java
# java - status is auto.
#   link currently points to /usr/lib/jvm/java-11-openjdk-11.0.25.../bin/java
#   java-11-alibaba-dragonwell... priority -1881773657     ← 负数优先级！
```

**Dragonwell 的优先级是负数** —— 在 `auto` 模式下永远排在最后。

**这是 `alternatives` 机制的一个注意点**：
安装新包时，系统会自动把软链接指向优先级更高的那个，
**可能覆盖你之前的选择**。

**验证方法**：

```bash
alternatives --display java        # 看当前指向和所有候选的优先级
readlink -f /usr/bin/java          # 看实际指向的路径
```

---

## 3. Tomcat 部署

### 3.1 安装方式选择

| 方式 | 命令 | 特点 |
|---|---|---|
| **yum 安装** | `yum install -y tomcat tomcat-webapps tomcat-admin-webapps` | 自动处理依赖、系统集成 |
| **官方 tar.gz** | 下载解压到 `/opt/tomcat` | 目录结构清晰、版本可选 |

**选择 yum**，因为：

1. 自动处理 servlet-api、jsp-api 等依赖
2. **自带 systemd unit** —— 可以对比自己手写的服务单元
3. 符合 RHEL 系的目录规范

**安装结果**：

```
tomcat            1:9.0.87-2.al8    96 k
tomcat-admin-webapps                77 k
tomcat-webapps                      84 k
tomcat-lib                         5.6 M
tomcat-servlet-4.0-api             290 k
tomcat-jsp-2.3-api                  75 k
tomcat-el-3.0-api                  109 k
tomcat-native                       81 k
...（共 15 个包，50 MB）
```

### 3.2 RHEL 系的目录布局（与官方文档不同）

**yum 安装**：

```
/usr/share/tomcat/          程序（jar 包、脚本）
/etc/tomcat/                配置（server.xml 等）
/var/lib/tomcat/            数据（webapps）
/var/log/tomcat/            日志
/var/cache/tomcat/          缓存（工作目录）
```

**官方 tar.gz 安装**：

```
/opt/tomcat/bin/            启动脚本
/opt/tomcat/conf/           配置
/opt/tomcat/webapps/        应用
/opt/tomcat/logs/           日志
```

**这个差异很重要** —— 网上大部分教程按 tar.gz 布局写，照抄会找不到文件。

### 3.3 自带 unit 文件的分析

```bash
systemctl cat tomcat
```

```ini
[Unit]
Description=Apache Tomcat Web Application Container
After=syslog.target network.target

[Service]
Type=simple
EnvironmentFile=/etc/tomcat/tomcat.conf      ← 环境变量外部化
Environment="NAME="
EnvironmentFile=-/etc/sysconfig/tomcat       ← 减号 = 文件不存在也不报错
ExecStart=/usr/libexec/tomcat/server start   ← 包装脚本，不是直接跑 java
SuccessExitStatus=143                        ← 143 视为正常退出
User=tomcat                                  ← 非 root 用户

[Install]
WantedBy=multi-user.target
```

**三个值得对比的设计**（与之前手写的 `quest02.service`）：

| 设计 | Tomcat 的做法 | 我的 quest02 做法 |
|---|---|---|
| **环境变量** | `EnvironmentFile` 外部化 | 硬编码在脚本里 |
| **文件缺失容错** | `EnvironmentFile=-`（减号） | 无此机制 |
| **信号处理** | `SuccessExitStatus=143` 在服务层声明 | 脚本里 `trap cleanup TERM` |

**`SuccessExitStatus=143` 的原理**：

```
143 = 128 + 15（SIGTERM 的信号编号）

systemd 默认把非 0 退出码视为失败
但 systemctl stop 发的就是 SIGTERM
→ 不声明的话，每次正常停止都会记录为"失败"
```

**两种解决方案对比**：

| 方案 | 做法 | 优劣 |
|---|---|---|
| **应用层** | 脚本 `trap` + `exit 0` | 需改脚本，但更可控 |
| **服务层** | unit 里声明 `SuccessExitStatus` | 不动脚本，集中管理 |

**这是"分层处理"的实例** —— 同一需求可以在不同层次解决。

### 3.4 缺 Restart 策略（与 nginx 同样的问题）

```bash
systemctl show tomcat -p Restart
# Restart=no
```

**发行版的 unit 普遍不设 `Restart`** —— 设计者认为服务崩溃应该人工介入。

之前审计发现 10 个服务存在此问题，Tomcat 是第 11 个。

**修复方式**（复用 nginx 的方案）：

```bash
mkdir -p /etc/systemd/system/tomcat.service.d

cat > /etc/systemd/system/tomcat.service.d/override.conf <<'EOF'
[Service]
Restart=always
RestartSec=5s
EOF

systemctl daemon-reload
systemctl show tomcat -p Restart -p RestartUSec
```

**drop-in 的价值**：

| 价值 | 说明 |
|---|---|
| **升级安全** | `yum update` 更新原 unit 时，修改不丢 |
| **可追溯** | `systemctl cat tomcat` 能看到全部修改 |
| **可回滚** | 删除 drop-in 目录即恢复原状 |

---

## 4. 端口冲突：服务 active 但功能不可用

### 4.1 现象

```bash
systemctl start tomcat
systemctl status tomcat
```

```
● tomcat.service - Apache Tomcat Web Application Container
   Active: active (running) since Tue 2026-09-22 16:44:20 CST; 5s ago
 Main PID: 221773 (java)
    Tasks: 22
   Memory: 116.7M
```

**看起来完全正常。** 但访问测试暴露了问题：

```bash
curl -I http://127.0.0.1:8080/
```

```
HTTP/1.1 200 OK
Server: nginx                     ← 返回的是 nginx！
Content-Length: 11843
```

### 4.2 排查

```bash
# Tomcat 进程监听了哪些端口
ss -tlnp | grep java
LISTEN [::ffff:127.0.0.1]:8005     ← 只有 shutdown 端口
                                   ← 没有 8080！

# 8080 是谁
ss -tlnp | grep ':8080'
LISTEN 0.0.0.0:8080  users:(("nginx",...))
```

### 4.3 根因

**日志里有关键错误**：

```bash
journalctl -u tomcat | grep -iE 'severe|bind'
```

```
SEVERE [main] Failed to initialize component [Connector["http-nio-8080"]]
Caused by: java.net.BindException: Address already in use
```

**完整链条**：

```
Tomcat 启动
    ↓
尝试绑定 8080 → 被 nginx 占用 → BindException
    ↓
HTTP 连接器（Connector）初始化失败
    ↓
但 shutdown 端口 8005 绑定成功
    ↓
JVM 主进程继续运行
    ↓
systemd 看到主进程活着 → 报告 active (running)
    ↓
【服务状态正常，但功能完全不可用】
```

### 4.4 为什么这个故障特别隐蔽

| 检查项 | 结果 | 能否发现问题 |
|---|---|---|
| `systemctl is-active` | active | ❌ |
| `systemctl status` | running, 116MB 内存 | ❌ |
| 进程存在 | java PID 221773 | ❌ |
| 8005 端口监听 | 有 | ❌ |
| **8080 端口监听** | **无** | ✅ |
| **curl 8080** | **200，但 Server 头是 nginx** | ✅ |

**`curl` 返回 200 反而是误导** —— 因为它访问到的是 nginx 的静态站点。

**判断"访问到的到底是谁"，要看 `Server` 响应头**：

```
Server: nginx                    → 走到了 nginx
无 Server 头 + Transfer-Encoding: chunked → 走到了 Tomcat
```

### 4.5 修复

```bash
# 1. 备份
cp /etc/tomcat/server.xml /etc/tomcat/server.xml.bak-$(date +%s)

# 2. 改端口（用行号精确定位，避免误改注释中的同名配置）
sed -i '69s/port="8080"/port="8082"/' /etc/tomcat/server.xml

# 3. 验证
sed -n '69p' /etc/tomcat/server.xml
```

**为什么用行号而不用全局替换**：

`server.xml` 里有两处 `port="8080"`，其中一处被注释掉（备选配置示例）。
全局替换虽然无害，但不够精确。

### 4.6 修复结果

| 项 | 修复前 | 修复后 |
|---|---|---|
| **8082 监听** | 无 | `*:8082`（java） |
| **curl 响应** | `Server: nginx` | 无 Server 头 + chunked |
| **日志** | `BindException` | 无错误，只有 `Started` |
| **Restart** | `no` | `always`（5s） |

**这是第三次遇到"服务状态正常但功能不可用"**：

| 场景 | 假正常的现象 | 真正的验证方法 |
|---|---|---|
| 心跳服务（手记 003） | `is-active` 说 active，日志不增长 | 观察产出是否变化 |
| Docker 端口（手记 006） | 容器 Up，端口未映射 | 从外部实测可达性 |
| **Tomcat（本次）** | active + 116MB 内存，但端口没监听 | `ss` 看端口 + `curl` 看 Server 头 |

**共同规律**：

> **`systemctl is-active` 只能证明进程存在，不能证明服务在工作。**

---

## 5. 崩溃自愈验证

**配好 `Restart=always` 后用 SIGKILL 验证**：

```bash
PID_BEFORE=$(systemctl show tomcat -p MainPID --value)   # 222298
kill -9 $PID_BEFORE
sleep 8
PID_AFTER=$(systemctl show tomcat -p MainPID --value)    # 222671
```

**结果**：

```
修复前 PID: 222298
修复后 PID: 222671     ← 变化了，说明重启成功
端口 8082 仍在监听（pid=222671）
```

**与 nginx 的验证方式一致** —— `kill -9` 模拟 OOM Killer 的行为
（不可捕获的信号，`trap` 无法处理）。

---

## 6. nginx 反向代理 Tomcat

### 6.1 架构

```
客户端
   ↓
nginx (8083)
   ├─ /app/  → Tomcat (127.0.0.1:8082)  ← 动态应用
   └─ /*     → 静态文件
```

### 6.2 配置

`/etc/nginx/conf.d/tomcat-proxy.conf`：

```nginx
upstream tomcat_backend {
    server 127.0.0.1:8082;
    keepalive 16;
}

server {
    listen       8083;
    listen       [::]:8083;
    server_name  _;

    access_log  /var/log/nginx/tomcat-proxy.access.log;
    error_log   /var/log/nginx/tomcat-proxy.error.log;

    location /app/ {
        limit_req  zone=general burst=20 nodelay;
        limit_conn perip 10;

        proxy_pass http://tomcat_backend/;

        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 5s;
        proxy_send_timeout    30s;
        proxy_read_timeout    30s;

        proxy_buffering off;
    }

    location / {
        root  /var/www/personal-site;
        index index.html;
    }
}
```

### 6.3 三个关键配置点

**`proxy_pass` 末尾的斜杠**

```nginx
proxy_pass http://tomcat_backend/;     # 有斜杠
proxy_pass http://tomcat_backend;      # 无斜杠
```

| 写法 | 请求 `/app/foo` 转发为 |
|---|---|
| **有斜杠** | `/foo`（替换掉 location 匹配的部分） |
| 无斜杠 | `/app/foo`（原样拼接） |

**这个差异容易踩坑**，写错会导致后端的路径不对。

**`proxy_http_version 1.1` + `Connection ""` 必须成对**

```nginx
proxy_http_version 1.1;
proxy_set_header Connection "";
```

**为什么两个都要写**：

```
nginx 默认对后端使用 HTTP/1.0，并带 Connection: close
    ↓
upstream 里配的 keepalive 完全不生效
    ↓
要启用长连接，必须：
  1. 声明使用 HTTP/1.1
  2. 清空 Connection 头（不能是 close）
```

**实测验证**（Tomcat 访问日志）：

```
修复前:  "GET / HTTP/1.0" 200 11044     ← HTTP/1.0
修复后:  "GET / HTTP/1.1" 200 11064     ← HTTP/1.1，且响应大了 20 字节
```

**四个传递头的作用**

| 头部 | 作用 | 不传的后果 |
|---|---|---|
| `Host` | 原始主机名 | Tomcat 看到的是 `127.0.0.1:8082` |
| `X-Real-IP` | 客户端真实 IP | Tomcat 日志里全是 nginx 的 IP |
| `X-Forwarded-For` | 代理链（多个代理时追加） | 无法追溯真实来源 |
| `X-Forwarded-Proto` | 原始协议 | 应用生成的绝对 URL 会用错协议 |

### 6.4 验证

**验证一：内容一致性**

```bash
curl -s http://127.0.0.1:8083/app/ | wc -c
# 11044 —— 与直连 Tomcat 的响应大小一致
```

**验证二：响应头对比**

```bash
# 直连 Tomcat
curl -I http://127.0.0.1:8082/
HTTP/1.1 200
Content-Type: text/html;charset=UTF-8
Transfer-Encoding: chunked
                              ← 无 Server 头

# 经 nginx 代理
curl -I http://127.0.0.1:8083/app/
HTTP/1.1 200
Server: nginx/1.24.0          ← nginx 加了 Server 头
Content-Type: text/html;charset=UTF-8
```

**验证三：404 页面的归属**

```bash
curl -I http://127.0.0.1:8082/nonexistent      # 直连
curl -I http://127.0.0.1:8083/app/nonexistent  # 代理
```

**两个响应都有 `Content-Language: en`**：

```
8082:  404, Content-Type: text/html;charset=utf-8, Content-Language: en
8083:  404, Content-Type: text/html;charset=utf-8, Content-Language: en
```

**`Content-Language: en` 是 Tomcat 错误页的特征** ——
nginx 自己的 404 页面不会有这个头。

**这证明了**：

1. 请求确实转发到了 Tomcat
2. 路径重写生效（`/app/nonexistent` → `/nonexistent`）
3. 404 是 Tomcat 生成的，不是 nginx

**验证四：Tomcat 访问日志**

```bash
tail -2 /var/log/tomcat/localhost_access_log.$(date +%Y-%m-%d).txt
```

```
127.0.0.1 - - [17:10:35] "GET / HTTP/1.0" 200 11044     ← 修复前
127.0.0.1 - - [17:19:08] "GET / HTTP/1.1" 200 11064     ← 修复后（HTTP/1.1）
```

### 6.5 限流验证

**给 `/app/` 加了限流**（复用 `00-limit-zones.conf` 定义的 zone）：

```nginx
limit_req  zone=general burst=20 nodelay;
limit_conn perip 10;
```

**实测 30 次连续请求**：

```
200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 429 429 429 429 429 429 429
└──────────────────── 23 个 200 ────────────────────┘└────── 7 个 429 ──────┘
```

**数字与配置精确匹配**：

```
rate=10r/s   → 平均每秒 10 个
burst=20     → 令牌桶容量 20
               初始 20 + 1 秒内补充的约 3-10 个 ≈ 23 个通过
               之后令牌耗尽 → 429
```

**关于 `nodelay`**：

| 配置 | 突发请求的行为 |
|---|---|
| `burst=20` | 超出的请求**排队等待**逐个放行（变慢但不拒绝） |
| **`burst=20 nodelay`** | 超出的请求**立即处理或拒绝**，不排队 |

实测结果是"23 个通过后直接 429"**，说明 `nodelay` 生效**（没有排队延迟）。

---

## 7. 遇到的一次配置事故

### 现象

修改配置后 `nginx -t` 报错：

```
nginx: [emerg] duplicate location "/app/" in /etc/nginx/conf.d/tomcat-proxy.conf:58
```

### 原因

**编辑时用粘贴代替了替换** —— 文件里出现了两个 `location /app/`：

```nginx
location /app/ {        ← 原有的
    ...
}

location /app/ {        ← 新加的（造成重复）
    ...
}
```

### `nginx -t` 拦住了这个错误

如果跳过检查直接 `reload`：

```
nginx 会拒绝加载新配置，继续使用旧配置
    ↓
配置文件的改动"看起来生效了"（因为文件确实改了）
    ↓
但实际运行的是旧配置 —— 磁盘与内存分叉
```

**这正是之前学到的原则的又一次验证**：

```
改配置 → nginx -t → reload → 检查 error.log
         └──┬──┘
      这一步拦住了一个错误
```

### 修复

因为改动只有几行，直接重写整个文件比在 vim 里删改更可靠。

**学到的方法**：

**编辑配置文件时，如果改动超过 3 行，重写整个文件往往比局部编辑更安全** ——
尤其在使用 `cat > file <<'EOF'` 这种能完整替换的方式时。

---

## 8. 最终状态

```
┌─────────────────────────────────────────────────────────┐
│  JDK: Dragonwell 11.0.31（alternatives 可切换）          │
├─────────────────────────────────────────────────────────┤
│  Tomcat 9.0.87                                          │
│    · 监听 8082（HTTP）+ 8005（shutdown）                 │
│    · User=tomcat（非 root）                              │
│    · Restart=always（drop-in 配置）                      │
│    · enabled（开机自启）                                 │
│    · 崩溃自愈已验证（kill -9 后 PID 变化）                │
├─────────────────────────────────────────────────────────┤
│  nginx 反向代理                                          │
│    · 8083/app/ → 127.0.0.1:8082                         │
│    · HTTP/1.1 + upstream keepalive                      │
│    · 限流：23 个 200 + 7 个 429（实测）                   │
│    · 传递 Host/X-Real-IP/X-Forwarded-For/Proto           │
├─────────────────────────────────────────────────────────┤
│  日常巡检扩展到 13 项（新增 Tomcat 代理链路检查）          │
└─────────────────────────────────────────────────────────┘
```

**端口全景**：

```
22     SSH
80     nginx（生产站点）
8080   nginx（测试站点）
8081   nginx（练习 lab01）
8082   Tomcat（HTTP）           ← 本次新增
8083   nginx（Tomcat 代理）      ← 本次新增
8090   nginx（练习 quest01）
9090   nginx（练习 lab02）
3306   MariaDB（仅本机）
6379   Redis（仅本机）
8005   Tomcat（shutdown，仅本机）
```

**共 11 个监听端口** —— 端口规划开始变得重要。

---

## 9. 结论

**软件包管理与已装版本可能冲突**

安装 Tomcat 时，依赖自动拉入了 `java-11-openjdk-headless`，
把 `alternatives` 的 `java` 软链接从 Dragonwell 切换走了。

**这类问题的特点**：

- 装包时不会提示"我将切换你的 java"
- 只在下次执行 `java -version` 时才发现
- 多版本共存环境下尤其容易发生

**排查方法**：`alternatives --display java` 看当前指向和优先级。

**"服务 active" 与 "功能可用" 是两件事（第三次遇到）**

Tomcat 的 8080 端口绑定失败，但主进程因为 8005 绑定成功而继续运行，
systemd 报告 `active (running)`。

**更麻烦的是 `curl` 返回 200** —— 因为访问到的是 nginx 的静态站点。

**判断"请求走到了谁"，要看 `Server` 响应头**：
`Server: nginx` 说明走到了 nginx；Tomcat 默认不发 Server 头。

**反向代理的配置细节决定行为**

三个必须理解的点：

| 配置 | 影响 |
|---|---|
| `proxy_pass` 末尾的斜杠 | 路径重写方式 |
| `proxy_http_version 1.1` + `Connection ""` | 长连接是否生效 |
| 四个 `proxy_set_header` | 后端能否看到真实客户端信息 |

**`nginx -t` 是配置修改的必要环节**

本次 `duplicate location` 的错误被它拦住。
如果直接 `reload`，文件改了但运行配置没变，会造成"磁盘与内存分叉"
（与之前遇到的 `limit_req_zone` 问题同类）。

**配置应该纳入版本控制**

`/etc/` 下的配置不在 Git 里，改坏了无法回滚、也无法追溯。
**做法**：在项目里维护一份副本（`conf/` 目录），每次修改后同步并提交。

---

## 10. 遗留

- Tomcat 的 manager 应用未配置（默认账号密码需要设置）
- 未部署真实的 JSP/Servlet 应用
- `X-Real-IP` 等头部的传递未做端到端验证（需要在 Tomcat 侧打印请求头）
- AJP 协议未实践
- 端口规划需要整理（已占用 11 个端口）

---

## 11. 协作说明

部署命令和配置内容由 AI 提供，执行和排查由我做。

两个地方走了弯路：

一是 Tomcat 装完后 `systemctl status` 显示正常，但 8080 端口实际没监听 ——
curl 拿到的是 nginx 的响应。要发现这个问题，得先想到
"服务状态"和"功能状态"是两件事。

二是验证代理是否生效。curl 返回 200 不足以说明问题，
因为返回的可能是 nginx 自己的响应。最后用 `Content-Language: en`
这个 Tomcat 错误页特有的响应头，确认 404 确实来自 Tomcat。

---

## 12. 后续计划

1. 部署一个真实的 JSP 应用，验证头部传递
2. 整理端口规划（11 个端口的用途归类）
3. 配置 HTTPS（备案通过后）
4. 继续系统练习 Linux 命令
