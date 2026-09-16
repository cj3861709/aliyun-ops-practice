# Shell 脚本踩坑记录

本文件记录本项目在写 Shell 脚本时踩过的坑。
每一条都是**真实发生过**的，不是假设。

---

## 坑 1：`set -o pipefail` + `grep -q` = 假失败

### 现象

```bash
set -o pipefail
if tar -tzf "$BACKUP" | grep -qx "etc/nginx/nginx.conf"; then
    echo "文件在备份中"
else
    echo "文件缺失"        # ← 实际存在，却走到这里
fi
```

同一份备份，**不同的文件有的通过有的失败**。

### 根因

`grep -q` 找到第一个匹配就**立即退出**，关闭管道读端。
上游 `tar` 继续写入时收到 **SIGPIPE（信号 13）**，退出码 **141**（128+13）。

由于设了 `pipefail`，管道的退出码变成 `141`（非 0），
于是 `if` 判断为假 → **假失败**。

实测数据：
```
管道整体退出码: 141        ← 128 + 13 (SIGPIPE)
```

### 为什么是间歇性的

取决于目标在输出流中的**位置**：

| 目标位置 | tar 是否被中断 | 检查结果 |
|---|---|---|
| 输出前部 | 是（grep 很快退出） | ❌ 假失败 |
| 输出末尾附近 | 否（tar 已写完） | ✅ 正确通过 |

**换个备份（文件顺序变了）结果就变。**
这是最难排查的一类 bug。

### 正确写法

```bash
# ❌ 危险
set -o pipefail
tar -tzf "$f" | grep -qx "target"

# ✅ 方式 1：先存列表再匹配（彻底避免 SIGPIPE）
LIST=$(tar -tzf "$f")
if printf '%s\n' "$LIST" | grep -qx "target"; then ...

# ✅ 方式 2：用 grep -c（会读完整流）
CNT=$(tar -tzf "$f" | grep -cF "target" || true)
[ "${CNT:-0}" -gt 0 ] && ...

# ✅ 方式 3：局部关闭 pipefail
set +o pipefail
tar -tzf "$f" | grep -q "target"; RC=$?
set -o pipefail
```

### 推广

**任何"输出量大 + 提前退出的消费者"都会触发此问题**：

| 上游 | 消费者 | 风险 |
|---|---|---|
| `tar -tzf` | `grep -q` / `head` | 高 |
| `find /` | `grep -q` / `head` | 高 |
| `journalctl` | `head` | 高 |
| `cat 大文件` | `head` | 中 |

`head -1` 也是提前退出！同样的坑。

---

## 坑 2：`set -u` + 数组未定义元素

### 现象

```bash
set -u
PS=("${PIPESTATUS[@]}")
echo "${PS[1]}"        # ← bash: PS[1]: unbound variable
```

### 根因

`PIPESTATUS` 的元素个数取决于**上一条命令实际有几个管道段**。
如果上一条命令不是管道（或只有一段），`PS[1]` 就未定义，
`set -u` 会让访问未定义变量直接报错退出。

### 正确写法

```bash
# ✅ 先检查长度，或用默认值
set +u
echo "${PS[1]:-N/A}"
set -u

# ✅ 或判断元素是否存在
if [ "${#PS[@]}" -gt 1 ]; then
    echo "${PS[1]}"
fi
```

---

## 坑 3：`grep -c` 无匹配时的输出

### 现象

```bash
CNT=$(grep -c "pattern" "$file" || echo 0)
if [ "$CNT" -gt 0 ]; then ...    # ← [: 0\n0: integer expression expected
```

### 根因

`grep -c` 无匹配时**已经输出 `0` 并返回退出码 1**。
`|| echo 0` 又追加了一个 `0`，变量变成 `"0\n0"`。

### 正确写法

```bash
# ✅ 用 || true 只处理退出码，不追加输出
CNT=$(grep -c "pattern" "$file" || true)
# ✅ 或直接忽略退出码
CNT=$(grep -c "pattern" "$file" 2>/dev/null) || CNT=0
```

---

## 坑 4：依赖命令输出的字段位置

### 现象（发生过 3 次）

| 命令 | 我的假设 | 实际 |
|---|---|---|
| `ss -tnp state established` | 输出含 State 列 | **不含**（被过滤条件隐去） |
| `systemctl is-enabled` | 输出是 `disabled` | 带换行，与 `\|\| echo` 叠加成双值 |
| `fail2ban-client get ignoreip` | 有 `127.0.0.1` | 被**规范化**为 `127.0.0.0/8` |

### 正确做法

1. **先打印原始输出**（用 `cat -A` 看不可见字符），再写匹配规则
2. 用**正则容纳等价表示**：`grep -qE '127\.0\.0\.(0/8|1)'`
3. 需要精确字段时用 `awk`/`python` 显式解析，或让命令输出**机器可读格式**
   （如 `ss -H`、`systemctl show -p X --value`）
4. **验证脚本本身也要被验证** —— 用已知的正例和反例测试它

---

## 坑 5：中文引号破坏 Shell 解析

### 现象

```bash
echo "从"决定恢复"到"服务可用""     # ← unexpected EOF while looking for matching `"'
```

中文引号 `""` 在某些编码环境下会被 shell 当作普通字符或引号处理不一致。

### 正确写法

- 中文文本里避免使用中文引号包裹，改用 `【】` 或 `「」`
- 或者：整个字符串用单引号包裹，中文引号放在里面
  ```bash
  echo '从"决定恢复"到"服务可用"'    # ✅ 安全
  ```

---

## 通用原则

1. **写完脚本必须实测** —— 语法通过不代表逻辑正确
2. **先看原始输出，再写匹配规则** —— 不要假设输出格式
3. **验证脚本也要有测试** —— 假警报和漏报同样有害
4. **间歇性 bug 优先怀疑管道与信号** —— 环境依赖、时序依赖
5. **把踩过的坑写下来** —— 第 3 次踩同类坑说明缺少检查清单
