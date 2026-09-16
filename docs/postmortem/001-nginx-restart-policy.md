# 故障复盘 #001：nginx 崩溃后服务无法自动恢复

| 项目 | 内容 |
|---|---|
| 日期 | 2026-09-13 |
| 环境 | Alibaba Cloud Linux 3.2104 / ECS 2 vCPU 2 GB / 华北1（青岛） |
| 组件 | nginx 1.24.0（alinux3-updates 仓库） |
| 影响 | 进程被杀后站点持续不可用，需人工介入才能恢复 |
| 发现方式 | 主动故障注入测试（kill -TERM 主进程） |
| 严重级别 | 中（无自动恢复能力，2 GB 内存机器 OOM 风险高，实际会发生） |
| 处理耗时 | 约 15 分钟 |

---

## 1. 现象

在部署完 nginx 并确认站点正常（HTTP 200）后，做了一次"服务自愈能力"验证：
主动 kill 掉 nginx master 进程，观察 systemd 是否会自动拉起。

结果：

```
当前 master PID: 15009
kill 之后 master PID: (空)
HTTP 000          ← 连接失败
```

**服务没有恢复。** 手动执行 `systemctl start nginx` 后立即恢复正常，说明 nginx 本身和配置都没有问题，问题出在**服务管理器没有做恢复动作**。

## 2. 排查过程

### 2.1 确认 systemd 对 nginx 的生效策略

```bash
$ systemctl show nginx -p Restart -p RestartSec
Restart=no
RestartSec=100ms        # 该值此时无意义，因为 Restart=no
```

关键点：`Restart=no` 表示 **systemd 不会因进程意外退出而重启服务**。

### 2.2 确认这是发行版默认行为，不是配置写错

```bash
$ systemctl cat nginx | grep -E '^Restart='
（无输出）
```

查看原始 unit 文件 `/usr/lib/systemd/system/nginx.service` 的完整关键字段：

```
Type=forking
PIDFile=/run/nginx.pid
ExecStartPre=/usr/bin/rm -f /run/nginx.pid
ExecStartPre=/usr/sbin/nginx -t
ExecStart=/usr/sbin/nginx
ExecReload=/usr/sbin/nginx -s reload
```

**该 unit 完全没有 `Restart=` 指令。** 在 systemd 中，`Restart` 未设置时默认值为 `no`。

> 注意：`ExecStartPre=/usr/sbin/nginx -t` 只保证**启动前**配置语法正确，
> 它和"进程崩了要不要重启"没有任何关系。这两个概念极易混淆。

## 3. 根因

nginx 的 systemd unit 由发行版（阿里云 Alibaba Cloud Linux）提供，**未声明任何重启策略**，
因此 systemd 采用默认值 `Restart=no`。

后果：任何导致 nginx 主进程退出的情况都不会被自动恢复，包括：

- OOM Killer 终止进程（**2 GB 内存机器上是现实风险**）
- 进程因未知 bug 崩溃（segfault）
- 运维人员误操作 kill

服务会一直处于 `inactive (dead)`，直到有人发现并手动 `systemctl start`。

## 4. 修复方案

采用 **systemd drop-in 覆盖**，而不是直接修改 `/usr/lib/systemd/system/nginx.service`。

选择 drop-in 的原因：

| 方案 | 问题 |
|---|---|
| 直接改 `/usr/lib/systemd/system/nginx.service` | nginx 包升级时会被覆盖，修复静默丢失 |
| 在 `/etc/systemd/system/nginx.service` 放完整副本 | 同样会被版本更新"冻结"，且与厂商版本脱节 |
| **`/etc/systemd/system/nginx.service.d/override.conf`** | 只覆盖差异字段，可随包升级继承其余改进 ✅ |

创建文件 `/etc/systemd/system/nginx.service.d/override.conf`：

```ini
[Service]
Restart=always
RestartSec=2s
TimeoutStartSec=30s
TimeoutStopSec=10s
```

各字段含义：

- `Restart=always` — 无论正常退出还是被信号杀死，都重启。
  对 nginx 这类"应当长期存活"的服务，也可以用 `on-failure`；但 `always` 能覆盖
  "有人手动 kill 后希望它自己回来"的场景，对无状态 Web 服务更合适。
- `RestartSec=2s` — 重启前等待 2 秒。默认 `100ms` 在配置错误时会造成
  秒级疯狂重启，把 journal 刷爆；2 秒给了一个缓和窗口。
- `TimeoutStartSec=30s` — 启动超时上限，防止启动过程卡死。
- `TimeoutStopSec=10s` — 停止时给 worker 处理完在途请求的时间（优雅停机）。

生效步骤：

```bash
systemctl daemon-reload      # 必须！否则 systemd 不会读取新 unit 配置
systemctl show nginx -p Restart      # 验证：Restart=always
systemctl reload nginx               # 不中断服务地应用
```

## 5. 验证

**不是"改完就算"，而是再次真实注入故障来证明修复有效。**

```bash
$ OLD=$(cat /run/nginx.pid); echo $OLD
15154

$ kill -KILL "$OLD"          # SIGKILL，模拟 OOM Killer 的不可捕获终止
$ sleep 5
$ cat /run/nginx.pid
15259                        # ← PID 已变化，说明被重新拉起

$ systemctl status nginx | head -6
     Active: active (running) since Sun 2026-09-13 15:00:01 CST; 2s ago
    Drop-In: /etc/systemd/system/nginx.service.d
             └─override.conf
```

连续可用性探测：

```
第 1 次探测: HTTP 200
第 2 次探测: HTTP 200
第 3 次探测: HTTP 200
```

**修复确认有效。** 使用 `SIGKILL` 而非 `SIGTERM` 是刻意的：
`SIGTERM` 可以被进程捕获并优雅处理，而 `SIGKILL` 不可捕获，
更接近 OOM Killer 和 segfault 的真实场景。

## 6. 遗留风险与后续改进

- **`Restart=always` 会掩盖反复崩溃的问题。**
  nginx 若因配置错误起不来，systemd 会每 2 秒重试一次。需要配套**告警**
  才能发现"服务在无限重启"。→ 后续引入 `StartLimitBurst` 限制 + 巡检告警。
- **本次仅修复 nginx。** 机器上其他自建服务也需要同样审查。
  → 后续写脚本批量检查所有 unit 的 `Restart` 策略。
- **没有外部监控。** 本次故障是"主动测试"发现的，不是被监控发现的。
  如果是生产环境，这将是一次**静默故障**。
  → 后续建立可用性探测 + 告警（这正是 MTTD 指标的意义）。

## 7. 本次沉淀的通用经验

1. **`enabled` 不等于"会自愈"。**
   `systemctl is-enabled nginx` 返回 `enabled` 只表示**开机时**会启动，
   完全不代表运行中崩溃后会恢复。这是两个独立机制，极易混淆。

2. **`ExecStartPre` 的语法检查 ≠ 运行时保护。**
   它只在启动那一刻执行一次。

3. **改 systemd 配置后必须 `daemon-reload`。**
   少这一步，`systemctl show` 看到的仍是旧值——很容易误判"改了没生效"。

4. **验证修复要用故障注入，而不是读配置。**
   `systemctl show nginx -p Restart` 输出 `always` 只能说明"配置写对了"，
   不能说明"行为符合预期"。本次用 `SIGKILL` + PID 变化 + HTTP 探测三重确认。

5. **优先用 drop-in 而非改原厂文件。**
   这是"改动可叠加、可继承、可回滚"的运维原则。

---

## 附：可复用的检查命令

```bash
# 一次性检查所有服务的重启策略（找出没有自愈能力的服务）
for u in $(systemctl list-units --type=service --state=running --no-legend | awk '{print $1}'); do
  printf '%-40s %s\n' "$u" "$(systemctl show "$u" -p Restart --value)"
done | sort -k2
```

这条命令的思路：**不假设，去枚举。** 本次故障的价值不在于修好 nginx，
而在于发现了"我们从未检查过服务的自愈能力"这个系统性盲区。
