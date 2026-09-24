# HTTPS 配置说明

本机网站已配置 HTTPS，使用 Let's Encrypt 免费证书。

---

## 当前状态

| 项 | 值 |
|---|---|
| **证书签发机构** | Let's Encrypt |
| **证书域名** | myzhiwei.xyz、www.myzhiwei.xyz |
| **有效期** | 90 天（自动续期） |
| **协议版本** | TLSv1.2 + TLSv1.3 |
| **HTTP 跳转** | 301 → HTTPS |
| **HSTS** | max-age 1 年 |

---

## 端口分工

```
80    域名访问 → 301 跳转到 HTTPS
80    default_server（IP 直连 / 未知域名）→ 保持 HTTP
443   HTTPS 主服务
8080  IP 备用入口（HTTP）
```

**为什么 IP 直连不跳转 HTTPS**：

证书签发给域名，不含 IP。如果用 `https://<IP>` 访问，
浏览器会因「证书名称不匹配」报错。所以 IP 入口保持 HTTP。

---

## 配置结构

```
/etc/nginx/conf.d/
├── personal-site.conf              三个 server 块（80 跳转 / 80 默认 / 443 / 8080）
└── snippets/
    └── site-common.conf            共用规则（限流、gzip、缓存、安全头）
```

**抽取共用片段的理由**：

改造前，80 和 8080 两个 server 块各自重复了一遍 location 规则。
现在改为 `include snippets/site-common.conf`，改一处即作用于所有端口。

---

## 证书续期

**certbot-renew.timer**（systemd timer）：

```
检查频率:  每 12 小时（OnCalendar=*-*-* 00/12:00:00）
触发条件:  剩余有效期 < 30 天
续期后:    执行 deploy hook
```

**deploy hook**（`/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`）：

```bash
#!/bin/bash
systemctl reload nginx
logger -t certbot-hook "证书已续期，nginx 已重载"
```

**为什么需要 deploy hook**：

```
证书更新了 → nginx 仍在用内存里的旧证书
             （nginx 只在启动/reload 时读取证书文件）
    ↓
必须 reload 才能加载新证书
    ↓
否则证书明明续期成功，浏览器仍显示"证书过期"
```

---

## 手动操作

```bash
# 查看证书状态
certbot certificates

# 测试续期流程（不会真的续期）
certbot renew --dry-run

# 强制续期（证书快过期时才用）
certbot renew --force-renewal

# 查看 timer 下次触发时间
systemctl list-timers certbot-renew.timer
```

---

## 关于 OCSP Stapling

**本机未启用**。

**原因**：Let's Encrypt 自 2025 年起不再在证书中提供 OCSP Responder URL
（改用 CRL 机制）。`ssl_stapling on` 不会生效，且会产生 `nginx -t` 警告。

**处理**：移除相关配置，使「配置内容」与「实际行为」一致。

---

## 安全加固项

| 配置 | 作用 |
|---|---|
| `ssl_protocols TLSv1.2 TLSv1.3` | 禁用已不安全的 TLSv1.0/1.1 |
| `ssl_ciphers ECDHE-...GCM...` | 只用前向保密的强套件 |
| `ssl_session_tickets off` | 关闭会话票据（避免密钥复用风险） |
| `Strict-Transport-Security` | 告诉浏览器一年内只用 HTTPS |
| `ssl_prefer_server_ciphers off` | 让客户端选择（现代实践） |

---

## 排查方法

**检查 HTTPS 是否正常**：

```bash
# 1. HTTP 跳转
curl -I http://myzhiwei.xyz/
# 期望: 301 + Location: https://myzhiwei.xyz/

# 2. HTTPS 访问与证书验证
curl -s -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' https://myzhiwei.xyz/
# 期望: 200 0   （0 = 证书验证通过）

# 3. 证书详情
echo | openssl s_client -connect myzhiwei.xyz:443 -servername myzhiwei.xyz 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates

# 4. 支持的 TLS 版本
for p in tls1_2 tls1_3; do
  echo | openssl s_client -$p -connect myzhiwei.xyz:443 2>/dev/null | grep Protocol
done
```

**如果证书过期了**：

```bash
# 看续期是否有报错
journalctl -u certbot-renew.service --since "7 days ago"

# 手动续期
certbot renew --force-renewal
systemctl reload nginx
```

---

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-24 | 首次部署：certbot 申请证书、nginx 配置 443、HTTP 跳转、HSTS |
| 2026-09-24 | 清理 OCSP 配置（Let's Encrypt 已移除 OCSP URL） |
