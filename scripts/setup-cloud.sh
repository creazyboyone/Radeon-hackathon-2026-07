#!/usr/bin/env bash
# setup-cloud.sh — AMD Cloud 一键部署脚本
#
# 用法: bash scripts/setup-cloud.sh
#
# 交互式选择集群模式:
#   1) 本地单节点 — 在 AMD Cloud host 上直装 Hadoop (无 Docker, 无 HA)
#   2) 远程 HA 集群 — 连接远程 Docker 3 节点 Hadoop HA (需要远程 SSH)
#
# 通用步骤 (两种模式都执行):
#   1. 编译 llama.cpp (ROCm)
#   2. 模型下载 + 启动 llama-server (bootstrap.sh)
#   3. 安装 Python 依赖
#   4. 构建前端
#   5. 启动 Agent + Web
#   6. rc-tunnel 公网暴露
set -euo pipefail

# ============================================================
# 配置
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
echo "#  AIOps Agent — AMD Cloud 一键部署"
echo "#  时间: $(date)"
echo "#  项目: $PROJ_DIR"
echo "############################################################"
echo ""

# ============================================================
# 交互式选择集群模式
# ============================================================
echo "请选择 Hadoop 集群模式:"
echo "  1) 本地单节点 — 在 AMD Cloud host 上直装 Hadoop (无 Docker, 无 HA)"
echo "  2) 远程 HA 集群 — 连接远程 Docker 3 节点 Hadoop HA"
echo ""
read -p "请输入 1 或 2 [默认 1]: " CLUSTER_MODE
CLUSTER_MODE="${CLUSTER_MODE:-1}"
echo ""

if [ "$CLUSTER_MODE" = "1" ]; then
  echo "  已选: 本地单节点直装"
else
  echo "  已选: 远程 HA 集群"
  read -p "  远程集群 SSH 地址 (如 8.148.228.51): " REMOTE_HOST
  read -p "  远程 SSH 端口 [默认 22]: " REMOTE_PORT
  REMOTE_PORT="${REMOTE_PORT:-22}"
  read -p "  远程 hadoop01 SSH 端口 [默认 2222]: " NODE01_PORT
  NODE01_PORT="${NODE01_PORT:-2222}"
  read -p "  远程 hadoop02 SSH 端口 [默认 2223]: " NODE02_PORT
  NODE02_PORT="${NODE02_PORT:-2223}"
  read -p "  远程 hadoop03 SSH 端口 [默认 2224]: " NODE03_PORT
  NODE03_PORT="${NODE03_PORT:-2224}"
  echo ""
fi

# ============================================================
# Step 1: 编译 llama.cpp
# ============================================================
echo "===== Step 1/6: 编译 llama.cpp (ROCm) ====="
if [ -x /opt/llama.cpp/llama-server ]; then
  echo "  已编译, 跳过"
else
  bash scripts/build-llama.sh
fi

# ============================================================
# Step 2: 模型下载 + 启动 llama-server
# ============================================================
echo ""
echo "===== Step 2/6: 模型下载 + 启动 llama-server ====="
export LLAMA_API_KEY
bash scripts/bootstrap.sh

# ============================================================
# Step 3: Hadoop 集群
# ============================================================
echo ""
echo "===== Step 3/6: Hadoop 集群 ====="

if [ "$CLUSTER_MODE" = "1" ]; then
  # ---- 本地单节点直装 ----
  echo "  [本地单节点] 直装 Hadoop + ZK + HBase + supervisord ..."
  bash scripts/setup-hadoop-direct.sh
else
  # ---- 远程 HA 集群 ----
  echo "  [远程 HA] 连接远程集群 $REMOTE_HOST:$REMOTE_PORT"
  echo "    hadoop01: $REMOTE_HOST:$NODE01_PORT"
  echo "    hadoop02: $REMOTE_HOST:$NODE02_PORT"
  echo "    hadoop03: $REMOTE_HOST:$NODE03_PORT"
  echo ""
  echo "  测试 SSH 连通性 ..."
  ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -p "$REMOTE_PORT" "root@$REMOTE_HOST" "echo OK" 2>/dev/null \
    && echo "  SSH 连通: OK" \
    || { echo "  [错误] SSH 连不上 $REMOTE_HOST:$REMOTE_PORT"; echo "  请确认远程集群已启动"; exit 1; }
fi

# ============================================================
# Step 4: 安装 Python 依赖
# ============================================================
echo ""
echo "===== Step 4/6: 安装 Python 依赖 ====="
pip3 install -r requirements.txt --break-system-packages -q 2>&1 | tail -3

# ============================================================
# Step 5: 构建前端 + 生成配置 + 启动 Agent
# ============================================================
echo ""
echo "===== Step 5/6: 构建前端 + 启动 Agent ====="

# 构建前端
if [ -f "$PROJ_DIR/web/dist/index.html" ]; then
  echo "  前端已构建, 跳过"
else
  bash scripts/build-frontend.sh
fi

# 生成 .env
SSH_KEY_PATH="$PROJ_DIR/deploy/config/ssh/id_rsa"

if [ "$CLUSTER_MODE" = "1" ]; then
  # 本地单节点: 所有 SSH 都连 localhost
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
export NODE01_SSH_PORT=2222
export NODE02_HOST=localhost
export NODE02_SSH_PORT=2223
export NODE03_HOST=localhost
export NODE03_SSH_PORT=2224
export PROMETHEUS_URL=http://localhost:9090
export ALERTMANAGER_URL=http://localhost:9093
export GRAFANA_URL=http://localhost:3000
EOF
else
  # 远程 HA 集群
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

echo "  .env 已生成"

# 杀旧 Agent 进程
pkill -f "python.*main.py" 2>/dev/null || true
sleep 2

# 启动 Agent + Web
cd "$PROJ_DIR"
source .env
nohup python3 -m main > /workspace/agent.log 2>&1 &
echo "  Agent 启动中 (PID $!)"

echo "  等待 Web 就绪..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "  Web 就绪 (${i}s)"
    HEALTH=$(curl -s http://127.0.0.1:8000/health)
    echo "  健康检查: $HEALTH"
    break
  fi
  sleep 2
done

# ============================================================
# Step 6: rc-tunnel 公网暴露
# ============================================================
echo ""
echo "===== Step 6/6: rc-tunnel 公网暴露 ====="
RC_TUNNEL="$HOME/.local/bin/rc-tunnel"

if [ ! -x "$RC_TUNNEL" ]; then
  echo "  安装 rc-tunnel..."
  if [ -f /var/run/secrets/frp-self-service/install ]; then
    /var/run/secrets/frp-self-service/install 2>&1 || echo "  [警告] rc-tunnel 安装失败"
  else
    echo "  [提示] rc-tunnel 不可用, 可能需重建 Notebook"
  fi
fi

if [ -x "$RC_TUNNEL" ]; then
  "$RC_TUNNEL" stop >/dev/null 2>&1 || true
  sleep 2
  echo "  暴露端口 8000..."
  TUNNEL_OUTPUT=$("$RC_TUNNEL" expose --port 8000 2>&1)
  TUNNEL_URL=$(echo "$TUNNEL_OUTPUT" | grep -oE 'https://rc-[a-z0-9]+\.radeon\.firstdg\.ai' | head -1)
  echo "$TUNNEL_URL" > /workspace/tunnel_url.txt

  if [ -n "$TUNNEL_URL" ]; then
    echo ""
    echo "  ============================================"
    echo "  公网访问地址: $TUNNEL_URL"
    echo "  ============================================"
  else
    echo "  [警告] 未获取到公网 URL"
  fi
else
  echo "  本地访问: http://127.0.0.1:8000"
fi

# ============================================================
# 完成
# ============================================================
echo ""
echo "############################################################"
echo "#  部署完成!"
echo "#"
echo "#  本地访问:    http://127.0.0.1:8000"
if [ -n "${TUNNEL_URL:-}" ]; then
  echo "#  公网访问:    $TUNNEL_URL"
fi
echo "#  Agent 日志:  /workspace/agent.log"
echo "#  LLM 日志:   /workspace/llama-server.log"
echo "#"
echo "#  运行 Demo:  bash scripts/demo.sh"
echo "############################################################"
