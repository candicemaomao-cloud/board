#!/usr/bin/env bash
# 拉取最新代码并重启。用法：在仓库根目录运行  bash deploy/update.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

cd "$REPO_DIR"
git pull --ff-only

# Yahoo yfinance 常用 query2；部分云机房只解析得到 query1。把 query2 指到同一 IP。
if ! getent hosts query2.finance.yahoo.com >/dev/null 2>&1; then
  Q1_IP="$(getent ahostsv4 query1.finance.yahoo.com 2>/dev/null | awk '{print $1; exit}')"
  if [ -n "${Q1_IP:-}" ]; then
    echo "==> 修复 query2.finance.yahoo.com DNS → $Q1_IP"
    grep -q 'query2.finance.yahoo.com' /etc/hosts 2>/dev/null || \
      echo "$Q1_IP query2.finance.yahoo.com" | sudo tee -a /etc/hosts >/dev/null
  fi
fi

mkdir -p "$REPO_DIR/backend/data/yfinance_cache" "$REPO_DIR/backend/data/cache"

# 给后台服务可用的 HOME / 缓存目录，避免 yfinance SQLite 打不开
if [ -f /etc/systemd/system/board-backend.service ]; then
  sudo sed -i '/^Environment=HOME=/d;/^Environment=XDG_CACHE_HOME=/d' /etc/systemd/system/board-backend.service
  sudo sed -i "/^WorkingDirectory=/a Environment=HOME=$HOME\\nEnvironment=XDG_CACHE_HOME=$REPO_DIR/backend/data/cache" \
    /etc/systemd/system/board-backend.service
  sudo systemctl daemon-reload
fi

cd "$REPO_DIR/backend"
"$UV" pip install --python .venv/bin/python -r requirements.txt

cd "$REPO_DIR/news-agent-service"
npm ci --omit=dev || npm install --omit=dev

cd "$REPO_DIR/frontend"
npm ci || npm install
NODE_OPTIONS=--max-old-space-size=2048 npm run build
sudo rm -rf /var/www/board/*
sudo cp -r dist/* /var/www/board/

echo "==> 构建手机端"
cd "$REPO_DIR/mobile-web"
npm ci || npm install
NODE_OPTIONS=--max-old-space-size=2048 VITE_BASE=/m/ npm run build
sudo mkdir -p /var/www/board-mobile
sudo rm -rf /var/www/board-mobile/*
sudo cp -r dist/* /var/www/board-mobile/

# 确保 nginx 有 /m/ 路由（幂等追加）
if [ -f /etc/nginx/sites-available/board ] && ! grep -q 'location /m/' /etc/nginx/sites-available/board; then
  echo "==> 写入 nginx /m/ 手机端路由"
  sudo python3 - <<'PY'
from pathlib import Path
path = Path('/etc/nginx/sites-available/board')
text = path.read_text()
snippet = '''
    location /m/ {
        alias /var/www/board-mobile/;
        try_files $uri $uri/ @mobile_spa;
    }
    location @mobile_spa {
        rewrite ^ /m/index.html last;
    }
    location = /m/index.html {
        alias /var/www/board-mobile/index.html;
    }
'''
marker = '    location / {'
if 'location /m/' not in text and marker in text:
    text = text.replace(marker, snippet + '\n' + marker, 1)
    path.write_text(text)
PY
  sudo nginx -t && sudo systemctl reload nginx
fi

sudo systemctl restart board-backend board-agent
echo "更新完成"
echo "桌面端: /   手机端: /m/"
