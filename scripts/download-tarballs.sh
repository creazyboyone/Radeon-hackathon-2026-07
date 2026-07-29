#!/usr/bin/env bash
# download-tarballs.sh — 下载 Hadoop 集群镜像构建所需的 tarball 包
#
# 用法: bash scripts/download-tarballs.sh
# 产出: deploy/image/tarballs/ 下 7 个文件 (~2GB)
# 来源: 优先从 8.148.228.51 服务器下载 (国内快), 回退 Apache 官方
set -euo pipefail

TARBALLS_DIR="$(cd "$(dirname "$0")/.." && pwd)/deploy/image/tarballs"
mkdir -p "$TARBALLS_DIR"
cd "$TARBALLS_DIR"

# 远程服务器 (优先, 国内快)
REMOTE_URL="http://8.148.228.51/repo/tarballs"

# Apache 官方 (回退)
declare -A OFFICIAL=(
  ["hadoop-3.3.6.tar.gz"]="https://archive.apache.org/dist/hadoop/common/hadoop-3.3.6/hadoop-3.3.6.tar.gz"
  ["apache-hive-4.2.0-bin.tar.gz"]="https://archive.apache.org/dist/hive/hive-4.2.0/apache-hive-4.2.0-bin.tar.gz"
  ["apache-tez-0.10.2-bin.tar.gz"]="https://archive.apache.org/dist/tez/0.10.2/apache-tez-0.10.2-bin.tar.gz"
  ["hbase-2.5.15-bin.tar.gz"]="https://archive.apache.org/dist/hbase/2.5.15/hbase-2.5.15-bin.tar.gz"
  ["apache-zookeeper-3.8.4-bin.tar.gz"]="https://archive.apache.org/dist/zookeeper/zookeeper-3.8.4/apache-zookeeper-3.8.4-bin.tar.gz"
  ["openlogic-openjdk-21.0.11+10-linux-x64.tar.gz"]="https://builds.openlogic.com/downloadJDK/openlogic-openjdk/21.0.11+10/openlogic-openjdk-21.0.11+10-linux-x64.tar.gz"
  ["mysql-connector-java-8.0.30.jar"]="https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.30/mysql-connector-java-8.0.30.jar"
)

echo "===== 下载 tarballs 到 $TARBALLS_DIR ====="

TOTAL=${#OFFICIAL[@]}
i=0
for filename in "${!OFFICIAL[@]}"; do
  i=$((i + 1))
  if [ -f "$filename" ]; then
    echo "  [$i/$TOTAL] $filename 已存在, 跳过"
    continue
  fi
  echo "  [$i/$TOTAL] 下载 $filename ..."
  # 优先从远程服务器
  if curl -sf --connect-timeout 5 "$REMOTE_URL/$filename" -o "$filename" 2>/dev/null; then
    echo "    从远程服务器下载完成"
  else
    echo "    远程不可用, 回退官方源 ..."
    wget -q -O "$filename" "${OFFICIAL[$filename]}"
  fi
done

echo ""
echo "===== 验证 ====="
for filename in "${!OFFICIAL[@]}"; do
  if [ -f "$filename" ]; then
    SIZE=$(ls -lh "$filename" | awk '{print $5}')
    echo "  [OK] $filename ($SIZE)"
  else
    echo "  [FAIL] $filename 缺失!"
  fi
done
echo "===== done ====="
