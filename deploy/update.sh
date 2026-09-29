#!/usr/bin/env bash
# 拉取最新代码并重启。用法：在仓库根目录运行  bash deploy/update.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

cd "$REPO_DIR"
git pull --ff-only

cd "$REPO_DIR/backend"
"$UV" pip install --python .venv/bin/python -r requirements.txt

cd "$REPO_DIR/news-agent-service"
npm ci --omit=dev || npm install --omit=dev

cd "$REPO_DIR/frontend"
npm ci || npm install
NODE_OPTIONS=--max-old-space-size=2048 npm run build
sudo rm -rf /var/www/board/*
sudo cp -r dist/* /var/www/board/

sudo systemctl restart board-backend board-agent
echo "更新完成"
