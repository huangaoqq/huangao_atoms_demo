# 阿里云 ECS 部署指南（Ubuntu 22.04 + PostgreSQL + Gunicorn + Nginx）

本文以 **Ubuntu 22.04 LTS** 为例，在一台阿里云 ECS 上部署本项目。示例域名使用 `demo.example.com`，请替换成自己的域名或 ECS 公网 IP。

最终链路：

```text
公网用户
  → 阿里云安全组 80/443
  → Nginx（静态文件 / 反向代理）
  → Gunicorn 127.0.0.1:8000
  → Django 4.2
  → PostgreSQL 127.0.0.1:5432
  → DeepSeek / 千问 API（任一失败时自动 Mock，赛马模式自动评分选优）
  → 检测到服务端需求时生成多文件后端项目，默认 Django
```

---

## 1. 准备 ECS

建议配置：

- 镜像：Ubuntu 22.04 LTS 64 位
- 规格：2 vCPU / 2 GiB 起（生成接口本身是 I/O 等待，CPU 压力不大）
- 磁盘：40 GiB 起
- 公网 IP 或已解析到 ECS 的域名

### 1.1 安全组

在阿里云控制台 → ECS → 安全组，添加入方向规则：

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| 22 | 你的固定公网 IP/32 | SSH 管理（不要对全网开放） |
| 80 | 0.0.0.0/0 | HTTP |
| 443 | 0.0.0.0/0 | HTTPS |

不要对公网开放 `5432`（PostgreSQL）和 `8000`（Gunicorn）。

### 1.2 登录服务器

```bash
ssh root@<ECS_PUBLIC_IP>
```

创建部署用户（如果已有普通用户可跳过）：

```bash
adduser deploy
usermod -aG sudo deploy
su - deploy
```

以下命令默认在 `deploy` 用户下执行。

---

## 2. 安装系统依赖

```bash
sudo apt update
sudo apt install -y \
  python3.10 python3.10-venv python3.10-dev \
  python3-pip build-essential \
  postgresql postgresql-contrib \
  nginx git curl
```

确认版本：

```bash
python3.10 --version
psql --version
nginx -v
```

> 如果使用 Alibaba Cloud Linux / CentOS，可用 `dnf install python3.10 postgresql-server postgresql-contrib nginx git`，后续配置思路一致，服务名可能略有差异。

启动并设置开机自启：

```bash
sudo systemctl enable --now postgresql
sudo systemctl enable --now nginx
```

---

## 3. 配置 PostgreSQL

### 3.1 创建数据库和用户

```bash
sudo -u postgres psql
```

在 psql 中执行（请把密码替换成强密码）：

```sql
CREATE USER webgen WITH PASSWORD 'Replace-With-A-Strong-Password';
CREATE DATABASE webgen OWNER webgen;
ALTER ROLE webgen SET client_encoding TO 'utf8';
ALTER ROLE webgen SET default_transaction_isolation TO 'read committed';
ALTER ROLE webgen SET timezone TO 'Asia/Shanghai';
\q
```

### 3.2 验证本地密码登录

```bash
psql "postgresql://webgen:Replace-With-A-Strong-Password@127.0.0.1:5432/webgen" -c "SELECT version();"
```

如果提示认证失败，检查：

```bash
sudo nano /etc/postgresql/14/main/pg_hba.conf
```

确认存在类似规则：

```text
host    webgen    webgen    127.0.0.1/32    scram-sha-256
```

修改后重启：

```bash
sudo systemctl restart postgresql
```

### 3.3 密码 URL 编码

`DATABASE_URL` 中的密码如果包含 `@ : / # ? %` 等字符，必须 URL 编码。例如密码 `P@ss#1` 要写成 `P%40ss%231`。生成编码可用：

```bash
python3 -c "import urllib.parse; print(urllib.parse.quote('你的密码', safe=''))"
```

---

## 4. 上传项目

### 4.1 创建部署目录

```bash
sudo mkdir -p /srv/promptforge
sudo chown -R deploy:www-data /srv/promptforge
sudo chmod 750 /srv/promptforge
```

### 4.2 上传代码

任选一种方式：

方式 A：从本机 rsync（在本机执行，排除虚拟环境和密钥）：

```bash
rsync -av --exclude ".venv" --exclude ".env" --exclude "db.sqlite3" \
  ./ deploy@<ECS_PUBLIC_IP>:/srv/promptforge/
```

方式 B：在 ECS 上用 Git：

```bash
cd /srv/promptforge
git clone <你的仓库地址> .
```

项目最终目录应包含 `manage.py`、`webgen/`、`generator/`、`requirements.txt`。

### 4.3 创建虚拟环境并安装依赖

```bash
cd /srv/promptforge
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

> `requirements.txt` 中已包含 PostgreSQL 驱动 `psycopg[binary]` 和 Linux 下需要的 Gunicorn。

---

## 5. 配置生产环境变量

生成 Django Secret Key：

```bash
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(50))"
```

创建 `.env`：

```bash
cd /srv/promptforge
cp .env.example .env
chmod 640 .env
sudo chown root:www-data .env
sudo nano .env
```

生产配置示例：

```dotenv
DJANGO_SECRET_KEY=<上一步生成的随机值>
DJANGO_DEBUG=False
ALLOWED_HOSTS=demo.example.com,<ECS_PUBLIC_IP>,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://demo.example.com

DATABASE_URL=postgresql://webgen:Replace-With-A-Strong-Password@127.0.0.1:5432/webgen
DB_SSL_REQUIRE=False

LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_TIMEOUT=30

# 千问 Token Plan Key（sk-sp- 开头）使用 token-plan 兼容端点
QWEN_API_KEY=sk-sp-xxxxxxxxxxxxxxxxxxxxxxxx
QWEN_BASE_URL=https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1
QWEN_MODEL=qwen3.6-flash
QWEN_TIMEOUT=60
QWEN_VISION_MODEL=qwen3.8-flash
QWEN_VISION_TIMEOUT=30
BACKEND_TIMEOUT=90
QWEN_BACKEND_TIMEOUT=180

# 完成 HTTPS 后再打开以下三项
DJANGO_SECURE_SSL_REDIRECT=False
DJANGO_SECURE_COOKIES=False
DJANGO_HSTS_SECONDS=0
```

说明：

- `DJANGO_DEBUG=False` 时，如果没有配置 `DATABASE_URL`，项目会拒绝启动，避免生产环境误用 SQLite。
- ECS 本机连接 PostgreSQL 时 `DB_SSL_REQUIRE=False`；如果使用阿里云 RDS 并开启 SSL，按 RDS 要求改为 `True`。
- 修改 `.env` 后必须重启 Gunicorn 才会生效。
- 不要把 `.env` 提交到 Git，也不要粘贴到聊天工具中。DeepSeek Key 与千问 Key 已在需求文本中出现时，建议部署前在各自控制台轮换。
- 服务器必须能出网访问 `api.deepseek.com` 和 `token-plan.maas.qianwenaiapi.com`（默认安全组出方向允许全部，若加固了出站规则需放行 443）。

---

## 6. 初始化数据库和静态文件

```bash
cd /srv/promptforge
source .venv/bin/activate

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
```

`check --deploy` 在启用 HTTPS 之前出现 HSTS / Secure Cookie 警告是正常的；完成第 9 节后应基本消除。

创建管理员（可选）：

```bash
python manage.py createsuperuser
```

设置目录权限，确保 Nginx / Gunicorn 运行用户可以读取：

```bash
sudo chown -R deploy:www-data /srv/promptforge
sudo find /srv/promptforge -type d -exec chmod 750 {} \;
sudo find /srv/promptforge -type f -exec chmod 640 {} \;
sudo chmod 750 /srv/promptforge/.venv/bin/*
sudo chmod 640 /srv/promptforge/.env
```

---

## 7. 配置 Gunicorn（systemd）

创建服务文件：

```bash
sudo nano /etc/systemd/system/promptforge.service
```

内容：

```ini
[Unit]
Description=PromptForge Django app (Gunicorn)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/srv/promptforge
EnvironmentFile=/srv/promptforge/.env
ExecStart=/srv/promptforge/.venv/bin/gunicorn \
    --workers 3 \
    --bind 127.0.0.1:8000 \
    --timeout 240 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

关键点：

- `--timeout 240` 明显大于千问全栈生成预算 180 秒；生成任务本身在后台线程执行，HTTP 请求会立即返回 202。
- `EnvironmentFile` 读取 `.env`；Django 内部也会用 python-dotenv 读取，二者不冲突。
- 只监听 `127.0.0.1:8000`，公网访问统一经过 Nginx。
- 生成任务在 Gunicorn worker 内以后台线程运行，返回页面后仍会继续；不要把 `--workers` 设置为 1，建议 3 个以上。
- 使用 PostgreSQL 承载并发任务和状态更新。部署重启会中断正在运行的后台线程，升级前请避开生成高峰。

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now promptforge
sudo systemctl status promptforge --no-pager
```

查看日志：

```bash
sudo journalctl -u promptforge -f
```

本机验证：

```bash
curl -I -H "Host: demo.example.com" http://127.0.0.1:8000/
```

预期返回 `200 OK`。

---

## 8. 配置 Nginx

创建站点配置：

```bash
sudo nano /etc/nginx/sites-available/promptforge
```

内容：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name demo.example.com;

    client_max_body_size 10m;

    location /static/ {
        alias /srv/promptforge/staticfiles/;
        expires 7d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # NDJSON 生成阶段与进度必须实时推送，关闭代理缓冲避免延迟到结束才出现
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;

        # 千问复杂页面最长约 60 秒，代理层必须留足余量
        proxy_connect_timeout 10s;
        proxy_send_timeout 240s;
        proxy_read_timeout 240s;
    }
}
```

启用：

```bash
sudo ln -s /etc/nginx/sites-available/promptforge /etc/nginx/sites-enabled/promptforge
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

验证：

```bash
curl -I http://demo.example.com/
```

如果使用纯公网 IP 访问，把 `server_name` 改成 `_`，并将公网 IP 加入 `ALLOWED_HOSTS`。

---

## 9. 配置 HTTPS（强烈建议）

前置条件：域名 A 记录已经指向 ECS 公网 IP。

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d demo.example.com
```

Certbot 会自动修改 Nginx 配置并配置续期。验证自动续期：

```bash
sudo certbot renew --dry-run
```

HTTPS 正常后编辑 `.env`：

```dotenv
CSRF_TRUSTED_ORIGINS=https://demo.example.com
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SECURE_COOKIES=True
DJANGO_HSTS_SECONDS=31536000
```

重启应用：

```bash
sudo systemctl restart promptforge
```

> 首次启用 HSTS 前请确认 HTTPS 已稳定；HSTS 会让浏览器在有效期内强制使用 HTTPS。

再次检查：

```bash
cd /srv/promptforge
/srv/promptforge/.venv/bin/python manage.py check --deploy
```

### 发布路由说明

- 版本发布后由 Django 直接提供 `https://demo.example.com/p/<slug>/`
- 无需额外 Nginx location；会走同一个反向代理
- 已发布页面响应包含 `Content-Security-Policy: sandbox allow-scripts ...`，生成页面无法读取主站 Cookie / localStorage
- 全栈项目只发布 `PREVIEW_HTML` 静态预览，生成的后端代码不会在当前 ECS Django 进程中执行；完整项目请下载 ZIP
- 取消发布或删除对应版本后，原发布 URL 立即返回 404
- 发布记录写入 PostgreSQL，重启服务后仍然有效

---

## 10. 防火墙

如果 ECS 内部启用了 UFW：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

同时确认阿里云安全组已经放行 80/443。

---

## 11. 部署验收清单

1. `sudo systemctl status promptforge` 为 active (running)
2. `sudo nginx -t` 通过
3. 首页正常显示需求输入框、左侧项目列表（默认折叠版本）和右侧五个带界面缩略图的模板
4. 输入需求后自动进入 `/workspace/`，左侧阶段、进度和模型处理阶段持续刷新，不应等生成结束才一次性出现
5. 生成完成后右侧自动出现预览；切换「查看源码」后左侧显示完整项目目录树，点击文件右侧显示文件内容
6. 检查目录中包含后端模块、前端 templates/static、依赖清单、`.env.example` 和 `DEPLOY.md`，不是单 HTML
7. 在线修改开启时点击或拖拽框选预览中的组件，左侧对话会列出框选中的所有组件，并询问是否只修改框选组件
8. 选择“否”后，输入框可用，修改请求不携带选区限制；选择“是”后才按选区组件修改
9. 如果模型给出的是千问方案，在线修改助手应显示 `QW 机器人`；DeepSeek 方案显示 `DP 机器人`
10. 刷新 `/workspace/?history=...&version=...` 仍能打开对应版本
11. 返回项目主页，默认只看到项目级信息；点击箭头展开缩进版本列表，点击项目标题打开最新版本，点击具体版本打开对应版本
12. 点击版本「发布」，生成 `/p/<slug>/` 链接且能在浏览器正常打开；点击「取消发布」后原链接返回 404
13. 「下载」得到完整 ZIP，解压后包含前端/后端目录树、依赖清单、部署文档和预览文件；旧版单文件项目下载 HTML
14. 点击「开始生成」或模板后弹出模式选择框；选择双模型赛马时两个模型分别生成完整 Django 项目，按预览质量评分并保留高分方案
15. 点击「评分详情」，能查看两个模型的十维评分明细
16. 新建任意项目（含简单游戏）都应生成完整 Django 项目目录，而不是单 HTML
17. 清空一个模型 Key 后重启，赛马仍会自动选用另一个真实结果；两个 Key 都失效时生成完整的 Mock Django 计算器项目并正常入库
18. 生成中点击「项目主页」，后台任务继续执行，主页任务面板最终变为“已完成”并自动出现新项目
19. 用两个浏览器同时生成两个任务，两边都能独立完成，任务面板各自只显示本浏览器任务
20. 运行中任务卡片点击「删除」并确认后，任务立即从列表消失，后台不再生成项目；失败任务点击「删除」后记录消失
21. 打开带 `.fade-in` 入场动画的预览（如个人作品集），页面主体、项目卡片和导航交互都应正常显示，不能只显示导航后一片空白
22. 删除任务后刷新页面或重新查询 `/api/jobs/`，已删除任务不应重新出现
23. 在工作台提交在线修改后立即返回项目主页，任务面板应显示“修改「项目名」vN”且继续运行；完成后自动刷新项目列表并出现新版本
24. 打开已发布的 2048 游戏，产生最高分后刷新页面，最高分和依赖 `localStorage` 的状态应保留；发布页 URL 会出现 `__pf_store` 参数
25. 提交需求后弹出模式选择框，展示历史赛马记录、双方胜率和推荐模型；可选择推荐模型、另一个单模型或双模型赛马；关闭弹窗则不生成，确认后任务按所选模式与模型执行
26. 直接访问 `http://<ECS_IP>:8000` 应失败（Gunicorn 仅监听本机）

---

## 12. 常见问题排查

### 502 Bad Gateway

```bash
sudo systemctl status promptforge --no-pager
sudo journalctl -u promptforge -n 100 --no-pager
```

常见原因：Gunicorn 未启动、`.env` 权限不足、`DJANGO_SECRET_KEY` 缺失、数据库连接失败。

### 400 Bad Request / DisallowedHost

把域名或公网 IP 加入 `.env`：

```dotenv
ALLOWED_HOSTS=demo.example.com,<ECS_PUBLIC_IP>,127.0.0.1,localhost
```

然后重启 `promptforge`。

### 403 CSRF verification failed

确认 HTTPS 域名写入：

```dotenv
CSRF_TRUSTED_ORIGINS=https://demo.example.com
```

并检查 Nginx 是否传递了正确的 `Host` 和 `X-Forwarded-Proto`。

### 数据库连接失败

```bash
psql "postgresql://webgen:<password>@127.0.0.1:5432/webgen" -c "select 1;"
```

检查密码是否 URL 编码、`pg_hba.conf`、PostgreSQL 服务状态。

### 生成阶段和进度长时间不刷新

Nginx 缓冲了 NDJSON 流。确认 `location /` 中包含：

```nginx
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_buffering off;
proxy_cache off;
chunked_transfer_encoding on;
```

修改后执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

同时确认 Gunicorn 使用 `--timeout 240`。

### 生成请求约 30 秒或 60 秒才返回 Mock

说明对应模型调用超时或失败，这是预期的兜底行为。检查：

```bash
sudo journalctl -u promptforge -n 100 --no-pager
```

重点看 `DeepSeek request failed; using mock HTML` 或 `千问 request failed; using mock HTML` 日志。同时确认服务器能访问两个接口：

```bash
curl -I https://api.deepseek.com
curl -I https://token-plan.maas.qianwenaiapi.com
```

如果使用千问 Token Plan Key，必须搭配 `token-plan...qianwenaiapi.com` 端点；通用 DashScope Key 才使用 `dashscope.aliyuncs.com`，两类 Key 不能混用。若千问生成较慢，可保留已验证的 `qwen3.6-flash`，或在模型额度允许时切换其他 `QWEN_MODEL`。

### Nginx/Gunicorn 超时

- DeepSeek `LLM_TIMEOUT` 为 30 秒，千问 `QWEN_TIMEOUT` 为 60 秒
- 全栈项目后台生成：DeepSeek `BACKEND_TIMEOUT=90` 秒，千问 `QWEN_BACKEND_TIMEOUT=180` 秒
- Gunicorn `--timeout` 设为 240 秒
- Nginx `proxy_read_timeout` 设为 240 秒

后台生成接口会立即返回 202，模型调用在后台线程继续；预留更大的 Gunicorn/Nginx 超时用于旧版同步流式接口和极端网络情况。建议至少：Nginx ≥ Gunicorn ≥ 千问后台预算 180 秒。

### 管理后台样式丢失

```bash
source /srv/promptforge/.venv/bin/activate
cd /srv/promptforge
python manage.py collectstatic --noinput
```

并检查 Nginx `/static/` 路径。

---

## 13. 更新发布流程

在 ECS 上：

```bash
cd /srv/promptforge
source .venv/bin/activate

# 1. 拉取/上传新代码（不要覆盖 .env）
# git pull 或 rsync

# 2. 安装可能新增的依赖
pip install -r requirements.txt

# 3. 数据库迁移
python manage.py migrate

# 4. 收集静态文件
python manage.py collectstatic --noinput

# 5. 重启
sudo systemctl restart promptforge
sudo systemctl reload nginx
```

---

## 14. 数据备份

每天备份 PostgreSQL：

```bash
mkdir -p /srv/backups
pg_dump "postgresql://webgen:<password>@127.0.0.1:5432/webgen" \
  -Fc -f /srv/backups/promptforge_$(date +%F).dump
```

可加入 crontab：

```cron
0 3 * * * pg_dump "postgresql://webgen:<password>@127.0.0.1:5432/webgen" -Fc -f /srv/backups/promptforge_$(date +\%F).dump
```

恢复：

```bash
pg_restore -d "postgresql://webgen:<password>@127.0.0.1:5432/webgen" -c /srv/backups/promptforge_2026-01-01.dump
```
