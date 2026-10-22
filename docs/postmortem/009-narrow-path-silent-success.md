# 变更记录 #009：两个回滚脚本在窄 PATH 下"报告成功但什么都没做"

| 项目 | 内容 |
|---|---|
| 日期 | 2026-10-22 |
| 环境 | Alibaba Cloud Linux 3 / bash 4.4 / cronie / at / util-linux 2.37 |
| 组件 | `bin/deadman-revert.sh`、`bin/fail2ban-rollback.sh` |
| 影响 | 两个回滚脚本在不含 `/usr/sbin` 的 PATH 下静默失败，却打印"已恢复""已清理"；验证过程本身又误碰了宿主机的 fail2ban |
| 发现方式 | 横向审计（手记 010 遗留问题第 4、5 条） |
| 严重级别 | 高（静默失败，且发生在"出事兜底"的脚本里） |
| 处理耗时 | 约 40 分钟 |

---

## 1. 现象

两个脚本都只在一种情况下才会被运行：**别的东西已经坏了**。

- `bin/deadman-revert.sh`：sysctl 加固把人锁在门外时，由 `at` 排程自动恢复原始参数
- `bin/fail2ban-rollback.sh`：fail2ban 出问题时一键清空封禁、卸载服务

它们的共同点是：执行失败不会被任何人注意到，因为**成功和失败打印的是同一句话**。

```bash
# bin/deadman-revert.sh 旧版（第 34-40 行）
sysctl -w kernel.dmesg_restrict=0 >/dev/null 2>&1
sysctl -w net.ipv4.conf.all.log_martians=0 >/dev/null 2>&1
sysctl -w net.ipv4.conf.all.send_redirects=1 >/dev/null 2>&1
sysctl -w net.ipv4.conf.default.send_redirects=1 >/dev/null 2>&1
sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null 2>&1
sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null 2>&1
echo "  已恢复原始参数"
```

六条 `sysctl -w` 全部把 stderr 丢进 `/dev/null`，第 40 行的 `echo` 与它们的返回值无关。
命令找不到（exit 127）、参数被拒（exit 255）、成功（exit 0）——**三种情况在日志里长得一模一样**。

`bin/fail2ban-rollback.sh` 是同一个毛病的另一副面孔：

```bash
# 旧版第 48 行
echo "  剩余 f2b 相关规则: $(iptables -S 2>/dev/null | grep -c f2b | tr -d '\n')"
```

`iptables` 找不到时，管道上游失败，`grep -c` 收到空输入，老老实实输出 `0`。
于是屏幕上出现的是「剩余 f2b 相关规则: 0」——**读起来是"已经清干净了"。**

---

## 2. 排查：先把"窄 PATH"这个环境造出来

这类 bug 在登录 shell 里跑一百次都是绿的。`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin` 里 `sysctl`、`iptables`、`ipset` 全在，看不出任何异常。

所以第一步不是读代码，是**复现执行环境**。用户 crontab 的默认 PATH 只有：

```bash
$ export PATH=/usr/bin:/bin
$ command -v sysctl || echo "找不到"
找不到
$ command -v iptables || echo "找不到"
找不到
```

把旧版脚本在这个 PATH 下跑一遍（`/etc/sysctl.d` 与日志目录临时用 tmpfs 遮住，真实内核参数不动）：

```text
PATH=/usr/bin:/bin
rc=0
--- deadman.log ---
  kernel.dmesg_restrict                      =
  net.ipv4.conf.all.log_martians             =
  net.ipv4.conf.all.rp_filter                =
  sshd 服务: active
  已恢复原始参数                                    ← 结论
--- 用绝对路径读真实值 ---
1                                                ← kernel.dmesg_restrict 一点没变
```

三件事同时出现了：

1. 状态行全是空的（`sysctl -n` 也找不到命令，只有 `sed` 的缩进还在）
2. 打印了「已恢复原始参数」
3. 退出码是 **0**

同样的构造下跑旧版 fail2ban 回滚：

```text
[3/4] 清理残留的 iptables 链和 ipset
  剩余 f2b 相关规则: 0
[4/4] 卸载软件包与配置
  Removed: fail2ban
  验证:
  iptables f2b 规则数: 0
✅ 回滚完成
rc=0
```

而宿主机此时实际有 **3 条** f2b 规则。**0 不是测量结果，是"量不出来"被写成了 0。**

---

## 3. 根因：三个叠在一起的小问题

### 3.1 脚本没有自带 PATH

`docs/SHELL-CONVENTIONS.md` 第一节就写着「所有脚本在 `set` 之后、第一条命令之前显式声明 PATH」，但两个脚本都没有那一行。规范存在 ≠ 规范被遵守——`grep -L 'export PATH' bin/*.sh` 的结果是 14 个脚本**一个都没有**。

### 3.2 失败被吞掉，然后无条件宣告成功

`>/dev/null 2>&1` 本身不是错，错在它后面那句 `echo` 与结果无关。
这是"静默失败"的第二种形态：不是没有检查，而是**检查的结论与检查的对象脱钩**。

第一种形态是"根本没有输出"，这一种更坏——它给了一个**看起来正确的结论**。

### 3.3 `grep -c` 的返回码被当成"统计失败"

修第一版时我踩进去过：想判断"iptables 调用失败"，写成了

```bash
if ! n=$(iptables -S | grep -c f2b); then
    echo "无法统计（iptables 调用失败）"
```

`grep -c` 在**没有匹配**时的行为是：打印 `0`，返回 **1**。
于是"规则已经清空"这个好消息被报成了"量不出来"——从"谎报干净"直接翻到"谎报失败"。

正确做法是分开两件事：命令本身是否成功、成功之后数出了几条。

```bash
f2b_rule_count() {
  local out n
  if ! out=$(iptables -S 2>/dev/null); then
    echo "无法统计（iptables 调用失败）"
    return
  fi
  n=$(printf '%s\n' "$out" | grep -c f2b || true)
  echo "${n:-0}"
}
```

### 3.4 附带发现：写死的文件名让一步彻底失效

`bin/deadman-revert.sh` 里移除加固配置的那一步写的是：

```bash
if [ -f /etc/sysctl.d/60-ops-lab-hardening.conf ]; then
```

而机器上实际存在的文件是 `99-zz-ops-lab-hardening.conf`（复盘 #005 里为了压过发行版预设改的名）。`[ -f 60-... ]` 永远为假，于是**这一步从改名那天起就再也没执行过**，而且不留任何痕迹——连"命令找不到"的报错都没有。

---

## 4. 修复

两个脚本改动方向一致，共四条：

**（1）自带 PATH**（照抄规范那一行，放在 `set -uo pipefail` 之后）

```bash
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
```

**（2）预检：缺命令就当场停下并写清原因**，不再一路空转到结尾

```bash
MISSING=""
for c in sysctl ss systemctl date; do
  command -v "$c" >/dev/null 2>&1 || MISSING="$MISSING $c"
done
if [ -n "$MISSING" ]; then
  echo "缺少命令:$MISSING" >> "$LOG"
  echo "PATH=$PATH" >> "$LOG"
  exit 78
fi
```

**（3）逐项判断返回值，并让退出码反映真实结果**

```bash
for kv in kernel.dmesg_restrict=0 net.ipv4.conf.all.log_martians=0 ...; do
  if sysctl -w "$kv" >/dev/null 2>&1; then
    echo "  ✅ $kv"
  else
    echo "  ❌ $kv 失败"
    FAILED=$((FAILED+1))
  fi
done
[ "$FAILED" -eq 0 ] || exit 1
```

**（4）把"量不出来"和"数出 0 条"分开**，并且统计不出来时也算失败

```bash
count_and_judge() {
  local label cnt
  label="$1"; cnt=$(f2b_rule_count)
  echo "  $label: $cnt"
  case "$cnt" in *无法统计*) FAILED=$((FAILED+1)) ;; esac
}
```

另外把 `while iptables -S INPUT | grep -q -- "-j $chain"` 换成先取输出再判断（复盘 #007 的 SIGPIPE 同一个坑），把写死的 `60-ops-lab-hardening.conf` 换成通配 `*ops-lab-hardening*.conf`，找不到时明确打印"跳过移除"。

---

## 5. 验证：在 mount namespace 里做 A/B

两个脚本都不能真跑：一个会改内核参数，一个会卸包、刷防火墙。所以验证放在 **mount namespace** 里做，用桩替换破坏性命令，宿主机不受影响。

```bash
unshare -m --propagation private bash -c '
  mount -t tmpfs none /etc/sysctl.d          # 让 mv 无处可动
  mount --bind /tmp/stub/sysctl /usr/sbin/sysctl   # 用桩替换真命令
  export PATH=/usr/bin:/bin                  # 抄 cron 的裸 PATH
  bash bin/deadman-revert.sh
'
```

用 `git show HEAD:bin/deadman-revert.sh` 取出旧版做对照，七组结果：

| | 对象 | 结果 |
|---|---|---|
| T1 | 旧 deadman，裸 PATH | rc=0，打印「已恢复原始参数」，真实值仍为 1 |
| T2 | 新 deadman，桩全成功 | rc=0，桩收到 6 次 `sysctl -w`，打印「6 项全部成功」 |
| T3 | 新 deadman，桩全失败 | rc=1，「共 6 项失败」出现 1 次，「已恢复原始参数」出现 **0** 次 |
| T4 | 旧 f2b，裸 PATH | rc=0，「剩余 f2b 相关规则: 0」+「✅ 回滚完成」（实际 3 条） |
| T5 | 新 f2b，iptables 桩 | 桩收到 `-F` / `-S INPUT` / `-D INPUT -j f2b-sshd` / `-X`，rc=0 |
| T6 | 新 f2b，iptables 常量失败 | 「无法统计（iptables 调用失败）」两次，rc=1 |
| T7 | 宿主机 | `dmesg_restrict=1`、`rp_filter=0`、nginx active、 `/etc/fail2ban` 仍在 |

T1 与 T4 是"修复前"的复现，T2/T3/T5/T6 是"修复后"的双向验证：**既要证明正确的能通过，也要证明错误的能被检出**（规范 7）。

**一个必须记下来的事实：`at` 的 PATH 里有 `/usr/sbin`。**

```text
$ echo true | at now + 5 minutes
$ at -c 5 | grep -E '^(PATH|SHELL)='
SHELL=/bin/bash; export SHELL
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin; export PATH
```

所以死人开关**经 `at` 触发时 `sysctl` 其实是找得到的**，这条缺陷在 at 这个入口上不会发作。
它发作的是 cron、或者任何从窄 PATH 里 source 它的场合。修复仍然要做（补齐 PATH 一次解决一族），但严重性的表述必须按事实来：**潜伏，未触发**。

---

## 6. 验证过程本身被污染（这一节比修复更值得看）

第一轮跑完 T7，宿主机状态看着干干净净。但 `iptables -S | grep -c f2b` 从 3 变成了 **2**。

查 `/var/log/fail2ban.log`：

```text
2026-10-22 13:26:55,689 fail2ban.actions NOTICE [sshd] Flush ticket(s) with iptables-multiport
2026-10-22 13:26:55,704 fail2ban.actions NOTICE [nginx-limit-req] Flush ticket(s) with iptables-multiport
2026-10-22 13:26:55,705 fail2ban.actions NOTICE [nginx-botsearch] Flush ticket(s) with iptables-multiport
（以上三行重复了三轮）
```

三个 jail 的封禁被**真的清空了**。

原因：T6 的测试脚本给 `systemctl`、`yum`、`iptables`、`ipset` 都做了桩，**唯独漏了 `fail2ban-client`**。于是脚本里的 `fail2ban-client unban --all` 直接连上了宿主机的 fail2ban，把三个 jail 的封禁全解了。

后果评估（当场核过）：

- `passwordauthentication no`，只有密钥能登录。被解封的来源拿不到任何东西。
- fail2ban 仍是 `active`，sshd jail 的 `Currently failed` 在继续计数，达到 `maxretry` 会重新封。
- `/etc/fail2ban`、`jail.local` 完好（`rm -rf` 打在 tmpfs 上，报 `Device or resource busy`）。

所以实际损失是：一条正在生效的封禁被提前解除，需要靠重新触发才能恢复。

**这件事的意义不在"解封了一个 IP"，在"我用来证明『不会碰宿主机』的那套装置，自己碰了宿主机"。**

桩系统有个结构性弱点：它是**按我想到的命令逐个搭的**，我没想到的命令就直接穿透到真实系统。解决办法不是"下次更小心"，而是让装置自己会拒绝：

> 测试脚本启动时先把待测脚本里出现的外部命令全部提取出来，逐个检查是否已有桩；缺一个就**拒绝运行**，而不是照跑。

第二轮补上 `fail2ban-client` 的桩后，T6 重跑：`rc=1`，「无法统计」两次，宿主机 `iptables -S | grep -c f2b` 仍是 2（不再变化）。

---

## 7. 沉淀的经验

### 7.1 "成功"必须是测出来的，不能是写上去的

第 40 行那个 `echo "  已恢复原始参数"` 与它上面六条命令的返回值没有任何关系。这类句子的正确写法只有两种：跟着判断走，或者干脆删掉。

判断标准很简单：**把这条命令换成 `false`，这行输出还会不会变？** 不会变，它就不是结论，是装饰。

### 7.2 回滚脚本是最不该静默失败的地方

部署失败有回滚、有健康检查、有 error.log；而回滚脚本失败时，**已经有一个故障在等它救场**。它的可靠性与日志清晰度应该高于平均水准，实际上却因为"很少被运行"而长期无人验证。

### 7.3 同一个数字，"0"和"量不出来"必须是两种输出

`grep -c` 打 0、命令找不到打 0、文件不存在打 0——三个 0 含义相反，而屏幕上长得一样。
凡是这类统计，输出里要能区分"数了，是 0"和"没数成"。

### 7.4 环境不同要先复现环境，再读代码

这个缺陷读代码也能看出来，但**只有把 PATH 抄成 cron 的样子跑一遍，才知道它会打印什么**。手记 010 里已经吃过一次同样的教训（登录 shell 永远绿、cron 永远红）。

### 7.5 隔离装置的覆盖面，本身要被断言

namespace、桩、tmpfs 都是"我以为我隔离住了"。这次漏一个桩就破了功。
装置要么自证（检查清单、拒绝运行），要么就不能宣称"不会影响宿主机"。

---

## 8. 遗留问题与后续计划

**遗留**

- `bin/*.sh` 共 14 个脚本仍无 `export PATH`，本次只补了两个。其余多数不在任何调度入口，风险低，但属于同一族。
- `bin/deploy.sh:140` 的 `if nginx -t 2>&1 | grep -qi emerg` 同样存在"命令找不到 → 条件不成立 → 门禁静默放行"的问题，尚未修。
- fail2ban 的三个 jail 封禁已被清空，需靠后续触发重新建立，短时内攻击来源不受拦截（仅密钥登录可进）。

**后续计划**

1. 修 `bin/deploy.sh:140`，并给它配一个负向测试（把 nginx 替换成会失败的桩，验证发布被拦住）。
2. 把"脚本在裸 PATH 下是否自洽"做成自动化检查：`PATH=/usr/bin:/bin bash -n` 之外，还要能检出"调用了 /usr/sbin 下的命令但没声明 PATH"。
3. 给测试脚手架加桩完整性断言：待测脚本出现的外部命令若无对应桩，直接拒绝运行。
