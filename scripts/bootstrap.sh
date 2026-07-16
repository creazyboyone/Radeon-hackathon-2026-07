#!/usr/bin/env bash
# bootstrap.sh - 重启后一键恢复：SSH + 模型 + llama-server
# 存放: /workspace/bootstrap.sh  用法: bash /workspace/bootstrap.sh
set -uo pipefail
LOG=/workspace/bootstrap.log
exec > >(tee -a "$LOG") 2>&1
echo "===== bootstrap $(date) ====="

SSH_PUBKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILE26aXvfany6iLqzLswaV/UoKGmbEjQq/ZFD+TV0aPJ 1020401390@qq.com'
MODEL_PATH=/workspace/Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf
MODEL_REPO=Jackrong/Qwopus3.6-27B-v2-MTP-GGUF
MODEL_FILE=Qwopus3.6-27B-v2-MTP-Q4_K_M.gguf
LLAMA_DIR=/opt/llama.cpp
PORT=8080
API_KEY=fengfeng123

# 1. SSH
echo "[1/4] 安装 SSH..."
if ! command -v sshd >/dev/null 2>&1; then
  apt-get update -y && apt-get install -y openssh-server
fi
mkdir -p /run/sshd /root/.ssh && chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
grep -qF "$SSH_PUBKEY" /root/.ssh/authorized_keys 2>/dev/null || echo "$SSH_PUBKEY" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
pgrep -x sshd >/dev/null 2>&1 || /usr/sbin/sshd
echo "  sshd: $(pgrep -x sshd >/dev/null 2>&1 && echo OK || echo FAIL)"

# 2. modelscope
echo "[2/4] 安装 modelscope..."
python3 -c "import modelscope" 2>/dev/null || pip3 install -U modelscope --break-system-packages -q
echo "  modelscope: $(python3 -c 'import modelscope;print(modelscope.__version__)' 2>/dev/null || echo FAIL)"

# 3. 模型
echo "[3/4] 检查模型..."
if [ -f "$MODEL_PATH" ]; then
  echo "  已存在: $(ls -lh "$MODEL_PATH" | awk '{print $5}')"
else
  echo "  下载 $MODEL_FILE ..."
  modelscope download --model "$MODEL_REPO" --local_dir /workspace "$MODEL_FILE" \
    && echo "  下载完成: $(ls -lh "$MODEL_PATH" | awk '{print $5}')" \
    || echo "  [警告] 下载失败"
fi

# 4. llama-server
echo "[4/4] 启动 llama-server..."
if pgrep -f "llama-server" >/dev/null 2>&1; then
  echo "  已在运行"
else
  if [ -f "$MODEL_PATH" ] && [ -x "$LLAMA_DIR/llama-server" ]; then
    cd "$LLAMA_DIR"
    HIP_VISIBLE_DEVICES=0 nohup ./llama-server \
      -m "$MODEL_PATH" -c 131072 -ngl 999 \
      -ctk q8_0 -ctv q8_0 -fa on --jinja \
      -t 16 -b 512 -ub 512 -np 1 \
      --host 0.0.0.0 --port "$PORT" --api-key "$API_KEY" \
      > /workspace/llama-server.log 2>&1 &
    echo "  启动中 (PID $!), 日志 /workspace/llama-server.log"
    sleep 10
  else
    echo "  [警告] 模型或二进制缺失，跳过"
  fi
fi

echo "===== 状态 ====="
echo "  sshd:$(pgrep -x sshd >/dev/null 2>&1 && echo ON || echo OFF) llama:$(pgrep -f llama-server >/dev/null 2>&1 && echo ON || echo OFF)"
echo "===== done ====="
