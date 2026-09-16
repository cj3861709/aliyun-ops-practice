# 练习 02 —— 渐进式提示

> **强烈建议先自己排查至少 15 分钟再看提示。**
> 这个练习考的就是排查思路，直接看答案等于没练。

### 提示 1：从"现象"开始，不要跳到"修复"

第一步永远是**准确描述现象**：

```bash
curl -v http://127.0.0.1:9090/
```

看返回的**状态码**，它直接指向问题类别：

| 状态码 | 含义 | 方向 |
|---|---|---|
| 000 | 连不上 | 端口/服务/防火墙 |
| 403 | 拒绝访问 | **权限** 或配置的 deny 规则 |
| 404 | 找不到 | 文件路径（root 配置） |
| 502/504 | 后端无响应 | 反向代理的上游 |
| 500 | 服务端错误 | 应用本身 |

**记下你看到的状态码** —— 这是 findings.md 的第一部分。

---

### 提示 2：服务是 active，这是干扰项

```bash
systemctl status nginx
ss -tlnp | grep 9090
```

你会发现：
- 服务 **active (running)** ✅
- 9090 端口 **正常监听** ✅

**"服务活着 + 端口在听" ≠ "网站能访问"。**

这是个很重要的认知：
> Nginx 的 master 进程正常，不代表每个 server 块都能正常工作。
> 一个 server 块的 root 目录访问不了，只会影响那个站点，不影响进程状态。

**继续往下查，不要停在这里。**

---

### 提示 3：去看 error.log —— 最直接的证据

```bash
tail -20 /var/log/nginx/lab02.error.log
```

你应该会看到类似这样的记录：

```
[error] ... open() "/srv/www/lab02/index.html" failed (13: Permission denied)
```

**`13: Permission denied`** —— 这是决定性证据。

（如果日志里还没有内容，先 `curl` 几次产生访问记录再来看。）

**同时也看看 access log**：

```bash
tail -5 /var/log/nginx/lab02.access.log
```

它会显示返回的**状态码**，和 curl 看到的应该一致。

---

### 提示 4：权限问题的关键认知

**Nginx 的 worker 进程以 `nginx` 用户运行，不是 root。**

```bash
# 确认 worker 以谁的身份运行
ps -eo user,pid,cmd | grep 'nginx: worker'
```

所以 `nginx` 用户必须能：
1. **进入目录**（需要目录的 `x` 权限）
2. **读取文件**（需要文件的 `r` 权限）

检查当前权限：

```bash
stat -c '%A %U:%G %n' /srv/www/lab02 /srv/www/lab02/index.html
```

如果目录是 `700 root:root`，那 nginx 用户**连目录都进不去**，自然读不到文件。

**验证方式（这一步很重要，不要跳过）**：

```bash
# 切换成 nginx 用户，试试能不能读
sudo -u nginx test -r /srv/www/lab02/index.html && echo "可读" || echo "不可读"
sudo -u nginx test -x /srv/www/lab02 && echo "可进入" || echo "无法进入"
```

> **为什么不能只看 `ls -l` 就下结论？**
> 因为权限要沿着**整条路径**检查，任何一级目录缺 `x` 都会失败。
> 用 `namei -l` 可以看完整权限链：
> ```bash
> namei -l /srv/www/lab02/index.html
> ```

---

### 提示 5：正确的修复方式

有两种思路，**理解它们的区别**：

**思路 A：改属主（推荐）**

```bash
chown -R nginx:nginx /srv/www/lab02
chmod 755 /srv/www/lab02
chmod 644 /srv/www/lab02/index.html
```

优点：遵循"文件归服务用户所有"的惯例。

**思路 B：只改权限位**

```bash
chmod 755 /srv/www/lab02
chmod 644 /srv/www/lab02/index.html
```

优点：改动更小。缺点是属主仍是 root，将来 nginx 要写文件（如上传）会失败。

**❌ 不要做的事**：

```bash
chmod -R 777 /srv/www/lab02     # 过度授权！任何用户都能改你的网站内容
```

> `777` 意味着**任何人都能修改**你的网站文件 ——
> 如果服务器被入侵，攻击者可以篡改你的页面。
> 这是新手最常见的安全错误。

**修复后立刻验证**：

```bash
nginx -t                            # 语法没改，但习惯性检查
systemctl reload nginx              # 这里其实不需要 reload（权限是运行时的）
curl -s http://127.0.0.1:9090/      # 应该返回 LAB02-OK
```

**注意**：改权限**不需要** reload nginx ——
权限是文件系统的属性，nginx 每次访问都会重新检查。
这也是一个值得理解的细节。

---

### 提示 6：怎么写出好的 findings.md

面试官问"你怎么排障"时，他期待听到的是**方法论**，不是"我 chmod 了一下"。

好的记录长这样：

```markdown
## 现象

curl http://127.0.0.1:9090/ 返回 403 Forbidden。
systemctl status nginx 显示 active (running)，ss -tlnp 显示 9090 正常监听。

## 排查过程

1. 先确认服务层面正常：服务 active、端口监听 —— 排除"服务挂了"
2. 看状态码 403，判断是权限或访问控制问题，不是路径问题（404）
3. 查 error.log：
   [error] open() "/srv/www/lab02/index.html" failed (13: Permission denied)
   → 明确指向文件权限
4. 检查权限：目录 700 root:root，文件 600 root:root
5. 确认 nginx worker 身份：nginx 用户
6. 用 sudo -u nginx 验证：无法进入目录、无法读取文件 → 假设确认

## 根因

Nginx worker 以 nginx 用户运行，而站点目录权限为 700 root:root，
nginx 用户既无 x 权限进入目录，也无 r 权限读取文件，导致 403。

## 修复

chown -R nginx:nginx /srv/www/lab02
chmod 755 /srv/www/lab02 && chmod 644 /srv/www/lab02/index.html

选择改属主而非只改权限位，因为这是"文件归服务用户所有"的惯例，
将来 nginx 需要写文件时不会再次遇到权限问题。

验证：curl 返回 200 且内容含 LAB02-OK。
```

**注意它包含了**：
- 具体的命令和输出（有证据）
- 排除了什么（"排除服务挂了"）
- 为什么这么改（不只是"改了什么"）
- 验证方法

---

### 提示 7：这个练习和真实工作的关系

**这个故障在真实运维中极其常见。** 典型场景：

- 用 root 部署了代码，忘了改属主 → 服务读不到文件
- 从 tar 包解压出来，权限不对
- 挂载了新磁盘，权限没继承
- SELinux 上下文不对（本项目 SELinux 已关闭，所以没这个坑）

**更重要的收获是这个排查顺序**：

```
现象（状态码）→ 服务层（active?监听?）→ 日志（error.log）→ 具体原因（权限）→ 验证假设 → 修复 → 再验证
```

面试时如果你能清晰地讲出这套顺序，并说明**每一步排除了什么**，
比背出一堆命令要有说服力得多。

---

### 还是没做出来？

告诉我：**你看到的 403？404？还是 000？error.log 里写了什么？**
我会根据你实际看到的东西给针对性引导。
