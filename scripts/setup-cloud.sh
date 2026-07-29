#!/usr/bin/env bash
# setup-cloud.sh — AMD Cloud one-click deployment script
#
# Usage: bash scripts/setup-cloud.sh
#
# Interactive cluster mode selection:
#   1) Local single-node — Direct Hadoop install on AMD Cloud host (no Docker, no HA)
#   2) Remote HA cluster — Connect to remote Docker 3-node Hadoop HA (requires remote SSH)
#
# Common steps (both modes):
#   1. Compile llama.cpp (ROCm)
#   2. Download model + start llama-server (bootstrap.sh)
#   3. Install Python dependencies
#   4. Build frontend
#   5. Start Agent + Web
#   6. rc-tunnel public exposure
set -euo pipefail

# ============================================================
# Configuration
# ============================================================
export LLAMA_API_KEY="${LLAMA_API_KEY:-aiops-$(date +%s)}"
export LLM_API_KEY="$LLAMA_API_KEY"
export LLM_MODEL="${LLM_MODEL:-/workspace/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf}"
export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:8080/v1}"
export AUTONOMY="${AUTONOMY:-autonomous}"
export PROMPT_LANGUAGE="${PROMPT_LANGUAGE:-zh}"
export CLUSTER_BACKEND="${CLUSTER_BACKEND:-apache}"
export DB_PATH="${DB_PATH:-/workspace/aiops.db}"

PROJ_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ_DIR"

LOG_FILE="/workspace/setup-cloud.log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo ""
echo "############################################################"
echo "#  AIOps Agent — AMD Cloud One-Click Deployment"
echo "#  Time: $(date)"
echo "#  Project: $PROJ_DIR"
echo "############################################################"
echo ""

# ============================================================
# Interactive cluster mode selection
# ============================================================
echo "Select Hadoop cluster mode:"
echo "  1) Local single-node — Direct Hadoop install on AMD Cloud host (no Docker, no HA)"
echo "  2) Remote HA cluster — Connect to remote Docker 3-node Hadoop HA"
echo ""
read -p "Enter 1 or 2 [default 1]: " CLUSTER_MODE
CLUSTER_MODE="${CLUSTER_MODE:-1}"
echo ""

if [ "$CLUSTER_MODE" = "1" ]; then
  echo "  Selected: Local single-node direct install"
else
  echo "  Selected: Remote HA cluster"
  read -p "  Remote cluster SSH address (e.g. 8.148.228.51): " REMOTE_HOST
  read -p "  Remote SSH port [default 22]: " REMOTE_PORT
  REMOTE_PORT="${REMOTE_PORT:-22}"
  read -p "  Remote hadoop01 SSH port [default 2222]: " NODE01_PORT
  NODE01_PORT="${NODE01_PORT:-2222}"
  read -p "  Remote hadoop02 SSH port [default 2223]: " NODE02_PORT
  NODE02_PORT="${NODE02_PORT:-2223}"
  read -p "  Remote hadoop03 SSH port [default 2224]: " NODE03_PORT
  NODE03_PORT="${NODE03_PORT:-2224}"
  echo ""
fi

# ============================================================
# Step 1: Compile llama.cpp
# ============================================================
echo "===== Step 1/6: Compile llama.cpp (ROCm) ====="
if [ -x /opt/llama.cpp/llama-server ]; then
  echo "  Already compiled, skipping"
else
  bash scripts/build-llama.sh
fi

# ============================================================
# Step 2: Model download + start llama-server
# ============================================================
echo ""
echo "===== Step 2/6: Model download + start llama-server ====="
export LLAMA_API_KEY
bash scripts/bootstrap.sh

# ============================================================
# Step 3: Hadoop cluster
# ============================================================
echo ""
echo "===== Step 3/6: Hadoop cluster ====="

if [ "$CLUSTER_MODE" = "1" ]; then
  # ---- Local single-node direct install ----
  echo "  [Local single-node] Direct install Hadoop + ZK + HBase + Hive + Tez + MySQL + supervisord ..."
  bash scripts/setup-hadoop-direct.sh
else
  # ---- Remote HA cluster ----
  echo "  [Remote HA] Connecting to remote cluster $REMOTE_HOST:$REMOTE_PORT"
  echo "    hadoop01: $REMOTE_HOST:$NODE01_PORT"
  echo "    hadoop02: $REMOTE_HOST:$NODE02_PORT"
  echo "    hadoop03: $REMOTE_HOST:$NODE03_PORT"
  echo ""
  echo "  Testing SSH connectivity ..."
  ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -p "$REMOTE_PORT" "root@$REMOTE_HOST" "echo OK" 2>/dev/null \
    && echo "  SSH connected: OK" \
    || { echo "  [ERROR] SSH connection failed to $REMOTE_HOST:$REMOTE_PORT"; echo "  Please confirm remote cluster is running"; exit 1; }
fi

# ============================================================
# Step 4: Install Python dependencies
# ============================================================
echo ""
echo "===== Step 4/6: Install Python dependencies ====="
pip3 install -r requirements.txt --break-system-packages -q 2>&1 | tail -3

# ============================================================
# Step 5: Build frontend + generate config + start Agent
# ============================================================
echo ""
echo "===== Step 5/6: Build frontend + start Agent ====="

# Build frontend
if [ -f "$PROJ_DIR/web/dist/index.html" ]; then
  echo "  Frontend already built, skipping"
else
  bash scripts/build-frontend.sh
fi

# Generate .env
SSH_KEY_PATH="$PROJ_DIR/deploy/config/ssh/id_rsa"
# SSH requires 0600 on private keys; Git checkout leaves 0644
chmod 600 "$SSH_KEY_PATH" 2>/dev/null || true

if [ "$CLUSTER_MODE" = "1" ]; then
  # Local single-node: only 1 SSH to localhost:22 (no Docker port mapping)
  cat > "$PROJ_DIR/.env" << EOF
export LLM_BASE_URL="$LLM_BASE_URL"
export LLM_API_KEY="$LLAMA_API_KEY"
export LLM_MODEL="$LLM_MODEL"
export DB_PATH="$DB_PATH"
export AUTONOMY="$AUTONOMY"
export PROMPT_LANGUAGE="$PROMPT_LANGUAGE"
export CLUSTER_BACKEND="$CLUSTER_BACKEND"
export SSH_USER=root
export SSH_KEY_PATH="$SSH_KEY_PATH"
export NODE01_HOST=localhost
export NODE01_SSH_PORT=22
export PROMETHEUS_URL=http://localhost:9090
export ALERTMANAGER_URL=http://localhost:9093
export GRAFANA_URL=http://localhost:3000
EOF
else
  # Remote HA cluster
  cat > "$PROJ_DIR/.env" << EOF
export LLM_BASE_URL="$LLM_BASE_URL"
export LLM_API_KEY="$LLAMA_API_KEY"
export LLM_MODEL="$LLM_MODEL"
export DB_PATH="$DB_PATH"
export AUTONOMY="$AUTONOMY"
export PROMPT_LANGUAGE="$PROMPT_LANGUAGE"
export CLUSTER_BACKEND="$CLUSTER_BACKEND"
export SSH_USER=root
export SSH_KEY_PATH="$SSH_KEY_PATH"
export NODE01_HOST=$REMOTE_HOST
export NODE01_SSH_PORT=$NODE01_PORT
export NODE02_HOST=$REMOTE_HOST
export NODE02_SSH_PORT=$NODE02_PORT
export NODE03_HOST=$REMOTE_HOST
export NODE03_SSH_PORT=$NODE03_PORT
export PROMETHEUS_URL=http://$REMOTE_HOST:9090
export ALERTMANAGER_URL=http://$REMOTE_HOST:9093
export GRAFANA_URL=http://$REMOTE_HOST:3000
EOF
fi

echo "  .env generated"

# Kill old Agent process
pkill -f "python.*main.py" 2>/dev/null || true
sleep 2

# Start Agent + Web
cd "$PROJ_DIR"
source .env
nohup python3 -m main > /workspace/agent.log 2>&1 &
echo "  Agent starting (PID $!)"

echo "  Waiting for web to be ready..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "  Web ready (${i}s)"
    HEALTH=$(curl -s http://127.0.0.1:8000/health)
    echo "  Health check: $HEALTH"
    break
  fi
  sleep 2
done

# ============================================================
# Step 6: rc-tunnel public exposure
# ============================================================
echo ""
echo "===== Step 6/6: rc-tunnel public exposure ====="
RC_TUNNEL="$HOME/.local/bin/rc-tunnel"

# Install rc-tunnel if not present
if [ ! -x "$RC_TUNNEL" ]; then
  echo "  rc-tunnel not found, installing..."
  if [ -f /var/run/secrets/frp-self-service/install ]; then
    bash /var/run/secrets/frp-self-service/install 2>&1
    # Re-check after install
    if [ ! -x "$RC_TUNNEL" ]; then
      # Maybe installed to a different path, try finding it
      RC_TUNNEL=$(which rc-tunnel 2>/dev/null || find /root/.local/bin /usr/local/bin /usr/bin -name rc-tunnel -type f 2>/dev/null | head -1)
    fi
  else
    echo "  [WARN] /var/run/secrets/frp-self-service/install not found"
    echo "  [INFO] May need to recreate Notebook to get rc-tunnel"
  fi
fi

if [ -x "$RC_TUNNEL" ]; then
  echo "  rc-tunnel found: $RC_TUNNEL"

  # Stop any existing tunnel first
  "$RC_TUNNEL" stop >/dev/null 2>&1 || true
  sleep 2

  # Expose port 8000
  echo "  Exposing port 8000..."
  TUNNEL_OUTPUT=$("$RC_TUNNEL" expose --port 8000 2>&1)
  echo "  Raw output: $TUNNEL_OUTPUT"

  # Extract public URL (format: https://rc-xxx.radeon.firstdg.ai)
  TUNNEL_URL=$(echo "$TUNNEL_OUTPUT" | grep -oE 'https://rc-[a-z0-9]+\.radeon\.firstdg\.ai' | head -1)

  if [ -n "$TUNNEL_URL" ]; then
    echo "$TUNNEL_URL" > /workspace/tunnel_url.txt
    echo ""
    echo "  ============================================"
    echo "  Public URL: $TUNNEL_URL"
    echo "  ============================================"

    # Verify connectivity (wait for FRP to establish)
    echo "  Verifying connectivity (waiting 5s)..."
    sleep 5
    if curl -sf --connect-timeout 10 -m 15 "$TUNNEL_URL/health" >/dev/null 2>&1; then
      echo "  Public access: OK"
    else
      echo "  [WARN] Not ready yet, FRP may still be connecting"
      echo "  Manual check: curl $TUNNEL_URL/health"
    fi
  else
    echo "  [WARN] Failed to extract public URL from output"
    echo "  Try manual: $RC_TUNNEL expose --port 8000"
    # Check tunnel status for debugging
    "$RC_TUNNEL" status 2>&1 || true
  fi
else
  echo "  [WARN] rc-tunnel not available"
  echo "  Local access only: http://127.0.0.1:8000"
fi

# ============================================================
# Complete
# ============================================================
# Read tunnel URL from file as fallback (variable may be out of scope)
if [ -z "${TUNNEL_URL:-}" ] && [ -f /workspace/tunnel_url.txt ]; then
  TUNNEL_URL=$(cat /workspace/tunnel_url.txt)
fi

echo ""
echo "############################################################"
echo "#  Deployment complete!"
echo "#"
echo "#  Local access:  http://127.0.0.1:8000"
if [ -n "${TUNNEL_URL:-}" ]; then
  echo "#  Public access: $TUNNEL_URL"
fi
echo "#  Agent log:     /workspace/agent.log"
echo "#  LLM log:       /workspace/llama-server.log"
echo "#"
echo "#  Run demo:      bash scripts/demo.sh"
echo "############################################################"
echo ""
if [ -n "${TUNNEL_URL:-}" ]; then
  echo ">>> Open in browser: $TUNNEL_URL"
else
  echo ">>> Open in browser: http://127.0.0.1:8000"
fi
echo ""
