# Tarballs — 构建镜像所需的安装包

Dockerfile 构建时需要以下安装包放在本目录（`deploy/image/tarballs/`）下。
这些文件因体积较大（~2GB），不纳入 git，需手动下载。

## 所需文件清单

| 文件名 | 版本 | 下载地址 |
|--------|------|----------|
| `hadoop-3.3.6.tar.gz` | 3.3.6 | https://archive.apache.org/dist/hadoop/common/hadoop-3.3.6/hadoop-3.3.6.tar.gz |
| `apache-hive-4.2.0-bin.tar.gz` | 4.2.0 | https://archive.apache.org/dist/hive/hive-4.2.0/apache-hive-4.2.0-bin.tar.gz |
| `apache-tez-0.10.2-bin.tar.gz` | 0.10.2 | https://archive.apache.org/dist/tez/0.10.2/apache-tez-0.10.2-bin.tar.gz |
| `hbase-2.5.15-bin.tar.gz` | 2.5.15 | https://archive.apache.org/dist/hbase/2.5.15/hbase-2.5.15-bin.tar.gz |
| `apache-zookeeper-3.8.4-bin.tar.gz` | 3.8.4 | https://archive.apache.org/dist/zookeeper/zookeeper-3.8.4/apache-zookeeper-3.8.4-bin.tar.gz |
| `openlogic-openjdk-21.0.11+10-linux-x64.tar.gz` | 21.0.11 | https://builds.openlogic.com/downloadJDK/openlogic-openjdk/21.0.11+10/openlogic-openjdk-21.0.11+10-linux-x64.tar.gz |
| `mysql-connector-java-8.0.30.jar` | 8.0.30 | https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.30/mysql-connector-java-8.0.30.jar |

## 一键下载脚本 (Linux/macOS)

```bash
cd deploy/image/tarballs

# Hadoop
wget https://archive.apache.org/dist/hadoop/common/hadoop-3.3.6/hadoop-3.3.6.tar.gz

# Hive
wget https://archive.apache.org/dist/hive/hive-4.2.0/apache-hive-4.2.0-bin.tar.gz

# Tez
wget https://archive.apache.org/dist/tez/0.10.2/apache-tez-0.10.2-bin.tar.gz

# HBase
wget https://archive.apache.org/dist/hbase/2.5.15/hbase-2.5.15-bin.tar.gz

# ZooKeeper
wget https://archive.apache.org/dist/zookeeper/zookeeper-3.8.4/apache-zookeeper-3.8.4-bin.tar.gz

# JDK 21
wget "https://builds.openlogic.com/downloadJDK/openlogic-openjdk/21.0.11+10/openlogic-openjdk-21.0.11+10-linux-x64.tar.gz"

# MySQL Connector
wget https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.30/mysql-connector-java-8.0.30.jar
```

## 注意事项

- 文件名必须与上表完全一致（Dockerfile 中 `COPY` 指令按文件名引用）
- JDK 21 的 tar.gz 解压后目录名为 `openlogic-openjdk-21.0.11+10-linux-x64`，Dockerfile 会自动重命名为 `java-21-openjdk-amd64`
- HBase 使用 JDK 8（系统已预装 `openjdk-8-jdk-headless`），Hive 使用 JDK 21
