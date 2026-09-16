# 手记 003：心跳服务的静默失效

**日期**: 2026-09-16
**用时**: 约 45 分钟
**难度**: ⭐⭐⭐☆☆

---

## 1. 现象

`quest02` 是一个心跳服务，设计为每分钟向 `/var/log/quest02.log`
追加一行带时间戳的记录。该服务已连续运行两天，此前无异常。

本次收到一句无具体信息的反馈："这个服务好像有问题了。"

### 1.1 进入排查的切入点

执行 `systemctl status quest02` 时，输出中出现了**大量重复的错误信息**：

```
Sep 16 13:32:12 ... quest02.sh[43122]: /usr/local/bin/quest02.sh: line 49: ...
Sep 16 13:33:12 ... quest02.sh[43122]: /usr/local/bin/quest02.sh: line 49: ...
Sep 16 13:34:12 ... quest02.sh[43122]: /usr/local/bin/quest02.sh: line 49: ...
...（每分钟一条，持续不断）
```

**每分钟一次的重复错误是本故障唯一的可见信号。**

这构成了一个明显的矛盾：

| 观察项 | 结果 |
|---|---|
| `systemctl is-active quest02` | **active** |
| `systemctl is-enabled quest02` | **enabled** |
| `systemctl status quest02` | **active (running)** |
| 但日志中每分钟出现一条错误 | **异常** |

即：**服务的状态是"正常"的，但它在持续报错。**

---

## 2. 排查过程

### 2.1 先看完整的错误内容

`systemctl status` 的输出被截断，改用 `journalctl` 查看完整信息：

```bash
journalctl -u quest02 -n 30 --no-pager
```

得到关键输出：

```
Sep 16 13:25:12 ... quest02.sh[43122]: /usr/local/bin/quest02.sh: line 49:
                                    /var/log/quest02.log: Permission denied
Sep 16 13:28:12 ... （同上）
...（每 60 秒重复）
```

**`Permission denied` 是突破口。** 服务在尝试写日志文件时被拒绝。

### 2.2 确认日志文件的实际状态

```bash
tail -20 /var/log/quest02.log      # 无输出（空文件）
ls -la /var/log/quest02.log
```

输出：

```
-rw-r--r-- 1 root root 0 Sep 16 13:27 /var/log/quest02.log
```

两个信息：

1. **文件大小为 0** —— 与"日志不再增长"一致
2. **属主是 `root:root`** —— 而服务不应该以 root 运行

### 2.3 确认服务的运行身份

```bash
ps -o user,pid,cmd -p $(systemctl show quest02 -p MainPID --value)
```

输出：

```
USER         PID CMD
chenjin    43122 /bin/bash /usr/local/bin/quest02.sh
```

**矛盾出现：**

| 项目 | 值 |
|---|---|
| 服务进程身份 | `chenjin` |
| 日志文件属主 | `root:root` |
| 文件权限 | `644`（root 可写，其他只读） |

服务进程以 `chenjin` 身份运行，但日志文件归 `root` 所有且其他用户只有读权限——
**因此写入必然失败。**

### 2.4 追溯"谁改了文件"

这一步是本此排查的关键转折。

服务自身**不可能**修改自己日志文件的属主——它的权限不足以执行 `chown`。
且该文件此前的属主是正确的（`chenjin:chenjin`），说明有**外部进程**在某个时刻改动了它。

在 Linux 系统中，会周期性操作 `/var/log/` 下文件的机制主要是**日志轮转**。
检查其配置：

```bash
cat /etc/logrotate.d/quest02
```

输出：

```
/var/log/quest02.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
```

**问题在最后一行：`create 0644 root root`。**

### 2.5 确认轮转确实发生过

```bash
ls -la /var/log/quest02.log*
```

输出：

```
-rw-r--r-- 1 root    root        0 Sep 16 13:27 /var/log/quest02.log
-rw-r--r-- 1 chenjin chenjin    94 Sep 16 13:27 /var/log/quest02.log.1
-rw-r--r-- 1 chenjin chenjin 67047 Sep 16 03:17 /var/log/quest02.log-20260916
...
```

时间线清晰：

| 文件 | 属主 | 时间 |
|---|---|---|
| `quest02.log-20260916` | `chenjin:chenjin` | 09-16 03:17（旧日志） |
| `quest02.log.1` | `chenjin:chenjin` | 09-16 13:27（被重命名的原文件） |
| **`quest02.log`** | **`root:root`** | **09-16 13:27（新建的文件）** |

**13:27 发生了一次轮转：**
- 原 `quest02.log`（属主 chenjin）被重命名为 `quest02.log.1` —— **保留原属主**
- logrotate 创建了新的 `quest02.log` —— **属主是 root**

**从这一刻起，服务再也无法写入日志。**

---

## 3. 根因

### 3.1 直接原因

`/etc/logrotate.d/quest02` 中的 `create` 指令把新文件的属主指定成了 `root root`：

```
create 0644 root root
```

logrotate 以 **root 身份**运行，因此在轮转时创建的新文件归 root 所有。
而服务以 `chenjin` 运行，对该文件**只有读权限，没有写权限**。

### 3.2 为什么错误没有第一时间暴露

脚本中写日志的语句带有错误抑制：

```bash
printf '%s [TICK ] 第 %d 次心跳\n' "$(date '+...')" "$COUNT" >> "$LOG_FILE" 2>/dev/null
```

**但这里有一个值得注意的细节：`2>/dev/null` 并未完全掩盖错误。**

原因是 shell 对重定向的处理顺序：

```
1. bash 解析命令
2. 处理 >> "$LOG_FILE"  → 尝试打开文件
3. 打开失败 → 立即向【当前】stderr 报错
4. 此时 2>/dev/null 尚未生效
```

因此错误仍然进入了 journal。

**这个"失败"反而有利**：如果错误被完全抑制，该故障将不会有任何可见症状，
只能通过"日志不增长"这一间接指标发现，排查难度会显著提高。

### 3.3 故障的性质

这是一次典型的**静默失效**：

| 检查项 | 结果 | 是否暴露问题 |
|---|---|---|
| 服务状态 | active | ❌ 看不出 |
| 开机自启 | enabled | ❌ 看不出 |
| 进程存在 | 是 | ❌ 看不出 |
| 进程身份正确 | chenjin | ❌ 看不出 |
| **日志是否在增长** | **否** | ✅ **唯一能发现问题的指标** |

**结论：`systemctl is-active` 返回 `active` 不能说明服务在正常工作。**
它只说明主进程存在。

---

## 4. 修复

### 4.1 修复过程

**步骤 1：恢复当前的文件写入能力**

```bash
chown chenjin:chenjin /var/log/quest02.log
```

验证：

```bash
sudo -u chenjin test -w /var/log/quest02.log && echo "可写" || echo "不可写"
```

输出 `可写`。

**步骤 2：修复根因 —— 改正 logrotate 配置**

用编辑器修改 `/etc/logrotate.d/quest02`，把：

```
create 0644 root root
```

改为：

```
create 0644 chenjin chenjin
```

**这一步与步骤 1 同等重要。** 若只修改文件属主而不修改配置，
下一次轮转会把属主重新设回 root，故障将再次发生。

**步骤 3：重启服务**

```bash
systemctl restart quest02
```

重启后主进程 PID 从 `43122` 变为 `58340`。

**步骤 4：验证功能恢复**

```bash
wc -l /var/log/quest02.log    # 记录当前行数
# 等待 60 秒以上
wc -l /var/log/quest02.log    # 再次记录
```

结果：`0 → 1` 行，最后一行：

```
2026-09-16 14:03:35 [TICK ] 第 9 次心跳
```

**日志确实在增长，功能已恢复。**

**步骤 5：验证预防措施有效**

用 dry-run 检查下次轮转会创建什么属主的文件：

```bash
logrotate -f /etc/logrotate.d/quest02
```

（注意：普通 `logrotate -d` 因当日已轮转而跳过，需用 `-f` 强制。）

```bash
ls -l /var/log/quest02.log
```

输出：

```
-rw-r--r-- 1 chenjin chenjin 0 Sep 16 14:02 /var/log/quest02.log
```

**轮转后的新文件属主已是 `chenjin`，配置修复生效。**

### 4.2 任务检查结果

任务检查器 10/10 通过：

```
✅ 日志属主正确（chenjin:chenjin）
✅ chenjin 用户可写日志
✅ 配置包含 create 指令
✅ create 指令指定了 chenjin 属主
✅ 服务处于 active
✅ 日志正在增长（0 → 1 行）
✅ 生产站点 / /postmortem.html / /journal.html / /ops.html 全部 HTTP 200
```

### 4.3 排查过程中的两处失误

**失误 1：执行了不必要的重启**

排查过程中额外执行了 `systemctl restart nginx`。nginx 与本次故障无关，
不应重启。该操作会：

- 造成不必要的服务中断（站点在重启期间短暂不可用）
- 若 nginx 存在其他隐患，重启会将其掩盖

**正确做法**：动手前确认目标服务与当前问题是否相关。
若怀疑某服务受影响，应先**验证**而非重启：

```bash
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/
```

**原则：只动需要动的东西。**

**失误 2：把配置指令当作命令执行**

```
# create 0644 chenjin chenjin
-bash: create: command not found
```

`create 0644 chenjin chenjin` 是 logrotate 配置文件中的**一行内容**，
不是 shell 命令。

**区分方法**：文档中若说明该内容位于"某个文件的某一行"，则不应直接粘贴到终端。

同类例子：

```
listen 8090;                # nginx 配置行
server_name quest01.test;   # nginx 配置行
Type=simple                 # systemd 配置行
```

---

## 5. 结论

### 5.1 排查方法论

**其一，明确"症状"与"原因"的层次。**

本次故障中：

| 层次 | 内容 |
|---|---|
| 症状 | 日志文件属主是 root，服务写不进去 |
| 原因 | logrotate 配置中的 `create` 指令写错了属主 |

**只修症状**（改文件属主）→ 下次轮转后故障重现。
**修原因**（改 logrotate 配置）→ 不再复发。

**因此修复必须覆盖两层。** 判断标准是：**改动完成后，同样的触发条件再次出现时，
问题还会不会发生？**

**其二，从"状态正常"中识别"功能异常"。**

本次故障的全部可见信息只有两条：

1. 服务的 journal 中每分钟一条 `Permission denied`
2. 日志文件的修改时间停滞

其余各项检查（服务状态、自启状态、进程身份）**全部显示正常**。

**这说明检查的深度需要分层：**

| 层次 | 检查方式 | 能否发现本次故障 |
|---|---|---|
| 进程存在 | `systemctl is-active` | ❌ |
| 配置正确 | `systemctl is-enabled` | ❌ |
| 进程身份 | `ps -o user=` | ❌ |
| **功能产出** | **观察日志是否增长** | ✅ |

**排查应从"功能产出"这一层开始，而不是从最弱的一层开始。**

**其三，遇到"不该如此"的状态，应追问"谁导致的"。**

本次的关键转折点是发现：

```
进程身份: chenjin
文件属主: root
```

服务进程不可能修改自己日志文件的属主（权限不足）。既然当前状态不可能由服务自身造成，
就必然存在**外部作用者**。沿着这个方向检查周期性操作日志文件的机制，
即定位到 logrotate。

**"谁改的"通常比"怎么改回来"更接近根因。**

### 5.2 配置原则

**配置日志轮转时，`create` 指令必须显式指定服务用户。**

```
create 0644 chenjin chenjin
         ↑     ↑        ↑
       权限   属主      属组
```

省略属主参数时，logrotate 保持原文件属主，行为看似正常——
但这依赖于"原文件属主恰好正确"这一前提。
**一旦原文件的属主被其他原因改变，问题就会在下次轮转时暴露。**

因此**不应依赖默认行为，而应显式声明**。

### 5.3 遗留注意点

本次故障的可见信号（journal 中的错误）带有偶然性。
如果脚本中的错误重定向顺序不同，错误可能被完全抑制，
届时唯一的发现途径将是"文件修改时间停滞"。

**这类故障无法通过服务状态检查发现，只能通过检查功能产出（日志是否增长、
数据是否更新、请求是否有响应）来发现。** 监控设计应考虑这一点。

---

## 6. 遗留问题

无。任务 003 完成。

---

## 7. 后续计划

当前已完成三项任务：

| 编号 | 内容 | 主要收获 |
|---|---|---|
| 001 | 部署静态站点 | nginx 配置、权限模型、`nginx -t` 的局限 |
| 002 | systemd 托管服务 | unit 文件、CGroup、logrotate 基础 |
| 003 | 心跳服务故障排查 | 静默失效识别、症状与原因的分层、预防性验证 |

后续计划编写实际的运维脚本（如磁盘检查、服务巡检），
综合运用前三个任务涉及的配置、权限与故障处理知识。
