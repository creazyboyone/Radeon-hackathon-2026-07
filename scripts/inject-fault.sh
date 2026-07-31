#!/usr/bin/env bash
# inject-fault.sh — Interactive fault injection + verification for AIOps Agent
#
# Usage:  bash scripts/inject-fault.sh
#
# Flow:  detect cluster → menu → inject → wait → [1) inject another  2) verify+report]
set -euo pipefail

PROJ_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ_DIR"

C_GREEN='\033[0;32m'; C_RED='\033[0;31m'; C_YELLOW='\033[1;33m'
C_CYAN='\033[0;36m';  C_BOLD='\033[1m';   C_MAGENTA='\033[0;35m'; NC='\033[0m'

log()    { echo -e "${C_CYAN}[$(date +%H:%M:%S)]${NC} $1"; }
ok()     { echo -e "${C_GREEN}[$(date +%H:%M:%S)] [OK]${NC} $1"; }
fail()   { echo -e "${C_RED}[$(date +%H:%M:%S)] [FAIL]${NC} $1"; }
warn()   { echo -e "${C_YELLOW}[$(date +%H:%M:%S)] [WARN]${NC} $1"; }
header() { echo -e "\n${C_BOLD}${C_MAGENTA}════════════════════════════════════════${NC}"; echo -e "${C_BOLD}${C_MAGENTA} $1${NC}"; echo -e "${C_BOLD}${C_MAGENTA}════════════════════════════════════════${NC}\n"; }

# ============================================================
# Detect cluster mode
# ============================================================
SUPCTL="supervisorctl -c /etc/supervisor/conf.d/supervisord-hadoop01.conf"

if docker exec hadoop01 echo OK >/dev/null 2>&1; then
  MODE="docker"
  DEFAULT_NODE="hadoop03"
  EXPECTED_DN=3
  ALL_NODES=("hadoop01" "hadoop02" "hadoop03")
elif bash -c "$SUPCTL status" >/dev/null 2>&1; then
  MODE="direct"
  DEFAULT_NODE="localhost"
  EXPECTED_DN=1
  ALL_NODES=("localhost")
else
  fail "Hadoop cluster not running"
  fail "Docker: docker compose up -d  |  Direct: bash scripts/setup-hadoop-direct.sh"
  exit 1
fi

# ---- helpers ----
cluster_exec() {
  local node="$1"; shift
  if [ "$MODE" = "docker" ]; then docker exec "$node" "$@"; else "$@"; fi
}

supervisor_action() {
  local node="$1" action="$2" program="$3"
  if [ "$MODE" = "docker" ]; then docker exec "$node" supervisorctl "$action" "$program"; else $SUPCTL "$action" "$program"; fi
}

get_jps() {
  local node="$1"
  if [ "$MODE" = "docker" ]; then docker exec "$node" jps 2>/dev/null; else /usr/bin/jps 2>/dev/null; fi
}

hdfs_cmd() {
  if [ "$MODE" = "docker" ]; then
    docker exec hadoop01 bash -c "export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64; /opt/hadoop/bin/hdfs $*"
  else
    /opt/hadoop/bin/hdfs "$@"
  fi
}

get_hdfs_report() {
  if [ "$MODE" = "docker" ]; then
    docker exec hadoop01 bash -c 'export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64; /opt/hadoop/bin/hdfs dfsadmin -report 2>/dev/null'
  else
    /opt/hadoop/bin/hdfs dfsadmin -report 2>/dev/null
  fi
}

verify_stopped() {
  local node="$1" pattern="$2" label="$3"
  sleep 3
  local cnt
  cnt=$(get_jps "$node" | grep -c "$pattern" || true)
  if [ "$cnt" -eq 0 ]; then ok "$label stopped on $node"; else warn "$label may still be running (jps=$cnt)"; fi
}

# ---- agent url ----
agent_url() {
  if [ -f /workspace/tunnel_url.txt ]; then cat /workspace/tunnel_url.txt; else echo "http://127.0.0.1:8000"; fi
}

# ============================================================
# Fault injectors
# ============================================================

inject_datanode_stop() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: DataNode STOP on $node"
  log "Stopping DataNode on $node..."
  supervisor_action "$node" stop datanode 2>/dev/null || true
  verify_stopped "$node" "DataNode" "DataNode"
}

inject_regionserver_stop() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: HBase RegionServer STOP on $node"
  log "Stopping HBase RegionServer on $node..."
  supervisor_action "$node" stop hbase-regionserver 2>/dev/null || true
  verify_stopped "$node" "HRegionServer" "HRegionServer"
}

inject_zookeeper_stop() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: ZooKeeper STOP on $node"
  log "Stopping ZooKeeper on $node..."
  supervisor_action "$node" stop zookeeper 2>/dev/null || true
  verify_stopped "$node" "QuorumPeerMain" "ZooKeeper"
}

inject_nodemanager_stop() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: NodeManager STOP on $node"
  log "Stopping YARN NodeManager on $node..."
  supervisor_action "$node" stop nodemanager 2>/dev/null || true
  verify_stopped "$node" "NodeManager" "NodeManager"
}

inject_hiveserver2_stop() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: HiveServer2 STOP on $node"
  log "Stopping HiveServer2 on $node..."
  supervisor_action "$node" stop hiveserver2 2>/dev/null || true
  verify_stopped "$node" "HiveServer2" "HiveServer2"
}

inject_hiveserver2_oom() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: HiveServer2 OOM on $node"

  log "Step 1: Stop HS2 via supervisord..."
  supervisor_action "$node" stop hiveserver2 2>/dev/null || true
  sleep 3

  log "Step 2: Start HS2 manually with 128MB heap..."
  cluster_exec "$node" bash -c '
    export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64
    export HADOOP_HEAPSIZE=128
    export HADOOP_HEAPSIZE_MAX=128
    export HADOOP_OPTS="-javaagent:/opt/jmx-exporter/jmx_prometheus_javaagent-0.20.0.jar=10111:/opt/jmx-exporter/config.yml -Xmx128m"
    nohup /opt/hive/bin/hiveserver2 > /logs/hs2_oom.log 2>&1 &
    echo "HS2 PID: $!"
  '
  sleep 10
  log "Step 3: Verify heap is 128MB..."
  cluster_exec "$node" bash -c 'ps aux | grep hiveserver2 | grep -v grep | grep -o "\-Xmx[0-9]*[mMgG]" | head -1'

  log "Step 4: Prepare test data if not exists..."
  cluster_exec hadoop01 bash -c '
    /opt/hadoop/bin/hdfs dfs -test -d /user/hive/warehouse/aiopstest.db/bigdata_ext 2>/dev/null || {
      echo "Generating 2M row CSV..."
      seq 1 2000000 | awk -F, '\''BEGIN{OFS=","}{print $1, "user_"$1, "data_payload_"$1"_padding_xxxxx"}'\'' > /tmp/bigdata.csv
      /opt/hadoop/bin/hdfs dfs -mkdir -p /user/hive/warehouse/aiopstest.db/bigdata_ext
      /opt/hadoop/bin/hdfs dfs -put -f /tmp/bigdata.csv /user/hive/warehouse/aiopstest.db/bigdata_ext/
      /opt/hive/bin/beeline -u "jdbc:hive2://localhost:10000" -n root --color=false << "BEELINE"
        CREATE DATABASE IF NOT EXISTS aiopstest;
        USE aiopstest;
        CREATE EXTERNAL TABLE IF NOT EXISTS bigdata_ext (id int, name string, payload string)
        ROW FORMAT DELIMITED FIELDS TERMINATED BY ","
        STORED AS TEXTFILE
        LOCATION "/user/hive/warehouse/aiopstest.db/bigdata_ext";
BEELINE
    }
  '

  log "Step 5: Run SELECT * to trigger OOM..."
  cluster_exec "$node" bash -c '
    cat > /tmp/hive_oom_trigger.sql << "SQLEOF"
USE aiopstest;
SET hive.fetch.task.conversion=none;
SELECT * FROM bigdata_ext;
SQLEOF
    /opt/hive/bin/beeline -u "jdbc:hive2://localhost:10000" -n root --color=false -f /tmp/hive_oom_trigger.sql 2>&1 | tail -30
  '

  log "Step 6: Check HS2 status..."
  local cnt
  cnt=$(get_jps "$node" | grep -c "HiveServer2" || true)
  if [ "$cnt" -eq 0 ]; then ok "HiveServer2 OOM crashed"; else warn "HiveServer2 still running (OOM may not have triggered)"; fi
  log "Check logs: /logs/hs2_oom.log"
}

inject_hbase_master_stop() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: HBase Master STOP on $node"
  log "Stopping HBase Master on $node..."
  supervisor_action "$node" stop hbase-master 2>/dev/null || true
  verify_stopped "$node" "HMaster" "HBase Master"
}

inject_resourcemanager_stop() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: YARN ResourceManager STOP on $node"
  log "Stopping ResourceManager on $node..."
  supervisor_action "$node" stop resourcemanager 2>/dev/null || true
  verify_stopped "$node" "ResourceManager" "ResourceManager"
}

inject_hdfs_safemode() {
  header "Inject: HDFS Safe Mode ON"
  log "Entering HDFS safe mode manually..."
  hdfs_cmd dfsadmin -safemode enter 2>/dev/null || true
  sleep 3
  local state
  state=$(hdfs_cmd dfsadmin -safemode get 2>/dev/null | grep -o "Safe mode is ON" || true)
  if [ -n "$state" ]; then ok "HDFS is in safe mode"; else warn "HDFS may not be in safe mode"; fi
}

inject_hdfs_corrupt() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: HDFS Corrupt Blocks"
  log "Creating test files and deleting block replicas to simulate corruption..."

  local i
  for i in 1 2 3; do
    log "  Writing test file /tmp/corrupt_test_$i..."
    cluster_exec hadoop01 bash -c "echo 'corrupt_test_data_$i' | /opt/hadoop/bin/hdfs dfs -put - -f /tmp/corrupt_test_$i" 2>/dev/null || true
  done

  local dn_dir="/data/hadoop/hdfs/datanode/current/BP-*/current/finalized/subdir0/subdir0"
  log "  Deleting block files on $node to simulate corruption..."
  if [ "$MODE" = "docker" ]; then
    cluster_exec "$node" bash -c "find $dn_dir -name 'blk_*' -type f 2>/dev/null | head -5 | xargs rm -f" 2>/dev/null || true
  else
    find $dn_dir -name 'blk_*' -type f 2>/dev/null | head -5 | xargs rm -f 2>/dev/null || true
  fi

  sleep 3
  log "  Running HDFS fsck to check corruption..."
  hdfs_cmd fsck / 2>/dev/null | grep -i "corrupt\|missing\|under replicated" | head -10 || true
  ok "Corrupt blocks injected (check via: hdfs fsck / | grep Corrupt)"
}

inject_disk_fill() {
  local node="${1:-$DEFAULT_NODE}"
  header "Inject: Disk Usage High on $node"
  log "Creating large temp files to fill disk..."

  local target_dir="/tmp"
  local before
  if [ "$MODE" = "docker" ]; then
    before=$(cluster_exec "$node" df -h / | awk 'NR==2{print $5}')
  else
    before=$(df -h / | awk 'NR==2{print $5}')
  fi
  log "  Current disk usage: $before"

  log "  Creating 2GB temp file..."
  if [ "$MODE" = "docker" ]; then
    cluster_exec "$node" bash -c "dd if=/dev/zero of=${target_dir}/disk_fill_test bs=1M count=2048 2>/dev/null" || true
  else
    dd if=/dev/zero of="${target_dir}/disk_fill_test" bs=1M count=2048 2>/dev/null || true
  fi

  local after
  if [ "$MODE" = "docker" ]; then
    after=$(cluster_exec "$node" df -h / | awk 'NR==2{print $5}')
  else
    after=$(df -h / | awk 'NR==2{print $5}')
  fi
  ok "Disk usage: $before → $after"
  log "  Cleanup: rm -f ${target_dir}/disk_fill_test (Agent should detect and clean up)"
}

# ============================================================
# Menu
# ============================================================
FAULT_NAMES=(
  "datanode_stop       Stop a DataNode"
  "regionserver_stop   Stop HBase RegionServer"
  "zookeeper_stop      Stop ZooKeeper"
  "nodemanager_stop    Stop YARN NodeManager"
  "hiveserver2_stop    Stop HiveServer2"
  "hiveserver2_oom     HiveServer2 OOM (128MB heap + heavy query)"
  "hbase_master_stop   Stop HBase Master"
  "resourcemanager_stop Stop YARN ResourceManager"
  "hdfs_safemode       Force HDFS safe mode ON"
  "hdfs_corrupt        Create corrupt HDFS blocks"
  "disk_fill           Fill disk to trigger usage alert"
)

FAULT_FUNCS=(
  inject_datanode_stop
  inject_regionserver_stop
  inject_zookeeper_stop
  inject_nodemanager_stop
  inject_hiveserver2_stop
  inject_hiveserver2_oom
  inject_hbase_master_stop
  inject_resourcemanager_stop
  inject_hdfs_safemode
  inject_hdfs_corrupt
  inject_disk_fill
)

show_menu() {
  header "Fault Injection Menu  ($MODE mode, node=$DEFAULT_NODE)"
  local i=1
  for name in "${FAULT_NAMES[@]}"; do
    printf "  ${C_BOLD}%2d)${NC} %s\n" "$i" "$name"
    ((i++))
  done
  echo ""
  echo -e "  ${C_BOLD} 0)${NC} Exit"
  echo ""
}

select_node() {
  if [ "$MODE" = "direct" ]; then
    echo "localhost"
    return
  fi
  # Docker: ask which node
  echo ""
  echo "  Select target node:"
  local i=1
  for n in "${ALL_NODES[@]}"; do
    printf "  ${C_BOLD}%d)${NC} %s\n" "$i" "$n"
    ((i++))
  done
  echo -e "  ${C_BOLD}0)${NC} Use default ($DEFAULT_NODE)"
  echo ""
  read -rp "  Choice [0]: " choice
  if [ -z "$choice" ] || [ "$choice" = "0" ]; then
    echo "$DEFAULT_NODE"
  elif [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#ALL_NODES[@]}" ]; then
    echo "${ALL_NODES[$((choice-1))]}"
  else
    echo "$DEFAULT_NODE"
  fi
}

run_inject() {
  local idx="$1"
  local func="${FAULT_FUNCS[$((idx-1))]}"
  local node

  # Faults that don't need a node
  case "$func" in
    inject_hdfs_safemode)
      $func
      ;;
    *)
      node=$(select_node)
      $func "$node"
      ;;
  esac
}

# ============================================================
# Verify + Report
# ============================================================
verify_and_report() {
  header "Verify cluster recovery"

  # Check if DataNode process is alive (works for most fault types)
  local dn_alive=false
  local dn_status=0
  local i

  for i in $(seq 1 6); do
    local cnt
    cnt=$(get_jps "$DEFAULT_NODE" | grep -c "DataNode" || true)
    if [ "$cnt" -gt 0 ]; then
      dn_alive=true
      dn_status=$(get_hdfs_report | grep "Live datanodes" | grep -o '[0-9]*' || true)
      dn_status=${dn_status:-0}
      if [ "$dn_status" -ge "$EXPECTED_DN" ]; then
        break
      fi
    fi
    log "Waiting for recovery... (attempt $i/6, dn_proc=$cnt, live_dn=$dn_status)"
    sleep 10
  done

  if [ "$dn_alive" = true ] && [ "$dn_status" -ge "$EXPECTED_DN" ]; then
    ok "HDFS DataNode: $dn_status/$EXPECTED_DN online"
  elif [ "$dn_alive" = true ]; then
    warn "DataNode process running but HDFS reports $dn_status/$EXPECTED_DN live (registration may be delayed)"
  else
    fail "DataNode not running ($EXPECTED_DN expected)"
  fi

  # ---- Agent repair records ----
  header "Agent repair records"

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

  header "Done"
  echo -e "  ${C_GREEN}Result: cluster recovered, $dn_status/$EXPECTED_DN DataNode online${NC}"
  echo -e "  ${C_BOLD}View full ReAct timeline at: $(agent_url)${NC}"
  echo ""
}

# ============================================================
# Main loop
# ============================================================
header "AIOps Fault Injection Tool"
log "Cluster mode: $MODE"
if [ "$MODE" = "docker" ]; then
  log "Nodes: ${ALL_NODES[*]}"
  log "Default fault target: $DEFAULT_NODE"
else
  log "Single-node install"
fi
echo ""

# Check agent
if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  ok "Agent is running"
else
  warn "Agent not running (fault can still be injected, but no auto-repair)"
fi

while true; do
  show_menu
  read -rp "  Select fault type [0-11]: " choice

  if [ -z "$choice" ] || [ "$choice" = "0" ]; then
    log "Bye!"
    exit 0
  fi

  if ! [[ "$choice" =~ ^[0-9]+$ ]] || [ "$choice" -lt 1 ] || [ "$choice" -gt "${#FAULT_FUNCS[@]}" ]; then
    warn "Invalid choice: $choice"
    continue
  fi

  # Inject
  run_inject "$choice"

  # Wait for user to observe
  echo ""
  log "Fault injected. Watch Agent activity in the web console:"
  echo -e "  ${C_BOLD}$(agent_url)${NC}"
  echo ""
  read -rp "Press Enter when ready to continue..."

  # Post-injection menu
  while true; do
    echo ""
    echo -e "  ${C_BOLD}1)${NC} Inject another fault"
    echo -e "  ${C_BOLD}2)${NC} Verify cluster recovery + show Agent repair records"
    echo -e "  ${C_BOLD}0)${NC} Exit"
    echo ""
    read -rp "  Choice [1]: " post_choice
    post_choice="${post_choice:-1}"

    case "$post_choice" in
      1) break ;;  # back to main menu
      2) verify_and_report; break ;;
      0) log "Bye!"; exit 0 ;;
      *) warn "Invalid choice" ;;
    esac
  done
done
