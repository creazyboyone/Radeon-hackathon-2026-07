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
exec supervisord -n -c "/etc/supervisor/conf.d/supervisord-${NODE_ROLE}.conf"
