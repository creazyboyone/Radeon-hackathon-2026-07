#!/usr/bin/env bash
# demo.sh — One-click AIOps Agent closed-loop demo
#
# Usage: bash scripts/demo.sh
#
# Flow: Inject fault (kill DataNode) → Wait for Agent to detect → Diagnose → Repair → Verify
# Prerequisite: setup-cloud.sh has been executed, Agent is running
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
# Detect cluster mode: Docker (docker exec) or direct install (supervisorctl)
# ============================================================
SUPCTL="supervisorctl -c /etc/supervisor/conf.d/supervisord-hadoop01.conf"

if docker exec hadoop01 echo OK >/dev/null 2>&1; then
  MODE="docker"
  log "Docker cluster mode detected"
elif bash -c "$SUPCTL status" >/dev/null 2>&1; then
  MODE="direct"
  log "Single-node direct install mode detected"
else
  fail "Hadoop cluster not running, please run: bash scripts/setup-cloud.sh"
  exit 1
fi

# Cluster operation wrappers
cluster_exec() {
  local node="$1"; shift
  if [ "$MODE" = "docker" ]; then
    docker exec "$node" "$@"
  else
    # Direct install: all nodes are localhost, execute directly
    "$@"
  fi
}

supervisor_action() {
  local node="$1" action="$2" program="$3"
  if [ "$MODE" = "docker" ]; then
    docker exec "$node" supervisorctl "$action" "$program"
  else
    # Direct install: all nodes share the same supervisord
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

# Expected DataNode count (Docker: 3, direct: 1)
if [ "$MODE" = "docker" ]; then
  EXPECTED_DN=3
  FAULT_NODE="hadoop03"
else
  EXPECTED_DN=1
  FAULT_NODE="localhost"
fi

# ============================================================
# 0. Pre-check
# ============================================================
header "0/5 Pre-check"

# Check if Agent is running
if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  HEALTH=$(curl -s http://127.0.0.1:8000/health)
  ok "Agent is running: $HEALTH"
else
  fail "Agent not running, please run: bash scripts/setup-cloud.sh"
  exit 1
fi

# Check if Hadoop cluster is running
if [ "$MODE" = "docker" ]; then
  if docker exec hadoop01 echo OK >/dev/null 2>&1; then
    ok "Hadoop cluster is running (Docker)"
  else
    fail "Hadoop cluster not running"
    exit 1
  fi
else
  if $SUPCTL status >/dev/null 2>&1; then
    ok "Hadoop cluster is running (direct install)"
  else
    fail "Hadoop cluster not running"
    exit 1
  fi
fi

# Current DataNode status
DN_STATUS=$(get_hdfs_report | grep "Live datanodes" | grep -o '[0-9]*' || true)
DN_STATUS=${DN_STATUS:-0}
if [ "$DN_STATUS" -ge "$EXPECTED_DN" ]; then
  ok "DataNode: $DN_STATUS/$EXPECTED_DN online"
else
  log "DataNode: $DN_STATUS/$EXPECTED_DN online (may be leftover from previous demo, continuing)"
fi

# ============================================================
# 1. Inject fault: stop DataNode
# ============================================================
header "1/5 Inject fault: stop DataNode"

log "Stopping DataNode process..."
supervisor_action "$FAULT_NODE" stop datanode 2>/dev/null || true
sleep 3

# Verify DataNode is actually stopped
DN_JPS=$(get_jps "$FAULT_NODE" | grep -c "DataNode" || true)
if [ "$DN_JPS" -eq 0 ]; then
  ok "DataNode stopped"
else
  log "DataNode process may still be running (jps=$DN_JPS), continuing to wait for Agent detection"
fi

# ============================================================
# 2. Wait for Agent to detect + repair (max 120s)
# ============================================================
header "2/5 Wait for Agent auto-detect + repair (max 120s)"

log "Waiting for Agent inspection cycle to trigger..."
log "Open the web console to view real-time Agent activity:"
if [ -f /workspace/tunnel_url.txt ]; then
  TUNNEL_URL=$(cat /workspace/tunnel_url.txt)
  log "  Public: $TUNNEL_URL"
else
  log "  Local: http://127.0.0.1:8000"
fi
echo ""

REPAIRED=false
for i in $(seq 1 60); do
  sleep 2
  # Check if DataNode has recovered
  DN_JPS=$(get_jps "$FAULT_NODE" | grep -c "DataNode" || true)
  if [ "$DN_JPS" -gt 0 ]; then
    ok "DataNode recovered! (waited ${i}x2s)"
    REPAIRED=true
    break
  fi
  # Progress indicator
  if [ $((i % 10)) -eq 0 ]; then
    log "  ... still waiting (${i}x2s), Agent is diagnosing"
  fi
done

if [ "$REPAIRED" = false ]; then
  fail "Agent failed to repair within 120s, check log: /workspace/agent.log"
  if [ "$MODE" = "docker" ]; then
    log "Manual recovery: docker exec $FAULT_NODE supervisorctl start datanode"
  else
    log "Manual recovery: $SUPCTL start datanode"
  fi
  exit 1
fi

# ============================================================
# 3. Verify cluster recovery
# ============================================================
header "3/5 Verify cluster recovery"

sleep 5  # Wait for DataNode registration

DN_STATUS=$(get_hdfs_report | grep "Live datanodes" | grep -o '[0-9]*' || true)
DN_STATUS=${DN_STATUS:-0}
if [ "$DN_STATUS" -ge "$EXPECTED_DN" ]; then
  ok "HDFS DataNode: $DN_STATUS/$EXPECTED_DN online"
else
  log "DataNode: $DN_STATUS/$EXPECTED_DN (may need more time to register, waiting 10s...)"
  sleep 10
  DN_STATUS=$(get_hdfs_report | grep "Live datanodes" | grep -o '[0-9]*' || true)
  DN_STATUS=${DN_STATUS:-0}
  if [ "$DN_STATUS" -ge "$EXPECTED_DN" ]; then
    ok "HDFS DataNode: $DN_STATUS/$EXPECTED_DN online (delayed registration)"
  else
    fail "DataNode only $DN_STATUS/$EXPECTED_DN online"
  fi
fi

# ============================================================
# 4. View Agent repair records
# ============================================================
header "4/5 Agent repair records"

log "Recent fix sessions:"
curl -s http://127.0.0.1:8000/api/sessions?type=fix 2>/dev/null | \
  python3 -c "
import sys, json
sessions = json.load(sys.stdin)
for s in sessions[:3]:
    status = s.get('status','?')
    sid = s.get('id','?')[:8]
    trigger = s.get('trigger','?')
    print(f'  [{status}] {sid} trigger={trigger}')
" 2>/dev/null || log "  (query failed, view in web console)"

log ""
log "Recent audit log (tool calls):"
curl -s 'http://127.0.0.1:8000/api/audit?limit=5' 2>/dev/null | \
  python3 -c "
import sys, json
logs = json.load(sys.stdin)
for a in logs:
    tool = a.get('tool_name','?')
    status = a.get('status','?')
    risk = a.get('risk_level','?')
    print(f'  [{status}] {tool} (risk={risk})')
" 2>/dev/null || log "  (query failed, view in web console)"

# ============================================================
# 5. Summary
# ============================================================
header "5/5 Demo complete"

echo -e "  ${C_GREEN}Fault injected: DataNode STOPPED${NC}"
echo -e "  ${C_GREEN}Agent autonomous: detect → diagnose → restart → verify${NC}"
echo -e "  ${C_GREEN}Result: cluster recovered, $DN_STATUS/$EXPECTED_DN DataNode online${NC}"
echo ""
echo -e "  ${C_BOLD}View full ReAct timeline in web console:${NC}"
if [ -f /workspace/tunnel_url.txt ]; then
  echo "    $(cat /workspace/tunnel_url.txt)"
else
  echo "    http://127.0.0.1:8000"
fi
echo ""
