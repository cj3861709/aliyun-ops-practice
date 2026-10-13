# 大数据平台及安装配置

本章从零搭建一套单机伪分布式大数据实验平台：在 VMware 虚拟机上安装 Ubuntu Kylin 18.04，再依次部署 JDK、Hadoop、Spark、PySpark、Maven、MySQL 与 Kafka，最后逐项验证。所有组件装在同一个虚拟机上，用单机方式模拟分布式环境，适合在个人电脑上完成。

<!-- FLOW: 大数据平台软件栈 || Ubuntu 虚拟机 -> JDK 8 -> Hadoop/HDFS -> Spark 与 PySpark -> MySQL 与 Kafka -->

## 1.1 这是什么

大数据平台指一组分工协作的软件：操作系统提供运行环境，JDK 提供 Java 运行时，Hadoop 负责分布式存储与批处理，Spark 负责内存中的分布式计算，MySQL 负责结构化数据存储，Kafka 负责流式数据的传递。它们层层依赖，安装顺序不能颠倒。

- **Linux 发行版与虚拟机**：Linux 发行版是打包好的操作系统，Ubuntu Kylin 是面向中文用户的一种。虚拟机是用软件模拟出来的一台完整计算机，VMware Workstation 负责创建它，虚拟机与宿主机共享硬件但系统彼此独立。
- **`/dev/sda` 分区与 swap**：Linux 把硬盘当作文件来访问，`/dev/sda` 表示第一块硬盘。分区是把一块磁盘切成若干段，便于分别格式化与挂载；swap 是交换分区，物理内存不足时用来暂存不常用的数据。
- **JDK 与 `JAVA_HOME`**：JDK 是 Java 开发工具包，Hadoop、Spark、Kafka 都运行在 JVM 上；`JAVA_HOME` 是环境变量，保存 JDK 的安装目录，供其他程序查找 Java。
- **Hadoop 与 HDFS**：Hadoop 是分布式计算框架，核心是 HDFS 分布式文件系统与 MapReduce 计算模型。HDFS 把文件切成数据块分散存放，对外表现为一个文件系统。
- **NameNode 与 DataNode**：NameNode 是 HDFS 的主节点，记录文件与数据块的对应关系；DataNode 是工作节点，实际保存数据块。
- **Spark**：基于内存的分布式计算引擎，把数据分片后并行处理。相比 MapReduce 少了反复落盘，迭代计算快得多。
- **Anaconda 与 Jupyter**：Anaconda 是集成了 Python 与常用科学计算库的发行包，Jupyter 是运行在浏览器里的交互式笔记本，适合边写边跑 Python 代码。
- **PySpark**：Spark 的 Python 接口，用 Python 编写 Spark 程序。
- **Maven**：Java 项目的构建工具，负责下载依赖、编译源码和打包。
- **MySQL**：关系型数据库，用表保存结构化数据。
- **Kafka**：分布式消息队列，把上游数据按主题分发给下游消费者。

<!-- FLOW: 安装顺序 || 装 Linux -> 扩磁盘 -> 装 JDK -> 装 Hadoop -> 装 Spark -> 装组件 -->

## 1.2 前置条件

- 宿主机为 Windows 10/11 64 位，内存 8 GB 以上，空闲磁盘 80 GB 以上。
- BIOS 中已开启 CPU 虚拟化（Intel VT-x 或 AMD-V），否则 VMware 无法启动 64 位虚拟机。
- 已安装 VMware Workstation Pro 15 或更高版本，并准备 Ubuntu Kylin 18.04 桌面版 ISO。
- 已下载安装包：jdk-8u162-linux-x64.tar.gz、hadoop-3.1.3.tar.gz、spark-3.2.1-bin-hadoop3.2.tgz、apache-maven-3.3.9-bin.zip、kafka_2.12-3.6.1.tgz 和 Anaconda3 安装脚本。
- 统一约定：用户名 hadoop，计算机名 dblab，密码 hadoop；软件安装到 /usr/local；命令默认在普通用户 hadoop 下执行，需要提权时加 sudo。
- 虚拟机网络使用 NAT，能访问外网，apt 与 pip 可以正常联网。

## 1.3 操作步骤

### 1.3.1 安装 Linux 系统

这一节完成虚拟机的创建与操作系统安装，是整章的第一步，后面所有软件都装在这台虚拟机里。Ubuntu Kylin 是 Ubuntu 的中文本地化发行版，18.04 是长期支持版本。执行目录为 `/home/hadoop`。

**（1）新建虚拟机并分配硬件。** 打开 VMware 选「创建新的虚拟机」，类型选「自定义」。安装来源选「稍后安装操作系统」，客户机操作系统选 Linux、版本选 Ubuntu 64 位，虚拟机名称填 UbuntuKylin18，位置选 `D:\vm\UbuntuKylin18`。内存填 4096 MB，处理器 1 个、每处理器 2 核，网络类型选 NAT；磁盘容量填 60 GB 并勾选「将虚拟磁盘存储为单个文件」，磁盘过小后面装不下 Hadoop 与 Spark 的数据目录。
![VMware 新建虚拟机向导首页，提示可以自定义安装方式](IMG:bd1-s11-01.png)

![安装来源选择界面，此处选择稍后安装操作系统](IMG:bd1-s11-03.png)

![指定虚拟磁盘容量的界面，此处填写 60 GB](IMG:bd1-s11-06.png)

![自定义硬件界面，在此调整内存与处理器数量](IMG:bd1-s11-07.png)

**（2）安装操作系统。** 在虚拟机设置里把 CD/DVD 指向 Ubuntu Kylin 的 ISO 文件，然后开启电源。安装类型选「清除整个磁盘并安装 Ubuntu Kylin」由安装程序自动分区；若要手动分区，可按 `/dev/sda1` EFI 512 MB、`/dev/sda2` ext4 挂载 `/` 40 GB、`/dev/sda3` swap 4 GB 分配，`/dev/sda` 表示第一块硬盘，swap 分区在内存不足时充当缓冲区。用户名填 hadoop，计算机名填 dblab，密码填 hadoop。
```bash
cd /home/hadoop
lsblk
free -h
```
![安装类型选择界面，选择清除整个磁盘以自动分区](IMG:bd1-s11-10.png)

![安装最后阶段填写用户名、计算机名与密码的界面](IMG:bd1-s11-12.png)

**（3）首次登录与基础配置。** 安装完成后重启进入桌面，先更新软件源并安装常用工具。net-tools 提供 netstat，openssh-server 提供 ssh 服务，Hadoop 的启动脚本依赖本机 ssh。
```bash
cd /home/hadoop
sudo apt-get update
sudo apt-get install -y vim net-tools openssh-server
sudo systemctl enable ssh
sudo systemctl start ssh
```
![Ubuntu Kylin 的登录界面](IMG:bd1-s11-13.png)

![登录后进入 Ubuntu Kylin 桌面的界面](IMG:bd1-s11-14.png)

**（4）安装 VMware Tools。** 在 VMware 菜单点「虚拟机 → 安装 VMware Tools」，挂载虚拟光盘后执行安装脚本，可启用宿主机与虚拟机之间的复制粘贴与共享文件夹。
```bash
cd /home/hadoop
sudo mkdir -p /mnt/cdrom
sudo mount /dev/cdrom /mnt/cdrom
cd /mnt/cdrom
sudo ./vmware-install.pl -d
```
也可以用 apt 安装开源版本：`sudo apt-get install -y open-vm-tools open-vm-tools-desktop`。

### 1.3.2 VMware 扩展磁盘空间

这一节解决磁盘不够用的问题。虚拟机的磁盘是一块虚拟磁盘文件，可以在关机后从宿主机侧直接扩大容量，再在系统内把新增空间划成新分区使用。执行目录为 `/home/hadoop`。

**（1）在 VMware 中扩展磁盘。** 关闭虚拟机，选「编辑虚拟机设置 → 硬盘 → 扩展」，把容量改大并确认。虚拟机开机或挂起时该按钮不可用，必须先完全关机。
```bash
cd /home/hadoop
lsblk
df -h
```
**（2）新建分区并格式化。** 执行 `sudo fdisk /dev/sda`，在交互界面依次输入 `n`、`p`、分区号 `3`，起始与结束扇区都直接回车，最后输入 `w` 写入并退出。新建分区后让内核重读分区表，再格式化并挂载。
```bash
cd /home/hadoop
sudo fdisk /dev/sda
sudo partprobe /dev/sda
sudo mkfs -t ext4 /dev/sda3
sudo mkdir -p /home/hadoop/extend
sudo mount /dev/sda3 /home/hadoop/extend
df -h
```
**（3）配置开机自动挂载。** 直接写设备名在重启后可能变化，因此用 UUID 标识分区。先执行 `blkid` 查询 UUID，再把下面这一行追加到 `/etc/fstab` 末尾并把 UUID 换成实际输出，最后用 `mount -a` 校验。
```bash
cd /home/hadoop
sudo blkid /dev/sda3
sudo vim /etc/fstab
sudo mount -a
df -h
```
```text
UUID=8f4a1c2e-1b7d-4f5a-9c3e-2d6b8a7f0c11 /home/hadoop/extend ext4 defaults 0 2
```
**（4）LVM 场景的扩展。** 如果安装系统时用了 LVM，新增分区需要依次加入物理卷与卷组，扩展逻辑卷后再刷新文件系统，容量才会生效。
```bash
cd /home/hadoop
sudo pvcreate /dev/sda3
sudo vgextend ubuntu-vg /dev/sda3
sudo lvextend -l +100%FREE /dev/ubuntu-vg/ubuntu-lv
sudo resize2fs /dev/ubuntu-vg/ubuntu-lv
```

### 1.3.3 JDK 的安装

这一节为后面所有 Java 组件准备运行时。JDK 是 Java 开发工具包，Hadoop、Spark、Kafka 都跑在 JVM 上；`JAVA_HOME` 保存 JDK 安装路径，供这些程序查找 Java。执行目录为 `/home/hadoop`。

**（1）解压 JDK。** Oracle JDK 8u162 解压后目录名为 jdk1.8.0_162，注意目录名中是下划线而不是点号。
```bash
cd /home/hadoop/下载
sudo mkdir -p /usr/lib/jvm
sudo tar -zxvf jdk-8u162-linux-x64.tar.gz -C /usr/lib/jvm
sudo chown -R hadoop:hadoop /usr/lib/jvm/jdk1.8.0_162
ls /usr/lib/jvm
```
![用 profile 配置文件设置 JAVA_HOME 等环境变量的文本编辑界面](IMG:bd1-s13-01.png)

**（2）配置环境变量。** 编辑 `/etc/profile`，在末尾追加以下四行。CLASSPATH 指定 Java 类的搜索路径，PATH 决定命令的查找顺序。
```bash
cd /home/hadoop
sudo vim /etc/profile
```
```text
export JAVA_HOME=/usr/lib/jvm/jdk1.8.0_162
export JRE_HOME=${JAVA_HOME}/jre
export CLASSPATH=.:${JAVA_HOME}/lib:${JRE_HOME}/lib
export PATH=${JAVA_HOME}/bin:${PATH}
```
```bash
cd /home/hadoop
source /etc/profile
echo $JAVA_HOME
java -version
javac -version
```
![执行 echo $JAVA_HOME 与 java -version 的验证结果](IMG:bd1-s13-02.png)

**（3）写测试类验证。** 用一个小程序打印 Java 运行时看到的 JAVA_HOME，确认环境变量对程序可见。
```bash
cd /home/hadoop
vim Hello.java
javac Hello.java
java Hello
```
```text
JAVA_HOME = /usr/lib/jvm/jdk1.8.0_162/jre
```

### 1.3.4 Hadoop 的安装和使用方法

这一节安装分布式计算框架 Hadoop 并完成伪分布式配置。Hadoop 的核心是 HDFS 分布式文件系统与 MapReduce 计算模型，其中 NameNode 记录文件与数据块的对应关系，DataNode 实际保存数据块。执行目录为 `/home/hadoop`。

**（1）配置 ssh 免密登录。** Hadoop 的启动脚本要通过 ssh 登录本机启动各进程，配置免密后无需反复输密码。
```bash
cd /home/hadoop
ssh-keygen -t rsa
cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
ssh localhost
```
![ssh localhost 免密登录成功的验证界面](IMG:bd1-s14-03.png)

**（2）解压并写入环境变量。** 解压到 `/usr/local` 后改名为 hadoop，再把 HADOOP_HOME 等写入 `~/.bashrc`，让每个终端都能找到 Hadoop 的命令。
```bash
cd /home/hadoop/下载
sudo tar -zxvf hadoop-3.1.3.tar.gz -C /usr/local
cd /usr/local
sudo mv hadoop-3.1.3/ hadoop
sudo chown -R hadoop:hadoop /usr/local/hadoop
cd /home/hadoop
vim ~/.bashrc
source ~/.bashrc
hadoop version
```
```text
export HADOOP_HOME=/usr/local/hadoop
export HADOOP_INSTALL=$HADOOP_HOME
export HADOOP_MAPRED_HOME=$HADOOP_HOME
export HADOOP_COMMON_HOME=$HADOOP_HOME
export HADOOP_HDFS_HOME=$HADOOP_HOME
export YARN_HOME=$HADOOP_HOME
export HADOOP_COMMON_LIB_NATIVE_DIR=$HADOOP_HOME/lib/native
export HADOOP_OPTS="-Djava.library.path=$HADOOP_HOME/lib/native"
export PATH=$PATH:$HADOOP_HOME/sbin:$HADOOP_HOME/bin
```
![编辑 .bashrc 追加 Hadoop 环境变量的界面](IMG:bd1-s14-02.png)

**（3）修改 Hadoop 配置文件。** 配置文件都在 `/usr/local/hadoop/etc/hadoop`。先在 hadoop-env.sh 中写死 JAVA_HOME，否则启动时会报 JAVA_HOME 未设置。
```bash
cd /usr/local/hadoop/etc/hadoop
vim hadoop-env.sh
```
```text
export JAVA_HOME=/usr/lib/jvm/jdk1.8.0_162
```
![编辑 hadoop-env.sh 指定 JAVA_HOME 的界面](IMG:bd1-s14-05.png)

core-site.xml 指定默认文件系统与临时目录，hdfs-site.xml 设置副本数以及 NameNode、DataNode 的数据目录。
```xml
<configuration>
  <property><name>hadoop.tmp.dir</name><value>file:/usr/local/hadoop/tmp</value></property>
  <property><name>fs.defaultFS</name><value>hdfs://localhost:9000</value></property>
</configuration>
```
```xml
<configuration>
  <property><name>dfs.replication</name><value>1</value></property>
  <property><name>dfs.namenode.name.dir</name><value>file:/usr/local/hadoop/tmp/dfs/name</value></property>
  <property><name>dfs.datanode.data.dir</name><value>file:/usr/local/hadoop/tmp/dfs/data</value></property>
</configuration>
```
![core-site.xml 中设置 hadoop.tmp.dir 与 fs.defaultFS 的界面](IMG:bd1-s14-06.png)

把 `mapred-site.xml.template` 复制为 `mapred-site.xml`，指定计算框架为 YARN；yarn-site.xml 指定 NodeManager 的附属服务。
```xml
<configuration>
  <property><name>mapreduce.framework.name</name><value>yarn</value></property>
</configuration>
```
```xml
<configuration>
  <property><name>yarn.nodemanager.aux-services</name><value>mapreduce_shuffle</value></property>
</configuration>
```
**（4）格式化并启动。** 格式化只在首次安装时执行一次，重复格式化会导致集群 ID 不一致。依次启动三个脚本，再用 jps 查看进程。
```bash
cd /usr/local/hadoop
./bin/hdfs namenode -format
./sbin/start-dfs.sh
./sbin/start-yarn.sh
./sbin/mr-jobhistory-daemon.sh start historyserver
jps
```
```text
NameNode
DataNode
SecondaryNameNode
ResourceManager
NodeManager
JobHistoryServer
```
![执行 jps 后显示的 Hadoop 相关进程](IMG:bd1-s14-12.png)

<!-- FLOW: Hadoop 启动流程 || 格式化 NameNode -> 启动 HDFS -> 启动 YARN -> jps 查看进程 -->

**（5）运行自带示例。** Hadoop 自带 MapReduce 示例程序，用 grep 统计输入文件中符合正则的词，输出目录不能预先存在。浏览器访问 `http://localhost:9870` 可查看 HDFS 概览，Hadoop 2.x 的对应端口是 50070。
```bash
cd /usr/local/hadoop
hdfs dfs -mkdir -p /user/hadoop
hdfs dfs -mkdir input
hdfs dfs -put ./etc/hadoop/core-site.xml input
hadoop jar ./share/hadoop/mapreduce/hadoop-mapreduce-examples-3.1.3.jar grep input output 'dfs[a-z.]+'
hdfs dfs -cat output/*
```
![MapReduce grep 示例作业的运行结果](IMG:bd1-s14-14.png)

### 1.3.5 分布式文件系统 HDFS 介绍及其常用 Shell 命令

这一节练习 HDFS 的日常操作。HDFS 是 Hadoop 的分布式文件系统，把文件切成数据块分散存放，对外表现为一个普通文件系统，用 `hdfs dfs` 系列命令操作。执行目录为 `/home/hadoop`。

**（1）查看命令帮助与目录操作。** 命令很多，先学会查用法。创建用户目录要用 `-p`，否则父目录不存在会报错；`-ls` 列出内容，`-ls -R` 递归列出。
```bash
cd /home/hadoop
hdfs dfs -help
hadoop fs -help put
hdfs dfs -mkdir -p /user/hadoop
hdfs dfs -ls /
hdfs dfs -ls -R /user/hadoop
```
![列出 hadoop fs 全部子命令的帮助界面](IMG:bd1-s15-01.png)

![用 -help 查看某个子命令用法的界面](IMG:bd1-s15-02.png)

**（2）上传、查看与下载。** 从本地文件系统上传用 `-put`，查看内容用 `-cat`，取回本地用 `-get`。示例文件 myLocalFile.txt 有三行内容。
```bash
cd /home/hadoop
echo -e "Hadoop\nSpark\nXMU DBLAB" > myLocalFile.txt
hdfs dfs -put myLocalFile.txt input
hdfs dfs -ls input
hdfs dfs -cat input/myLocalFile.txt
hdfs dfs -get input/myLocalFile.txt ./myLocalFile.bak
```
![执行 hdfs dfs -put 上传文件后查看 HDFS 目录的结果](IMG:bd1-s15-07.jpg)

![用 hdfs dfs -cat 查看 HDFS 文件内容的结果](IMG:bd1-s15-08.jpg)

**（3）复制、移动与删除。** `-cp` 与 `-mv` 作用于 HDFS 内部路径，`-rmdir` 只能删空目录，删除非空目录要用 `-rm -r`。
```bash
cd /home/hadoop
hdfs dfs -cp input/myLocalFile.txt /user/hadoop/copy.txt
hdfs dfs -mv /user/hadoop/copy.txt /user/hadoop/moved.txt
hdfs dfs -rm /user/hadoop/moved.txt
hdfs dfs -rm -r input
```
**（4）安全模式与状态报告。** 安全模式是 HDFS 启动后的保护状态，此时不允许写操作；dfsadmin 用来查看和维护集群状态。
```bash
cd /home/hadoop
hdfs dfsadmin -safemode get
hdfs dfsadmin -safemode enter
hdfs dfsadmin -safemode leave
hdfs dfsadmin -report
```
```text
Safe mode is OFF
```
![查询 HDFS 安全模式状态的输出](IMG:bd1-s15-12.jpg)

**（5）用 Java API 读写 HDFS。** 除了 Shell，程序也可以通过 FileSystem 类访问 HDFS。在 Eclipse 中新建 Java 工程，导入 Hadoop 的 jar 包，用 `create` 与 `open` 读写文件，最后导出为可执行 jar 并提交运行。浏览器访问 `http://localhost:9870` 可以通过网页浏览 HDFS 目录结构。
![在 Eclipse 中新建 Java 工程并编辑 HDFS API 程序](IMG:bd1-s15-16.jpg)

![把写好的 HDFS API 程序导出为可执行 jar 包](IMG:bd1-s15-18.jpg)

```bash
cd /home/hadoop
hadoop jar ./HDFSExample.jar HDFSExample
hdfs dfs -ls /user/hadoop
```
![运行后的 HDFS 文件浏览网页](IMG:bd1-s15-14.jpg)

### 1.3.6 Linux 下 Spark 平台安装

这一节安装 Spark。Spark 是基于内存的分布式计算引擎，把数据分片后并行处理，相比 MapReduce 少了反复落盘，迭代计算快得多。执行目录为 `/home/hadoop`。

**（1）解压安装。** 选择与 Hadoop 3.2 配套的发行包，解压后改名为 spark。
```bash
cd /home/hadoop/下载
sudo tar -zxvf spark-3.2.1-bin-hadoop3.2.tgz -C /usr/local
cd /usr/local
sudo mv spark-3.2.1-bin-hadoop3.2/ spark
sudo chown -R hadoop:hadoop /usr/local/spark
ls /usr/local/spark
```
![Spark 解压安装后 /usr/local/spark 目录下的结构](IMG:bd1-s16-01.jpg)

**（2）配置 spark-env.sh。** 复制模板后写入 JAVA_HOME、HADOOP_HOME 和 SPARK_DIST_CLASSPATH，后者让 Spark 能读到 Hadoop 的类库；再把 SPARK_HOME 与 bin 目录写入 `~/.bashrc` 并生效。
```bash
cd /usr/local/spark/conf
cp spark-env.sh.template spark-env.sh
vim spark-env.sh
cd /home/hadoop
echo 'export SPARK_HOME=/usr/local/spark' >> ~/.bashrc
echo 'export PATH=$PATH:$SPARK_HOME/bin' >> ~/.bashrc
source ~/.bashrc
```
```text
export JAVA_HOME=/usr/lib/jvm/jdk1.8.0_162
export HADOOP_HOME=/usr/local/hadoop
export SPARK_DIST_CLASSPATH=$(/usr/local/hadoop/bin/hadoop classpath)
export SPARK_MASTER_HOST=dblab
export SPARK_MASTER_PORT=7077
export SPARK_MASTER_WEBUI_PORT=8080
```
![编辑 spark-env.sh 写入 JAVA_HOME、HADOOP_HOME 等内容的界面](IMG:bd1-s16-02.png)

**（3）用自带示例与 spark-shell 验证。** SparkPi 用蒙特卡洛方法估算圆周率，能跑通说明本地模式可用；spark-shell 是 Scala 交互式环境，默认以 local[*] 模式启动，可以直接读写文件并做转换。
```bash
cd /usr/local/spark
bin/run-example SparkPi 2>&1 | grep "Pi is roughly"
bin/spark-shell
```
```text
Pi is roughly 3.1434957174785874
```
![spark-shell 启动完成后出现 scala 交互提示符](IMG:bd1-s16-03.jpg)

**（4）启动 standalone 集群并提交作业。** 先起 Master，再起 Worker 并注册到 Master，然后用 spark-submit 提交作业；浏览器打开 `http://dblab:8080` 可查看集群状态。
```bash
cd /usr/local/spark
./sbin/start-master.sh
./sbin/start-worker.sh spark://dblab:7077
jps
./bin/spark-submit --master spark://dblab:7077 \
  --class org.apache.spark.examples.SparkPi \
  ./examples/jars/spark-examples_2.12-3.2.1.jar 100
```
![Spark standalone 模式下的 Master Web UI](IMG:bd1-s16-06.png)

![用 spark-submit 向 standalone 集群提交作业的运行日志](IMG:bd1-s16-07.jpg)

**（5）停止服务。** 停止顺序为先 Worker 后 Master。
```bash
cd /usr/local/spark
./sbin/stop-worker.sh
./sbin/stop-master.sh
```

### 1.3.7 Pyspark 环境配置

这一节让 Spark 支持 Python。PySpark 是 Spark 的 Python 接口，配合 Anaconda 与 Jupyter 可以边写边跑，更适合做数据分析。执行目录为 `/home/hadoop`。

**（1）安装 Anaconda。** Anaconda 集成了 Python 与常用科学计算库，安装脚本按提示一路回车即可，安装目录选 /home/hadoop/anaconda3。
```bash
cd /home/hadoop/下载
bash Anaconda3-2021.05-Linux-x86_64.sh
cd /home/hadoop
source ~/.bashrc
conda --version
python3 --version
```
![执行 Anaconda 安装脚本的终端过程](IMG:bd1-s17-02.png)

**（2）配置 PySpark 环境变量。** 在 `/etc/profile` 中追加 SPARK_HOME、PYTHONPATH 和 PYSPARK_PYTHON。PYTHONPATH 让 Python 能找到 pyspark 模块，PYSPARK_PYTHON 指定执行器使用的 Python 解释器；也可以用 pip 单独安装 PySpark。
```bash
cd /home/hadoop
sudo vim /etc/profile
source /etc/profile
pyspark
pip3 install pyspark==3.2.1 -i https://pypi.tuna.tsinghua.edu.cn/simple
```
```text
export SPARK_HOME=/usr/local/spark
export PYTHONPATH=$SPARK_HOME/python:$SPARK_HOME/python/lib/py4j-0.10.9-src.zip:$PYTHONPATH
export PYSPARK_PYTHON=python3
export PATH=$SPARK_HOME/bin:$PATH
```
![用 pyspark 启动交互式环境后的欢迎信息与提示符](IMG:bd1-s17-24.png)

**（3）配置 Jupyter Notebook。** 生成配置文件并设置监听地址与端口，之后在浏览器中打开；在配置文件中设置 `c.NotebookApp.ip = '*'`、`c.NotebookApp.port = 8888`、`c.NotebookApp.open_browser = False`。
```bash
cd /home/hadoop
jupyter notebook --generate-config
vim ~/.jupyter/jupyter_notebook_config.py
jupyter notebook --no-browser --port=8888
```
![编辑 jupyter_notebook_config.py 设置监听地址与端口的界面](IMG:bd1-s17-10.png)

![浏览器中的 Jupyter 首页，可查看文件列表并新建笔记本](IMG:bd1-s17-12.png)

![新建的 Jupyter 笔记本中执行 Python 代码并输出结果](IMG:bd1-s17-14.png)

**（4）提交一个 Python 作业。** 写一个词频统计程序，用 SparkConf 与 SparkContext 建立上下文，经过 flatMap、map、reduceByKey 后收集结果。日志较多时，把 `conf/log4j.properties.template` 复制为 `conf/log4j.properties`，将 `log4j.rootCategory` 改为 `WARN`。
```bash
cd /home/hadoop
mkdir -p ~/pysparkapp
vim ~/pysparkapp/wordcount.py
cd /home/hadoop/pysparkapp
spark-submit --master local wordcount.py
```
```text
Spark: 16
Hadoop: 4
the: 22
```
![spark-submit 运行 wordcount.py 后输出的词频统计结果](IMG:bd1-s17-28.png)

### 1.3.8 Maven 安装与使用

这一节安装 Maven 并用它编译一个 Hadoop 程序。Maven 是 Java 项目的构建工具，负责下载依赖、编译源码和打包，项目结构由 pom.xml 描述。执行目录为 `/home/hadoop`。

**（1）解压安装。** 解压后改名为 maven，并写入环境变量。
```bash
cd /home/hadoop/下载
sudo unzip apache-maven-3.3.9-bin.zip -d /usr/local
cd /usr/local
sudo mv apache-maven-3.3.9/ maven
sudo chown -R hadoop:hadoop /usr/local/maven
cd /home/hadoop
echo 'export MAVEN_HOME=/usr/local/maven' >> ~/.bashrc
echo 'export PATH=$PATH:$MAVEN_HOME/bin' >> ~/.bashrc
source ~/.bashrc
mvn -v
```
![解压 Maven 后的 /usr/local/maven 目录结构](IMG:bd1-s18-01.png)

**（2）配置国内镜像。** 默认从中央仓库下载依赖很慢，在 `~/.m2/settings.xml` 中配置阿里云镜像。
```bash
cd /home/hadoop
mkdir -p ~/.m2
vim ~/.m2/settings.xml
```
```xml
<settings>
  <mirrors>
    <mirror>
      <id>alimaven</id>
      <mirrorOf>central</mirrorOf>
      <name>aliyun maven</name>
      <url>https://maven.aliyun.com/repository/public</url>
    </mirror>
  </mirrors>
</settings>
```
![编辑 ~/.m2/settings.xml 并配置阿里云镜像的界面](IMG:bd1-s18-02.png)

**（3）编写项目。** 建立 wordcount 工程，pom.xml 中声明 hadoop-client 依赖，版本要与本机 `hadoop version` 的输出一致。源码放在 `src/main/java/WordCount.java`，包含 TokenizerMapper、IntSumReducer 和 main 三部分。
```xml
<dependency>
  <groupId>org.apache.hadoop</groupId>
  <artifactId>hadoop-client</artifactId>
  <version>3.1.3</version>
</dependency>
```
**（4）编译打包并运行。** 打包成功后用 hadoop 提交作业，输出目录不能预先存在。
```bash
cd /home/hadoop/wordcount
mvn clean package -DskipTests
jar tf target/wordcount-1.0.jar | grep WordCount
hdfs dfs -rm -r /user/hadoop/output
hadoop jar target/wordcount-1.0.jar WordCount /user/hadoop/input /user/hadoop/output
hdfs dfs -cat /user/hadoop/output/part-r-00000 | head
```
```text
BUILD SUCCESS
```
![mvn clean package 构建成功并生成 jar 包的界面](IMG:bd1-s18-04.png)

### 1.3.9 MySQL 数据库的安装和基本使用方法

这一节安装关系型数据库 MySQL。MySQL 用表保存结构化数据，在本实训里既保存业务数据，也作为 Hive 等组件的元数据库。执行目录为 `/home/hadoop`。

**（1）安装并启动。** Ubuntu 18.04 官方源中的 mysql-server 就是 5.7 版本，安装过程不弹设置密码的界面；3306 是 MySQL 的默认监听端口，`netstat` 能查到说明服务已对外提供服务。
```bash
cd /home/hadoop
sudo apt-get update
sudo apt-get install -y mysql-server mysql-client
sudo systemctl start mysql
sudo systemctl enable mysql
sudo systemctl status mysql --no-pager
sudo netstat -tlnp | grep 3306
```
```text
Active: active (running)
```
![apt 安装 mysql-server 与 mysql-client 的过程](IMG:bd1-s19-01.png)

![systemctl status 显示 MySQL 服务处于 active (running) 状态](IMG:bd1-s19-02.png)

**（2）安全初始化。** 安装脚本会引导删除匿名用户、禁止 root 远程登录、删除测试库，这些默认配置在实验环境里都是隐患。按提示依次选择不安装密码强度插件（n）、设置 root 密码（hadoop），随后各项选 y。
```bash
cd /home/hadoop
sudo mysql_secure_installation
```
**（3）登录并建库建用户。** Ubuntu 下 root 默认由 auth_socket 插件管理，用 sudo mysql 即可进入。建库时指定 utf8mb4 字符集避免中文乱码，同时创建 localhost 与 `%` 两个来源的用户：前者供本机程序连接，后者供宿主机或其他机器连接。
```bash
cd /home/hadoop
sudo mysql -u root -p
```
![进入 mysql 提示符后查看版本与数据库列表](IMG:bd1-s19-05.png)

```sql
CREATE DATABASE dblab DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE USER 'hadoop'@'localhost' IDENTIFIED BY 'hadoop';
CREATE USER 'hadoop'@'%' IDENTIFIED BY 'hadoop';
GRANT ALL PRIVILEGES ON dblab.* TO 'hadoop'@'localhost';
GRANT ALL PRIVILEGES ON dblab.* TO 'hadoop'@'%';
FLUSH PRIVILEGES;
```
![建库、建用户与授权的 SQL 执行过程](IMG:bd1-s19-06.png)

**（4）表的增删改查。** 建表同样要指定 utf8mb4，这一组语句覆盖后续章节最常用的数据库操作。
```bash
cd /home/hadoop
mysql -u hadoop -p -h localhost
```
```sql
USE dblab;
CREATE TABLE student (id INT NOT NULL AUTO_INCREMENT, name VARCHAR(50) NOT NULL,
  age INT, PRIMARY KEY (id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO student(name, age) VALUES ('zhangsan', 21), ('lisi', 22);
SELECT * FROM student;
UPDATE student SET age = 23 WHERE name = 'lisi';
DELETE FROM student WHERE name = 'lisi';
```
![建表与插入数据的 SQL 执行过程](IMG:bd1-s19-08.png)

![SELECT 查询 student 表的结果](IMG:bd1-s19-09.png)

**（5）导入导出与停止服务。** mysqldump 把库导出为 SQL 文本，配合 gzip 可直接压缩；导入就是把 SQL 重新执行一遍，所以目标库必须先存在。
```bash
cd /home/hadoop
sudo mkdir -p /home/hadoop/sqlbak
mysqldump -u hadoop -p dblab > /home/hadoop/sqlbak/dblab.sql
mysqldump -u hadoop -p dblab student | gzip > /home/hadoop/sqlbak/student.sql.gz
mysql -u hadoop -p dblab < /home/hadoop/sqlbak/dblab.sql
zcat /home/hadoop/sqlbak/student.sql.gz | mysql -u hadoop -p dblab
sudo systemctl stop mysql
```
![mysqldump 导出与重新导入的终端过程](IMG:bd1-s19-11.png)

### 1.3.10 Kafka 安装及使用

这一节安装 Kafka 并完成一次消息收发。Kafka 是分布式消息队列，生产者把消息追加到某个主题，消息按顺序保存在 Broker 上，消费者按自己的进度拉取，两者的速度互不影响。执行目录为 `/home/hadoop`。

**（1）解压安装。** Kafka 3.x 自带 ZooKeeper，无需单独下载。ZooKeeper 负责保存 Kafka 的集群元数据，例如主题、分区与消费者组的信息。
```bash
cd /home/hadoop/下载
sudo tar -zxvf kafka_2.12-3.6.1.tgz -C /usr/local
cd /usr/local
sudo mv kafka_2.12-3.6.1/ kafka
sudo chown -R hadoop:hadoop /usr/local/kafka
ls /usr/local/kafka
```
**（2）确认配置。** 单机环境默认配置基本可用，重点确认监听地址与数据目录；若希望数据持久保存，把 `log.dirs` 改为 `/usr/local/kafka/logs`，避免系统重启清空 /tmp。
```bash
cd /usr/local/kafka
vim config/server.properties
```
```text
broker.id=0
listeners=PLAINTEXT://localhost:9092
log.dirs=/tmp/kafka-logs
zookeeper.connect=localhost:2181
```
**（3）启动 ZooKeeper 与 Kafka。** `-daemon` 表示后台启动，否则终端会被前台进程占住；QuorumPeerMain 是 ZooKeeper 的进程名，Kafka 是消息代理的进程名。
```bash
cd /usr/local/kafka
sudo mkdir -p /tmp/zookeeper
bin/zookeeper-server-start.sh -daemon config/zookeeper.properties
bin/kafka-server-start.sh -daemon config/server.properties
jps
netstat -an | grep 9092
```
```text
QuorumPeerMain
Kafka
```
**（4）创建主题。** 主题是消息的分类单位，生产者往哪个主题发，消费者就从哪个主题收。只有一台 Broker，副本因子设为 1。
```bash
cd /usr/local/kafka
bin/kafka-topics.sh --create --bootstrap-server localhost:9092 \
  --replication-factor 1 --partitions 1 --topic words
bin/kafka-topics.sh --list --bootstrap-server localhost:9092
```
**（5）生产与消费消息。** 先新开一个终端启动控制台生产者，每输入一行回车就发送一条消息，该终端保持打开；再开一个终端从最早位置消费，应原样收到这三行。
```bash
cd /usr/local/kafka
bin/kafka-console-producer.sh --broker-list localhost:9092 --topic words
```
```text
hello kafka
hello hadoop
hello spark
```
```bash
cd /usr/local/kafka
bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 \
  --topic words --from-beginning
```
![消费者终端接收到的三行消息](IMG:bd1-s110-01.png)

<!-- FLOW: Kafka 消息流转 || 生产者 -> Broker 主题 words -> 消费者 -->

**（6）停止服务。** 先停 Kafka 再停 ZooKeeper，顺序不能颠倒，否则 Kafka 无法向 ZooKeeper 注销自己，残留状态会导致下次启动报错。
```bash
cd /usr/local/kafka
bin/kafka-server-stop.sh
bin/zookeeper-server-stop.sh
```

## 1.4 结果验证

按小节列出可观察的判据，每条都能明确判定通过或不通过。

**1.3.1 安装 Linux 系统**

- 能进入 Ubuntu Kylin 18.04 桌面，`lsb_release -a` 输出含 `Ubuntu 18.04`；`whoami` 输出 `hadoop`，`hostname` 输出 `dblab`。
- `ping -c 2 www.baidu.com` 有回包，说明虚拟机已联网。
- `systemctl status ssh --no-pager` 显示 `active (running)`；`vmware-hgfsclient` 有输出，说明 VMware Tools 已生效。

**1.3.2 VMware 扩展磁盘空间**

- VMware「编辑虚拟机设置 → 硬盘」中显示的容量已变大，`lsblk` 能看到新增分区 `/dev/sda3`。
- `sudo blkid /dev/sda3` 返回带 `UUID` 与 `TYPE="ext4"` 的信息，`df -h` 中出现 `/home/hadoop/extend` 这一挂载行。
- `sudo mount -a` 没有任何报错输出，重启虚拟机后 `df -h` 中该挂载点仍然存在。

**1.3.3 JDK 的安装**

- `ls /usr/lib/jvm` 能看到 `jdk1.8.0_162` 目录，`echo $JAVA_HOME` 输出 `/usr/lib/jvm/jdk1.8.0_162`。
- `java -version` 版本号为 `1.8.0_162`，`javac -version` 输出 `javac 1.8.0_162`，说明装的是 JDK 而不是 JRE。
- `which java` 指向 `/usr/lib/jvm/jdk1.8.0_162/bin/java`，运行 `java Hello` 能打印出 `JAVA_HOME = ...` 一行。

**1.3.4 Hadoop 的安装和使用方法**

- `hadoop version` 打印的版本号为 `Hadoop 3.1.3`，`hdfs namenode -format` 输出中出现 `successfully formatted`。
- `jps` 能看到 `NameNode`、`DataNode`、`SecondaryNameNode`，启动 YARN 后额外出现 `ResourceManager`、`NodeManager`、`JobHistoryServer`。
- 浏览器访问 `http://localhost:9870` 能看到 HDFS 概览页，grep 示例作业执行成功且 `hdfs dfs -cat output/*` 有统计结果输出。

**1.3.5 分布式文件系统 HDFS 介绍及其常用 Shell 命令**

- `hdfs dfs -mkdir -p /user/hadoop` 与 `hdfs dfs -put` 执行无报错，`hdfs dfs -ls input` 能看到刚上传的文件名。
- `hdfs dfs -cat input/myLocalFile.txt` 输出三行内容，`hdfs dfs -get` 之后本地目录能看到取回的文件。
- `hdfs dfsadmin -safemode get` 输出 `Safe mode is OFF`；`hdfs dfs -rm -r input` 之后 `hdfs dfs -ls /` 不再显示该目录，Java API 程序读写的文件在 `hdfs dfs -ls` 中可见。

**1.3.6 Linux 下 Spark 平台安装**

- `ls /usr/local/spark` 能看到 `bin`、`sbin`、`conf` 等目录，`bin/run-example SparkPi` 输出接近 3.14 的近似值。
- `bin/spark-shell` 能进入 `scala>` 提示符，`sc.textFile(...).count()` 返回整数结果。
- standalone 模式下 `jps` 能看到 `Master` 与 `Worker`，浏览器打开 `http://dblab:8080` 能看到 Spark Master 页面并列出已注册的 Worker。

**1.3.7 Pyspark 环境配置**

- `echo $SPARK_HOME` 输出 `/usr/local/spark`，`conda --version` 有版本号输出，说明 Anaconda 已进入 PATH。
- `python3 -c "import pyspark; print(pyspark.__version__)"` 输出 `3.2.1`，`pyspark` 能进入交互式提示符并显示 `sc` 变量。
- `jupyter notebook --no-browser --port=8888` 启动后浏览器能打开首页并新建笔记本；`spark-submit --master local wordcount.py` 输出各单词计数，出现 `Spark: 16` 一类结果。

**1.3.8 Maven 安装与使用**

- `mvn -v` 输出 `Apache Maven 3.3.9` 与正确的 Java home。
- `mvn clean package` 输出 `BUILD SUCCESS`，并在 `target/` 下生成 `wordcount-1.0.jar`。
- `jar tf target/wordcount-1.0.jar | grep WordCount` 有输出，说明主类已打进 jar；`hadoop jar target/wordcount-1.0.jar WordCount ...` 作业成功结束，`hdfs dfs -cat output/part-r-00000` 能看到词频统计结果。

**1.3.9 MySQL 数据库的安装和基本使用方法**

- `systemctl status mysql --no-pager` 显示 `active (running)`，`sudo netstat -tlnp | grep 3306` 能看到监听记录。
- `sudo mysql -u root` 能进入 `mysql>` 提示符，`show databases;` 的结果中包含自建的 `dblab`。
- `SELECT * FROM student;` 能查到插入的数据；`mysqldump` 生成的文件非空，导入过程无报错。

**1.3.10 Kafka 安装及使用**

- `jps` 同时显示 `QuorumPeerMain` 与 `Kafka` 两个进程，`netstat -an | grep 9092` 显示该端口处于 LISTEN 状态。
- `kafka-topics.sh --list --bootstrap-server localhost:9092` 的结果中包含 `words`。
- 生产者的三行输入在消费者终端原样出现；执行 `kafka-server-stop.sh` 之后 `jps` 中不再有 `Kafka` 进程。

## 1.5 常见问题

按环节列出实际容易遇到的问题，报错原文照抄，便于直接检索。

**1.3.1 安装 Linux 系统**

| 现象 | 原因 | 处理 |
|---|---|---|
| 开机报错 `此主机支持 Intel VT-x，但 Intel VT-x 处于禁用状态` | BIOS 中未开启 CPU 虚拟化 | 重启进 BIOS 开启 Intel Virtualization Technology，并在「任务管理器 → 性能 → CPU」确认虚拟化已启用 |
| `sudo apt-get update` 报 `Hash 校验和不符` | 软件源同步不完整 | 在「系统设置 → 软件和更新 → 下载自」改为 `mirrors.aliyun.com` 后重新载入 |
| 虚拟机无法联网，宿主机能上网 | NAT 网络在新旧网络环境切换后失效 | 网络适配器改为桥接模式，或在「虚拟网络编辑器」中恢复默认 NAT 设置 |

**1.3.2 VMware 扩展磁盘空间**

| 现象 | 原因 | 处理 |
|---|---|---|
| VMware 里「扩展」按钮灰色不可点 | 虚拟机处于开机或挂起状态 | 完全关机后再操作 |
| `fdisk` 提示 `No free sectors available` | 磁盘未真正扩展，或未让内核重读分区表 | 确认容量已变大，再执行 `sudo partprobe /dev/sda` |
| 重启后新分区不见 | 没写 `/etc/fstab` 或 UUID 写错 | 用 `blkid` 核对 UUID，修正后 `sudo mount -a`；若启动卡在 emergency mode，把 `defaults` 改为 `defaults,nofail` |

**1.3.3 JDK 的安装**

| 现象 | 原因 | 处理 |
|---|---|---|
| `java -version` 报命令未找到 | PATH 没生效或路径写错 | 先 `source /etc/profile`，再 `echo $JAVA_HOME` 核对目录名 |
| 提示找不到 `jdk1.8.0_162` | 解压出的目录名与预期不同 | `ls /usr/lib/jvm` 查看实际名字，按实际名修改 `JAVA_HOME` |
| `java -version` 显示的是系统自带 OpenJDK | PATH 里系统 JDK 排在前面 | 把 `$JAVA_HOME/bin` 放在 PATH 最前，并检查 `~/.bashrc` 是否另设了一套 JDK |

**1.3.4 Hadoop 的安装和使用方法**

| 现象 | 原因 | 处理 |
|---|---|---|
| `Error: JAVA_HOME is not set and could not be found.` | `hadoop-env.sh` 中仍是占位值 | 改成 `/usr/lib/jvm/jdk1.8.0_162` 这类真实路径 |
| 启动时刷屏 `ssh: Could not resolve hostname xxx` | 主机名没有对应的 IP 映射 | `sudo vim /etc/hosts` 追加 `127.0.0.1 dblab` |
| `jps` 看不到 NameNode，或 DataNode 起不来 | 重复格式化导致 clusterID 不一致 | `stop-dfs.sh`、`rm -r ./tmp`、重新 `hdfs namenode -format` 再启动 |

**1.3.5 HDFS 常用 Shell 命令**

| 现象 | 原因 | 处理 |
|---|---|---|
| `-rm` 删非空目录报 `Directory is not empty` | 目录内还有文件 | 改用 `hdfs dfs -rm -r 目录` |
| 提示 `Name node is in safe mode` | NameNode 刚启动，或 DataNode 上报的块数不足 | 等待片刻，确认数据无异常后执行 `hdfs dfsadmin -safemode leave` |
| Web 界面 `:50070` 打不开 | Hadoop 3.x 的端口已改为 9870 | 改用 `http://localhost:9870` |

**1.3.6 Spark 平台安装**

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动报 `JAVA_HOME is not set` | `spark-env.sh` 里没写或写错 | 用绝对路径写 `export JAVA_HOME=/usr/lib/jvm/jdk1.8.0_162` |
| 报 `ClassNotFoundException: org.apache.hadoop...` 或读 HDFS 失败 | `SPARK_DIST_CLASSPATH` 未生效 | 确认 `$(/usr/local/hadoop/bin/hadoop classpath)` 有输出，并把该行放在文件顶部 |
| `start-worker.sh` 报 `Connection refused` | Master 还没起来 | 先 `start-master.sh`，`jps` 看到 Master 后再起 Worker |

**1.3.7 PySpark 环境配置**

| 现象 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'pyspark'` | `PYTHONPATH` 没生效，或没有 pip 安装 | 先 `source /etc/profile`，仍不行就用 pip 安装 pyspark |
| `Exception: Python in worker has different version` | 驱动与执行器的 Python 版本不一致 | 确认 `PYSPARK_PYTHON=python3`，并保证各节点版本一致 |
| 输出大量 INFO 日志 | log4j 默认级别是 INFO | 复制 `conf/log4j.properties.template` 为 `log4j.properties`，把 `log4j.rootCategory` 改为 `WARN` |

**1.3.8 Maven 安装与使用**

| 现象 | 原因 | 处理 |
|---|---|---|
| 卡在 `Downloading from central` 很久 | 未配镜像，或镜像地址不可达 | 检查 `~/.m2/settings.xml` 的 `<mirrorOf>central</mirrorOf>` 与文件路径 |
| 报 `程序包 org.apache.hadoop 不存在` | 依赖缺失，或依赖版本与本机不一致 | 让 pom.xml 里的 `<hadoop.version>` 与 `hadoop version` 输出一致 |
| 提交后报 `Class WordCount not found` | jar 里没有主类，或类名写错 | 用 `jar tf target/wordcount-1.0.jar \| grep WordCount` 检查 |

**1.3.9 MySQL 数据库的安装和基本使用方法**

| 现象 | 原因 | 处理 |
|---|---|---|
| `mysql -u root -p` 提示 Access denied | Ubuntu 的 root 默认用 `auth_socket` 而非密码登录 | 改用 `sudo mysql` 进入，或把 root 改为 `mysql_native_password` |
| `Can't connect to local MySQL server through socket '/var/run/mysqld/mysqld.sock'` | 服务没有启动 | `sudo systemctl start mysql`，仍失败则看 `sudo tail -n 50 /var/log/mysql/error.log` |
| 导入中文出现乱码 | 库或表的字符集不是 utf8mb4 | 建库时指定 `DEFAULT CHARACTER SET utf8mb4`，导入前执行 `SET NAMES utf8mb4;` |

**1.3.10 Kafka 安装及使用**

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动报 `java.net.BindException: Address already in use` | 9092 或 2181 端口被占用，或重复启动了服务 | 用 `jps` 找出残留进程，执行 `bin/kafka-server-stop.sh` 后重试 |
| 报 `JAVA_HOME is not set` | 未配置 JDK 环境变量 | 回到 1.3.3 节配置 `/etc/profile` 并 source |
| 消费者收不到消息 | 主题名写错，或消费者启动早于生产者且未指定从头消费 | 核对主题名，并给消费者加上 `--from-beginning` |
