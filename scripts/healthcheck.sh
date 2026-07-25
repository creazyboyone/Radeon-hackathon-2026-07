#!/bin/bash
# ============================================================
# Hadoop Cluster Health Check Script (Git Bash)
# Usage: bash scripts/healthcheck.sh
# ============================================================

# Note: set -e is intentionally omitted so a single failed check
# does not abort the entire script.

NODES=(hadoop01 hadoop02 hadoop03)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

FAILED=0

check_pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
check_fail() { echo -e "${RED}[FAIL]${NC} $1"; FAILED=1; }
check_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo "============================================================"
echo "  Hadoop Cluster Health Check"
echo "  Time: $(date)"
echo "============================================================"
echo ""

for node in "${NODES[@]}"; do
  echo ">>> Node: $node"
  echo ""

  # Container check
  if ! docker exec "$node" echo OK 2>/dev/null | grep -q OK; then
    check_fail "Container not running"
    echo ""
    continue
  fi
  check_pass "Container running"

  # Java processes
  JPS=$(docker exec "$node" jps 2>/dev/null)
  case $node in
    hadoop01)
      for proc in NameNode DataNode JournalNode ResourceManager NodeManager JobHistoryServer HMaster HRegionServer QuorumPeerMain DFSZKFailoverController; do
        if echo "$JPS" | grep -q "$proc"; then check_pass "Process: $proc"; else check_fail "Process: $proc"; fi
      done
      ;;
    hadoop02)
      for proc in NameNode DataNode JournalNode ResourceManager NodeManager HMaster HRegionServer QuorumPeerMain DFSZKFailoverController; do
        if echo "$JPS" | grep -q "$proc"; then check_pass "Process: $proc"; else check_fail "Process: $proc"; fi
      done
      ;;
    hadoop03)
      for proc in DataNode JournalNode NodeManager HRegionServer QuorumPeerMain; do
        if echo "$JPS" | grep -q "$proc"; then check_pass "Process: $proc"; else check_fail "Process: $proc"; fi
      done
      ;;
  esac
  echo ""

  # HBase (only check Master status on nodes running HMaster; hadoop03 only has RegionServer)
  if [ "$node" = "hadoop01" ] || [ "$node" = "hadoop02" ]; then
    echo "  [HBase]"
    HBASE=$(docker exec "$node" curl -s http://localhost:16010/jmx 2>/dev/null)
    if echo "$HBASE" | grep -q "tag.isActiveMaster"; then
      IS_ACTIVE=$(echo "$HBASE" | grep "tag.isActiveMaster" | head -1 | grep -o "true\|false")
      if [ "$IS_ACTIVE" = "true" ]; then check_pass "Master: ACTIVE"; else check_pass "Master: STANDBY"; fi
    else
      check_fail "Master status"
    fi
    echo ""
  fi

  # ZooKeeper (use nc -z to check port, avoids zkCli.sh localhost resolution issue in Git Bash)
  echo "  [ZooKeeper]"
  if docker exec "$node" nc -z localhost 2181 2>/dev/null; then
    check_pass "Port 2181 reachable"
  else
    check_fail "Port 2181 unreachable"
  fi
  echo ""

  # Hive (hadoop01/02)
  if [ "$node" = "hadoop01" ] || [ "$node" = "hadoop02" ]; then
    echo "  [Hive]"
    if docker exec "$node" nc -z localhost 10000 2>/dev/null; then check_pass "HiveServer2"; else check_fail "HiveServer2"; fi
    if docker exec "$node" nc -z localhost 9083 2>/dev/null; then check_pass "MetaStore"; else check_fail "MetaStore"; fi
    echo ""
  fi

  echo "------------------------------------------------------------"
  echo ""
done

# Cluster-level checks (via hadoop01)
echo ">>> Cluster-level checks (hadoop01)"
echo ""

echo "  [HDFS]"
REPORT=$(docker exec hadoop01 hdfs dfsadmin -report 2>/dev/null)
LIVE=$(echo "$REPORT" | grep -o "Live datanodes ([0-9]*)" | grep -o "[0-9]*")
LIVE=${LIVE:-0}
if [ "$LIVE" -eq 3 ]; then check_pass "DataNode: 3/3"; else check_fail "DataNode: $LIVE/3"; fi
MISSING=$(echo "$REPORT" | grep "Missing blocks:" | head -1 | awk '{print $3}')
MISSING=${MISSING:-0}
if [ "$MISSING" -eq 0 ]; then check_pass "Missing blocks: 0"; else check_warn "Missing blocks: $MISSING"; fi
TEST="/tmp/hc_$(date +%s).txt"
if docker exec hadoop01 bash -c "echo test | hdfs dfs -put - $TEST && hdfs dfs -cat $TEST && hdfs dfs -rm $TEST" 2>/dev/null | grep -q test; then
  check_pass "Read/write test"
else
  check_fail "Read/write test"
fi
echo ""

echo "  [YARN]"
# grep -c exits with code 1 when no match but still prints 0; do not use || echo "0"
NODES_COUNT=$(docker exec hadoop01 yarn node -list 2>/dev/null | grep -c RUNNING)
NODES_COUNT=${NODES_COUNT:-0}
if [ "$NODES_COUNT" -eq 3 ]; then check_pass "NodeManager: $NODES_COUNT RUNNING"; else check_fail "NodeManager: $NODES_COUNT/3"; fi
NN1=$(docker exec hadoop01 hdfs haadmin -getServiceState nn1 2>/dev/null || echo "unknown")
NN2=$(docker exec hadoop01 hdfs haadmin -getServiceState nn2 2>/dev/null || echo "unknown")
check_pass "NameNode HA: nn1=$NN1, nn2=$NN2"
RM1=$(docker exec hadoop01 yarn rmadmin -getServiceState rm1 2>/dev/null || echo "unknown")
RM2=$(docker exec hadoop01 yarn rmadmin -getServiceState rm2 2>/dev/null || echo "unknown")
check_pass "ResourceManager HA: rm1=$RM1, rm2=$RM2"
echo ""

# Summary
echo "============================================================"
if [ $FAILED -eq 0 ]; then
  echo -e "${GREEN}All checks passed!${NC}"
  exit 0
else
  echo -e "${RED}Some checks failed.${NC}"
  exit 1
fi
