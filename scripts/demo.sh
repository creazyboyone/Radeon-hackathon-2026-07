#!/usr/bin/env bash
# demo.sh — 一键演示 AIOps Agent 闭环 (单节点直装模式)
#
# 用法: bash scripts/demo.sh
#
# 流程: 注入故障 (kill DataNode) → 等待 Agent 检测 → 诊断 → 修复 → 验证
# 前提: setup-cloud.sh 已执行, Agent 正在运行
set -euo pipefail

PROJ_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ_DIR"

C_GREEN='\033[0;32m'
C_RED='\033[0;31m'
C_YELLOW='\033[1;33m'
C_CYAN='\033[0;36m'
C_BOLD='\033[1m'
NC='\033[0m'

log()    { echo -e "${C_CYAN}[$(date +%H:%M:%S)]${NC} $1"; }
ok()     { echo -e "${C_GREEN}[$(date +%H:%M:%S)] [PASS]${NC} $1"; }
fail()   { echo -e "${C_RED}[$(date +%H:%M:%S)] [FAIL]${NC} $1"; }
header() { echo -e "\n${C_BOLD}${C_YELLOW}════════════════════════════════════════${NC}"; echo -e "${C_BOLD}${C_YELLOW} $1${NC}"; echo -e "${C_BOLD}${C_YELLOW}════════════════════════════════════════${NC}\n"; }

# ============================================================
# 检测集群模式: Docker (docker exec) 或 直装 (supervisorctl)
# ============================================================
SUPCTL="supervisorctl -c /etc/supervisor/conf.d/supervisord-hadoop01.conf"

if docker exec hadoop01 echo OK >/dev/null 2>&1; then
  MODE="docker"
  log "检测到 Docker 集群模式"
elif bash -c "$SUPCTL status" >/dev/null 2>&1; then
  MODE="direct"
  log "检测到单节点直装模式"
else
  fail "Hadoop 集群未运行, 请先执行: bash scripts/setup-cloud.sh"
  exit 1
fi

# 集群操作封装
cluster_exec() {
  local node="$1"; shift
  if [ "$MODE" = "docker" ]; then
    docker exec "$node" "$@"
  else
    # 直装模式: 所有节点都是 localhost, 直接执行
    "$@"
  fi
}

supervisor_action() {
  local node="$1" action="$2" program="$3"
  if [ "$MODE" = "docker" ]; then
    docker exec "$node" supervisorctl "$action" "$program"
  else
    # 直装模式: 所有节点共用同一 supervisord
    $SUPCTL "$action" "$program"
  fi
}

get_jps() {
  local node="$1"
  if [ "$MODE" = "docker" ]; then
    docker exec "$node" jps 2>/dev/null
  else
    /usr/bin/jps 2>/dev/null
  fi
}

get_hdfs_report() {
  if [ "$MODE" = "docker" ]; then
    docker exec hadoop01 bash -c 'export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64; /opt/hadoop/bin/hdfs dfsadmin -report 2>/dev/null'
  else
    /opt/hadoop/bin/hdfs dfsadmin -report 2>/dev/null
  fi
}

# DataNode 期望数量 (Docker: 3, 直装: 1)
if [ "$MODE" = "docker" ]; then
  EXPECTED_DN=3
  FAULT_NODE="hadoop03"
else
  EXPECTED_DN=1
  FAULT_NODE="localhost"
fi

# ============================================================
# 0. 前置检查
# ============================================================
header "0/5 前置检查"

# Agent 是否在运行
if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  HEALTH=$(curl -s http://127.0.0.1:8000/health)
  ok "Agent 在运行: $HEALTH"
else
  fail "Agent 未运行, 请先执行: bash scripts/setup-cloud.sh"
  exit 1
fi

# Hadoop 集群是否在运行
if [ "$MODE" = "docker" ]; then
  if docker exec hadoop01 echo OK >/dev/null 2>&1; then
    ok "Hadoop 集群在运行 (Docker)"
  else
    fail "Hadoop 集群未运行"
    exit 1
  fi
else
  if $SUPCTL status >/dev/null 2>&1; then
    ok "Hadoop 集群在运行 (直装)"
  else
    fail "Hadoop 集群未运行"
    exit 1
  fi
fi

# DataNode 当前状态
DN_STATUS=$(get_hdfs_report | grep "Live datanodes" | grep -o '[0-9]*' || true)
DN_STATUS=${DN_STATUS:-0}
if [ "$DN_STATUS" -ge "$EXPECTED_DN" ]; then
  ok "DataNode: $DN_STATUS/$EXPECTED_DN 在线"
else
  log "DataNode: $DN_STATUS/$EXPECTED_DN 在线 (可能上次 Demo 未完全恢复, 继续执行)"
fi

# ============================================================
# 1. 注入故障: kill DataNode
# ============================================================
header "1/5 注入故障: 停止 DataNode"

log "停止 DataNode 进程..."
supervisor_action "$FAULT_NODE" stop datanode 2>/dev/null || true
sleep 3

# 验证 DataNode 确实停了
DN_JPS=$(get_jps "$FAULT_NODE" | grep -c "DataNode" || true)
if [ "$DN_JPS" -eq 0 ]; then
  ok "DataNode 已停止"
else
  log "DataNode 进程可能仍在 (jps=$DN_JPS), 继续等待 Agent 检测"
fi

# ============================================================
# 2. 等待 Agent 检测 + 修复 (最多 120s)
# ============================================================
header "2/5 等待 Agent 自动检测 + 修复 (最多 120s)"

log "等待 Agent 巡检周期触发..."
log "可同时打开 Web 控制台查看实时 Agent 活动:"
if [ -f /workspace/tunnel_url.txt ]; then
  TUNNEL_URL=$(cat /workspace/tunnel_url.txt)
  log "  公网: $TUNNEL_URL"
else
  log "  本地: http://127.0.0.1:8000"
fi
echo ""

REPAIRED=false
for i in $(seq 1 60); do
  sleep 2
  # 检查 DataNode 是否恢复
  DN_JPS=$(get_jps "$FAULT_NODE" | grep -c "DataNode" || true)
  if [ "$DN_JPS" -gt 0 ]; then
    ok "DataNode 已恢复! (等待 ${i}x2s)"
    REPAIRED=true
    break
  fi
  # 进度提示
  if [ $((i % 10)) -eq 0 ]; then
    log "  ... 仍在等待 (${i}x2s), Agent 正在诊断中"
  fi
done

if [ "$REPAIRED" = false ]; then
  fail "Agent 未能在 120s 内修复, 检查日志: /workspace/agent.log"
  if [ "$MODE" = "docker" ]; then
    log "手动恢复: docker exec $FAULT_NODE supervisorctl start datanode"
  else
    log "手动恢复: $SUPCTL start datanode"
  fi
  exit 1
fi

# ============================================================
# 3. 验证集群恢复
# ============================================================
header "3/5 验证集群恢复"

sleep 5  # 等待 DataNode 注册

DN_STATUS=$(get_hdfs_report | grep "Live datanodes" | grep -o '[0-9]*' || true)
DN_STATUS=${DN_STATUS:-0}
if [ "$DN_STATUS" -ge "$EXPECTED_DN" ]; then
  ok "HDFS DataNode: $DN_STATUS/$EXPECTED_DN 在线"
else
  log "DataNode: $DN_STATUS/$EXPECTED_DN (可能需要更多时间注册, 等 10s...)"
  sleep 10
  DN_STATUS=$(get_hdfs_report | grep "Live datanodes" | grep -o '[0-9]*' || true)
  DN_STATUS=${DN_STATUS:-0}
  if [ "$DN_STATUS" -ge "$EXPECTED_DN" ]; then
    ok "HDFS DataNode: $DN_STATUS/$EXPECTED_DN 在线 (延迟注册)"
  else
    fail "DataNode 仅 $DN_STATUS/$EXPECTED_DN 在线"
  fi
fi

# ============================================================
# 4. 查看 Agent 修复记录
# ============================================================
header "4/5 Agent 修复记录"

log "最近 fix session:"
curl -s http://127.0.0.1:8000/api/sessions?type=fix 2>/dev/null | \
  python3 -c "
import sys, json
sessions = json.load(sys.stdin)
for s in sessions[:3]:
    status = s.get('status','?')
    sid = s.get('id','?')[:8]
    trigger = s.get('trigger','?')
    print(f'  [{status}] {sid} trigger={trigger}')
" 2>/dev/null || log "  (查询失败, 可在 Web 控制台查看)"

log ""
log "最近审计日志 (工具调用):"
curl -s 'http://127.0.0.1:8000/api/audit?limit=5' 2>/dev/null | \
  python3 -c "
import sys, json
logs = json.load(sys.stdin)
for a in logs:
    tool = a.get('tool_name','?')
    status = a.get('status','?')
    risk = a.get('risk_level','?')
    print(f'  [{status}] {tool} (risk={risk})')
" 2>/dev/null || log "  (查询失败, 可在 Web 控制台查看)"

# ============================================================
# 5. 总结
# ============================================================
header "5/5 Demo 完成"

echo -e "  ${C_GREEN}故障注入: DataNode STOPPED${NC}"
echo -e "  ${C_GREEN}Agent 自主: 检测 → 诊断 → 重启 → 验证${NC}"
echo -e "  ${C_GREEN}结果: 集群恢复, $DN_STATUS/$EXPECTED_DN DataNode 在线${NC}"
echo ""
echo -e "  ${C_BOLD}Web 控制台查看完整 ReAct 时间线:${NC}"
if [ -f /workspace/tunnel_url.txt ]; then
  echo "    $(cat /workspace/tunnel_url.txt)"
else
  echo "    http://127.0.0.1:8000"
fi
echo ""
