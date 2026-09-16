# Shell 脚本编写规范

本项目的所有 Shell 脚本遵循以下规范。
每条都是从**真实故障**中总结的，不是教条。

---

## 规范 1：优先用 `set -uo pipefail`，谨慎使用 `-e`

### 推荐的默认设置

```bash
set -uo pipefail
```

| 选项 | 作用 | 为什么推荐 |
|---|---|---|
| `-u` | 引用未定义变量时报错 | 捕获拼写错误，避免 `rm -rf $DIR/` 变成 `rm -rf /` |
| `-o pipefail` | 管道中任一命令失败则整体失败 | 避免 `cmd1 | cmd2` 中 cmd1 失败被忽略 |
| ~~`-e`~~ | 任何命令失败立即退出 | **谨慎使用**，原因见下 |

### 为什么对 `-e` 保持谨慎

`set -e` 看起来是"最安全"的选项，但它与大量**查询类命令**天然冲突：

| 命令 | 非 0 退出码的含义 | 是错误吗 |
|---|---|---|
| `systemctl is-active` | 服务未运行 | ❌ 不是，是查询结果 |
| `systemctl is-enabled` | 服务未启用 | ❌ 不是 |
| `grep pattern file` | 未找到匹配 | ❌ 不是 |
| `diff a b` | 文件有差异 | ❌ 不是 |
| `test` / `[` | 条件不成立 | ❌ 不是 |

**在 `set -e` 下，这些"正常的否定结果"会中断脚本。**

### 真实案例（复盘 #008）

```bash
set -euo pipefail

systemctl stop nginx                     # 成功
STOPPED=$(systemctl is-active nginx)     # 服务已停 → 返回 3 → 脚本在此退出！
systemctl start nginx                    # ← 永远执行不到
```

**结果：nginx 被停掉，但重新启动的语句没执行，站点不可用。**

这个 bug 的恶劣之处：
- `bash -n` 查不出（语法正确）
- 单独运行 `systemctl is-active nginx` 看起来正常（只打印 inactive）
- 只在 `set -e` 脚本里暴露
- **留下半完成状态**（比直接失败更危险）

---

## 规范 2：查询类命令必须显式处理返回值

```bash
# ❌ 在 set -e 下会中断
X=$(systemctl is-active nginx)

# ✅ 方式 1：加 || true（最简洁，适合取值场景）
X=$(systemctl is-active nginx 2>/dev/null || true)

# ✅ 方式 2：用 if（语义最清晰，适合条件判断）
if systemctl is-active --quiet nginx; then
    echo "运行中"
fi

# ✅ 方式 3：局部关闭 errexit（适合连续多次查询）
set +e
A=$(systemctl is-active nginx)
B=$(systemctl is-active fail2ban)
set -e
```

**本项目统一使用方式 1**（简洁且不易遗漏）。

---

## 规范 3：破坏性脚本必须用 `trap` 恢复环境

```bash
#!/bin/bash
set -uo pipefail

SERVICE="$1"

cleanup() {
    # 无论脚本成功、失败还是被 Ctrl-C 中断，都恢复服务
    if ! systemctl is-active --quiet "$SERVICE"; then
        echo "[保护] 恢复服务..."
        systemctl start "$SERVICE" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

systemctl stop "$SERVICE"
# ... 测试逻辑 ...
systemctl start "$SERVICE"
```

**`trap ... EXIT` 是"先停后启"类脚本的必备保险。**
没有它，中途失败会让服务停在停止状态。

---

## 规范 4：破坏性操作前武装死人开关

```bash
# 1. 先排程自动回滚
echo "$REVERT_SCRIPT" | at now + 5 minutes

# 2. 执行危险操作
# ...

# 3. 验证成功 → 拆除回滚
atq | cut -f1 | xargs -r atrm
```

本项目已用此模式三次：fail2ban 部署、sysctl 加固、冷启动测试。
**每次都靠它兜底。**

---

## 规范 5：不用 `bc`，用整数运算

```bash
# ❌ 依赖 bc（不一定装有）
T0=$(date +%s.%N)
echo "$T1 - $T0" | bc

# ✅ 整数纳秒（零依赖）
T0=$(date +%s%N)
T1=$(date +%s%N)
echo "$(( (T1 - T0) / 1000000 )) 毫秒"
```

**更少的依赖 = 更少的故障点。**

---

## 规范 6：管道 + `grep -q` 要防止 SIGPIPE（复盘 #007）

```bash
# ❌ 上游被 SIGPIPE 杀死，pipefail 让管道返回 141
tar -tzf "$f" | grep -qx "target"

# ✅ 先存变量
LIST=$(tar -tzf "$f")
printf '%s\n' "$LIST" | grep -qx "target"

# ✅ 或用 grep -c
CNT=$(tar -tzf "$f" | grep -cF "target" || true)
[ "${CNT:-0}" -gt 0 ]
```

---

## 规范 7：验证脚本本身也要双向测试

**不只验证"正确的能通过"，还要验证"错误的能被检出"。**

```bash
# 验证判分器
echo "正向测试：用正确配置 → 应通过"
bash labs/01/check.sh          # 期望退出码 0

echo "反向测试：故意用错误配置 → 应失败"
# 临时注入错误配置
bash labs/01/check.sh          # 期望报告错误
```

本项目已有 **5 次** "验证逻辑写错导致假警报" 的记录：

| # | 场景 | 错误类型 |
|---|---|---|
| 1 | `systemctl is-enabled` 换行未处理 | 假失败 |
| 2 | fail2ban IP 规范化（127.0.0.1 → 127.0.0.0/8） | 假警报 |
| 3 | `pipefail + grep -q` 的 SIGPIPE | 假失败 |
| 4 | fstab 检查器把 swap 的 `none` 当缺失 | 假警报 |
| 5 | `systemd-analyze verify` 用法错误 | 假警报 |
| 6 | 练习 01 冲突检测匹配到注释 | 假警报 |

**假警报和漏报同样有害，但假警报会先摧毁你对监控的信任。**

---

## 规范 8：能用专用工具就别自己写逻辑

| 需求 | 别自己写 | 用这个 |
|---|---|---|
| 验证 fstab | 解析字段、检查挂载点 | `findmnt --verify` |
| 验证 systemd unit | 人工检查依赖 | `systemd-analyze verify <文件路径>` |
| 验证 nginx 配置 | grep 关键字 | `nginx -t` |
| 查看 unit 文件路径 | 猜路径 | `systemctl show X -p FragmentPath --value` |

**自己写验证逻辑 = 自己制造 bug 的机会。**

---

## 规范 9：脚本必须能独立运行

```bash
# 判断依赖的函数是否已定义
if ! declare -F check_ok >/dev/null 2>&1; then
    # 自己定义一份，使其不依赖外部加载
    check_ok() { ... }
fi
```

**好处**：可单独测试、可在 cron/CI 里复用、排查问题方便。

---

## 规范 10：PowerShell 脚本用纯 ASCII

**PowerShell 5.1 默认按 GBK 读取 .ps1 文件**（除非有 BOM），
中文会被解码成乱码导致解析失败。

本项目已两次踩坑：
- 第 2 轮：`ssh-teacher.ps1` 中文注释导致 "表达式或语句中包含意外的标记"
- 第 10 轮：`download-backup.ps1` 中文输出全部乱码成 `鉂?鏂囦欢`

**规范：跨平台 PowerShell 脚本一律用英文注释和输出。**
