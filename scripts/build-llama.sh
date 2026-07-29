#!/usr/bin/env bash
# build-llama.sh — 在 AMD Cloud 上编译 llama.cpp (ROCm/HIPBLAS)
#
# 用法: bash scripts/build-llama.sh
# 产出: /opt/llama.cpp/llama-server 二进制
# 前提: ROCm 已安装 (AMD Cloud 预装)
set -euo pipefail
LLAMA_DIR=/opt/llama.cpp
LLAMA_REPO=https://github.com/ggml-org/llama.cpp
LLAMA_COMMIT=035cd8f9a  # 与设计文档一致 (build 9766)

echo "===== [1/4] 检查 ROCm ====="
if ! command -v rocminfo >/dev/null 2>&1; then
  echo "  [错误] rocminfo 未找到, ROCm 未安装?"
  echo "  AMD Cloud 应预装 ROCm, 请检查环境"
  exit 1
fi
GPU_INFO=$(rocminfo 2>/dev/null | grep "Marketing Name" | head -1 || true)
echo "  GPU: ${GPU_INFO:-unknown}"
echo "  ROCm: OK"

echo "===== [2/4] 克隆 llama.cpp ====="
if [ -d "$LLAMA_DIR/.git" ]; then
  echo "  已存在, 拉取更新..."
  cd "$LLAMA_DIR"
  git fetch --all -q
  git checkout "$LLAMA_COMMIT" -q 2>/dev/null || echo "  (commit 不存在, 用最新)"
else
  git clone --recursive "$LLAMA_REPO" "$LLAMA_DIR"
  cd "$LLAMA_DIR"
  git checkout "$LLAMA_COMMIT" -q 2>/dev/null || echo "  (commit 不存在, 用最新)"
fi

echo "===== [3/4] 编译 (ROCm/HIPBLAS) ====="
# AMD Cloud W7900D = gfx1100, ROCm 官方支持, 无需 override
cmake -B build \
  -DGGML_HIPBLAS=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_CUDA_NO_PEER_MEMORY=ON \
  2>&1 | tail -5

cmake --build build --config Release -j"$(nproc)" \
  --target llama-server llama-bench \
  2>&1 | tail -5

echo "===== [4/4] 安装 ====="
# 符号链接: /opt/llama.cpp/llama-server -> build/bin/llama-server
# 统一路径, bootstrap.sh 和 setup-cloud.sh 都用 /opt/llama.cpp/llama-server
mkdir -p /opt/llama.cpp/build/bin
ln -sf build/bin/llama-server /opt/llama.cpp/llama-server
ln -sf build/bin/llama-bench /opt/llama.cpp/llama-bench
echo "  llama-server: $(ls -la /opt/llama.cpp/llama-server 2>/dev/null || echo 'FAIL')"
echo "  ldd 验证 ROCm 库:"
ldd /opt/llama.cpp/llama-server 2>/dev/null | grep -E 'amdhip64|hipblas|rocblas' || echo "  [警告] 未检测到 ROCm 库"
echo "===== done ====="
