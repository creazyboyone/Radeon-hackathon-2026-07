# hive-env.sh - Hive 4.2.0 on Java 21 的 JVM flags + Tez 集成
#
# 1. JLine 3.25.0 FfmTerminalProvider 用 Java 21 preview FFM API, 需开 preview + native-access
# 2. TEZ_CONF_DIR 指向 tez-site.xml (否则 tez.lib.uris 读不到, 报 Invalid configuration of tez jars)
export JAVA_TOOL_OPTIONS="--enable-preview --enable-native-access=ALL-UNNAMED"
export TEZ_CONF_DIR=/opt/tez/conf
