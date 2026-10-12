# 手记 010：cron 的 PATH 与登录 shell 不同 —— 连续 18 天的假告警

**日期**: 2026-10-10
**用时**: 约 60 分钟
**难度**: ⭐⭐⭐☆☆

---

## 1. 现象

按站点上「日常巡检」的步骤跑了一遍。第一步 `healthcheck.sh` 正常，
第二步读自动巡检结果时，看到数据库一项报错：

```
==========================================================
 系统巡检  2026-10-10 08:00:04
==========================================================

  ❌ 数据库    检查失败

----------------------------------------------------------
 结果: CRITICAL（警告 0，严重 1）
----------------------------------------------------------
```

有三个地方不对劲：

1. 报错**没有任何细节**，只有「检查失败」四个字，看不出是哪一项失败；
2. 这不是偶发 —— `logs/daily-check/cron.log` 里**首次出现是 2026-09-23 08:00**，
   之后每一天都是同一行，到当天已连续 18 天；
3. 但手动执行同一个检查脚本，**8 项全部正常**。

同一个脚本，一个入口红、一个入口绿。

---

## 2. 排查过程

### 2.1 先确认数据库本身没问题

直接跑检查脚本：

```bash
bash bin/db/check-mariadb.sh
```

输出 8 项全绿 —— 容器运行中、可连接（11.8.9-MariaDB）、
内存约 50MiB / 400MiB、数据卷 162M、备份在时效内、
**仅绑定 127.0.0.1:3306**、凭据文件权限 600 ——
末行 `结果: 全部正常`，退出码 0。

**数据库是健康的。** 所以问题出在「谁在检查」，而不是「被检查的东西」。

### 2.2 看告警的时间分布

```bash
ls -t logs/alerts/ | head -n 5
```

```
alert-CRITICAL-20261010-080004.txt
alert-CRITICAL-20261009-080004.txt
alert-CRITICAL-20261008-080003.txt
alert-CRITICAL-20261007-080004.txt
alert-CRITICAL-20261006-080004.txt
```

看最近的执行记录：

```bash
tail -n 50 logs/daily-check/cron.log
```

每天都是同一行数据库报错，而且每次都附一句
`（上次已是 CRITICAL，不重复发送）`。

那最早是从哪天开始的？翻这个日志的最前面：

```bash
head -n 14 logs/daily-check/cron.log
```

```
==========================================================
 系统巡检  2026-09-22 08:00:01
==========================================================

  所有检查项正常

    · 磁盘     使用率 28%，剩余 27G
    · inode  使用率 4%
    · 服务     5 个服务全部运行中
    · 心跳     42 秒前更新（属主 chenjin:chenjin）
    · 内存     可用 1302 MB
    · 负载     0.03
    · 流量     日均预算用量 0.03%
    · 占用     最大目录: 系统日志 43 MB (/var/log)
```

2026-09-22 08:00 那次巡检是全 OK，而且根本没有「数据库」这一项。
那天自己的日志文件（`logs/daily-check/2026-09-22.log`）末尾写着
`结果: OK（警告 0，严重 0）`。

而当天下午手动跑的那几次，数据库已经在了，而且是绿的。
从 09-23 08:00 起，每天都是数据库报错。

数据库检查是在 09-22 白天接进 `daily-check.py` 的。
第一次真正交给 cron 跑是 09-23 08:00 —— 当场红，此后再没绿过。
换句话说，这个功能从在 cron 里生效的第一天就是坏的，
只是「手动跑」永远看不出来。

### 2.3 找两个环境的差异

同一个脚本，手动绿、cron 红，第一反应是环境变量。先看 crontab：

```bash
crontab -l
```

```
0 8 * * * /usr/bin/python3 /root/ops-lab/lib/daily-check.py >> ...
30 3 * * * /root/ops-lab/bin/db/backup-mariadb.sh >> ...
*/10 * * * * /usr/bin/python3 /root/ops-lab/bin/visit-stats.py --output ...
```

**没有任何 `PATH=` 行。**

于是照 cron 的默认 PATH 复现。cron 给用户 crontab 的默认 PATH 是
`/usr/bin:/bin`，原样抄过来：

```bash
env -i HOME=/root PATH=/usr/bin:/bin bash bin/db/check-mariadb.sh; echo "rc=$?"
```

```
【7】网络暴露（安全）
  ⚠️  未监听 3306

 结果: 异常（1 项失败，0 项警告）
rc=2
```

**复现了。** 换回完整 PATH 立刻转绿，两次只差 PATH 一项：

```bash
env -i HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    bash bin/db/check-mariadb.sh; echo "rc=$?"
```

```
【7】网络暴露（安全）
  ✅ 仅绑定本机（127.0.0.1:3306）

 结果: 全部正常
rc=0
```

### 2.4 定位缺失的命令

```bash
env -i HOME=/root PATH=/usr/bin:/bin bash -c 'command -v ss || echo MISSING'
```

```
MISSING
```

```bash
ls -l /usr/sbin/ss
```

```
-rwxr-xr-x. 1 root root 199720 Oct 21  2024 /usr/sbin/ss
```

`ss` 在 `/usr/sbin`，而 cron 的默认 PATH 里没有 `/usr/sbin`。

再对比其他定时入口 —— 它们都写对了：

```bash
grep -n 'PATH' /etc/crontab
```

```
2:PATH=/sbin:/bin:/usr/sbin:/usr/bin
```

```bash
grep -rn 'PATH' /etc/cron.d/
```

```
/etc/cron.d/ops-lab-traffic:9:PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
/etc/cron.d/ops-lab-daily-report:3:PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

`/etc/crontab` 自带 PATH，`/etc/cron.d/*` 由管理员显式写了 PATH，
只有用户 crontab 是裸的。 所以走 `/etc/cron.d` 的 `traffic-check.sh`
一直正常，唯独走用户 crontab 的 `daily-check.py` 踩了这个坑。
同一个 PATH 问题，取决于从哪个入口进。

---

## 3. 根因

`bin/db/check-mariadb.sh` 第 7 节用了裸命令：

```bash
BIND=$(ss -tlnp 2>/dev/null | grep ':3306' | awk '{print $4}' | head -1)
```

脚本头部的 `set -uo pipefail` 也救不了它：`ss` 不存在时后面还有管道，
整条命令依旧返回 0，`BIND` 只是空字符串。于是形成这条链：

```
cron 的 PATH 不含 /usr/sbin
  → ss 找不到
  → BIND 为空
  → 落进「未监听 3306」分支，FAIL+1
  → 脚本 exit 2
  → daily-check.py 把 rc∉{0,1} 判为 CRITICAL
  → 一个健康的数据库被报成严重故障
```

有两个细节把伤害放大了。

一是那行输出自相矛盾。 它打的是 `⚠️ 未监听 3306`（警告符号），
却把计数加进了 `FAIL`、退出码给了 2。显示说是警告，退出码说是异常。

二是下游拿不到细节。 `daily-check.py` 在 `rc==2` 时，
去输出里正则抓 `❌` 开头的行拼成摘要 —— 可这次输出里
一个 `❌` 都没有（`grep -c '❌'` 结果是 0），
于是摘要只剩「检查失败」四个字。
连续 18 天，每天都是这四个字，没人看得出是 PATH 问题。

---

## 4. 修复步骤

### 4.1 复现

```bash
cd /root/ops-lab
env -i HOME=/root PATH=/usr/bin:/bin bash bin/db/check-mariadb.sh; echo "rc=$?"
```

预期：`【7】⚠️ 未监听 3306`、`结果: 异常（1 项失败，0 项警告）`、`rc=2`。

### 4.2 让脚本自带 PATH

改 `bin/db/_common.sh`，在文件头注释块之后、`DB_CONF=...` 之前插入：

```bash
# ── 统一 PATH ──
# cron 执行用户 crontab 时默认 PATH 是 /usr/bin:/bin，不含 /usr/sbin，
# 裸调 ss 会静默失败。脚本不应依赖调用方（登录 shell / cron / systemd）的 PATH。
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
```

用 `${PATH:-}` 而不是 `$PATH`，因为这些脚本带 `set -u`，
PATH 未定义时只有前者安全。

`check-mariadb.sh` 在第 12 行 `source _common.sh`，
而 `ss` 到第 107 行才用，顺序天然成立，不用再动它。

### 4.3 重写 check-mariadb.sh 第 7 节

把「工具缺失」「确实没监听」「绑到了公网」拆成三种情况分开报：

```bash
echo
echo "【7】网络暴露（安全）"
SS_BIN=$(command -v ss || true)
if [ -z "$SS_BIN" ]; then
    echo "  ${C_BAD}❌ 无法检查：找不到 ss 命令（当前 PATH=$PATH）${C_RST}"
    FAIL=$((FAIL + 1))
else
    BIND=$("$SS_BIN" -tlnp 2>/dev/null | grep ':3306' | awk '{print $4}' | head -1)
    if [ -z "$BIND" ]; then
        echo "  ${C_BAD}❌ 未监听 3306${C_RST}"
        FAIL=$((FAIL + 1))
    elif echo "$BIND" | grep -q '127\.0\.0\.1:3306'; then
        echo "  ${C_OK}✅ 仅绑定本机（$BIND）${C_RST}"
    else
        echo "  ${C_BAD}❌ 绑定地址异常: $BIND（可能暴露到公网）${C_RST}"
        FAIL=$((FAIL + 1))
    fi
fi
```

### 4.4 其余脚本同样声明 PATH

`bin/healthcheck.sh`、`bin/99-reset-for-practice.sh`、`bin/deadman-revert.sh`
这三个脚本不 source `_common.sh`，各自在文件头部加同一行 `export PATH=...`。

用「声明 PATH」而不是把命令改成 `/usr/sbin/ss`：
`/usr/sbin` 下还住着 `nginx`、`iptables`、`sysctl`、`logrotate`、`useradd`
一批命令，补齐 PATH 一次解决同族问题；
硬编码绝对路径只是把下一个坑推迟。

### 4.5 daily-check.py：rc==2 时带出原因

```python
    else:
        # 提取失败项；抓不到明细时必须把原因带出来，
        # 否则告警只剩"检查失败"四个字，无从排查
        fails = re.findall(r'❌\s*([^\n]+)', out)
        msg = '检查失败'
        if fails:
            msg += '：' + '；'.join(f.strip() for f in fails[:3])
        elif err.strip():
            msg += '（退出码 %d，stderr: %s）' % (rc, err.strip().splitlines()[0][:120])
        else:
            tail = [ln.strip() for ln in out.splitlines() if ln.strip()][-3:]
            msg += '（退出码 %d，输出末尾: %s）' % (rc, ' / '.join(tail)[:180])
        rep.add('CRIT', '数据库', msg)
```

### 4.6 用户 crontab 补 PATH

```bash
crontab -l > /tmp/crontab.bak
( echo 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'; crontab -l ) | crontab -
crontab -l | head -5
```

`PATH=` 行不能加引号，且必须在所有作业行之前；
内容与 `/etc/cron.d/ops-lab-traffic` 里已有的那行保持一致。

### 4.7 语法检查

```bash
bash -n bin/db/_common.sh && bash -n bin/db/check-mariadb.sh \
  && bash -n bin/healthcheck.sh && bash -n bin/99-reset-for-practice.sh \
  && bash -n bin/deadman-revert.sh && echo "bash -n ALL OK"
python3 -m py_compile lib/daily-check.py && echo "py_compile OK"
```

### 4.8 验收（必须站在 cron 的 PATH 上）

```bash
env -i HOME=/root PATH=/usr/bin:/bin bash bin/db/check-mariadb.sh; echo "rc=$?"
env -i HOME=/root PATH=/usr/bin:/bin bash bin/healthcheck.sh
env -i HOME=/root PATH=/usr/bin:/bin /usr/bin/python3 lib/daily-check.py --no-mail --verbose | tail -20
```

告警细节用故障注入验证：

```bash
printf '#!/bin/bash\necho "simulated failure" >&2\nexit 2\n' > /tmp/always2.sh && chmod +x /tmp/always2.sh
cp lib/daily-check.py /tmp/dc-backup.py
sed -i 's|^DB_CHECK_SCRIPT = .*|DB_CHECK_SCRIPT = "/tmp/always2.sh"|' lib/daily-check.py
env -i HOME=/root PATH=/usr/bin:/bin /usr/bin/python3 lib/daily-check.py --no-mail | grep -E '数据库|结果'
cp /tmp/dc-backup.py lib/daily-check.py
```

### 4.9 写进规范并做同类自查

在 `docs/SHELL-CONVENTIONS.md` 增加「脚本必须自带 PATH」一节。
再把四条 cron 入口逐个用 cron 的 PATH 跑一遍，找 `command not found`：

```bash
for c in "bash bin/db/backup-mariadb.sh" "python3 bin/visit-stats.py --stdout" \
         "bash bin/traffic-check.sh" "python3 lib/daily-check.py --no-mail"; do
  echo "== $c"
  env -i HOME=/root PATH=/usr/bin:/bin bash -c "$c" 2>&1 \
    | grep -iE 'not found|No such file' || echo "  ok"
done
```

### 4.10 提交

```bash
git add bin/db/_common.sh bin/db/check-mariadb.sh bin/healthcheck.sh \
        bin/99-reset-for-practice.sh bin/deadman-revert.sh \
        lib/daily-check.py docs/SHELL-CONVENTIONS.md
git diff --cached --stat
git commit -F /root/ops-lab/_j6_msg.txt
```

---

## 5. 遇到的问题与解决方案

### 问题 1：手动跑永远绿，cron 永远红

第一次手工执行 `check-mariadb.sh` 得到「全部正常」，
与自动巡检的 CRITICAL 直接冲突，一度以为自动巡检的日志是旧的。

**解决**：不要在两个入口之间反复比对，先把「入口」本身固定住。
用 `env -i HOME=/root PATH=/usr/bin:/bin` 把 cron 的环境抄过来再跑，一次就复现。
之后**所有验收都带这个前缀**，不再采信登录 shell 的结果。

### 问题 2：第一次验收没有转绿

改完 `daily-check.py` 先跑了一次：

```bash
env -i HOME=/root PATH=/usr/bin:/bin bash bin/db/check-mariadb.sh
```

仍然是 `⚠️ 未监听 3306`、`rc=2`。

**原因**：那时还没改 `_common.sh`。PATH 是 `_common.sh` 提供的，
它不改，`check-mariadb.sh` 在 cron 视角下就还是瞎的。

**解决**：在 `_common.sh` 里加上 `export PATH=...` 之后，
同一条命令从 `rc=2` 变成 `rc=0`，`【7】` 从 ⚠️ 变成
`✅ 仅绑定本机（127.0.0.1:3306）`。
反过来验证了「修在脚本自带 PATH 这一层」是对的。

### 问题 3：注入测试里那一行还是 ⚠️ 而不是 ❌

按 4.8 的方法把 `grep ':3306'` 改成 `grep ':33069'` 制造「端口不存在」，
期望看到 `❌ 未监听 3306`，实际看到的是：

```
【7】网络暴露（安全）
  ⚠️  未监听 3306

 结果: 异常（1 项失败，0 项警告）
```

退出码是对的（2），但符号还是 ⚠️；`grep -c '❌'` 的结果是 **0**。

**原因**：4.3 那一步（重写第 7 节）没有落地，
⚠️/FAIL 的语义矛盾原样保留 —— 显示说警告、计数说失败。

**解决**：这正是 4.3 存在的理由。三种情况拆开之后，
「没查成」和「确实没监听」是两条不同的分支，都打 ❌ 并计入 FAIL，
下游才有可抓的文字。

### 问题 4：healthcheck 的「练习环境」整段消失

用 cron 的 PATH 跑 `healthcheck.sh`，快照里出现：

```
--- 监听端口 ---
bin/healthcheck.sh: line 27: ss: command not found

（中间的内存 / CPU / 流量 / fail2ban 几段略）

--- 练习环境 ---
  练习01 判分: 通过 17 项，未通过 2 项
```

`端口 8081: HTTP 200`、`端口 9090: HTTP 200` 两行**不见了**，
而且没有报错 —— 第 43 行的 `ss` 是在 `if` 条件里用的，
命令找不到时条件直接判为不成立。

**这是这类故障最阴的地方：它不是「报错」，是「少一行」。**
第 27 行还会打一句 `command not found`，第 43 行连这句都没有。

**解决**：同 4.4，在 `healthcheck.sh` 头部声明 PATH。
验收时专门盯「练习环境」这一段 —— 它在，就说明第 43 行的 `ss` 活了。

### 问题 5：仓库根多出一个文件名是 ESC 的文件

`git status --short` 里出现：

```
?? "\033"
```

某个命令把转义符写进了文件名。

**解决**：手打 `rm "\033"` 删不掉 —— 那是反斜杠加 0 加 3 加 3 四个字符。
要用 shell 的 `$'\033'` 生成真正的 ESC 字符：

```bash
ls -lb | grep -i '033'
find . -maxdepth 1 -name $'\033' -print -delete
ls -lb | grep -i '033' || echo "已清除"
```

`ls -lb` 会把不可见字符显示成 `\033`，先用它确认再删。

### 问题 6：练习判分脚本也有同族问题

对比两种 PATH 下的 `labs/01/check.sh`：

```
登录 shell：      19 项全部 ✅
cron 的 PATH：    2 项 ❌ —— nginx 配置语法正确 / 8081 端口正在监听
```

那 2 项是**假 ❌**：配置语法本来是对的、8081 本来就在监听，
只是判定用的 `nginx -t` 和 `ss` 在 cron 的 PATH 下找不到。

**解决**：暂未处理，记入遗留问题。
判分器出现假 ❌ 比漏报更糟 —— 它会把做对的练习判成没做对。

---

## 6. 结论

**两个 PATH 不是一回事。** 登录 shell 的 PATH 是给「人」用的，cron 的
PATH 是给「守护进程」用的，两者不一样。脚本只要依赖了其中之一，就必然
在另一个入口下失效。脚本默认了「外面会给我一个完整的 PATH」，这就是
问题所在。

**两类失败要分开报。** 原来的实现把「找不到命令」和「检查不通过」压在
同一个 `-z "$BIND"` 分支里，于是「我找不到 ss」被伪装成
「数据库端口有问题」。一个查不动的检查，报出来比不报更危险，
因为它会撒谎。

**告警要报得能查。** `rc==2` 时抓不到细节就只剩「检查失败」，这句告警
连续出现 18 天，没有提供任何可排查的线索。

**验收要站在故障的入口上。** 这个 bug 的全部特征就是「登录 shell 永远绿、
cron 永远红」，所有在登录 shell 下做的验收都是假绿。验收环境抄错了，
等于没验收。

---

## 7. 遗留问题

**已经修好的**：

- `bin/db/_common.sh` 统一声明 PATH —— 本次故障的直接原因已消除
- `lib/daily-check.py` 在 `rc==2` 时带出退出码与 stderr
- 用户 crontab 顶部补 `PATH=` 一行
- `docs/SHELL-CONVENTIONS.md` 增加 PATH 规范

**还没做的**：

- `bin/db/check-mariadb.sh` 第 7 节的重写（4.3）——
  ⚠️/FAIL 的语义矛盾仍在
- `bin/healthcheck.sh`（第 27、43 行）、`bin/99-reset-for-practice.sh`
  （第 63、155 行）、`bin/deadman-revert.sh`（第 22 行）的 PATH 声明（4.4）

这两项都不在定时任务里，不影响本次闭环，但属于同一族的潜伏点。

**更远一层的三个高危项**（都在仓库里、都不在任何调度入口，尚未触发）：

| 位置 | 症状 | 后果 |
|---|---|---|
| `bin/deploy.sh:140` | `if nginx -t 2>&1 \| grep -qi emerg` —— `nginx` 找不到时，`if` 把失败当成「条件不成立」，门禁**静默放行** | 坏配置照常发布 |
| `bin/deadman-revert.sh` | 6 个 `sysctl -w` 找不到命令，却照常打印「已恢复原始参数」 | 死人开关以为回滚成功，实际什么都没做 |
| `bin/fail2ban-rollback.sh` | `iptables` / `ipset` 找不到，却打印「剩余 f2b 相关规则: 0」 | 防火墙规则以为清干净了，实际还在 |

`bin/deploy.sh` 那条尤其值得注意：文件头有 `set -euo pipefail` 也拦不住，
因为 `if 命令 | grep` 里的命令失败不会让脚本退出。
这和复盘 008 里 `set -e` 撞上 `systemctl is-active` 是同一族问题。

**其他**：

- `labs/01/check.sh`、`labs/02/check.sh`、`labs/02/reset.sh`、
  `quests/redis/check.sh`、`quests/check-001.sh` 等练习脚本的同类假 ❌
- `at` 排程（死人开关的触发路径）给什么 PATH，尚无样本可验

---

## 8. 后续计划

1. 补齐 4.3 与 4.4：重写 `check-mariadb.sh` 第 7 节；
   给 `healthcheck.sh` / `99-reset-for-practice.sh` / `deadman-revert.sh`
   声明 PATH
2. 按优先级处理三个高危潜伏项，先从 `bin/deploy.sh:140` 的门禁开始
3. 把「脚本在 cron 的 PATH 下必须自洽」固化进 `bin/test-daily-check.sh`，
   用自动化挡住这一族问题 —— 它靠人眼看是防不住的

---

## 9. 协作说明

本文的排查方向与修复步骤由 AI 提供；执行操作、验证结果、判断是否达标由我完成。

过程中有三点印象比较深。

复现比修复重要。一开始手动跑是绿的，很容易停在「数据库其实没事、
是日志看错了」。把 cron 的 PATH 抄过来之后才第一次看到红色，
后面所有判断才站得住。

验收环境必须抄对。这个故障只在 cron 的 PATH 下存在，所以我后来所有
验收都带 `env -i HOME=/root PATH=/usr/bin:/bin`。用登录 shell 验收
怎么测都是绿的，那种绿没有意义。

漏做会被验收自己抓出来。4.3 和 4.4 我没有做，结果 4.8 的注入测试和
healthcheck 快照立刻报了出来（⚠️ 而不是 ❌、「练习环境」整段消失）。
验收用例写对了，它不会因为「我忘了」而放行。
