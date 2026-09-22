# 配置副本

这里的文件是**服务器上生效配置的副本**，用于版本控制和回滚。

## 为什么要有副本

| 问题 | 说明 |
|---|---|
| **`/etc/` 不在 Git 里** | 改了配置没有任何记录 |
| **无法回滚** | 配置改坏只能靠记忆恢复 |
| **无法追溯** | 面试或复查时说不清改了什么 |

**做法**：每次改完 `/etc/` 下的配置，把副本同步到 `conf/` 并提交。

## 目录对照

```
conf/nginx/tomcat-proxy.conf
    → /etc/nginx/conf.d/tomcat-proxy.conf

conf/systemd/tomcat-override.conf
    → /etc/systemd/system/tomcat.service.d/override.conf

conf/tomcat/connectors.txt
    → /etc/tomcat/server.xml 的 Connector 片段（摘要）
```

## 同步方式

```bash
# 从 /etc/ 同步到项目（改完配置后执行）
cp /etc/nginx/conf.d/tomcat-proxy.conf \
   /root/ops-lab/conf/nginx/

cd /root/ops-lab
git add conf/
git commit -m "同步 nginx 配置：<说明>"
```

## 注意

**这里的文件不是"源"，`/etc/` 下的才是。**

副本用于：回溯、对比、恢复、展示。
修改配置时改 `/etc/`，然后同步过来。
