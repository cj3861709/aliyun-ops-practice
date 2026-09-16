# 已知偏差登记（Known Deviations Register）

> **这是什么**：本系统上**有意偏离默认/基线配置**的所有项目。
>
> **为什么需要**：运维中"没做"和"故意不做"看起来一样。
> 没有这份登记，未来的维护者（包括三个月后的自己）会：
> - 把有意偏差当成故障，试图"修复"，造成状态反复
> - 或者反复重新调查同一个问题，浪费精力
>
> **使用方式**：改动任何配置前先查这里。如果你的改动与某条偏差冲突，
> 先读它的"决策依据"，不要直接覆盖。

---

## DEV-001：kdump 服务被禁用

| 项目 | 内容 |
|---|---|
| 状态 | **有意禁用**（intentional） |
| 日期 | 2026-09-13 |
| 影响范围 | 内核崩溃时无自动转储能力 |
| 决策文档 | `/root/ops-lab/docs/kdump-decision.md` |

### 现象

```bash
$ systemctl is-enabled kdump
disabled
```

### 为什么禁用

内核命令行规则对本机预留 0 内存：

```
crashkernel=0M-2G:0M,2G-8G:192M,...
本机 1871MB → 落在 "0M-2G" → 预留 0M
```

`kdumpctl` 因此报 `No memory reserved for crash kernel` 并退出码 1，
服务状态长期为 `failed`。

**这不是配置错误**，而是规则明确不为 <2G 内存的机器预留 kdump 空间。
不处理它的后果是 `systemctl --failed` 永远有一项，导致对 failed 状态脱敏。

### 为什么不修好（而是禁用）

| 方案 | 成本 | 需要重启 |
|---|---|---|
| 改 grub 预留 128M | 128 MB（6.8% 内存） | 是 |
| 改 grub 预留 192M | 192 MB（10.3% 内存） | 是 |
| **禁用（已采用）** | **0** | **否** |

理由：
1. 2G 机器上 128-192MB 是显著开销，而内核 panic 极罕见
2. 改 grub 需要重启，重启是"需要人在场"的操作
3. 练习环境遇到内核崩溃的合理处理是重建，而非分析 dump

### 如何恢复（如果你确实需要 kdump）

完整步骤见 `/root/ops-lab/docs/kdump-decision.md` 第 6 节。摘要：

```bash
# 1. 改 /etc/default/grub，把 crashkernel 设为固定值 128M
# 2. grub2-mkconfig -o /boot/efi/EFI/alinux/grub.cfg   （本机是 UEFI）
# 3. systemctl enable kdump
# 4. reboot
# 5. 验证：cat /sys/kernel/kexec_crash_loaded  应输出 1
```

### 谁不应该撤销这个决定

**除非你满足以下任一条件，否则不要 enable kdump：**
- 内存扩容到 4G 以上
- 有明确的内核崩溃分析需求
- 能接受重启后内存减少 128M

如果只是"看到 disabled 觉得不对劲"，请先读本文档。

---

## DEV-002：net.ipv4.conf.all.rp_filter 保持 0（未按基线加固）

| 项目 | 内容 |
|---|---|
| 状态 | **有意保留**（intentional，经风险评估） |
| 日期 | 2026-09-13 |
| 影响范围 | 理论上无法防御 IP 源地址欺骗 |
| 决策文档 | `/root/ops-lab/docs/postmortem/005-sysctl-hardening-override.md` |

### 现象

安全基线要求 `rp_filter = 1`，但本机保持 `0`。

### 为什么保持 0

`/etc/sysctl.conf`（发行版预设）设 `rp_filter = 0`，且它在
`sysctl --system` 中**永远最后加载**，会覆盖 `/etc/sysctl.d/` 下的任何设置。

强行覆盖需要直接改 `/etc/sysctl.conf`，风险是：
- yum 更新可能覆盖
- 阿里云预设此值可能有其网络架构考虑

### 风险评估（为什么可以接受）

1. `rp_filter=0` 的风险是 IP 源地址欺骗
2. 本机是**单网卡 Web 服务器**，不承担路由/转发角色
3. 入口流量已被**阿里云安全组**过滤（仅 22 端口放行）
4. 攻击者需先能发包到本机才谈得上利用

**结论：风险可接受，选择不修改。**

### 关于 Docker 的额外说明

不要试图把 `net.ipv4.ip_forward` 改为 0 来"加固"——
Docker 依赖它，改了会导致**容器网络完全瘫痪**。
这也是"不照抄 CIS 基准"的原因之一。

---

## 新增偏差的流程

任何人做出有意偏离默认配置的改动时，必须：

1. **在本文件新增一条 DEV-XXX 记录**，包含：
   - 状态（有意/临时）
   - 现象（命令 + 输出）
   - 为什么这么做（决策依据）
   - 如何恢复（具体步骤）
   - 什么情况下应该撤销
2. **链接到详细文档**（如果决策复杂）
3. **在巡检脚本中加入检查**（见 `bin/deviation-check.sh`）

> 原则：**系统的每一个"异常"都应该是"已知且有意"的。**
> 不允许存在"不知道为什么这样但没人管"的状态。

---

## 相关文档

- `/root/ops-lab/docs/kdump-decision.md` —— kdump 详细决策 + 启用步骤
- `/root/ops-lab/docs/postmortem/005-sysctl-hardening-override.md` —— sysctl 覆盖问题
- `/root/ops-lab/docs/postmortem/001-nginx-restart-policy.md` —— 状态不干净掩盖真问题

---

## DEV-003：fail2ban 使用 iptables 封禁，但 INPUT 策略保持 ACCEPT

| 项目 | 内容 |
|---|---|
| 状态 | **有意设计**（intentional） |
| 日期 | 2026-09-13 |
| 影响范围 | 未封禁的 IP 可以访问任何监听端口（由安全组负责过滤） |
| 相关脚本 | `bin/f2b-status.sh`、`bin/fail2ban-rollback.sh` |

### 当前状态

```bash
$ iptables -S INPUT
-P INPUT ACCEPT                    # 策略仍是 ACCEPT
$ iptables -S | grep f2b
-N f2b-sshd
-A INPUT -p tcp --dport 22 -j f2b-sshd   # 只对被封 IP 生效
```

**iptables 只对"被 fail2ban 判定为恶意"的具体 IP 加 REJECT 规则，
并没有把 INPUT 策略改为 DROP。**

### 为什么这样设计

1. **安全组是第一道防线**：阿里云安全组已限制入方向（目前仅 22 端口），
   iptables 是第二层，职责是"动态封禁具体攻击源"，而非"整体拒绝"。

2. **降低自锁风险**：把 INPUT 改为 DROP 是高风险操作
   （一条规则写错就可能失联，而本机只有一个带外通道）。
   fail2ban 的按 IP 封禁不会影响未被封的连接。

3. **可逆性**：`fail2ban-rollback.sh` 能一键清理所有 f2b 链与 ipset，
   恢复后 iptables 与安装前完全一致。

### 什么情况下应该改变这个设计

- 如果安全组无法满足需求（例如需要更细粒度的端口控制）
- 如果能接受"改 INPUT 策略需在控制台带外操作"的风险

**届时需要你我同时在线操作**，并配合死人开关。

### 不要做的事

❌ 不要手动 `iptables -P INPUT DROP` —— 会立即断开当前所有连接（包括 SSH）
❌ 不要在 SSH 会话中直接 flush INPUT 链
❌ 不要在没有带外通道（控制台 Workbench）可用时改防火墙策略

---

## DEV-004：fail2ban jail 级 ignoreip 必须留空（继承 DEFAULT）

| 项目 | 内容 |
|---|---|
| 状态 | **有意设计**（intentional，踩坑后修正） |
| 日期 | 2026-09-13 |

### 要求

`/etc/fail2ban/jail.d/ops-lab-*.local` 中的 jail **不要**设置 `ignoreip`。

### 原因

fail2ban 的 jail 级 `ignoreip` 是**覆盖**语义，不是追加。
初期在两个 nginx jail 里写了 `ignoreip = 127.0.0.1/8 ::1`，
结果丢失了 `jail.local` 的 `[DEFAULT]` 段中：
- 管理 IP `ADMIN_IP`
- 阿里云内网段 `100.64.0.0/10`、`172.16.0.0/12`

**后果**：网站开放后，管理员自己浏览站点若触发限流，
会被自己的 fail2ban 封禁（只封 80/443，不影响 SSH，但自己看不了自己的站）。

### 如何检查

```bash
# 每个 jail 的白名单都应该是完整的 5 项
for j in sshd nginx-limit-req nginx-botsearch; do
  echo "[$j]"; fail2ban-client get $j ignoreip
done
```

期望输出（每个 jail 都应包含）：
```
127.0.0.0/8     ← 注意：不是 127.0.0.1，fail2ban 会规范化
100.64.0.0/10
172.16.0.0/12
::1
ADMIN_IP
```

`bin/f2b-status.sh` 已包含此检查，白名单缺失时会告警。
