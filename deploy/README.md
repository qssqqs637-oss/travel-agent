# 云服务器部署指南

目标：Ubuntu 云服务器 + Nginx + systemd，前端静态文件由 Nginx 提供，后端只监听 `127.0.0.1:8000`。

下面用两个占位符，部署时替换成你的：

- 域名：`travel.example.com`
- 项目目录：`/opt/travel-planner`

---

## 0. 前提

- 一台 Ubuntu 22.04/24.04 云服务器
- 域名已解析到服务器公网 IP（没有域名可以先用 IP，跳过 HTTPS）
- 云厂商安全组放行 **80 / 443**，不要放行 8000

---

## 1. 安装依赖

```bash
sudo apt update
sudo apt install -y nginx git sqlite3 build-essential

# Python 3.12+（Ubuntu 24.04 自带 3.12 即可；需要 3.13 时用 deadsnakes）
sudo apt install -y python3 python3-venv python3-pip

# Node 22（Vite 要求 ^22.18 或 >=24.12）
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

---

## 2. 上传代码

```bash
sudo mkdir -p /opt/travel-planner
# 用 git clone / scp / rsync 把项目放到 /opt/travel-planner
```

目录里应该有 `main.py`、`graph/`、`travel-frontend/`、`requirements.txt`、`deploy/`。

---

## 3. 配置后端

```bash
cd /opt/travel-planner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`：

```dotenv
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_API_URL=https://api.deepseek.com
AMAP_API_KEY=...
CHECKPOINT_DB=/opt/travel-planner/data/checkpoints.sqlite
ROOT_PATH=/api
```

创建数据目录并设置权限：

```bash
sudo mkdir -p /opt/travel-planner/data
sudo chown -R www-data:www-data /opt/travel-planner
sudo chmod 600 /opt/travel-planner/.env
```

先手动试跑：

```bash
sudo -u www-data /opt/travel-planner/.venv/bin/uvicorn \
  main:app --host 127.0.0.1 --port 8000 --workers 1
```

看到 `Application startup complete.` 和 `[amap] enabled` 即可，Ctrl+C 停掉。

> `--workers 1` 是必须的：SQLite checkpointer 多 worker 会争抢写锁。

---

## 4. 构建前端

```bash
cd /opt/travel-planner/travel-frontend
npm ci
VITE_API_BASE=/api npm run build

sudo mkdir -p /var/www/travel-planner
sudo rm -rf /var/www/travel-planner/*
sudo cp -r dist/* /var/www/travel-planner/
```

`VITE_API_BASE=/api` 会被打进静态文件，前端请求 `/api/chat`、`/api/reset`。

---

## 5. systemd 托管后端

```bash
sudo cp /opt/travel-planner/deploy/travel-planner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now travel-planner
sudo systemctl status travel-planner
```

看日志：

```bash
sudo journalctl -u travel-planner -f
```

---

## 6. Nginx

```bash
sudo cp /opt/travel-planner/deploy/nginx.conf /etc/nginx/sites-available/travel-planner
sudo sed -i 's/travel.example.com/你的域名/' /etc/nginx/sites-available/travel-planner
sudo ln -sf /etc/nginx/sites-available/travel-planner /etc/nginx/sites-enabled/travel-planner
sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t
sudo systemctl reload nginx
```

没有域名时，把 `server_name` 改成 `_`。

---

## 7. HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名
```

---

## 8. 验证

```bash
curl https://你的域名/api/health
# {"status":"ok"}
```

流式接口：

```bash
curl -N -X POST https://你的域名/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"北京旅行三天 10月29日","session_id":"deploy-test-001"}'
```

应该逐条看到 `data: {"type":"token",...}`。

浏览器打开域名，测：

1. 规划一次行程 → 文字流式 + 卡片
2. 点“新对话” → 重新追问
3. 重启后端 → 同一标签页继续问 → 会话还在（SQLite）

---

## 9. 更新部署

```bash
cd /opt/travel-planner
git pull                # 或重新上传代码

source .venv/bin/activate
pip install -r requirements.txt

cd travel-frontend
npm ci
VITE_API_BASE=/api npm run build
sudo rm -rf /var/www/travel-planner/*
sudo cp -r dist/* /var/www/travel-planner/

sudo systemctl restart travel-planner
sudo systemctl reload nginx
```

---

## 10. 备份 SQLite

```bash
sudo mkdir -p /opt/backup
sudo sqlite3 /opt/travel-planner/data/checkpoints.sqlite \
  ".backup '/opt/backup/checkpoints-$(date +%F).sqlite'"
```

建议加一条 cron，每天备份一次。

---

## 常见问题

| 现象 | 原因 |
|---|---|
| 502 Bad Gateway | 后端没起，或 `www-data` 没有目录/数据库权限 |
| 流式变成一次性返回 | Nginx 少了 `proxy_buffering off` |
| 前端仍然请求 `127.0.0.1:8000` | 构建时没加 `VITE_API_BASE=/api` |
| Swagger 打不开 | `.env` 没设 `ROOT_PATH=/api` |
| `database is locked` | 启动时用了多个 worker |
| 高德 `INVALID_USER_KEY` | Key 不是 Web服务，或 IP 白名单没加服务器公网 IP |
| 高德 `USERKEY_PLAT_NOMATCH` | Key 类型选错，重新建一个 Web服务 Key |

---

## 安全清单

- `.env` 权限 `600`，不要提交到 git
- 8000 端口只监听 `127.0.0.1`，不要对公网开放
- 安全组只开 80/443
- 高德 Key 配 IP 白名单
- DeepSeek Key 如果泄露，立即在控制台吊销
