# 练习 01：部署一个静态站点并用 systemd 管理

**难度**: ⭐⭐☆☆☆ （入门）
**预计时间**: 30-45 分钟
**前置**: 无（这是第一个练习）

---

## 背景故事

你入职一家小公司，需要在一台新服务器上部署内部文档站。
要求：**能开机自启、崩溃能自动恢复、配置改动前有备份**。

这台服务器上**已经有一个生产站点在运行**，你需要在不影响它的前提下
部署一个**新的**站点。

---

## 目标

- [ ] 站点根目录为 `/srv/www/lab01`
- [ ] 站点首页内容包含字符串 `LAB01-OK`
- [ ] nginx 监听 **8081** 端口
- [ ] nginx **开机自启**（enabled）
- [ ] nginx 配置了 `Restart=always`（崩溃后能自动恢复）
- [ ] 存在一个**可执行**的备份脚本 `/root/ops-lab/bin/lab01-backup.sh`，
      能把 `/etc/nginx` 打包到 `/root/ops-lab/backups/` 下

---

## ⚠️ 先说清楚：为什么是 8081 而不是 8080？

**因为 8080 已经被占用了。** 这是本练习的第一个考点。

先自己验证一下：

```bash
ss -tlnp | grep -E ':(80|8080)'
```

你会看到 80 和 8080 都已被现有站点监听。

### 如果你硬要配 8080 会发生什么

nginx **不会报错**，只会给一条警告：

```
$ nginx -t
nginx: [warn] conflicting server name "_" on 0.0.0.0:8080, ignored
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

**"test is successful"** —— 看起来没问题。但实际上：

```bash
$ curl http://127.0.0.1:8080/
# 返回的是【已有生产站点】的页面，不是你的 lab01
```

你的配置**被静默忽略了**。

### 这个现象背后的知识

同一个 `listen` 端口上可以有多个 `server` 块，nginx 用 `server_name` 决定用哪个：

| 情况 | 行为 |
|---|---|
| `server_name` 不同 | 按域名匹配，互不干扰（虚拟主机） |
| `server_name` 相同 | **后者被忽略**，并给出 warning |
| 有 `default_server` 标记 | 未匹配到任何 server_name 时用它 |

本项目现有站点用的是 `server_name _;`（下划线表示"任意/默认"）。
如果你也写 `_`，就撞名了。

### 真实运维中的正确做法

遇到端口冲突，**先查清楚谁在用，再决定怎么办**：

```bash
# 1. 谁在用这个端口
ss -tlnp | grep :8080
# 或
lsof -i :8080

# 2. 查这个进程属于哪个服务
systemctl status nginx
ps -eo pid,cmd | grep <PID>

# 3. 判断能不能动它
#    - 是别人的服务？→ 换端口，别动
#    - 是自己的测试服务？→ 可以停，但要记录
```

**绝不要**直接 `kill` 掉占端口的进程 —— 那可能是生产服务。

本练习的正确选择：**用 8081，不碰生产站点。**

---

## 你可以用的工具

项目里已有的工具（**建议先读懂它们**）：

```bash
/root/ops-lab/bin/backup-config.sh    # 备份脚本范例
/root/ops-lab/bin/healthcheck.sh      # 巡检脚本
systemctl status nginx                # 服务状态
nginx -t                              # 校验配置（改完必做）
ss -tlnp                              # 查看监听端口
curl http://127.0.0.1:8081/           # 验证站点
```

---

## 必须遵守的工程习惯

1. **改配置前先备份**
2. **改完必须 `nginx -t`**
3. **用 `reload` 而不是 `restart`**
4. **改完要验证**（不能只看命令有没有报错）
5. **⚠️ 注意 `nginx -t` 的 warning** —— 本练习的关键点就是：
   **warning 可能意味着你的配置被忽略了**

---

## 判分

```bash
lab check 01
```

判分检查**系统实际状态**，不检查你敲过什么命令。
其中一项会**真的 kill 掉 nginx 进程**，验证它能否自动恢复 ——
因为"配置里写了 `Restart=always`"和"真的会自愈"是两件事。

---

## 进阶问题（做完后思考）

1. `Restart=always` 和 `Restart=on-failure` 的区别？何时用哪个？
2. 为什么 `reload` 不中断服务，而 `restart` 会？
3. `RestartSec=2s` 改成 `100ms` 会有什么问题？
4. **`nginx -t` 输出 warning 但有 "test is successful" 时，服务能正常工作吗？**
5. 如果两个 server 块都监听 80，但 `server_name` 不同，会发生什么？

**第 4 题的答案就是你刚才踩的坑。**
参考：`/root/ops-lab/docs/postmortem/002-nginx-config-validate.md`

---

## 完成后

```bash
lab check 01
lab reset 01     # 重置，再做一遍
```

**建议至少做两遍**：第一遍看说明，第二遍不看说明独立完成。
