#!/usr/bin/env bash
# 在 Oracle Cloud Ubuntu 22.04/24.04（ARM 或 x86）上一键部署。
# 用法：先 git clone 仓库，然后在仓库根目录运行  bash deploy/setup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
WEB_ROOT=/var/www/board
PY_VERSION=3.14

echo "==> 仓库目录: $REPO_DIR  运行用户: $RUN_USER"

echo "==> 系统依赖"
sudo apt-get update -y
sudo apt-get install -y nginx git curl ca-certificates build-essential iptables-persistent
sudo timedatectl set-timezone Asia/Shanghai || true

if ! command -v node >/dev/null || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]; then
  echo "==> 安装 Node.js 22"
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
  echo "==> 安装 uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

echo "==> 后端 Python 环境"
cd "$REPO_DIR/backend"
[ -d .venv ] || "$UV" venv --python "$PY_VERSION" .venv
"$UV" pip install --python .venv/bin/python -r requirements.txt
mkdir -p data
if [ ! -f .env ]; then
  cat > .env <<EOF
JWT_SECRET=$(openssl rand -hex 32)
CORS_ORIGINS=http://localhost
EOF
  echo "    已生成 backend/.env（随机 JWT_SECRET）"
fi

echo "==> 新闻 Agent 服务"
cd "$REPO_DIR/news-agent-service"
npm ci --omit=dev || npm install --omit=dev
[ -f .env ] || cp .env.example .env

echo "==> 构建前端"
cd "$REPO_DIR/frontend"
npm ci || npm install
npm run build
sudo mkdir -p "$WEB_ROOT"
sudo rm -rf "$WEB_ROOT"/*
sudo cp -r dist/* "$WEB_ROOT"/

echo "==> systemd 服务"
sudo tee /etc/systemd/system/board-backend.service >/dev/null <<EOF
[Unit]
Description=Board FastAPI backend
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$REPO_DIR/backend
ExecStart=$REPO_DIR/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/board-agent.service >/dev/null <<EOF
[Unit]
Description=Board news agent
After=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$REPO_DIR/news-agent-service
ExecStart=$(command -v node) server.js
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "==> Nginx"
sudo tee /etc/nginx/sites-available/board >/dev/null <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    root /var/www/board;
    index index.html;
    client_max_body_size 20m;

    location /api/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
    }

    location /agent-api/ {
        proxy_pass http://127.0.0.1:4001/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_read_timeout 300s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/board /etc/nginx/sites-enabled/board
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t

echo "==> 放行 80/443 端口（Oracle 镜像默认 iptables 会拦）"
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 5 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
done
sudo netfilter-persistent save

sudo systemctl daemon-reload
sudo systemctl enable --now board-backend board-agent
sudo systemctl restart board-backend board-agent nginx

sleep 3
IP="$(curl -s --max-time 5 ifconfig.me || echo '<服务器公网IP>')"
echo
echo "部署完成：http://$IP"
echo "默认账号 admin / 123456，登录后请马上改密码。"
echo "查看日志：journalctl -u board-backend -f"
