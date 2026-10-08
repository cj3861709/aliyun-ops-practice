# 手记 002：用 systemd 托管自建服务

**日期**: 2026-09-14
**用时**: 约 60 分钟
**难度**: ⭐⭐⭐☆☆

---

## 1. 现象

任务要求创建一个 systemd 服务 `quest02.service`：以非 root 用户运行，
每分钟向 `/var/log/quest02.log` 追加带时间戳的记录，开机自启，崩溃自愈。

执行过程中遇到一次权限阻塞，并涉及两个此前未接触的机制
（CGroup 进程管理、logrotate 的 inode 处理）。

### 1.1 非 root 用户无法创建日志文件

切换到普通用户 `chenjin` 后尝试创建日志文件：

```
$ su chenjin
$ vi /var/log/quest02
$ sudo vi /var/log/quest02
[sudo] password for chenjin:
chenjin is not in the sudoers file.  This incident will be reported.
```

`chenjin` 不在 sudoers 中，无法提权。

### 1.2 操作身份的定位错误

初次排查时的思路是"以 chenjin 身份去创建属于 chenjin 的文件"，
方向不正确。实际上安装配置阶段应以 root 操作，
服务的运行身份（chenjin）只影响运行时。

---

## 2. 排查过程

### 2.1 确认操作分工

服务运行身份与安装配置身份是两个概念：

| 阶段 | 操作者 | 内容 |
|---|---|---|
| 安装配置 | root | 建日志文件、放置脚本、编写 unit、启动服务 |
| 运行时 | chenjin | 服务进程的实际身份 |

因此正确做法是：以 root 创建日志文件，再通过 `chown` 将属主移交。

### 2.2 创建日志文件并设置属主

```bash
touch /var/log/quest02.log
chown chenjin:chenjin /var/log/quest02.log
```

```bash
ls -l /var/log/quest02.log
```

验证：

```
-rw-r--r-- 1 chenjin chenjin 0 /var/log/quest02.log
```

### 2.3 验证写入权限

```bash
sudo -u chenjin test -w /var/log/quest02.log && echo "可写" || echo "不可写"
```

输出 `可写`。使用 `sudo -u` 代入目标用户身份验证，
而非仅凭 `ls -l` 判断——后者无法覆盖路径链上的全部权限检查。

### 2.4 先手动运行脚本，再包成服务

```bash
sudo -u chenjin /usr/local/bin/quest02.sh    # Ctrl+C 停止
```

日志输出：

```
2026-09-14 16:28:25 [START] quest02 服务启动 (pid=42676, user=chenjin)
2026-09-14 16:28:25 [TICK ] 第 1 次心跳
2026-09-14 16:28:43 [STOP ] quest02 服务停止
```

三点得到验证：运行身份为 chenjin、主循环工作、
收到中断信号后通过 trap 干净退出。

该步骤的作用是隔离变量：若直接写 unit 并启动，
一旦失败将无法判断问题出在脚本还是 unit 配置。

### 2.5 编写 unit 文件

`/etc/systemd/system/quest02.service`：

```ini
[Unit]
Description=Quest02 heartbeat service
After=network.target

[Service]
Type=simple
User=chenjin
Group=chenjin
ExecStart=/usr/local/bin/quest02.sh
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

加载与启动：

```bash
systemctl daemon-reload
systemctl start quest02
systemctl status quest02
```

首次 `status` 显示：

```
Loaded: loaded (/etc/systemd/system/quest02.service; disabled; ...)
Active: active (running) since Mon 2026-09-14 16:32:04 CST; 4s ago
Main PID: 42879 (quest02.sh)
CGroup: /system.slice/quest02.service
        ├─42879 /bin/bash /usr/local/bin/quest02.sh
        └─42883 sleep 60
```

`disabled` 表明服务能运行但重启后不会自动拉起，与任务要求不符。

### 2.6 启用开机自启

```bash
systemctl enable quest02
```

输出：

```
Created symlink /etc/systemd/system/multi-user.target.wants/quest02.service
  → /etc/systemd/system/quest02.service.
```

该输出说明 `enable` 的机制是在 `multi-user.target.wants/` 目录下创建符号链接，
这正是 unit 文件中 `[Install] WantedBy=multi-user.target` 的作用。

### 2.7 验证崩溃自愈

```bash
kill -9 $(systemctl show quest02 -p MainPID --value)
sleep 6
systemctl status quest02
```

结果：

```
Active: active (running) since Mon 2026-09-14 16:36:03 CST; 10s ago
Main PID: 43122 (quest02.sh)          ← 原为 42879
```

日志：

```
2026-09-14 16:36:03 [START] quest02 服务启动 (pid=43122, user=chenjin)
2026-09-14 16:36:03 [TICK ] 第 1 次心跳
```

使用 `kill -9`（不可捕获信号）而非 `SIGTERM`，
以模拟 OOM Killer 的行为。服务自动恢复，`Restart=always` 生效。

---

## 3. 根因

本章共涉及四项机制，其中两项在任务要求之外，但影响服务的长期可靠性。

### 3.1 Type= 决定 systemd 如何判定"启动完成"

`systemd` 需要知道 `ExecStart` 之后，进程处于什么状态才算启动成功：

| Type | 判定条件 | 典型场景 |
|---|---|---|
| `simple`（默认） | `ExecStart` 的进程即主进程 | 前台运行的脚本、`crond -n` |
| `forking` | 父进程 fork 后退出，systemd 认为启动完成 | `nginx`（需配合 `PIDFile=`） |
| `notify` | 进程主动通过 socket 通知就绪 | `sshd`、`docker` |
| `oneshot` | 进程执行完退出，不常驻 | 初始化脚本 |

对 `forking` 类型的准确描述是：**父进程 fork 出子进程后自身退出，
systemd 以此作为启动完成信号**；真正的主进程是那个子进程，
systemd 需要通过 `PIDFile=` 定位它。

本任务的脚本在前台持续循环，不 fork 也不退出，因此适用 `Type=simple`。
若误写为 `forking`，systemd 会等待 fork 动作，而脚本永不 fork，
最终以启动超时失败。

### 3.2 start 与 enable 的区别

| 命令 | 作用范围 | 效果 |
|---|---|---|
| `systemctl start` | 当前运行时 | 立即启动，重启后失效 |
| `systemctl enable` | 开机启动 | 建立符号链接，不影响当前状态 |

`enable` 依赖 unit 文件中的 `[Install] WantedBy=` 段。
若缺少该段，`enable` 会直接报错
（`The unit files have no installation config`）。

### 3.3 CGroup 决定服务的进程边界（任务要求之外）

`systemctl status` 的输出包含两个进程：

```
CGroup: /system.slice/quest02.service
        ├─42879 /bin/bash /usr/local/bin/quest02.sh
        └─42883 sleep 60
```

`sleep` 被计入服务范围，原因是 systemd 以 **CGroup** 而非单个 PID 界定服务边界：

- 服务启动时，其进程被放入 `/system.slice/quest02.service` 这个 cgroup
- 脚本 fork 出的所有子孙进程（包括 `sleep`）自动继承该 cgroup
- `KillMode=control-group`（默认）使 `systemctl stop` 杀死该 cgroup 内的全部进程

这解释了传统 init 脚本与 systemd 的差别：
前者靠记录 PID 停止服务，服务 fork 出的子进程会残留为孤儿进程；
后者按 cgroup 清理，不会有残留。

`sleep` 在该脚本中的作用是让出 CPU。若去掉 `sleep`：

```bash
while true; do echo x >> log; done            # 忙等待，占满一个 CPU 核
while true; do echo x >> log; sleep 60; done  # 让出 CPU
```

### 3.4 logrotate 的 inode 处理（任务要求之外）

为控制日志体积，为 `/var/log/quest02.log` 添加轮转规则：

```
/var/log/quest02.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 chenjin chenjin
}
```

**`create` 指令是本配置的关键。** logrotate 轮转时的动作是：

1. 重命名 `quest02.log` → `quest02.log.1`
2. **创建新的** `quest02.log`

第 2 步由 logrotate（以 root 运行）执行，若未指定 `create` 的属主，
新文件的属主会是 `root:root`，而服务以 `chenjin` 运行——
**服务将无法再写入日志，且因为脚本中 `2>/dev/null` 吞掉了错误，
故障不会产生任何提示，表现为静默失效。**

因此必须显式声明 `create 0644 chenjin chenjin`。

**验证时遇到的第一个障碍**：普通 `logrotate -d` 因
"当日已轮转"的状态记录而跳过，看不到 `create` 动作。
使用 `-d -f` 强制演示后才观察到关键输出行：

```
creating new /var/log/quest02.log mode = 0644 uid = 1000 gid = 1000
```

`uid=1000 gid=1000` 与 `id chenjin` 的输出一致，确认配置正确。
确认后才执行真实轮转。

**另一层机制**：轮转后服务能否继续写入，取决于脚本打开文件的方式。

| 写法 | 行为 | 轮转后 |
|---|---|---|
| 每次 `>>` 打开 | 按路径重新打开 | 自动写入新文件，正确 |
| `exec 3>>` 持有 fd | 长期持有文件描述符 | 继续写入旧 inode，日志"消失" |

本任务脚本使用第一种写法，因此不受影响。

实测对比（在 `/tmp` 构造两个脚本并轮转）：

```
写法 A（每次 >>）：新文件 7 行，旧文件定格 3 行    ← 正确
写法 B（持有 fd）：新文件 0 行，旧文件 10 行       ← 数据持续写入已重命名的文件
```

**这也解释了 nginx 与 fail2ban 的 logrotate 配置为何包含 `postrotate` 段**：
nginx 持有文件描述符，需要通过信号（`kill -USR1`）通知它重新打开日志文件；
fail2ban 则通过 `fail2ban-client flushlogs` 达到同样目的。

---

## 4. 修复

### 4.1 日志文件权限

```bash
touch /var/log/quest02.log
chown chenjin:chenjin /var/log/quest02.log
```

### 4.2 服务启用与验证

```bash
systemctl enable quest02
systemctl is-enabled quest02        # enabled
kill -9 $(systemctl show quest02 -p MainPID --value)
sleep 6
systemctl status quest02            # 主 PID 已变化，自动恢复
```

### 4.3 日志轮转配置

新建 `/etc/logrotate.d/quest02`，核心是 `create 0644 chenjin chenjin`。

### 4.4 验证结果

任务完成度 9/9：

```
✅ 服务名为 quest02.service     ✅ 日志属主为 chenjin
✅ 服务在运行                   ✅ 日志有轮转配置
✅ 开机自启                     ✅ 日志在增长
✅ 非 root 运行                 ✅ Restart=always
✅ 日志文件存在
```

轮转后的文件列表确认 `create` 生效：

```
-rw-r--r-- 1 chenjin chenjin 225 /var/log/quest02.log
-rw-r--r-- 1 chenjin chenjin 270 /var/log/quest02.log.1
-rw-r--r-- 1 chenjin chenjin 272 /var/log/quest02.log.2.gz
```

三个文件属主均为 `chenjin:chenjin`，服务在轮转后持续写入。

---

## 5. 结论

本章的主要收获集中在三方面。

**Linux 的账号权限模型**

权限由"属主 / 属组 / 其他"三组位决定，而实际访问判定需要沿路径逐级检查——
路径中任一级目录缺少 `x` 位都会导致失败。验证权限应代入目标用户身份
（`sudo -u <user> test -r`），而不是仅查看目标文件自身的权限位。

同时，操作身份与运行身份是两个独立的维度：
安装配置由 root 完成，服务的运行时身份由 unit 文件中的 `User=` 指定。
`chenjin` 不在 sudoers 中是预期行为——最小权限原则要求普通账号默认不具备提权能力。

**CGroup 的进程组织逻辑**

systemd 以 cgroup 而非单个 PID 界定服务边界，
因此服务产生的所有子孙进程都在同一 cgroup 内，
`systemctl stop` 能一并清理。这一点是 systemd 相对于传统 init 脚本的实质改进：
后者按 PID 停止服务时，fork 出的子进程会残留。

**脚本与周边工具的配合**

脚本本身的实现方式会影响它与外部工具的兼容性。
以日志为例，`>>` 每次重开文件与 `exec 3>>` 长期持有 fd，
在 logrotate 场景下行为完全不同：
前者自动适应轮转，后者需要额外的 `postrotate` 通知机制。
选择合适的实现方式可以避免引入额外的复杂度。

**遗留的注意点**：`/var/log/quest02.log` 的轮转依赖 `create` 指令维持属主。
若将来修改轮转配置时删除了这一行，服务会在下一次轮转后静默停止写日志。
这类"不报错的故障"需要通过定期检查日志文件是否在增长来发现，
而不能只依赖服务状态检查。

---

## 6. 遗留问题

无。任务 002 完成。

---

## 7. 后续计划

当前已完成静态站点部署（任务 001）与 systemd 服务托管（任务 002）。
下一阶段计划转向故障排查：在无预先提示的情况下定位并修复注入的故障。
