#!/usr/bin/env bash
# build-frontend.sh — 构建 React 前端 (生产模式)
#
# 用法: bash scripts/build-frontend.sh
# 产出: web/dist/ 目录 (FastAPI 静态托管)
set -euo pipefail

WEB_DIR="$(cd "$(dirname "$0")/.." && pwd)/web"
cd "$WEB_DIR"

echo "===== [1/3] 检查 Node.js ====="
if ! command -v node >/dev/null 2>&1; then
  echo "  Node.js 未安装, 安装中..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - 2>/dev/null
  apt-get install -y nodejs
fi
echo "  Node: $(node --version)"
echo "  npm:  $(npm --version)"

echo "===== [2/3] 安装依赖 ====="
npm install --silent 2>&1 | tail -3

echo "===== [3/3] 生产构建 ====="
npm run build 2>&1 | tail -10

echo ""
echo "===== 验证 ====="
if [ -f "dist/index.html" ]; then
  echo "  [OK] web/dist/index.html 存在"
  echo "  产出文件:"
  find dist -type f | head -20 | sed 's/^/    /'
  SIZE=$(du -sh dist | awk '{print $1}')
  echo "  总大小: $SIZE"
else
  echo "  [FAIL] web/dist/index.html 不存在, 构建失败"
  exit 1
fi
echo "===== done ====="
