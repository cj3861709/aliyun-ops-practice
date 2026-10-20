# aliyun-ops-practice

在一台 **2 核 2 GB 的阿里云 ECS** 上，从零搭建并运维一个静态技术展示站。

重点不在于页面本身，而在于**背后的运维体系** —— 发布流程、回滚机制、崩溃自愈、
流量防护、监控告警、数据库运维，以及每一项能力的**验证证据**。

本仓库的命令默认在仓库根目录 `/root/ops-lab` 下执行；需要切换到别的目录时会显式写出 `cd`。

---

## 这个项目在做什么

```
阿里云 ECS（2C2G，Alibaba Cloud Linux 3）
│
├─ 静态站点（nginx）
│   ├─ 原子发布：不可变发布物 + 符号链接切换，零停机
│   ├─ 秒级回滚：版本目录不覆盖，回滚只需切软链接
│   └─ 在线访问：https://myzhiwei.xyz/
│
├─ 服务管理（systemd）
│   ├─ 自建心跳服务（非 root 运行 + 崩溃自愈）
│   └─ 修复发行版 nginx unit 缺失的 Restart 策略
│
├─ 安全防护
│   ├─ fail2ban（3 个 jail：sshd / nginx-limit-req / nginx-botsearch）
│   ├─ nginx 限流限连（实测拦截 11,547 次，正常用户零误伤）
│   └─ SSH 仅密钥认证（实测爆破尝试 0 次成功）
│
├─ 监控告警
│   ├─ 每日巡检（11 项检查 + 邮件告警 + 等级去重）
│   ├─ 流量预算监控（20 GB/月免费额度，分级告警）
│   └─ nginx 日志分析（状态码 / 404 探测 / 429 限流 / 扫描器识别）
│
└─ 数据库（Docker + MariaDB）
    ├─ 容器化部署（内存限制 400MB，端口仅绑本机）
    ├─ 数据持久化（命名卷，容器删除重建数据完好）
    ├─ 自动备份（每日 03:30 + SHA256 校验 + 14 天轮转）
    └─ 崩溃恢复验证（kill -9 后 InnoDB 重放 redo log）
```

---

## 实测数据

| 指标 | 数值 | 验证方式 |
|---|---|---|
| 发布中断时长 | **0 秒** | 发布期间 25,315 请求零失败 |
| 对照组（restart） | 408 失败 | 证明测试方法有效，不是盲区 |
| 回滚耗时 | **1.11 秒** | 期间 20,821 请求零失败 |
| 限流拦截 | 11,547 次 | 正常用户零误伤（双向验证） |
| SSH 爆破成功 | **0 次** | 密码认证已关闭 |
| 崩溃自愈 | 自动恢复 | `kill -9` 注入验证 |
| 数据库崩溃恢复 | **数据无丢失** | InnoDB 从 LSN=45568 恢复到 55529，重放 31 页 |
| 流量占用 | **0.07%** | 日均额度使用率 |

---

## 目录结构

```
bin/                    运维脚本
├── deploy.sh           原子发布（含权限预检、健康检查、失败自动回滚）
├── rollback.sh         秒级回滚
├── healthcheck.sh      系统快照
├── traffic-check.sh    流量预算检查
├── deviation-check.sh  已知偏差审计
├── install-daily-check-cron.sh
├── test-daily-check.sh
└── db/                 数据库运维
    ├── _common.sh              凭据加载（从 /etc/ops-lab/db.conf）
    ├── backup-mariadb.sh       备份（三重校验 + 自动轮转）
    ├── restore-mariadb.sh      恢复（恢复前快照 + 二次确认）
    ├── check-mariadb.sh        8 项健康检查
    └── install-backup-cron.sh  cron 安装器

lib/                    Python 工具
├── generate-site.py    静态站点生成器（零第三方依赖）
├── md2html.py          Markdown 渲染
├── verify-site.py      站点完整性验证
├── log-analysis.py     nginx 日志分析（404/429/扫描器识别/gz 支持）
├── daily-check.py      日常巡检（11 项 + 邮件告警）
├── notify.py           邮件通知模块
├── traffic-monitor.py  流量统计
└── loadtest.py         并发压测器

docs/                   文档
├── postmortem/         8 篇参考复盘（环境搭建过程中的技术记录）
├── KNOWN-DEVIATIONS.md 已知偏差登记表
├── SHELL-CONVENTIONS.md Shell 编写规范
├── SCRIPTING-PITFALLS.md 脚本陷阱清单
└── 运维命令速查.md      按「要回答什么问题」组织的命令参考

my-journal/             我的实操手记（6 篇）
├── 001-部署第一个页面.md
├── 002-systemd托管服务.md
├── 003-logrotate属主故障.md
├── 004-python运维脚本.md
├── 005-变量名冲突.md
└── 006-docker与mariadb.md

labs/                   练习环境
├── 01/                 部署静态站点（可判分）
└── 02/                 故障排查（可判分）

quests/                 任务卡与检查器
```

---

## 核心设计说明

### 原子发布与零停机

```bash
# 发布流程
1. 构建新版本到 /var/www/releases/<timestamp>-<tag>/
2. 权限预检（nginx 用户能否读取）
3. nginx -t 语法校验
4. 切换软链接 /var/www/personal-site → 新版本目录
5. systemctl reload nginx（平滑加载，不中断连接）
6. 健康检查（curl 验证）
7. 失败则自动回滚到上一版本

# 版本目录不覆盖，所以回滚只是把软链接指回去
```

**为什么用符号链接而不是直接覆盖文件**：

| 方式 | 回滚成本 | 中断风险 |
|---|---|---|
| 直接覆盖 | 需要重新复制旧版本 | 复制期间文件不完整 |
| **符号链接切换** | **一次 `ln -sfn`（原子操作）** | **无** |

### 崩溃自愈

发现发行版 nginx unit **缺失 `Restart` 策略**（默认 `Restart=no`），
OOM 后不会自动恢复。用 systemd drop-in 修复：

```ini
# /etc/systemd/system/nginx.service.d/override.conf
[Service]
Restart=always
RestartSec=2s
```

用 `SIGKILL` 注入验证恢复能力。审计全机发现 10 个服务存在同类隐患。

### 流量防护

基于 **20 GB/月** 免费额度设计双层防护：

```nginx
# 第一层：限流限连
limit_req_zone  $binary_remote_addr zone=general:10m rate=10r/s;
limit_req_zone  $binary_remote_addr zone=strict:10m  rate=2r/s;
limit_conn_zone $binary_remote_addr zone=perip:10m;
limit_req_status 429;
limit_conn_status 429;

# 第二层：fail2ban 联动
# 触发限流的 IP 达到阈值后被封禁
```

**双向验证**：攻击流量被拦截 11,547 次，同时正常用户零误伤。

### 监控告警

```bash
# 每日 08:00 自动巡检 11 项
磁盘 / inode / 服务 / 心跳日志 / 内存 / 负载 /
流量预算 / 数据库 / 数据库备份 / 磁盘占用

# 退出码语义：0=正常 1=警告 2=严重
# 邮件发送时机：① 告警（等级变化时）② 恢复（本轮 OK 且上轮非 OK）③ 周报（每周一 09:00 强制一封存活报告）
```

**告警去重的设计**：

| 场景 | 是否发邮件 |
|---|---|
| 正常 → 警告 | 发 |
| 警告 → 警告 | 不发（避免每天重复） |
| 警告 → 严重 | 发 |
| 警告 → 正常 | 不发 |

### 数据库运维

```bash
# 三个设计决策
命名卷而非绑定挂载      → 避免 UID 映射问题
端口仅绑 127.0.0.1      → 公网不可达（已实测）
--memory=400m 禁用 swap → 数据库用 swap 会导致性能崩塌
```

**三个验证实验**：

| 实验 | 验证内容 | 结果 |
|---|---|---|
| 数据持久化 | 容器删除重建，数据是否还在 | 数据完好 |
| 备份恢复 | DROP TABLE 后能否还原 | 完全恢复 |
| **崩溃恢复** | kill -9 后已提交数据是否丢失 | **无丢失** |

---

## 已知偏差登记

运维中有些"不符合最佳实践但经评估接受"的配置。
本项目用登记表管理这些偏差，并有脚本自动审计：

| 编号 | 内容 | 原因 |
|---|---|---|
| DEV-001 | kdump 已禁用 | 2GB 内存机器上开销过大 |
| DEV-002 | `rp_filter=0` | 与 Docker 网络共存的需要 |
| DEV-003 | iptables INPUT 策略保持 ACCEPT | 依靠安全组 + fail2ban |
| DEV-004 | fail2ban jail 级 `ignoreip` 必须留空 | 非空会覆盖 DEFAULT 白名单（踩过坑） |
| DEV-005 | 允许 root SSH 登录 | 仅密钥认证，且是单机练习环境 |
| DEV-006 | `ip_forward=1` | Docker 依赖 |

```bash
# 审计（仓库根目录下执行）
cd /root/ops-lab
./bin/deviation-check.sh
```

---

## 技术栈

| 组件 | 版本 | 说明 |
|---|---|---|
| 操作系统 | Alibaba Cloud Linux 3.2104 | RHEL8 兼容 |
| 内核 | 5.10.134 | |
| Web 服务器 | nginx 1.24.0 | 静态托管 |
| 进程管理 | systemd | 含 drop-in 覆盖 |
| 容器运行时 | Docker 26.1.3 | |
| 数据库 | MariaDB 11.8.9 | 容器化部署 |
| Python | 3.6.8 | 脚本无第三方依赖 |
| 部署模式 | 不可变发布物 + 符号链接 | |

**为什么不用更"现代"的方案**：

- **不用 Docker 跑网站**：单容器相比 nginx 直跑多一层 overlayfs 和网络命名空间开销，
  内存多占 20-30 MB；2 GB 内存要留给其他练习
- **不用 Node/Python 后端**：静态托管内存占用 14.8 MB，而 Node 起步 50-100 MB；
  没有动态内容就不该引入运行时
- **不用宝塔等面板**：掩盖底层原理，面试时说不清"它到底改了什么"

---

## 工程规范

这个项目在过程中沉淀了三份规范文档：

| 文档 | 内容 |
|---|---|
| `SHELL-CONVENTIONS.md` | 变量命名、错误处理、输出格式、退出码约定 |
| `SCRIPTING-PITFALLS.md` | 踩过的脚本陷阱（pipefail + grep、set -e + 状态检查等） |
| `KNOWN-DEVIATIONS.md` | 已知偏差登记与审计方法 |

**规范 8 的一条**（来自实际踩坑）：

> 解析命令输出时，必须区分"数据行"和"提示行"。
> 例如 `dnf check-update` 会输出 `Last metadata expiration check...`，
> 用 `grep -c '^[a-zA-Z]'` 统计包数会把这个提示行也算进去。

---

## 已知问题与限制

| 项目 | 说明 |
|---|---|
| MariaDB 主从复制 | 未实践（单机内存受限） |
| 慢查询分析 | 未做 |
| K8s | 未部署（2GB 内存不足以运行控制平面） |
| 单机架构 | 无高可用，无负载均衡 |

---

## 相关文档

- **我的手记**：https://myzhiwei.xyz/journal.html
  （6 篇实操记录：部署、systemd、故障排查、Python 脚本、日志分析、Docker/数据库）
- **参考复盘**：https://myzhiwei.xyz/postmortem.html
  （8 篇技术记录：nginx、systemd、cron、安全加固）
- **运维命令速查**：`docs/运维命令速查.md`

---

## 本地开发

```bash
# 克隆
git clone git@github.com:<用户名>/<仓库名>.git
cd aliyun-ops-practice

# 凭据配置（不进仓库，需手动创建）
# /etc/ops-lab/mail.conf    邮件告警授权码
# /etc/ops-lab/db.conf      数据库密码

# 运行巡检
python3 lib/daily-check.py --no-mail --verbose

# 生成站点
python3 lib/generate-site.py
python3 lib/verify-site.py
./bin/deploy.sh site/build <tag>
```

---

## 许可

个人练习项目，仅用于学习记录。
