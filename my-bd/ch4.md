# Spark 流式计算

## 4.1 这是什么

本章做的是持续到达的数据怎么算。前面章节处理的数据都已经静静地躺在 HDFS 上，读一次、算一次就结束；流式计算的输入是不断产生的数据行，它没有读完之后就结束的时刻。Spark 提供了两条路线来应对这种情况：老一些的是 Spark Streaming，新一些的是 Structured Streaming；本章还会用 Kafka 换掉手工数据源。

Spark Streaming 把输入源按固定长度的时间片切开，每个时间片里的数据攒成一个小批交给 Spark 引擎处理，这个小批称为微批。它对外暴露的编程抽象叫 DStream，即离散化流，本质是一串按时间排列的 RDD，可以对它做 `map`、`reduceByKey` 这类与批处理相同的操作。因为每个微批都走一遍完整的 Spark 作业流程，所以 Spark Streaming 本质上仍是批处理，只是批次小、触发密。

Structured Streaming 把流换了一种眼光来看：数据流被当作一张不断追加行的表，新到达的数据就是往这张表的末尾插入新行，对照第 3 章的 DataFrame API 写查询，引擎负责把查询增量地算到每个微批上。Kafka 是分布式消息队列，负责把上游产生的数据流按主题分发给下游消费者；主题是消息的分类名，生产者往某个主题发布消息，消费者订阅该主题并取走消息，双方不必同时在线，也不需要知道对方的地址。

本章的产出是三个能跑通的例子：用 `nc` 模拟数据源跑通 Spark Streaming 的词频统计、用 Kafka 主题完成一次生产与消费、用 Structured Streaming 写出与 DataFrame 风格一致的流式词频统计。在整体流程里，本章处在实时数据处理这一步，前面依赖第 1 章装好的 Spark 与 Kafka，第 7 章、第 8 章的电影推荐仍走批处理路线。

<!-- FLOW: 流式计算数据流 || 数据源 -> Spark Streaming -> 微批切分 -> 结果输出 -->

## 4.2 前置条件

开始动手前逐条核对以下内容，全部满足后再进入操作步骤。

- JDK 已安装且版本为 1.8，`java -version` 输出中包含 `1.8.0`。
- Spark 已安装，`cd /home/hadoop/spark && ./bin/spark-shell` 能进入交互式 Shell，且 `./bin/run-example` 脚本存在。
- Spark 发行版自带的 Kafka 集成包可用，`ls /home/hadoop/spark/jars | grep kafka` 能列出 `spark-sql-kafka-*` 之类的 jar。
- 系统里有 `nc` 命令，执行 `nc -h` 能打印用法；没有的话先安装 netcat。
- Kafka 安装包已下载完成，本地存在 `kafka_2.11-2.4.0.tgz`。
- 本机 9999、2181、9092 三个端口都没有被别的进程占用。
- Spark 与 Kafka 的版本对应关系已经确认，本章按 Spark 2.4 配 Kafka 2.4 使用。
- 有两个可并排使用的终端窗口，本章的多个小节需要同时观察输入端与输出端。

## 4.3 操作步骤

### 4.3.1 用 nc 起数据源并跑通 Spark Streaming 词频统计

本小节用最省事的方式跑通一条流：一个终端手工打字当数据源，另一个终端做词频统计。开第一个终端，让 `nc` 在 9999 端口持续监听。

```bash
cd /home/hadoop
nc -lk 9999
```

执行后光标停在下一行，终端不再返回提示符，这是正常现象，表示它正在等待输入。开第二个终端，用 Spark 自带示例直接连上去。

```bash
cd /home/hadoop/spark
./bin/run-example streaming.NetworkWordCount localhost 9999
```

```
-------------------------------------------
Time: 1552296600000 ms
-------------------------------------------
```

回到第一个终端敲入 `hello world hello` 并回车，第二个终端在一个微批间隔（默认 1 秒）之后打印统计结果。

```
-------------------------------------------
Time: 1552296601000 ms
-------------------------------------------
(hello,2)
(world,1)
```

再敲一行 `hello spark`，新结果会在下一个时间片里单独打印，旧结果不会被重算，`Time:` 时间戳递增而计数从零开始。`nc` 占着前台，要结束它按 Ctrl+C；示例程序同样按 Ctrl+C 停止。必须先在第二个终端启动计算程序、再到第一个终端打字，顺序反了会丢掉最早到达的数据。

### 4.3.2 理解 DStream 与微批

本小节把 4.3.1 的效果换成自己写的程序，顺便看清 DStream 与微批的关系。DStream 是离散化流的缩写，它把连续的字节流按时间片切开，每一片对应一个 RDD，所以对 DStream 能用的算子与对 RDD 的算子几乎一一对应。在第二个终端启动 Shell。

```bash
cd /home/hadoop/spark
./bin/spark-shell
```

在 Shell 里逐行输入以下内容，建立一个每秒一个批的流式上下文。

```scala
import org.apache.spark.streaming._
val ssc = new StreamingContext(sc, Seconds(1))
val lines = ssc.socketTextStream("localhost", 9999)
val words = lines.flatMap(_.split(" "))
val pairs = words.map(word => (word, 1))
val wordCounts = pairs.reduceByKey(_ + _)
wordCounts.print()
ssc.start()
```

回到第一个终端继续敲词，Shell 里每个批次会打印一次。

```
-------------------------------------------
Time: 1552296720000 ms
-------------------------------------------
(hello,2)
(spark,1)
```

这里的计数只在单个批次内累加，下一个批次从零开始，跨批次累计要用 `updateStateByKey` 这类带状态的操作，或者把每次的结果写出去由外部汇总。`ssc.socketTextStream` 连不上时会静默重试，界面上看不到报错，确认数据源已启动最简单的方法是看批次里有没有输出。

<!-- FLOW: DStream 微批切分 || 输入流 -> 每秒切一片 -> 每片一个 RDD -> 逐批输出 -->

### 4.3.3 安装 Kafka 并创建主题

本小节把数据源从手工打字换成 Kafka。先把安装包解压到固定目录。

```bash
cd /home/hadoop
tar -zxvf kafka_2.11-2.4.0.tgz -C /home/hadoop
ls -d /home/hadoop/kafka_2.11-2.4.0
```

第二条命令应当输出 `/home/hadoop/kafka_2.11-2.4.0`。Kafka 依赖 ZooKeeper 保存集群元数据，先把它以后台方式拉起，再启动 Kafka 服务本身。

```bash
cd /home/hadoop/kafka_2.11-2.4.0
./bin/zookeeper-server-start.sh -daemon config/zookeeper.properties
./bin/kafka-server-start.sh -daemon config/server.properties
jps
```

```
3712 QuorumPeerMain
3841 Kafka
3920 Jps
```

创建一个名为 `wordcount` 的主题，单分区、单副本，然后用 `--list` 核对。

```bash
cd /home/hadoop/kafka_2.11-2.4.0
./bin/kafka-topics.sh --create --zookeeper localhost:2181 --replication-factor 1 --partitions 1 --topic wordcount
./bin/kafka-topics.sh --list --zookeeper localhost:2181
```

```
Created topic wordcount.
wordcount
```

服务端的 `server.properties` 里如果配置了 `advertised.listeners`，必须写成客户端能访问的地址，不然本机连得上、局域网里连不上。

### 4.3.4 Kafka 生产者与消费者验证

本小节用自带的命令行工具做一次最直观的生产与消费。开一个终端充当生产者。

```bash
cd /home/hadoop/kafka_2.11-2.4.0
./bin/kafka-console-producer.sh --broker-list localhost:9092 --topic wordcount
```

光标停下后逐行输入内容，每回车一次即发送一条消息，例如输入 `hello kafka` 与 `spark streaming`。再开一个终端充当消费者，从最早的位置开始读。

```bash
cd /home/hadoop/kafka_2.11-2.4.0
./bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic wordcount --from-beginning
```

```
hello kafka
spark streaming
```

生产者每敲一行，消费者终端几乎立刻出现同一行。两端互不知道对方存在，只约定同一个主题名。不再需要读历史消息时去掉 `--from-beginning`，消费者只读启动之后新到达的消息。用 Spark Streaming 消费这个主题，读法与 4.3.2 的 socket 源只差几行。

```scala
import org.apache.kafka.common.serialization.StringDeserializer
val kafkaParams = Map[String, Object](
  "bootstrap.servers" -> "localhost:9092",
  "key.deserializer" -> classOf[StringDeserializer],
  "value.deserializer" -> classOf[StringDeserializer],
  "group.id" -> "wordcount-group",
  "auto.offset.reset" -> "latest"
)
val stream = org.apache.spark.streaming.kafka010.KafkaUtils.createDirectStream[String, String](
  ssc,
  org.apache.spark.streaming.kafka010.LocationStrategies.PreferConsistent,
  org.apache.spark.streaming.kafka010.ConsumerStrategies.Subscribe[String, String](Array("wordcount"), kafkaParams)
)
stream.map(_.value()).flatMap(_.split(" ")).map((_, 1)).reduceByKey(_ + _).print()
```

`group.id` 相同的多个消费者会分摊同一主题的分区，调试时给每次运行换一个新的组名，否则会读不到历史消息。

<!-- FLOW: Kafka 消息流转 || 生产者 -> 主题分区 -> 消费者 -->

### 4.3.5 用 Structured Streaming 代替 DStream

本小节改用 Structured Streaming 重写同一个词频统计。它把流当成一张不断追加的表，新数据就是往表尾插入新行，查询用第 3 章的 DataFrame API 写，引擎负责增量计算。

```bash
cd /home/hadoop/spark
./bin/spark-shell
```

在 Shell 里输入以下内容，仍然以 9999 端口的 socket 为源。

```scala
val lines = spark.readStream.format("socket")
  .option("host", "localhost")
  .option("port", 9999)
  .load()
val words = lines.as[String].flatMap(_.split(" "))
val wordCounts = words.groupBy("value").count()
val query = wordCounts.writeStream
  .outputMode("complete")
  .format("console")
  .start()
```

在第一个终端继续敲词，Shell 里每个微批打印一张表。

```
-------------------------------------------
Batch: 1
-------------------------------------------
+-----+-----+
|value|count|
+-----+-----+
|hello|    2|
|spark|    1|
+-----+-----+
```

与 4.3.2 相比有两处明显不同：一是代码里没有出现微批这个概念，时间片由引擎按默认间隔切；二是 `outputMode` 显式声明了结果如何输出，`complete` 表示每批重算全量结果，`append` 表示只追加新行，`update` 表示只输出有变化的行。把源换成 Kafka 只需替换读取部分，其余查询不变。

```scala
val kafkaStream = spark.readStream.format("kafka")
  .option("kafka.bootstrap.servers", "localhost:9092")
  .option("subscribe", "wordcount")
  .load()
val lines2 = kafkaStream.selectExpr("CAST(value AS STRING) AS value")
```

查询启动后用 `query.status` 查看运行状态，用 `query.stop()` 结束它，直接退出 Shell 而不 stop 会让作业残留在后台。

## 4.4 结果验证

逐条对照以下判据，全部通过说明本章的任务完成。

- 第一个终端执行 `nc -lk 9999` 后光标停在下一行且不返回提示符；第二个终端执行 `./bin/run-example streaming.NetworkWordCount localhost 9999` 后敲入 `hello world hello`，输出中出现 `(hello,2)` 与 `(world,1)`。
- 继续敲入 `hello spark`，下一个 `Time:` 时间片里出现 `(spark,1)`，且该片内 `hello` 的计数从零重新开始。
- 在 `spark-shell` 里执行 `ssc.start()` 后每个批次都打印一条 `Time:` 分隔线，程序不抛异常。
- `jps` 输出中同时出现 `QuorumPeerMain` 与 `Kafka` 两个进程。
- `./bin/kafka-topics.sh --list --zookeeper localhost:2181` 输出中包含 `wordcount`；`--describe` 的输出中 `PartitionCount` 为 `1`、`ReplicationFactor` 为 `1`。
- 生产者终端输入的每一行，在消费者终端的 5 秒内原样出现，且大小写与空格都不变。
- Structured Streaming 的 `writeStream` 启动后，输出中出现 `Batch: 1`、`Batch: 2` 依次递增，`count` 列随新输入变化。
- 执行 `query.stop()` 后 `query.status` 返回的 `isActive` 为 `false`。

## 4.5 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `java.net.ConnectException: Connection refused` 出现在 `ERROR StreamExecution` 之后 | 9999 端口的 `nc` 没有启动，或端口写错 | 在第一个终端确认 `nc -lk 9999` 正在运行，再核对 `socketTextStream` 里的主机与端口 |
| `java.lang.ClassNotFoundException: kafka.serializer.StringDecoder` | 用了 Kafka 0.8 时代的旧包名 | 改成 `org.apache.kafka.common.serialization.StringDeserializer`，并确认 `KafkaUtils` 来自 `kafka010` 包 |
| `ERROR KafkaConsumer: Connection to node -1 could not be established. Broker may not be available.` | Kafka 服务没起来，或 `advertised.listeners` 配成了客户端访问不到的地址 | 用 `jps` 确认 `Kafka` 在运行；在 `config/server.properties` 里设置 `advertised.listeners=PLAINTEXT://localhost:9092` 后重启 |
| 启动 `nc` 报 `nc: Address already in use` | 9999 端口被上一个没退干净的进程占着 | 执行 `netstat -tlnp | grep 9999` 找到进程号，`kill` 掉之后再启动 |
| `Timed out waiting for a node assignment` 出现在 `kafka-topics.sh --create` | ZooKeeper 没有启动，或 Kafka 连不上 ZooKeeper | 先执行 `./bin/zookeeper-server-start.sh -daemon config/zookeeper.properties`，`jps` 看到 `QuorumPeerMain` 后再建主题 |
| Structured Streaming 的 `start()` 报 `No output operations registered, so nothing to execute`，或长时间没有任何 `Batch` 输出 | 只写了 `writeStream` 的配置而没有调用 `start()`，或源端一直没有新数据 | 补上 `.start()`；在源端敲一行数据，确认 socket 源确实有新数据进来 |
