#!/usr/bin/env bash
# bootstrap.sh - 重启后一键恢复：SSH + 模型 + llama-server + rc-tunnel 公网暴露
# 存放: /workspace/bootstrap.sh  用法: bash /workspace/bootstrap.sh
set -uo pipefail
LOG=/workspace/bootstrap.log
exec > >(tee -a "$LOG") 2>&1
echo "===== bootstrap $(date) ====="

# SSH 公钥与 API key 从环境变量注入 (勿硬编码提交):
#   export SSH_PUBKEY="ssh-ed25519 AAAA... you@host"
#   export LLAMA_API_KEY="your-key"
SSH_PUBKEY="${SSH_PUBKEY:-}"
MODEL_PATH=/workspace/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf
MODEL_REPO=Jackrong/Qwopus3.6-27B-v2-MTP-GGUF
MODEL_FILE=Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf
LLAMA_DIR=/opt/llama.cpp
PORT=8080
API_KEY="${LLAMA_API_KEY:-}"
TUNNEL_URL_FILE=/workspace/tunnel_url.txt

# 1. SSH (保留: 紧急调试用, 非主要访问通道)
echo "[1/5] 安装 SSH (紧急调试用)..."
if ! command -v sshd >/dev/null 2>&1; then
  apt-get update -y && apt-get install -y openssh-server
fi
mkdir -p /run/sshd /root/.ssh && chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
if [ -n "$SSH_PUBKEY" ]; then
  grep -qF "$SSH_PUBKEY" /root/.ssh/authorized_keys 2>/dev/null || echo "$SSH_PUBKEY" >> /root/.ssh/authorized_keys
else
  echo "  [提示] 未设置 SSH_PUBKEY 环境变量, 跳过公钥写入"
fi
chmod 600 /root/.ssh/authorized_keys
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
pgrep -x sshd >/dev/null 2>&1 || /usr/sbin/sshd
echo "  sshd: $(pgrep -x sshd >/dev/null 2>&1 && echo OK || echo FAIL)"

# 2. modelscope
echo "[2/5] 安装 modelscope..."
python3 -c "import modelscope" 2>/dev/null || pip3 install -U modelscope --break-system-packages -q
echo "  modelscope: $(python3 -c 'import modelscope;print(modelscope.__version__)' 2>/dev/null || echo FAIL)"

# 3. 模型
echo "[3/5] 检查模型..."
if [ -f "$MODEL_PATH" ]; then
  echo "  已存在: $(ls -lh "$MODEL_PATH" | awk '{print $5}')"
else
  echo "  下载 $MODEL_FILE ..."
  modelscope download --model "$MODEL_REPO" --local_dir /workspace "$MODEL_FILE" \
    && echo "  下载完成: $(ls -lh "$MODEL_PATH" | awk '{print $5}')" \
    || echo "  [警告] 下载失败"
fi

# 4. llama-server (绑定 127.0.0.1, rc-tunnel 只支持 127.0.0.1)
echo "[4/5] 启动 llama-server..."
if pgrep -f "llama-server" >/dev/null 2>&1; then
  echo "  已在运行"
else
  if [ -f "$MODEL_PATH" ] && [ -x "$LLAMA_DIR/llama-server" ]; then
    cd "$LLAMA_DIR"
    # 公网暴露必须启用鉴权 (rc-tunnel 文档: "your app must enforce login or authentication")
    if [ -z "$API_KEY" ]; then
      echo "  [警告] LLAMA_API_KEY 未设置, 公网暴露将无鉴权! 请设置环境变量后重试"
      API_KEY_ARG=""
    else
      API_KEY_ARG="--api-key $API_KEY"
    fi
    HIP_VISIBLE_DEVICES=0 nohup ./llama-server \
      -m "$MODEL_PATH" -c 131072 -ngl 999 \
      -ctk q8_0 -ctv q8_0 -fa on --jinja --spec-type draft-mtp --spec-draft-n-max 1 \
      -t 16 -b 512 -ub 512 -np 1 \
      --host 127.0.0.1 --port "$PORT" $API_KEY_ARG \
      > /workspace/llama-server.log 2>&1 &
    echo "  启动中 (PID $!), 日志 /workspace/llama-server.log"
    echo "  等待 llama-server 就绪..."
    for i in $(seq 1 30); do
      if curl -sf --connect-timeout 2 http://127.0.0.1:"$PORT"/v1/models >/dev/null 2>&1; then
        echo "  llama-server 就绪 (${i}s)"
        break
      fi
      sleep 1
    done
  else
    echo "  [警告] 模型或二进制缺失，跳过"
  fi
fi

# 5. rc-tunnel 公网暴露
echo "[5/5] rc-tunnel 公网暴露..."
RC_TUNNEL="$HOME/.local/bin/rc-tunnel"

# 安装 rc-tunnel (幂等)
if [ ! -x "$RC_TUNNEL" ]; then
  echo "  安装 rc-tunnel..."
  if [ -f /var/run/secrets/frp-self-service/install ]; then
    /var/run/secrets/frp-self-service/install 2>&1 || echo "  [警告] rc-tunnel 安装失败"
  else
    echo "  [错误] /var/run/secrets/frp-self-service/install 不存在"
    echo "  可能是旧 Pod, 需关闭并重建 Notebook 后重试"
  fi
fi

# 停止旧隧道 (如果有)
if [ -x "$RC_TUNNEL" ]; then
  "$RC_TUNNEL" stop >/dev/null 2>&1 || true
  sleep 2

  # 暴露端口, 获取公网 URL
  echo "  暴露端口 $PORT..."
  TUNNEL_OUTPUT=$("$RC_TUNNEL" expose --port "$PORT" 2>&1)
  echo "  $TUNNEL_OUTPUT"

  # 提取公网 URL (格式: https://rc-xxx.radeon.firstdg.ai)
  TUNNEL_URL=$(echo "$TUNNEL_OUTPUT" | grep -oE 'https://rc-[a-z0-9]+\.radeon\.firstdg\.ai' | head -1)

  if [ -n "$TUNNEL_URL" ]; then
    # 写入文件供本地读取
    echo "$TUNNEL_URL" > "$TUNNEL_URL_FILE"
    echo ""
    echo "  ============================================"
    echo "  公网 URL: $TUNNEL_URL"
    echo "  已写入: $TUNNEL_URL_FILE"
    echo "  ============================================"
    echo ""
    echo "  本地配置:"
    echo "    export LLM_BASE_URL=\"${TUNNEL_URL}/v1\""
    echo "    export LLM_API_KEY=\"$API_KEY\""
    echo ""
    # 验证公网可达 (等几秒让 FRP 建立连接)
    echo "  验证公网连通性 (等待 5s)..."
    sleep 5
    if curl -sf --connect-timeout 10 -m 15 \
         -H "Authorization: Bearer $API_KEY" \
         "$TUNNEL_URL/v1/models" >/dev/null 2>&1; then
      echo "  公网验证: OK"
    else
      echo "  公网验证: 尚未就绪, 可能需等待更长时间 (FRP 建连中)"
      echo "  可手动验证: curl -H 'Authorization: Bearer $API_KEY' $TUNNEL_URL/v1/models"
    fi
  else
    echo "  [警告] 未提取到公网 URL, 查看上方输出"
    echo "  排查: $RC_TUNNEL status ; $RC_TUNNEL logs --lines 50"
  fi
else
  echo "  [警告] rc-tunnel 未安装, 跳过公网暴露"
  echo "  可手动安装: /var/run/secrets/frp-self-service/install"
fi

echo "===== 状态 ====="
echo "  sshd:$(pgrep -x sshd >/dev/null 2>&1 && echo ON || echo OFF) llama:$(pgrep -f llama-server >/dev/null 2>&1 && echo ON || echo OFF) tunnel:$([ -f "$TUNNEL_URL_FILE" ] && echo "$(cat "$TUNNEL_URL_FILE")" || echo OFF)"
echo "===== done ====="
