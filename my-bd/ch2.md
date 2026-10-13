# 案例概述

## 2.1 这是什么

本章不写业务代码，只做三件事：把案例的技术栈与数据流定下来，把数据集准备好并核对格式，再把图形化 ETL 工具 Kettle 装好、用它把原始数据文件搬进 HDFS。本章的产出是一条打通的数据入口通道，以及 HDFS 上两个可以直接被后续章节读取的文件 `/input_spark/ratings.dat` 与 `/input_spark/movies.dat`。

案例使用的数据集是 MovieLens 电影评分数据集。它由三个文件组成。`ratings.dat` 是评分表，每行四列，用双冒号 `::` 分隔，形如 `1::1::5::978300760`，依次表示用户编号、电影编号、评分（1 到 5 的整数）和评分时间戳。`movies.dat` 是电影信息表，每行形如 `1::Toy Story (1995)::Animation|Children's|Comedy`，依次表示电影编号、片名（含年份）和用竖线分隔的类别。`personalRatings.txt` 是案例额外提供的当前用户评分文件，格式与 `ratings.dat` 相同，用户编号统一写成 `0`，用来为这位新用户生成个性化推荐。

案例涉及的技术按数据流顺序排列如下。Hadoop 提供分布式文件系统 HDFS，它把数据切成块分散在多台机器的磁盘上并保留多副本，是全部数据文件的存放位置。Spark 是基于内存的分布式计算引擎，把数据分片后并行处理，相比 MapReduce 少了反复落盘，迭代计算快得多，第 3 章到第 9 章的计算都由它完成。MySQL 存放计算结果与业务数据。Kettle 是图形化的 ETL 工具，用来做数据的抽取、转换和装载，本章用它完成本地文件到 HDFS 的搬运。Node.js 是让 JavaScript 跑在服务端的运行时环境，第 7 章、第 8 章用它把推荐结果渲染成网页。

在整体流程里，本章处在数据准备这一步。它前面依赖第 1 章装好的 JDK、Hadoop 与 Spark；它确定的数据集和 HDFS 路径是第 7 章、第 8 章电影推荐系统的输入，第 5 章的 ALS 算法同样直接读取这份 `ratings.dat`，第 3 章用 `movies.dat` 做结构化查询演示。

<!-- FLOW: 案例整体数据流 || 数据源 -> 数据采集 -> HDFS 存储 -> Spark 计算 -> 结果存储 -> 网页展示 -->

## 2.2 前置条件

开始动手前逐条核对以下内容，全部满足后再进入操作步骤。

- JDK 已安装且版本为 1.8，`java -version` 的输出中包含 `1.8.0`。
- Hadoop 已安装并完成伪分布式配置，执行 `jps` 能看到 `NameNode`、`DataNode`、`ResourceManager`、`NodeManager` 四个进程。
- HDFS 可用，`hdfs dfs -ls /` 能正常列出根目录，不报连接错误。
- Spark 已安装，`cd /home/hadoop/spark && ./bin/spark-shell` 能进入交互式 Shell。
- MySQL 已安装并启动，能用 `mysql -uroot -p` 登录，第 3 章与第 8 章要用。
- 磁盘剩余空间大于 2 GB，用于存放原始数据、Kettle 安装包和解压后的目录。
- MovieLens 数据集已下载完成，本地存在 `ratings.dat`、`movies.dat` 与案例提供的 `personalRatings.txt`。
- 会使用基本 Linux 命令 `cd`、`ls`、`unzip`、`head`、`wc`，能区分本地路径 `/home/hadoop/data/...` 与 HDFS 路径 `/input_spark/...`。
- 有一块可用的图形界面或 X11 转发环境，Kettle 的图形化设计器 `spoon.sh` 需要显示窗口。

## 2.3 操作步骤

### 2.3.1 确认整体技术栈与数据流

本小节检查各组件都已就位，并明确数据从原始文件流转到网页的先后顺序。先确认后台进程与安装目录。

```bash
cd /home/hadoop
jps
ls -1
```

```
5344 NameNode
5512 DataNode
5710 ResourceManager
5902 NodeManager
6133 Jps
hadoop-2.7.7
jdk1.8.0_181
mysql
spark-2.4.0-bin-hadoop2.7
```

再确认后续章节反复使用的两个目录约定：原始数据放在本地 `/home/hadoop/data/`，进入计算的数据放在 HDFS 的 `/input_spark/`。

```bash
cd /home/hadoop/hadoop-2.7.7
./bin/hdfs dfs -ls /
```

```
Found 1 items
drwxr-xr-x   - hadoop supergroup          0 2024-03-11 09:20 /input_spark
```

`jps` 只显示当前用户启动的 Java 进程，如果中途用 `su` 切换过账号，要回到启动 Hadoop 的那个账号再执行，否则会误判成服务没起来。

### 2.3.2 准备数据集

本小节把数据集下载到本地并核对每个文件的格式与行数。先下载并解压。

```bash
cd /home/hadoop
mkdir -p data
cd /home/hadoop/data
wget -c https://files.grouplens.org/datasets/movielens/ml-1m.zip
unzip -o ml-1m.zip
ls -l /home/hadoop/data/ml-1m
```

```
-rw-r--r-- 1 hadoop hadoop    171308 Nov  1  2003 movies.dat
-rw-r--r-- 1 hadoop hadoop  24058263 Nov  1  2003 ratings.dat
-rw-r--r-- 1 hadoop hadoop  13453741 Nov  1  2003 users.dat
```

`personalRatings.txt` 不包含在压缩包里，由案例单独提供，把它放到同一个目录。

```bash
cp /home/hadoop/upload/personalRatings.txt /home/hadoop/data/ml-1m/
```

看每个文件的前三行，确认列与分隔符。

```bash
cd /home/hadoop/data/ml-1m
head -3 ratings.dat
head -3 movies.dat
head -3 personalRatings.txt
```

```
1::1::5::978300760
1::3::4::978298709
1::6::5::978299246

1::Toy Story (1995)::Animation|Children's|Comedy
2::Jumanji (1995)::Adventure|Children's|Fantasy
3::Grumpier Old Men (1995)::Comedy|Romance

0::1::5::0
0::32::5::0
0::260::5::0
```

三个文件的字段含义逐条对照如下，这是第 7 章、第 8 章写解析代码的依据。

- `ratings.dat`：用户编号 `::` 电影编号 `::` 评分 `::` 时间戳，评分是 1 到 5 的整数。
- `movies.dat`：电影编号 `::` 片名（含年份）`::` 类别，类别之间用 `|` 分隔。
- `personalRatings.txt`：字段与 `ratings.dat` 相同，第一列固定为 `0`，代表当前这位新用户。

用 `wc -l` 统计行数并记下来，后续章节看到处理结果时好比对。

```bash
cd /home/hadoop/data/ml-1m
wc -l ratings.dat movies.dat personalRatings.txt
```

```
1000209 ratings.dat
  3883 movies.dat
    12 personalRatings.txt
```

评分文件的行数以百万计，不要用 `cat` 直接打印，用 `head`、`wc` 或 `hdfs dfs -cat | head` 分段查看。

### 2.3.3 安装 Kettle

Kettle 是图形化的 ETL 工具，用来做数据的抽取、转换和装载，即把数据从一种来源取出、按规则改造、再放进目标位置。它本身是 Java 写的，启动脚本要调用 JDK，所以必须先有可用的 JDK。本小节完成下载、解压与启动脚本配置。

```bash
cd /home/hadoop
java -version
```

```
java version "1.8.0_181"
Java(TM) SE Runtime Environment (build 1.8.0_181-b13)
Java HotSpot(TM) 64-Bit Server VM (build 25.181-b13, mixed mode)
```

下载 Kettle 安装包并解压到 `/home/hadoop`。

```bash
cd /home/hadoop
wget -c https://sourceforge.net/projects/pentaho/files/Data%20Integration/7.1/pdi-ce-7.1.0.0-12.zip
unzip -o pdi-ce-7.1.0.0-12.zip -d /home/hadoop
ls -d /home/hadoop/data-integration
```

```
/home/hadoop/data-integration
```

如果机器上有多个 JDK 版本，显式指定 Kettle 使用的那个，避免拿到不兼容的版本。

```bash
cd /home/hadoop
export PENTAHO_JAVA_HOME=/home/hadoop/jdk1.8.0_181
echo $PENTAHO_JAVA_HOME
```

```
/home/hadoop/jdk1.8.0_181
```

启动图形化设计器，随后弹出 `Spoon` 窗口。

```bash
cd /home/hadoop/data-integration
./spoon.sh
```

解压目录的完整路径里不要带中文和空格，否则 `spoon.sh` 会因为找不到库文件而启动失败。

### 2.3.4 用 Kettle 把数据文件导入 HDFS

本小节在 Spoon 里新建一个转换，把本地两份数据文件写入 HDFS 的 `/input_spark/`。转换由三个步骤串成：文本文件输入、字段切分、Hadoop 文件输出。

先让 Kettle 认识本机的 Hadoop 集群，把 Hadoop 的配置文件放进来。

```bash
cd /home/hadoop/data-integration/plugins/pentaho-big-data-plugin
ls hadoop-configurations
mkdir -p hadoop-configurations/hadoop-local
cp /home/hadoop/hadoop-2.7.7/etc/hadoop/core-site.xml hadoop-configurations/hadoop-local/
cp /home/hadoop/hadoop-2.7.7/etc/hadoop/hdfs-site.xml hadoop-configurations/hadoop-local/
```

把激活的配置目录改成刚建好的 `hadoop-local`。

```bash
cd /home/hadoop/data-integration/plugins/pentaho-big-data-plugin
sed -i 's/^active.hadoop.configuration=.*/active.hadoop.configuration=hadoop-local/' plugin.properties
grep active.hadoop.configuration plugin.properties
```

```
active.hadoop.configuration=hadoop-local
```

回到 Spoon 界面，按下列顺序操作。菜单「文件」→「新建」→「转换」。左侧「核心对象」树里展开「输入」，把「文本文件输入」拖到画布，双击后在「选中的文件和目录」里加入 `/home/hadoop/data/ml-1m/ratings.dat`，在「内容」页把分隔符填成 `::`，点「预览记录」应看到四列数据。展开「大数据」，把「Hadoop File Output」拖到画布，用 Shift 拖拽连线把两步接起来，在「文件」页的文件夹栏填 `hdfs://localhost:9000/input_spark/`，文件名栏填 `ratings.dat`，在「内容」页把字段分隔符设为 `::`。

按 F5 或点工具栏的播放按钮执行转换，工作区下方会出现执行结果窗口。用同样的方式再建一个转换，把 `movies.dat` 写入 `/input_spark/`，这个文件的分隔符同样是 `::`。

<!-- FLOW: Kettle 导入 HDFS 流程 || 文本文件输入 -> 字段切分 -> Hadoop 文件输出 -> HDFS -->

在无图形界面的环境里，也可以把转换保存为 `.ktr` 文件，再用命令行执行。

```bash
cd /home/hadoop/data-integration
./pan.sh -file=/home/hadoop/data/load_ratings.ktr -level=Basic
```

## 2.4 结果验证

逐条对照以下判据，全部通过说明本章的任务完成。

- `jps` 的输出里同时出现 `NameNode`、`DataNode`、`ResourceManager`、`NodeManager`。
- `cd /home/hadoop/hadoop-2.7.7 && ./bin/hdfs dfs -ls /input_spark` 列出 `ratings.dat` 与 `movies.dat` 两个文件，大小均不为 0。
- `./bin/hdfs dfs -cat /input_spark/ratings.dat | head -3` 输出的三行都是 `用户编号::电影编号::评分::时间戳` 的形态，第一行为 `1::1::5::978300760`。
- `./bin/hdfs dfs -cat /input_spark/ratings.dat | wc -l` 的结果为 `1000209`，与本地 `wc -l` 一致。
- `wc -l /home/hadoop/data/ml-1m/ratings.dat` 输出 `1000209`，`movies.dat` 输出 `3883`，`personalRatings.txt` 输出非零行数且每行第一列都是 `0`。
- 执行 `./spoon.sh` 后弹出标题为 `Spoon` 的图形窗口，能新建转换并看到左侧「核心对象」树。
- `grep active.hadoop.configuration /home/hadoop/data-integration/plugins/pentaho-big-data-plugin/plugin.properties` 输出 `active.hadoop.configuration=hadoop-local`。
- Kettle 转换执行结果窗口里「写入」计数与源文件行数相同，且没有出现红色错误行。

## 2.5 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ls /home/hadoop/data-integration` 报 `No such file or directory` | 安装包未下载完整或解压路径写错 | 用 `ls -l pdi-ce-7.1.0.0-12.zip` 核对文件大小是否接近完整包，重新执行 `unzip -o` 并指定 `-d /home/hadoop` |
| 执行 `./spoon.sh` 报 `Unsupported major.minor version 52.0` | 启动时用的不是 JDK 8 | 设置 `export PENTAHO_JAVA_HOME=/home/hadoop/jdk1.8.0_181` 后重新执行 |
| Kettle 连接 HDFS 报 `Could not connect to: localhost:9000` | 没把 Hadoop 配置文件放进 `hadoop-configurations`，或目录没激活 | 复制 `core-site.xml`、`hdfs-site.xml` 到 `hadoop-configurations/hadoop-local/`，并把 `active.hadoop.configuration` 改成 `hadoop-local` |
| `hdfs dfs -ls /` 报 `Call From ... to localhost:9000 failed on connection exception: java.net.ConnectException: Connection refused` | NameNode 没有启动 | 在 `/home/hadoop/hadoop-2.7.7` 下执行 `./sbin/start-dfs.sh`，再用 `jps` 确认 `NameNode` 在运行 |
| `head -3 ratings.dat` 输出的整行没有被切开，只有一列 | Kettle 或者读取代码里的分隔符写成了单个冒号 | 分隔符必须是两个连续的冒号 `::`，在文本文件输入的「内容」页核对 |
| 上传到 HDFS 的 `ratings.dat` 大小为 0 | 文本文件输入的路径指向了空文件，或转换中途被终止 | 先在「预览记录」里确认能取到数据，再重新执行转换，最后用 `hdfs dfs -cat ... | head` 复核 |
