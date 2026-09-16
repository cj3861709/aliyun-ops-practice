# kdump 处理决策记录

**日期**：2026-09-13
**决策**：明确禁用 kdump 服务（方案 C）
**操作者**：运维实验室

---

## 1. 故障现象

```
$ systemctl --failed
  UNIT          LOAD   ACTIVE SUB    DESCRIPTION
● kdump.service loaded failed failed Crash recovery kernel arming
```

从 2026-09-10 起持续 failed，跨 3 次重启每次都失败。

## 2. 根因分析

### 2.1 直接原因

```
$ journalctl -u kdump | grep kdumpctl
kdumpctl[978]: kdump: No memory reserved for crash kernel
kdumpctl[978]: kdump: Starting kdump: [FAILED]
```

kdump 需要一个预留给崩溃内核的内存区域，但本机没有预留。

### 2.2 为什么没有预留

查看内核命令行：

```
$ cat /proc/cmdline | tr ' ' '\n' | grep crashkernel
crashkernel=0M-2G:0M,2G-8G:192M,8G-128G:256M,128G-376G:384M,376G-:448M
```

这是一条**按内存大小分段的规则**：

| 物理内存区间 | 预留量 |
|---|---|
| **0M - 2G** | **0M** ← 本机在这里 |
| 2G - 8G | 192M |
| 8G - 128G | 256M |
| 128G - 376G | 384M |
| 376G 以上 | 448M |

本机物理内存 **1871 MB**，落在 `0M-2G` 区间，预留 **0M**。

### 2.3 结论：这不是配置错误

**规则本身明确不为小于 2G 内存的机器预留 kdump 内存**，这是一个
有意的设计决策——在内存紧张的机器上，牺牲崩溃转储能力来保证正常运行。

所以：
- ✅ `/etc/kdump.conf` 配置是对的
- ✅ kdumpctl / kexec / makedumpfile 工具都在
- ✅ `/var/crash` 目录存在且空间充足（28G 可用）
- ❌ 唯独 `crashkernel=0M` 导致无法启动

**这是"配置正确但环境不匹配"的典型案例。**

## 3. 三个方案的对比

| 方案 | 做法 | 内存成本 | 需重启 | 适用场景 |
|---|---|---|---|---|
| A | 改 grub 预留 128M | 128 MB (6.8%) | 是 | 需要内核崩溃分析 |
| B | 改 grub 预留 192M | 192 MB (10.3%) | 是 | 需要内核崩溃分析 |
| **C** | **明确禁用 kdump** | **0** | **否** | **练习机** ✅ |

## 4. 为什么选择方案 C

1. **内存成本显著**：128-192MB 在 1871MB 的机器上是 6.8%-10.3%，
   是实打实的开销。而这台机器要用来练习 Docker、监控等，
   内存是稀缺资源。

2. **需要重启**：`crashkernel` 是内核启动参数，改动必须重启才生效。
   而重启属于"需要人在场"的操作——万一启动失败，只能通过
   阿里云控制台 Workbench 抢救。风险和收益不成比例。

3. **实际价值低**：内核 panic 在正常运维中极罕见。即使发生，
   练习环境的合理处理方式是重建实例，而不是分析 dump 文件。

4. **但必须清理 failed 状态**（这是关键）：
   一个长期 failed 的服务会造成：
   - 污染 `systemctl --failed` 输出
   - 让人对 failed 状态**脱敏**，从而漏掉真正的新故障
   - 这与复盘 #001 发现的"10 个服务缺 Restart 策略"是同一类问题：
     **状态不干净会掩盖真问题**

   > 运维卫生原则：**failed 列表应该是空的，或者每一项都有明确的
   > "已知并接受"记录。** 不允许存在"不知道为什么失败但懒得管"的项。

## 5. 已执行的操作

```bash
systemctl disable kdump     # 取消开机自启（服务单元保留）
systemctl stop kdump        # 停止（本就没在运行）
systemctl reset-failed kdump # 清除失败计数
```

**注意**：`disable` 不等于删除。服务单元文件 `/usr/lib/systemd/system/kdump.service`
和配置 `/etc/kdump.conf` 都保留，将来启用只需改 grub + enable。

这是一个**可逆**的操作。

---

## 6. 将来如何启用 kdump（需要时的完整步骤）

### 前置检查

```bash
# 确认内存充足（建议 ≥2G，预留后仍有 1.5G 以上可用）
free -m
```

### 步骤 1：修改 grub 配置

```bash
# 备份
cp /etc/default/grub /etc/default/grub.bak-$(date +%Y%m%d)

# 查看当前的 crashkernel 参数
grep -n crashkernel /etc/default/grub
```

在 `GRUB_CMDLINE_LINUX` 中修改 `crashkernel` 值。对 2G 机器建议用
**固定值 128M**（而不是区间规则）：

```
# 修改前（示例）
GRUB_CMDLINE_LINUX="... crashkernel=0M-2G:0M,2G-8G:192M,..."

# 修改后：直接指定 128M，不依赖内存区间判断
GRUB_CMDLINE_LINUX="... crashkernel=128M"
```

**为什么不沿用区间规则**：现有规则对 <2G 预留 0M，
直接写固定值更明确，也不会因为内存微调（如从 1871MB 变成 2050MB）
而出现"突然开始预留 192M"的意外。

### 步骤 2：重新生成 grub 配置

```bash
# BIOS 启动
grub2-mkconfig -o /boot/grub2/grub.cfg

# 或 UEFI 启动（本机是 UEFI：有 /boot/efi 分区）
grub2-mkconfig -o /boot/efi/EFI/alinux/grub.cfg
```

**判断本机是 BIOS 还是 UEFI**：

```bash
[ -d /sys/firmware/efi ] && echo UEFI || echo BIOS
```

### 步骤 3：重启验证

```bash
# ⚠️ 重启前确认能通过控制台访问（万一启动失败要能救）
reboot
```

重启后验证：

```bash
# 1. 确认内核拿到了预留
cat /proc/cmdline | tr ' ' '\n' | grep crashkernel
dmesg | grep -i "crash kernel"        # 应看到 "Reserving 128MB of memory"

# 2. 确认服务启动成功
systemctl status kdump
systemctl is-active kdump             # 应为 active

# 3. 确认 kexec 已加载崩溃内核
cat /sys/kernel/kexec_crash_loaded    # 应为 1
```

### 步骤 4：验证转储能力（可选，进阶）

```bash
# 故意触发内核崩溃来测试（危险！只在练习机上做）
echo c > /proc/sysrq-trigger

# 重启后检查转储文件
ls -lh /var/crash/
```

**这一步会强制重启机器**，务必在能通过控制台访问时进行。

---

## 7. 参考：kdump 工作原理（面试可能会问）

```
正常运行                        内核崩溃

┌──────────────┐               ┌──────────────┐
│  主内核       │               │  主内核 panic │
│              │               │      ↓        │
│              │               │  kexec 跳转   │
└──────────────┘               └──────┬───────┘
                                      ↓
  预留内存区                        ┌──────────────┐
  (crashkernel)                    │  崩溃内核     │
  ↑ 平时不可用                      │  (capture)   │
  ↑ kdump 就靠它                    └──────┬───────┘
                                           ↓
                                    ┌──────────────┐
                                    │ makedumpfile │
                                    │ 写 /var/crash│
                                    └──────────────┘

关键点：
  1. crashkernel 预留的内存【平时不可用】，这是 kdump 的成本
  2. kexec 让系统能"不经过 BIOS"直接引导第二个内核，速度快
  3. 崩溃内核从预留区启动，此时主内核已死，但它仍能读内存并写磁盘
  4. makedumpfile 会过滤掉无用页（-d 31 是最激进的过滤级别）
```

---

## 8. 最终状态

```
$ systemctl --failed
（空）

$ systemctl is-enabled kdump
disabled

$ systemctl is-active kdump
inactive
```

决策记录完毕。此文档同时作为"如果将来需要启用"的操作手册。
