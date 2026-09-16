# 故障复盘 #008：`set -e` + `systemctl is-active` 导致脚本静默中断

| 项目 | 内容 |
|---|---|
| 日期 | 2026-09-13 |
| 环境 | Alibaba Cloud Linux 3 / bash 4.4 / systemd 239 |
| 组件 | Shell 脚本中的 `set -e` 与命令替换退出码 |
| 影响 | **测试脚本中途退出，把 nginx 停在了 inactive 状态**（站点短暂不可用） |
| 发现方式 | 冷启动测试脚本执行到一半退出（exit 1） |
| 严重级别 | 中（服务可恢复，但暴露了危险的脚本模式） |
| 处理耗时 | 约 20 分钟 |

---

## 1. 现象

我在做**启动韧性验证**——检查服务器重启后能否正常提供服务。
为了不开机重启就能验证，我用"完全停止 → 重新启动"模拟冷启动。

脚本执行到这一步就退出了：

```
┌──────────────────────────────────────────┐
│   ── 测试 nginx：stop → start ──          │
│   停止前:                                 │
│     active=active enabled=enabled         │
│     PID=30245                             │
│                                          │
│   [脚本在此处退出，exit code 1]           │
└──────────────────────────────────────────┘
```

**后果**：`nginx` 停在了 `inactive`，站点返回 `HTTP 000`。
更危险的是——**我在测试前武装的"死人开关"（3 分钟后自动恢复）也快到期了。**

---

## 2. 排查：三次错误假设

### 假设 A：`systemctl stop` 返回非 0

**验证**：

```bash
systemctl stop nginx; echo $?     # → 0
systemctl start nginx; echo $?    # → 0
```

**结论：A 不成立。** 两个命令都返回 0。

### 假设 B：某个命令在特定上下文下失败

我用独立的 trace 脚本重跑了一遍完整流程，开 `set -x`：

```
+ systemctl stop nginx
+ echo 'TRACE: stop 完成，退出码 0'
+ sleep 1
++ systemctl is-active nginx
++ true
+ STOPPED=inactive
+ systemctl start nginx
+ echo 'TRACE: start 完成'
=== TRACE: 正常结束 ===
trace 脚本退出码: 0
```

**结论：B 也不成立。** trace 脚本完全正常。

> 注意这里有个关键线索：我的 trace 脚本写的是
> `STOPPED=$(systemctl is-active nginx 2>/dev/null || true)` ——
> **我无意中加上了 `|| true`，正好避开了真正的 bug。**
> 这解释了为什么 trace 能通过而原脚本不能。

### 假设 C：stderr 输出干扰

**验证**：`systemctl stop nginx 2>&1` 输出为空，退出码 0。

**结论：C 不成立。**

### 真正的原因：直接测那一行的退出码

```bash
$ systemctl stop nginx
$ sleep 1
$ systemctl is-active nginx
inactive
$ echo $?
3                          ← 关键！
```

**`systemctl is-active` 在服务未运行时返回退出码 `3`。**

而原脚本里写的是：

```bash
STOPPED=$(systemctl is-active nginx 2>/dev/null)    # ← 赋值继承退出码 3
```

在 `set -e` 环境下，**赋值语句的退出码来自命令替换**，
所以这一行的退出码是 3，`set -e` 立即终止脚本。

**脚本停止在 `stop` 之后、`start` 之前 —— nginx 就停在那里了。**

---

## 3. 根因

```bash
set -euo pipefail

systemctl stop nginx                    # 成功，退出码 0
STOPPED=$(systemctl is-active nginx)    # 服务已停 → 返回 3 → set -e 退出
systemctl start nginx                   # ← 永远执行不到
```

### 为什么这个 bug 特别隐蔽

| 特征 | 说明 |
|---|---|
| `bash -n` 检查不出 | 语法完全正确 |
| 单独运行看起来正常 | `systemctl is-active nginx` 只是打印 `inactive`，不像出错 |
| 只在 `set -e` 脚本里暴露 | 恰恰是生产脚本的常见配置 |
| 无任何错误信息 | `2>/dev/null` 把 stderr 也吞了 |
| **会留下半完成状态** | 服务被停掉，但恢复步骤没执行 |

**这是"命令成功执行但退出码非 0"的典型案例** ——
`is-active` 的语义是"查询状态"，查询到了 `inactive` 这个事实，
命令本身成功了，但退出码表示"服务未激活"。

> 类似情况还有：
> - `grep` 无匹配返回 1（这是它的正常语义）
> - `diff` 有差异返回 1
> - `systemctl is-enabled` 对 `disabled` 服务返回 1
> - `test` 条件不成立返回 1

---

## 4. 附带发现：我的审计脚本有两处假警报

这次审计还暴露了两个**验证逻辑错误**（本项目第 4、5 次）：

### 错误 1：把 swap 行的 `none` 当成缺失的挂载点

```
/swapfile none swap sw 0 0
          ↑
          这是标准写法，表示"不挂载到目录树"
```

我的检查器报"挂载点 none 不存在，重启会失败" —— **假警报**。

**验证方法**（用官方工具而非自己写逻辑）：

```bash
$ findmnt --verify
0 parse errors, 0 errors, 1 warning
none
   [W] non-bind mount source /swapfile is a directory or regular file
```

`findmnt --verify` 是验证 fstab 的**官方工具**，退出码 0 = 正确。

**教训：能用专用工具验证的，不要自己写模式匹配。**

### 错误 2：`systemd-analyze verify` 用法错误

```bash
$ systemd-analyze verify nginx
Failed to prepare filename nginx: Invalid argument     ← 我的用法错
```

它需要**文件路径**，不是单元名：

```bash
$ systemd-analyze verify /usr/lib/systemd/system/nginx.service
（无输出 = 通过）
```

**如果我没验证这一点，就会误判"4 个服务有问题"。**

正确获取路径的方式：

```bash
systemctl show nginx -p FragmentPath --value
```

---

## 5. 修复

### 修复 1：脚本层面

```bash
# ❌ 危险：在 set -e 下会中断
STOPPED=$(systemctl is-active nginx 2>/dev/null)

# ✅ 方式 1：加 || true
STOPPED=$(systemctl is-active nginx 2>/dev/null || true)

# ✅ 方式 2：用 if（最适合条件判断）
if systemctl is-active --quiet nginx; then
    echo "运行中"
else
    echo "未运行"
fi

# ✅ 方式 3：局部关闭 errexit（适合连续查询）
set +e
A=$(systemctl is-active nginx)
B=$(systemctl is-active fail2ban)
set -e
```

**本项目所有脚本已统一改用方式 1。**

### 修复 2：修正后的冷启动测试

```bash
#!/bin/bash
set -euo pipefail

echo "测试前: $(systemctl is-active nginx 2>/dev/null || true)"

T0=$(date +%s%N)
systemctl stop nginx
sleep 1
STOPPED=$(systemctl is-active nginx 2>/dev/null || true)    # ← 关键修复
echo "stop 后: $STOPPED"

systemctl start nginx
T1=$(date +%s%N)
sleep 1
STARTED=$(systemctl is-active nginx 2>/dev/null || true)
echo "start 后: $STARTED"
echo "耗时: $(( (T1 - T0) / 1000000 )) 毫秒"
```

**实测结果**：

```
── nginx 冷启动测试（修正版）──
  测试前: active
  stop 后: inactive
  start 后: active
  耗时: 1066 毫秒
  ── 功能验证 ──
    / → HTTP 200
    /postmortem.html → HTTP 200
── fail2ban 冷启动测试 ──
  stop 后: inactive
  start 后: active
  jail: 3 个
── 完成 ──
修正版退出码: 0
```

### 修复 3：顺手改掉 `bc` 依赖

原脚本用 `bc` 做浮点计算：

```bash
T1=$(date +%s.%N)
echo "$T1 - $T0" | bc
```

`bc` 不一定所有系统都装。改用整数纳秒：

```bash
T0=$(date +%s%N)
T1=$(date +%s%N)
echo "$(( (T1 - T0) / 1000000 )) 毫秒"
```

**更少的依赖 = 更少的故障点。**

---

## 6. 死人开关再次证明了价值

这次事故中，**我在测试前武装的死人开关是关键保险**：

```
死人开关已武装: job 3 at Sun Sep 13 15:54:00 2026
```

脚本在 15:51 退出，死人开关会在 15:54 自动拉起服务。
我在 15:51:54 手动恢复了 nginx 并拆除了开关，**比自动恢复早了 2 分钟**。

**如果我没有这个机制**，nginx 会一直处于 inactive 直到有人发现。

> 这是本项目**第三次**依赖死人开关处理危险操作
> （前两次：fail2ban 部署、sysctl 加固）。
> **远程做危险变更时先埋自动回滚，已经是稳定收益的习惯。**

---

## 7. 沉淀的经验

### 7.1 `set -e` 与"查询类命令"天然冲突

`set -e` 的规则是"任何命令返回非 0 就退出"。
但很多**查询类命令**用非 0 退出码表达"查询结果是否定"：

| 命令 | 非 0 的含义 |
|---|---|
| `systemctl is-active` | 服务未运行 |
| `systemctl is-enabled` | 服务未启用 |
| `grep` | 未找到匹配 |
| `diff` | 有差异 |
| `test` / `[` | 条件不成立 |
| `cmp` | 文件不同 |

**这些非 0 都不是错误，是正常语义。**

**实践建议**：在 `set -e` 脚本里，凡是"查询类命令"都要显式处理：

```bash
# 方案 1：加 || true（简洁，但会丢失"查询结果"的语义区分）
X=$(systemctl is-active nginx 2>/dev/null || true)

# 方案 2：用 if（推荐，语义清晰）
if systemctl is-active --quiet nginx; then ... fi

# 方案 3：局部关 errexit（适合批量查询）
set +e; ...; set -e
```

### 7.2 命令替换会传播退出码

```bash
VAR=$(command)    # VAR 的赋值语句退出码 = command 的退出码
```

**这一点容易被忽略**，因为看起来只是"给变量赋值"。

### 7.3 破坏性测试必须幂等且有恢复路径

这个脚本的设计缺陷：**先 stop 再 start，但没有 `trap` 保护**。

如果中途失败，服务就停在那里了。正确做法：

```bash
#!/bin/bash
set -euo pipefail

cleanup() {
    # 无论脚本成功、失败还是被中断，都确保服务在运行
    systemctl is-active --quiet nginx || systemctl start nginx
}
trap cleanup EXIT INT TERM

systemctl stop nginx
# ... 测试逻辑 ...
systemctl start nginx
```

**`trap ... EXIT` 是破坏性脚本的必备保险** ——
它保证"无论怎么退出，都把环境恢复原状"。

### 7.4 调试时"无意中修好了"是重要线索

我的 trace 脚本能通过，是因为我**无意中加了 `|| true`**。
如果我没注意到这个差异，会陷入"为什么独立跑可以、完整脚本不行"的困惑。

**教训：当"最小复现"不重现问题时，一定要 diff 两者——差异就是答案。**

### 7.5 能用专用工具验证的，不要自己写逻辑

本次两个假警报都源于此：

| 我写的逻辑 | 应该用的工具 |
|---|---|
| 自己解析 fstab 检查挂载点 | `findmnt --verify` |
| 自己传单元名给 systemd-analyze | `systemctl show -p FragmentPath` 取路径 |

**自己写验证逻辑 = 自己制造 bug 的机会。**
除非工具不存在，否则优先用系统自带工具。

---

## 附：可复用的冷启动测试脚本

```bash
#!/bin/bash
# 服务冷启动能力测试（安全版）
#
# 用途：验证服务"完全停止后能否正常启动"，等价于模拟开机启动。
# 比只看 systemctl is-enabled 有力得多。
set -euo pipefail

SERVICE="${1:?用法: $0 <服务名>}"
PORT="${2:-}"      # 可选：服务监听的端口

# ---- 安全保护：无论怎么退出都恢复服务 ----
cleanup() {
    if ! systemctl is-active --quiet "$SERVICE"; then
        echo "[保护] 服务未运行，尝试恢复..."
        systemctl start "$SERVICE" 2>/dev/null || true
        sleep 2
    fi
    local st
    st=$(systemctl is-active "$SERVICE" 2>/dev/null || true)
    echo "[保护] 最终状态: $st"
}
trap cleanup EXIT INT TERM

echo "=== $SERVICE 冷启动测试 ==="
echo "测试前: $(systemctl is-active "$SERVICE" 2>/dev/null || true)"
echo "已启用: $(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"

# ---- 停止 ----
systemctl stop "$SERVICE"
sleep 1
echo "停止后: $(systemctl is-active "$SERVICE" 2>/dev/null || true)"   # || true 关键

# ---- 冷启动并计时 ----
T0=$(date +%s%N)
systemctl start "$SERVICE"
T1=$(date +%s%N)
sleep 1

echo "启动后: $(systemctl is-active "$SERVICE" 2>/dev/null || true)"
echo "启动耗时: $(( (T1 - T0) / 1000000 )) 毫秒"

# ---- 功能验证（起来了 ≠ 能用）----
if [ -n "$PORT" ]; then
    CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/" || echo 000)
    echo "端口 $PORT 响应: HTTP $CODE"
    [ "$CODE" = "200" ] && echo "✅ 功能正常" || echo "⚠️ 服务在跑但功能异常"
fi

echo "=== 测试完成 ==="
```

**核心要点**：
1. `|| true` 处理 `is-active` 的非 0
2. `trap cleanup EXIT` 保证环境恢复
3. 用整数纳秒计时，不依赖 `bc`
4. 功能验证与状态验证分开（`active` ≠ 能用）
