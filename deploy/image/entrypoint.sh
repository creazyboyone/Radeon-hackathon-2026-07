#!/bin/bash
# entrypoint.sh — 写 ZK myid → 启动 supervisord (PID 1)
# NODE_ROLE 由 docker-compose env 注入: hadoop01 / hadoop02 / hadoop03
set -e
case "${NODE_ROLE}" in
  hadoop01) MYID=1 ;;
  hadoop02) MYID=2 ;;
  hadoop03) MYID=3 ;;
  *) echo "ERROR: NODE_ROLE 未设置或非法 (${NODE_ROLE}), 需要 hadoop01|hadoop02|hadoop03"; exit 1 ;;
esac
mkdir -p /data/zookeeper
echo "${MYID}" > /data/zookeeper/myid

# Hive 4.2 编译 Tez 计划需 Tez jar 在 classpath (tez.lib.uris 只供 AM 用, 编译期要本地 jar)
# 启动前把 Tez jar 软链到 hive/lib (幂等, 容器每次启动都做)
for j in /opt/tez/*.jar; do
  ln -sf "$j" /opt/hive/lib/$(basename "$j") 2>/dev/null || true
done

exec supervisord -n -c "/etc/supervisor/conf.d/supervisord-${NODE_ROLE}.conf"
