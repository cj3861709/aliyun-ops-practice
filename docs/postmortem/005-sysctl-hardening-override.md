# 故障复盘 #005：安全加固被发行版预设静默覆盖——两次无效修复

| 项目 | 内容 |
|---|---|
| 日期 | 2026-09-13 |
| 环境 | Alibaba Cloud Linux 3.2104 / kernel 5.10 |
| 组件 | `sysctl` 内核参数加固 + 死人开关保护 |
| 影响 | 1 项加固未生效（静默）；2 次修复尝试无效；无服务中断 |
| 发现方式 | 逐项验证生效值（而非只看命令输出） |
| 严重级别 | 中（加固静默失效，但无实际损失） |
| 处理耗时 | 约 25 分钟 |

---

## 1. 背景

安全审计发现 5 项内核参数未按安全基线配置：

```
⚠️ kernel.dmesg_restrict = 0              期望 1
⚠️ net.ipv4.conf.all.log_martians = 0     期望 1
⚠️ net.ipv4.conf.all.send_redirects = 1   期望 0
⚠️ net.ipv4.conf.all.rp_filter = 0        期望 1
⚠️ journald 未设 SystemMaxUse             期望有限制
```

我决定加固，但**其中 `rp_filter` 涉及入站包过滤行为**，理论上存在
"配错导致 SSH 失联"的风险。而这台机器只有一个带外通道（控制台 Workbench），
失联代价很高。

于是我用了**死人开关**（dead-man switch）保护。

---

## 2. 死人开关：远程做危险变更的标准做法

```
1. 先把"恢复脚本"用 at 排程，5 分钟后自动执行
2. 再应用新配置
3. 从外部验证 SSH 是否仍可连接
4. 通了 → 撤销 at 作业（拆掉炸弹）
   不通 → 什么都不做，5 分钟后自动恢复
```

恢复脚本会：还原参数 → 移出加固配置 → 记录日志。

**这个机制工作得很好**：我应用配置后立刻发起新 SSH 连接验证，
通了，然后在 4 分钟内拆掉了 at 作业。

```
当前 at 队列:  1  Sun Sep 13 15:26:00 2026 a root
已删除作业 1
删除后队列: ✅ 空，死人开关已拆除
确认 deadman 未被执行: ✅ 无日志，未触发
```

**核心原则：每一个不可逆动作之前，先放一个自动回滚。**
没有这个机制，我根本不敢在只有一个带外通道的机器上碰网络参数。

---

## 3. 问题：rp_filter 加固静默失效

应用配置后逐项检查生效值：

```
kernel.dmesg_restrict                = 1  ✅ 生效
net.ipv4.conf.all.log_martians       = 1  ✅ 生效
net.ipv4.conf.all.send_redirects     = 0  ✅ 生效
net.ipv4.conf.all.rp_filter          = 0  ❌ 我要设 2，读回 0
```

**配置写了，命令没报错，但值没变。**

### 第一次修复尝试：改文件编号（无效）

我的分析是：sysctl 按文件名字典序加载，数字大的后加载、会覆盖。
实际加载顺序里有：

```
50-aliyun.conf            → rp_filter = 0
60-ops-lab-hardening.conf → rp_filter = 2  ← 我的
99-sysctl.conf            → rp_filter = 0  ← 覆盖回去了
```

于是我把文件改名成 `99-zz-ops-lab-hardening.conf`，指望它排在 `99-sysctl.conf` 之后。

**仍然无效。**

### 真相：`/etc/sysctl.conf` 永远最后加载

带行号的完整加载日志揭示了顺序：

```
 1: Applying /usr/lib/sysctl.d/10-default-yama-scope.conf
 2: Applying /etc/sysctl.d/50-aliyun.conf
 3:   net.ipv4.conf.all.rp_filter = 0
...
32:   net.ipv4.conf.all.rp_filter = 1     ← /usr/lib/sysctl.d/50-default.conf
...
42: Applying /etc/sysctl.d/99-sysctl.conf
46:   net.ipv4.conf.all.rp_filter = 0
...
55: Applying /etc/sysctl.d/99-zz-ops-lab-hardening.conf
59:   net.ipv4.conf.all.rp_filter = 2     ← 我的，确实排到了 99-sysctl.conf 之后
...
63: Applying /etc/sysctl.conf              ← ★ 在这里，永远最后
67:   net.ipv4.conf.all.rp_filter = 0     ← 最终值被设回 0
```

**`/etc/sysctl.conf` 不受 `sysctl.d` 编号体系约束，它在所有 `sysctl.d` 文件之后单独加载。**

`/etc/sysctl.d/99-sysctl.conf` 只是个**指向 `/etc/sysctl.conf` 的副本/链接**，
真正的"最后一张牌"是 `/etc/sysctl.conf` 本身。

> 所以调整 `sysctl.d` 里的文件名编号**永远无法**覆盖 `/etc/sysctl.conf` 的设置。
> 我的两次修复尝试（`60-` → `99-zz-`）**都是建立在这个错误认知上的**。

---

## 4. 第二个隐藏陷阱：接口级 max() 语义

即使 `conf/all` 设成功，也不一定生效。检查发现：

```
net.ipv4.conf.all.rp_filter      = 0
net.ipv4.conf.default.rp_filter  = 0
net.ipv4.conf.eth0.rp_filter     = 0
net.ipv4.conf.lo.rp_filter       = 0
net.ipv4.conf.docker0.rp_filter  = 0
```

内核实际使用的是 **`max(conf/all, conf/<interface>)`**：

- `conf/all` 是总开关，但**它取所有接口值的最大值**
- 必须同时设置 `conf/all` 和**具体接口**（或 `conf/default` 以影响后创建的接口）

**只设 `conf/all = 2` 而 `conf/eth0` 仍是 0 时，内核看到的是 `max(2, 0) = 2`，
表面上是生效的。但如果反过来只设接口不设 all，就会被 all 压住。**
这个 `max()` 语义是 rp_filter 最容易配错的地方。

---

## 5. 决策：不强行修改，记录为"经评估接受的偏差"

面对 `/etc/sysctl.conf` 的覆盖，有两个选择：

| 方案 | 优点 | 缺点 |
|---|---|---|
| A. 直接编辑 `/etc/sysctl.conf` | 能生效 | 修改发行版文件，yum 更新可能被覆盖；阿里云预设此值可能有其考虑 |
| B. 接受现状，评估后记录偏差 | 不动发行版配置 | 该项加固缺失 |

**我选择 B**，理由是：

1. `rp_filter=0` 的风险是 **IP 源地址欺骗**
2. 本机是**单网卡 Web 服务器**，不承担路由/转发角色
   （虽然 Docker 开了 `ip_forward`，但容器流量走独立 bridge，
   不经过 `eth0` 的 rp_filter 判定）
3. 入口流量已被**阿里云安全组**过滤（仅 22 端口放行）
4. 攻击者需要先能发包到本机，才谈得上利用源地址欺骗

**这是重要的运维判断：安全加固不等于"把所有 CIS 基准项都改成推荐值"。**

盲目照抄基准常常导致服务故障。典型例子：

- 把 `net.ipv4.ip_forward` 改成 0 → **Docker 网络立即瘫痪**
  （本机 `ip_forward=1` 是 Docker 的正常需求，我明确标注了**不改**）
- 把 `rp_filter` 强行设 1，在某些不对称路由场景下会**丢包**
  （这也是我最初选 2 而非 CIS 推荐的 1 的原因）

正确做法：**理解每一项的作用 → 评估本机场景的实际风险 → 做出有依据的决策**，
并对"不修改"这个决定本身负责。

---

## 6. 最终加固结果

| 项目 | 原值 | 新值 | 状态 |
|---|---|---|---|
| `kernel.dmesg_restrict` | 0 | **1** | ✅ 生效 |
| `net.ipv4.conf.all.log_martians` | 0 | **1** | ✅ 生效 |
| `net.ipv4.conf.all.send_redirects` | 1 | **0** | ✅ 生效 |
| `net.ipv4.conf.default.send_redirects` | 1 | **0** | ✅ 生效 |
| `journald SystemMaxUse` | 无限制（默认≈4G） | **200M** | ✅ 生效 |
| `net.ipv4.conf.all.rp_filter` | 0 | 0 | ⚠️ 经评估接受偏差 |

连通性验证：SSH `active`、nginx `active`、站点 HTTP 200、
默认路由正常、DNS 正常。**零服务中断。**

---

## 7. 沉淀的经验

### 7.1 配置"写进去"和"生效"是两件事

```
✅ 命令执行成功     ≠  配置生效
✅ 配置文件存在     ≠  配置生效
✅ sysctl -w 返回 0 ≠  最终值正确
```

**唯一可靠的验证是读回实际生效值**，而且要读**最终值**，不是命令的返回值。

本次之所以能发现，是因为我在每一步都做了"应用前 / 应用后"的值对比。

### 7.2 `/etc/sysctl.conf` 在加载顺序中永远最后

这是 RHEL/CentOS 系的实现细节，且**不受 `sysctl.d` 编号影响**：

```
实际顺序：
  /run/sysctl.d/*.conf
  /etc/sysctl.d/*.conf          （按字典序）
  /usr/local/lib/sysctl.d/*.conf
  /usr/lib/sysctl.d/*.conf
  /etc/sysctl.conf              ← 永远最后
```

**推论：想覆盖 `/etc/sysctl.conf` 的值，只能改 `/etc/sysctl.conf` 本身，
或者在运行时用 `sysctl -w` 强制写（但重启后会丢）。**

### 7.3 rp_filter 是 `max()` 语义，不是简单覆盖

内核实际用 `max(conf/all, conf/<iface>)`。配置时必须同时考虑
`conf/all`、`conf/default` 和具体接口，否则可能"看起来设了但没生效"。

### 7.4 危险变更要用死人开关

远程改网络参数的标准流程：

```bash
# 1. 排程自动回滚（先埋炸弹的引信）
echo "/path/to/revert.sh" | at now + 5 minutes

# 2. 应用变更
sysctl --system

# 3. 从【外部】验证连通性（不能只在本机验证！）
ssh -o ConnectTimeout=5 user@host 'echo OK'

# 4. 验证通过 → 拆除回滚作业
atq | cut -f1 | xargs -r atrm
```

**关键点：第 3 步必须从外部发起新连接。** 本机 `systemctl is-active sshd`
返回 active 不代表外部连得进来——防火墙/路由问题在本机是看不出来的。

### 7.5 安全加固要做风险权衡，不是照抄清单

| 基准建议 | 本机决策 | 原因 |
|---|---|---|
| `ip_forward = 0` | **不改**（保持 1） | Docker 需要，改了就瘫痪 |
| `rp_filter = 1`（严格） | 选 2（松散）/ 或接受 0 | 避免不对称路由丢包；评估后风险可接受 |
| 各种"推荐值" | 逐项评估 | 有的有实际收益，有的不适用于本场景 |

**"我不改这一项，因为……" 是成熟的运维判断；
"基准说改我就改" 是危险的行为。**

### 7.6 自己的诊断动作会污染系统

本次我又清理了一批自己产生的调试残留：

- `/tmp` 下的临时文件（限流测试、压测输出）
- nginx error log 里 3.5 MB 的 `limiting requests` 记录
  （来自我压测时触发的限流，属于我的测试流量而非真实问题）

**运维诊断要控制副作用。** 尤其在生产环境，
诊断命令的输出、临时文件、额外负载都应可回收。

---

## 附：可复用的 sysctl 加固检查清单

```bash
#!/bin/bash
# sysctl 加固的安全流程

TARGET_FILE=/etc/sysctl.conf          # 注意：想覆盖必须用这个文件
BACKUP=/root/ops-lab/backups/sysctl.conf.$(date +%s)

# 1. 备份
cp "$TARGET_FILE" "$BACKUP"

# 2. 记录原值
declare -A BEFORE
for p in $(grep -oE '^[a-z0-9_.]+' "$TARGET_FILE" | sort -u); do
    BEFORE[$p]=$(sysctl -n "$p" 2>/dev/null || echo N/A)
done

# 3. 修改（此处替换为实际改动）

# 4. 应用
sysctl --system >/dev/null 2>&1

# 5. 【关键】验证最终生效值，不是命令返回值
FAIL=0
for p in "${!BEFORE[@]}"; do
    NOW=$(sysctl -n "$p" 2>/dev/null || echo N/A)
    WANT=$(grep -E "^$p\s*=" "$TARGET_FILE" | tail -1 | awk -F= '{gsub(/ /,"",$2); print $2}')
    if [ -n "$WANT" ] && [ "$NOW" != "$WANT" ]; then
        echo "❌ $p 期望 $WANT 实际 $NOW（被其他文件覆盖？）"
        FAIL=1
    fi
done

# 6. 找出覆盖者
if [ "$FAIL" -eq 1 ]; then
    echo "--- 搜索所有设置该参数的文件 ---"
    grep -rn "$p" /etc/sysctl.conf /etc/sysctl.d/ /usr/lib/sysctl.d/ 2>/dev/null
fi

# 7. 连通性验证（必须从外部！）
echo "请从另一台机器验证: ssh -o ConnectTimeout=5 root@<本机IP> 'echo OK'"
```

**核心思想：配置管理中最危险的不是"改错了"，而是"以为改了"。**
