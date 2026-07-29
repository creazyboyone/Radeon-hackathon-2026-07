#!/usr/bin/env bash
# export-cluster.sh — 导出 Hadoop 镜像 + 已初始化的数据卷
# 用法: bash scripts/export-cluster.sh
# 产出: C:/Users/feng/Desktop/aiops-cluster-backup.tar.gz (~5GB)
set -euo pipefail

COMPOSE_PROJECT="aiops-ha"
IMAGE_NAME="aiops-hadoop:step1"
BACKUP_DIR="C:/Users/feng/Desktop/aiops-cluster-backup"
OUTPUT_FILE="C:/Users/feng/Desktop/aiops-cluster-backup.tar.gz"

VOLUMES=(
  "hadoop01-data"
  "hadoop02-data"
  "hadoop03-data"
  "mysql-data"
)

echo "===== 导出 Hadoop 集群 (镜像 + 数据卷) ====="
echo ""

# 前置检查
if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${IMAGE_NAME}$"; then
  echo "[错误] 镜像 $IMAGE_NAME 不存在"
  exit 1
fi

mkdir -p "$BACKUP_DIR/volumes"
rm -f "$BACKUP_DIR/image.tar" "$BACKUP_DIR"/volumes/*.tar.gz 2>/dev/null || true

TOTAL=$((${#VOLUMES[@]} + 1))

# 1. 导出镜像
echo "[1/${TOTAL}] 导出镜像 (~4GB)..."
docker save "$IMAGE_NAME" -o "$BACKUP_DIR/image.tar"
echo "  完成: $(du -sh "$BACKUP_DIR/image.tar" | cut -f1)"

# 2. 导出数据卷
i=2
for vol in "${VOLUMES[@]}"; do
  FULL_NAME="${COMPOSE_PROJECT}_${vol}"
  echo "[${i}/${TOTAL}] 导出卷 $FULL_NAME ..."
  # 用 stdout 重定向, 不挂载 backup 目录, 绕过 Git Bash 路径问题
  MSYS_NO_PATHCONV=1 docker run --rm --entrypoint tar \
    -v "${FULL_NAME}:/data:ro" \
    "$IMAGE_NAME" \
    czf - -C /data . > "$BACKUP_DIR/volumes/${vol}.tar.gz"
  echo "  完成: $(du -sh "$BACKUP_DIR/volumes/${vol}.tar.gz" | cut -f1)"
  i=$((i + 1))
done

# 3. 打包
echo ""
echo "[3] 打包为 aiops-cluster-backup.tar.gz ..."
tar czf "$OUTPUT_FILE" -C "$BACKUP_DIR" .
rm -rf "$BACKUP_DIR"

echo ""
echo "===== 完成 ====="
echo "  产出: $OUTPUT_FILE ($(du -sh "$OUTPUT_FILE" | cut -f1))"
echo ""
echo "  传到 AMD Cloud: scp $OUTPUT_FILE root@<cloud>:/workspace/"
