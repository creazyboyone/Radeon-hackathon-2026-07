# hive-env.sh - Hive 4.2.0 on Java 21 的 JVM flags + Tez 集成
#
# Hive 4.2.0 必须用 Java 21 运行（class file version 65.0）
# JLine 3.25.0 FfmTerminalProvider 用 Java 21 preview FFM API, 需开 preview + native-access
# TEZ_CONF_DIR 指向 tez-site.xml (否则 tez.lib.uris 读不到, 报 Invalid configuration of tez jars)

# 强制使用 Java 21
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
export PATH=$JAVA_HOME/bin:$PATH

# Java 21 需要的选项
export JAVA_TOOL_OPTIONS="--enable-preview --enable-native-access=ALL-UNNAMED"
export TEZ_CONF_DIR=/opt/tez/conf
