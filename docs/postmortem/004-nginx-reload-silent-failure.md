# 故障复盘 #004：nginx reload 静默失败——两个"成功"信号都是假的

| 项目 | 内容 |
|---|---|
| 日期 | 2026-09-13 |
| 环境 | Alibaba Cloud Linux 3 / nginx 1.24.0 |
| 组件 | `limit_req_zone` 的 key 变更 + `systemctl reload nginx` |
| 影响 | **配置静默不生效**，磁盘与内存配置分叉；诊断过程中站点一度对访问者返回 429 |
| 发现方式 | 隔离实验失败后查 error.log，发现 `[emerg]` |
| 严重级别 | **高**（两次错误判断 + 两个虚假成功信号 + 配置分叉） |
| 处理耗时 | 约 40 分钟 |

---

## 1. 起因

给 nginx 配好限流后，我注意到一个问题：**本地监控脚本被自己的限流挡住了**。

第一次发现是在发布站点后做验证时，连续请求 7 个页面，结果：

```
/  →  HTTP 200    8100 → 3478 字节
...
首页标题: <title>429 Too Many Requests</title>
```

限流配置 `10r/s + burst=20`，而我的脚本瞬时发了 7 个请求，触发了限流。

**这不是小问题**，因为：

1. `deploy.sh` 的健康检查就是访问 `http://127.0.0.1/`——如果它被 429，
   部署脚本会**误判服务异常，触发自动回滚，把好版本回滚掉**。
2. 未来若加 HTTP 可用性监控，同样会产生**假告警**。

所以我想让本地回环豁免限流。

---

## 2. 两次失败的修复尝试

### 尝试 v1：把本地映射成固定字符串

思路：用 `map` 把 127.0.0.1 的限流 key 从真实 IP 换成一个固定值。

```nginx
map $remote_addr $limit_key {
    default      $binary_remote_addr;
    127.0.0.1    "localhost";
}
limit_req_zone $limit_key zone=general:10m rate=10r/s;
```

`nginx -t` 通过。但实测：

```
连发 50 次 → 24 个 200，26 个 429
```

**失败了。** 我的分析是：key 非空就仍然计入限流，`"localhost"`
只是把所有本地请求归入同一个桶、共享 10r/s 配额，等于**没有豁免**。

### 尝试 v2：把本地映射成空字符串

我记得 nginx 文档提过"空 key 不计入限流"，于是改成：

```nginx
map $remote_addr $limit_key {
    default      $binary_remote_addr;
    127.0.0.1    "";
}
```

`nginx -t` 又通过了。但实测**更差**：

```
连发 50 次  → 15 个 200，35 个 429
连发 200 次 → 13 个 200，187 个 429
```

我当时的推测是"nginx 把 `""` 解析成了两个字面引号"。
**这个推测也是错的**，而且我差点又基于错误推测去改。

---

## 3. 用隔离实验定位——然后发现了真正的问题

我不再猜，改做隔离实验：在**另一个端口 8099** 上起独立测试 server，
不碰生产的 80/8080。实验第一步是"确认测试 server 真的活着"，
结果：

```
ss 检查:  (未监听！)
GET / → HTTP 000
❌ 测试 server 未正常工作
```

测试 server 起不来。看 error.log，**真相出现了**：

```
2026/09/13 15:15:28 [notice] 16684#0: reconfiguring
2026/09/13 15:15:28 [emerg]  16684#0: limit_req "general" uses the "$limit_key" key
                                        while previously it used the "$binary_remote_addr" key
```

**根因：`limit_req_zone` 的 key 在 nginx 运行时不允许变更。**

从我把 key 从 `$binary_remote_addr` 改成 `$limit_key` 那一刻起，
**每一次 `systemctl reload nginx` 都在 reconfiguring 阶段失败了**，
nginx 一直沿用**内存中的旧配置**。

---

## 4. 为什么这么难发现：两个"成功"信号都是假的

这是本次事故最核心的教训。

### 假信号 1：`nginx -t` 说"语法正确"

```
$ nginx -t
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

`-t` 检查的是**语法**，而 key 不可变是**运行时语义约束**——`-t` 检测不到。
语法对 ≠ 能加载。

### 假信号 2：`systemctl reload` 返回退出码 0

```
$ systemctl reload nginx
$ echo $?
0
```

而同一时刻 systemd 的 journal 里写着：

```
systemd[1]: Reloading The nginx HTTP and reverse proxy server.
systemd[1]: Reloaded The nginx HTTP and reverse proxy server.     ← "Reloaded"！
```

**systemd 报告 reload 成功，因为 SIGHUP 确实发出去了、nginx 也确实回了个响应。
但 nginx 内部 reconfiguring 失败后，选择继续用旧配置运行。**

### 结果：配置分叉

| 视角 | 看到的配置 |
|---|---|
| `nginx -T`（读磁盘） | `limit_req_zone $limit_key ...` |
| 实际行为（内存中） | 仍按 `$binary_remote_addr` 限流 |

**两者不一致，而我完全无感。** 我基于"改动已生效"这个错误前提，
做了两轮修复尝试，全部无效——**因为我改的配置从来没被加载过**。

---

## 5. 附带影响：我自己把站点打成了 429

排查过程中我的诊断脚本连发 50 次请求，**把限流额度打满**，
导致站点对外返回：

```
:80    HTTP 429
:8080  HTTP 429
首页标题: <title>429 Too Many Requests</title>
```

对访问者来说这就是故障。虽然它是限流的正常行为（停止请求后
令牌桶会恢复），但它说明：**诊断动作本身也会影响生产。**

教训：诊断脚本必须控制速率，且要意识到自己的行为会改变系统状态。

---

## 6. 修复

**决定：放弃在 nginx 层做本地豁免。**

理由：

1. 修改 key 需要 `restart`（有秒级中断），为了一个优化不值得；
2. **更简单的方案本来就有**——让监控脚本控制请求速率。
   我们的验证脚本早已加了 `0.25s` 间隔（约 4 请求/秒），
   实测完全不触发限流。

修复动作：把 key 改回 `$binary_remote_addr`。

```nginx
limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
```

因为 key 与内存中运行的配置**一致**了，reload 恢复正常：

```
✅ 无 emerg，reload 成功
站点可用性（8 次探测，间隔 0.5s）: HTTP 200 × 8
磁盘 (nginx -T): limit_req_zone $binary_remote_addr zone=general:10m
内存行为:        200 200 200 200 200 200   ← 无 429
```

**磁盘与内存配置重新一致。**

---

## 7. 沉淀的经验

### 7.1 `nginx -t` 通过 ≠ 配置能生效

`-t` 只查语法。运行时约束（如 zone key 不可变）它检测不到。

### 7.2 `systemctl reload` 返回 0 ≠ 配置已加载

这是最危险的。正确做法是**改完配置后必须做的三件事**：

```bash
# 1. 语法检查
nginx -t || exit 1

# 2. 加载
systemctl reload nginx

# 3. 【关键】确认真的加载成功了
#    方法 a：查 error.log 有没有 emerg
if tail -5 /var/log/nginx/error.log | grep -q emerg; then
    echo "reload 实际失败了！"
fi
#    方法 b：验证实际行为（而不是读配置）
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/
```

**"读配置"和"验行为"是两件事。** `nginx -T` 读的是磁盘文件，
只有实际请求才能反映内存中的真实配置。

### 7.3 修改 `limit_req_zone` 的 key 必须用 restart

- `reload`（SIGHUP）：平滑重载，但 **zone 的 key 不允许变**
- `restart`：完全重启，可以变更 key，但有秒级中断

所以：**能不改 key 就别改**；非要改，要选在低峰期用 restart。

### 7.4 磁盘配置与内存配置会分叉——要主动检测

```bash
# 一致性自检：如果 -T 的输出与实际行为不符，说明 reload 失败了
nginx -T | grep limit_req_zone                          # 磁盘
for i in 1 2 3; do curl -s -o /dev/null -w '%{http_code} ' http://127.0.0.1/; done  # 行为
```

本项目已把"检查 emerg"加进恢复流程（见 `42-emergency-recover.sh`）。

### 7.5 防御措施可能伤到自己人

限流防的是外部扫描器，却拦住了自己的健康检查。
**这类问题在设计任何安全策略时都要考虑**：限流、防火墙、WAF、
速率限制——都要给监控/运维通道留出豁免或调整空间。

本次的最终方案很朴素：**让监控脚本自己控制速率**。
比起在 nginx 里做复杂的 key 映射，这个方案更简单、无副作用、
也不需要改 key。**简单的方案往往是对的。**

### 7.6 排查方法论：隔离实验 + 先验证实验装置

两次基于推测的修复都失败了，直到我改用隔离实验才找到根因。

而且实验的第一步是**确认测试 server 真的活着**——正是这一步
（"未监听！HTTP 000"）把隐藏的 `[emerg]` 暴露出来。

> **如果实验装置本身是坏的，实验结论毫无意义。**
> 先验证装置，再做实验。

### 7.7 错误结论的成本

本次我连续得出两个错误结论：

1. "key 非空所以不豁免"（部分正确，但不是失败的真正原因）
2. "nginx 把 `""` 当字面引号"（完全错误，且会导致错误修复方向）

**根本原因都是：我在验证改动"是否生效"之前，就假设它生效了。**
正确顺序应该是：**先确认改动被加载，再评估改动效果。**

---

## 附：可复用的 nginx 配置变更检查清单

```bash
#!/bin/bash
# nginx 配置变更后的安全落地流程
set -uo pipefail

CONF="$1"
BACKUP="/root/ops-lab/backups/nginx-$(date +%F-%H%M%S).tar.gz"

# 0. 备份
tar -czf "$BACKUP" /etc/nginx 2>/dev/null

# 1. 语法检查
if ! nginx -t; then
    echo "❌ 语法错误，中止"; exit 1
fi

# 2. 记录 reload 前的 error.log 大小，用于后续只查新增部分
ERRLOG=/var/log/nginx/error.log
SIZE_BEFORE=$(stat -c %s "$ERRLOG" 2>/dev/null || echo 0)

# 3. 加载
systemctl reload nginx
sleep 2

# 4. 【关键】检查是否有新的 emerg
if tail -c +$((SIZE_BEFORE + 1)) "$ERRLOG" 2>/dev/null | grep -q emerg; then
    echo "❌ reload 实际失败（有 emerg），配置未生效！"
    tail -c +$((SIZE_BEFORE + 1)) "$ERRLOG" | grep emerg
    echo "   考虑回滚: tar -xzf $BACKUP -C /"
    exit 1
fi

# 5. 验证实际行为（不是读配置）
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/)
if [ "$CODE" != "200" ]; then
    echo "❌ 健康检查失败: HTTP $CODE"
    exit 1
fi

echo "✅ 配置已生效并验证通过"
```

**核心思想：每一次"成功"都要用独立证据验证，不能相信上游的返回码。**
