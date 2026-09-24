# 手记 009：从 HTTP 到 HTTPS

**日期**: 2026-09-24
**用时**: 约 90 分钟
**难度**: ⭐⭐⭐☆☆

---

## 1. 背景

ICP 备案通过，域名解析已生效，网站可以通过 `http://myzhiwei.xyz` 访问。

本次要做的：申请证书、配置 HTTPS、让 HTTP 自动跳转。

**前置条件**：

| 项 | 状态 |
|---|---|
| 域名解析 | ✅ 已指向服务器 |
| 80 端口 | ✅ 安全组已放行，公网可达 |
| 443 端口 | ❌ 待放行 |
| 系统 | Alibaba Cloud Linux 3（RHEL 8 兼容） |

---

## 2. 证书方案的选择

### 2.1 为什么用 Let's Encrypt

| 方案 | 成本 | 有效期 | 自动续期 |
|---|---|---|---|
| **Let's Encrypt** | 免费 | 90 天 | ✅ 支持 |
| 商业 DV 证书 | 几百元/年 | 1 年 | 手动 |
| 自签名 | 免费 | 自定义 | — |

**自签名不能用**：浏览器会显示安全警告，需要用户手动信任。对公开站点没有意义。

**Let's Encrypt 的取舍**：证书只有 90 天 —— 听起来短，但配合自动续期反而更安全（密钥泄露窗口更小）。

### 2.2 安装

```bash
yum install -y certbot python3-certbot-nginx
```

**两个包的分工**：

| 包 | 作用 |
|---|---|
| `certbot` | 核心程序 |
| `python3-certbot-nginx` | nginx 插件，用于自动完成 HTTP-01 验证 |

**版本**：certbot 1.22.0（EPEL 源）

---

## 3. 申请证书

### 3.1 先用 --dry-run 验证

```bash
certbot certonly --nginx \
  -d myzhiwei.xyz -d www.myzhiwei.xyz \
  --dry-run
```

**`--dry-run` 做什么**：

```
1. 连接 Let's Encrypt 的测试环境
2. 请求一个验证 token
3. 临时改 nginx 配置，添加验证用的 location
4. reload nginx
5. 让 Let's Encrypt 从外部访问验证 URL
6. 验证成功后还原配置
7. 报告结果（不签发真实证书）
```

**结果**：

```
Account registered.
Simulating a certificate request for myzhiwei.xyz and www.myzhiwei.xyz
The dry run was successful.
```

**这一句话证明了几件事**：

| 验证项 | 说明 |
|---|---|
| 域名解析正确 | Let's Encrypt 能找到你的服务器 |
| **80 端口公网可达** | 从外网访问到了验证 URL |
| nginx 配置正确 | 能处理 ACME 挑战 |
| 安全组规则正确 | 没有被防火墙拦截 |

### 3.2 正式申请

```bash
certbot certonly --nginx \
  -d myzhiwei.xyz -d www.myzhiwei.xyz \
  --agree-tos \
  --email <邮箱> \
  --non-interactive
```

**为什么用 `certonly` 而不是直接 `certbot --nginx`**：

| 命令 | 行为 |
|---|---|
| `certbot --nginx` | **会自动改写 nginx 配置**（加 443 server 块、改跳转） |
| **`certbot certonly --nginx`** | **只签发证书**，配置自己写 |

本机的 nginx 配置是自己维护的（多 server 块 + 限流 + 反向代理），
让 certbot 自动改写可能和现有结构冲突。所以选择只拿证书。

**结果**：

```
Successfully received certificate.
Certificate is saved at: /etc/letsencrypt/live/myzhiwei.xyz/fullchain.pem
Key is saved at:          /etc/letsencrypt/live/myzhiwei.xyz/privkey.pem
```

**证书文件**：

| 文件 | 用途 |
|---|---|
| `fullchain.pem` | 证书链（服务器证书 + 中间证书）← nginx 用 |
| `privkey.pem` | 私钥 ← nginx 用 |
| `cert.pem` | 仅服务器证书（一般不用） |
| `chain.pem` | 仅中间证书 |

**为什么用 fullchain 而不是 cert**：

```
浏览器需要验证证书链：
  Let's Encrypt 根证书（浏览器内置）
      ↓ 签发
  中间证书（不在浏览器里）
      ↓ 签发
  你的域名证书

如果只发 cert.pem（域名证书），浏览器找不到中间证书
→ 报 "证书链不完整"

fullchain.pem = 域名证书 + 中间证书，一次发全
```

---

## 4. nginx 配置改造

### 4.1 改造前的结构问题

原来的配置有两个 server 块（80 和 8080），**location 规则重复写了两遍**：

```nginx
server {
    listen 80 default_server;
    server_name _ myzhiwei.xyz www.myzhiwei.xyz;   ← 通配 + 域名混在一起
    # ... 20 行 location 规则 ...
}

server {
    listen 8080;
    server_name _;
    # ... 同样的 20 行 location 规则 ...
}
```

**两个问题**：

**问题一：`server_name` 混了 `_` 和具体域名**

```
server_name _ myzhiwei.xyz www.myzhiwei.xyz;
            └┬┘ └────────┬────────────────┘
        通配（兜底）   具体域名
```

**nginx 的 server_name 匹配顺序**：精确名称 → 通配开头 → 通配结尾 → 正则 → default_server。

`_` 在 nginx 里**不是通配符**，它只是一个永远不匹配的普通字符串。
真正让它生效的是那个 server 块上的 `default_server` 标记。

**这就导致无法区分"域名访问"和"IP 访问"** —— 两种请求都落进同一个 server 块，
没法只对域名做 HTTPS 跳转。

**问题二：location 规则重复**

80 和 8080 两个块各自写了一遍限流、gzip、缓存、安全头。

配置文件的注释里当时就写了一句：

> `下一步优化时再抽成 include（这也是一个很好的练习点）`

这次正好一起做掉。

### 4.2 改造后的结构

```
/etc/nginx/conf.d/
├── personal-site.conf
│   ├── server { listen 80; server_name myzhiwei.xyz www.myzhiwei.xyz; → 301 }
│   ├── server { listen 80 default_server; server_name _; → HTTP 服务 }
│   ├── server { listen 443 ssl http2; → HTTPS 主服务 }
│   └── server { listen 8080; server_name _; → HTTP 备用 }
└── snippets/
    └── site-common.conf        ← 共用规则（被后三个 server 块 include）
```

**关键设计**：

| 设计 | 理由 |
|---|---|
| **域名和 default 分成两个 server 块** | 才能只对域名做跳转 |
| **抽成 snippets** | 限流/缓存/gzip/安全头只维护一份 |
| **IP 入口保持 HTTP** | 证书不含 IP，走 HTTPS 会报证书名不匹配 |

**关于最后一条**：

```
证书签发给 → myzhiwei.xyz
用户访问   → https://<IP>
浏览器检查 → "证书是给 myzhiwei.xyz 的，但你访问的是 <IP>"
          → 报错 "证书名称不匹配"
```

所以 `default_server` 那个块保持 HTTP，不跳转。

### 4.3 跳转用 $host 而不是 $server_name

```nginx
return 301 https://$host$request_uri;
```

**两个变量的区别**：

| 变量 | 值 | 问题 |
|---|---|---|
| `$server_name` | 写死的第一个名称 | 访问 www 也会跳到裸域名 |
| **`$host`** | **客户端请求的 Host** | **保留 www** |

用 `$host` 后：

```
http://myzhiwei.xyz     → https://myzhiwei.xyz
http://www.myzhiwei.xyz → https://www.myzhiwei.xyz     ← www 保留
```

### 4.4 TLS 参数

```nginx
ssl_protocols       TLSv1.2 TLSv1.3;
ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:...;
ssl_prefer_server_ciphers off;
ssl_session_tickets off;
```

**逐项说明**：

| 配置 | 作用 |
|---|---|
| **只启用 TLSv1.2/1.3** | TLSv1.0/1.1 已被主流浏览器标记为不安全 |
| **只用 ECDHE + GCM** | ECDHE 提供前向保密；GCM 是认证加密模式 |
| **`ssl_prefer_server_ciphers off`** | 让客户端选（现代实践 —— 客户端更了解自己的硬件） |
| **`ssl_session_tickets off`** | 会话票据用静态密钥加密会话，关闭可避免该密钥泄露导致的历史会话解密 |

**前向保密（Forward Secrecy）的意义**：

```
没有前向保密：
  攻击者今天录下加密流量 → 将来拿到服务器私钥 → 解密出历史通信内容

有前向保密（ECDHE）：
  每次会话用临时密钥协商 → 即使服务器私钥泄露
  → 也无法解密之前录下的流量
```

### 4.5 HSTS

```nginx
add_header Strict-Transport-Security "max-age=31536000" always;
```

**作用**：告诉浏览器"这个域名一年内只用 HTTPS 访问"。

```
第一次访问 https://myzhiwei.xyz
    ↓
浏览器记住 HSTS 策略
    ↓
以后即使输入 http://myzhiwei.xyz
    ↓
浏览器【在本地】就把地址改写成 https://，根本不发 HTTP 请求
```

**好处**：防止 SSL 剥离攻击（中间人把 HTTPS 链接改成 HTTP）。

**⚠️ 注意事项**：

| 项 | 说明 |
|---|---|
| **max-age 设长要谨慎** | 一旦下发，浏览器会记住一年，期间 HTTPS 出问题会无法访问 |
| **建议先用短 max-age 测试** | 比如 300 秒，确认没问题再调长 |
| **本机直接用了 1 年** | 因为证书和续期都已验证通过 |

**如果将来 HTTPS 出问题怎么办**：只能在浏览器里手动清除 HSTS 记录，
或者换域名。**所以启用 HSTS 前要确保 HTTPS 稳定。**

---

## 5. 自动续期

### 5.1 续期链路

```
certbot-renew.timer（每 12 小时）
    ↓
检查证书剩余有效期
    ↓ 剩余 < 30 天
certbot renew 申请新证书
    ↓ 成功
执行 deploy hook
    ↓
systemctl reload nginx
    ↓
nginx 加载新证书
```

**timer 配置**：

```ini
[Timer]
OnCalendar=*-*-* 00/12:00:00      # 每天 00:00 和 12:00
RandomizedDelaySec=12hours        # 随机延迟（避免全球客户端同时请求 Let's Encrypt）
Persistent=true                   # 关机期间错过的任务，开机后补做
```

**为什么用 `RandomizedDelaySec`**：Let's Encrypt 的服务端要处理全球数百万证书的续期请求。
如果所有客户端都在整点请求，会造成流量尖峰。随机延迟把这个压力摊平。

### 5.2 deploy hook

**为什么必须要有这个 hook**：

```
证书文件更新了
    ↓
但 nginx 只在【启动】或【reload】时读取证书文件
    ↓
不 reload → nginx 仍用内存里的旧证书
    ↓
结果：证书明明续期成功，浏览器仍显示"证书已过期"
```

**hook 内容**：

```bash
#!/bin/bash
systemctl reload nginx
logger -t certbot-hook "证书已续期，nginx 已重载"
```

**`logger` 的作用**：把记录写进 syslog，方便事后排查"续期到底有没有执行"。

**验证方式**：

```bash
# 直接执行 hook
bash /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

# 检查 syslog
journalctl -t certbot-hook -n 5
```

**关于 `--dry-run` 是否执行 hook**：

`certbot renew --dry-run` **不会**执行 deploy hook —— 所以输出里看不到
`Running deploy-hook command`。这是正常的，验证 hook 要单独直接执行。

---

## 6. 验证

### 6.1 功能验证

```bash
# HTTP 跳转
curl -I http://myzhiwei.xyz/
```

```
HTTP/1.1 301 Moved Permanently
Location: https://myzhiwei.xyz/
```

```bash
# HTTPS 访问与证书验证
curl -s -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' https://myzhiwei.xyz/
```

```
200 0
```

**`ssl_verify_result=0` 表示证书验证通过**（0 是 OpenSSL 的成功码，
非 0 是各种验证失败的原因码）。

### 6.2 证书详情

```bash
echo | openssl s_client -connect myzhiwei.xyz:443 -servername myzhiwei.xyz 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

```
subject=CN = myzhiwei.xyz
issuer=C = US, O = Let's Encrypt, CN = YR2
notBefore=Sep 24 07:42:51 2026 GMT
notAfter=Dec 23 07:42:50 2026 GMT
```

**注意 `-servername` 参数**：这叫 SNI（Server Name Indication）。
一台服务器可能承载多个域名，SNI 告诉服务器"我要访问哪个域名"，
服务器才能返回对应的证书。

### 6.3 TLS 版本

```bash
for p in tls1_2 tls1_3; do
  echo | openssl s_client -$p -connect myzhiwei.xyz:443 2>/dev/null | grep Protocol
done
```

```
TLSv1.2     Cipher: ECDHE-RSA-AES256-GCM-SHA384
TLSv1.3     Cipher: TLS_AES_256_GCM_SHA384
```

**两个版本都支持，加密套件都是强套件。**

### 6.4 验证结果汇总

| 检查项 | 结果 |
|---|---|
| HTTP → HTTPS | 301 ✅ |
| HTTPS 访问 | 200 ✅ |
| 证书验证 | 0（通过）✅ |
| TLS 版本 | 1.2 + 1.3 ✅ |
| HSTS | max-age=31536000 ✅ |
| IP 直连 | 保持 HTTP 200（不跳转）✅ |
| Tomcat 代理链路 | 未受影响 ✅ |

---

## 7. 遇到的问题

### 问题一：OCSP Stapling 警告

```bash
nginx -t
```

```
nginx: [warn] "ssl_stapling" ignored, no OCSP responder URL in the certificate
```

**OCSP Stapling 是什么**：一种优化机制 —— 服务器提前向 CA 查询证书吊销状态，
把结果（签名的响应）随 TLS 握手一起发给客户端，
省去客户端自己去查 OCSP 服务器的往返。

**为什么警告**：

```
ssl_stapling on;  → nginx 需要从证书里读 OCSP Responder URL
     ↓
但证书里没有这个字段
     ↓
无法查询 → 配置被忽略 → 警告
```

**根因**：**Let's Encrypt 自 2025 年起不再在证书中提供 OCSP URL**，
改用 CRL（证书吊销列表）机制。

**这是 CA 侧的策略变化，不是配置错误。**

**处理**：移除那三行配置。

```nginx
# 修改前
ssl_stapling on;
ssl_stapling_verify on;
ssl_trusted_certificate /etc/letsencrypt/live/myzhiwei.xyz/chain.pem;
resolver 223.5.5.5 119.29.29.29 valid=300s;
resolver_timeout 5s;

# 修改后（只留说明注释）
# ---- OCSP Stapling：未启用 ----
# Let's Encrypt 自 2025 年起不再在证书中提供 OCSP Responder URL
# （改用 CRL 机制），因此 ssl_stapling 不生效且会产生 nginx -t 警告。
# 移除相关配置，使「配置内容」与「实际行为」一致。
```

**判断依据**：

> 配置应该反映实际生效的行为。
> 保留一条不生效的指令，会让人误以为 OCSP 在工作。

### 问题二：编辑长配置时终端断连

**现象**：粘贴 60+ 行的 `cat > file <<'EOF'` 时，SSH 会话中断，
命令只执行了一部分。

**后果**：不确定配置文件是否被写坏。

**排查**：

```bash
nginx -t                                    # 先确认配置没坏
head -20 /etc/nginx/conf.d/personal-site.conf  # 看文件内容
```

**结果**：`nginx -t` 通过，文件是旧内容（说明 `cat` 没执行）。

**改进方式**：**把长配置写成脚本文件，执行脚本而不是粘贴**。

**这个教训的一般形式**：

| 场景 | 风险 |
|---|---|
| 粘贴长命令 | 断连导致部分执行 |
| 分批执行 + 每步验证 | ✅ 安全 |

**后来又遇到一次类似情况**：一个脚本因为 `grep -c` 返回值处理不当（空结果导致
`[ "$N" -eq 0 ]` 报错）而中断。**同一类问题（输出解析不严谨）在项目里出现了多次。**

---

## 8. 遗留

- HSTS 的 `max-age` 未做渐进式验证（直接设了 1 年）
- 未测试证书续期的真实流程（要等 59 天后自动触发）
- 未配置 `ssl_ocsp` 的替代方案（CRL 检查）
- 8080 端口的 IP 备用入口未决定是否关闭

---

## 9. 后续计划

1. 等公安联网备案审核结果
2. 检查日志保留期是否满足《网络安全责任告知书》的要求
3. 整理端口规划（443 已加入）
4. 继续系统练习 Linux 命令

---

## 10. 协作说明

本次的命令与配置内容由 AI 提供，执行和判断由我做。

两处需要自己判断的地方：

一是 `certonly` 与默认模式的选择。certbot 的 nginx 插件默认会改写配置，
但本机配置结构是自己维护的（多 server 块 + 限流 + 反向代理），
让工具自动改写的风险大于收益 —— 所以选择只拿证书、自己配 nginx。

二是那个 OCSP 警告的处理。看到警告时的第一反应是"配置写错了"，
但查证后发现是 CA 侧策略变化（Let's Encrypt 移除 OCSP URL）。
**判断"这是配置问题还是外部变化"需要先理解 OCSP 的机制。**
