# Loom · AI 页面工坊

用一句自然语言生成可交互的单文件 HTML 页面和配套后端代码。Django 4.2 + PostgreSQL + 原生 HTML/CSS/JS，适配 Render 与阿里云 ECS 部署。

## 功能

- 自然语言生成完整单文件 HTML，并生成配套后端代码；自动识别用户指定的后端语言，未指定时默认 Java。
- 普通模式完全遵循用户原始提示词，不附加任何风格限制；只有勾选赛马模式时才应用“庄重内敛 / 活泼艳丽”两种风格。
- 五阶段动态进度：需求规划 → 生成中 → 测试 → 验收 → 完成；成功后进度栏自动收起。
- 预览 / 源码切换；HTML 与后端代码均可查看。
- 生成记录写入 `GenerateHistory`，刷新页面不丢失，点击历史记录可重新打开。
- 历史记录支持删除和下载 ZIP 代码包（`index.html` + 对应语言的后端源码）；生成过程中也可以继续删除或下载历史记录。
- 赛马模式：庄重内敛与活泼艳丽两种风格并行生成、左右对比，选择后只保留所选方案并入库。
- 点选修改：在预览中点选元素，输入自然语言建议，在线重写 HTML 并刷新预览。
- 无 `LLM_API_KEY`、接口超时或异常时自动返回可交互的加法计算器 Mock，记录照常入库。
- iframe 使用 `sandbox="allow-scripts"`，生成代码不能访问主页面数据。

## 项目结构

```text
.
├── manage.py
├── prompt_studio/
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── generator/
│   ├── migrations/0001_initial.py
│   ├── models.py
│   ├── views.py
│   ├── urls.py
│   ├── services.py              # DeepSeek、Mock 兜底、语言识别
│   └── templates/index.html
├── requirements.txt
├── .env.example
├── .gitignore
└── render.yaml
```

## 本地运行

建议使用 Python 3.10–3.12。

```bash
cd /d/bishidemo

# 1. 创建虚拟环境（Git Bash / macOS / Linux）
python -m venv .venv
# Windows 若提示 python 是 Microsoft Store 别名，改用：py -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash
# source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate           # Windows CMD / PowerShell

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
# .env 已存在时会直接读取；从模板重新开始可用：
# cp .env.example .env
# 然后填写 SECRET_KEY、LLM_API_KEY 等。.env 已被 .gitignore 忽略。

# 4. 执行数据库迁移
python manage.py migrate

# 5. 启动
python manage.py runserver 8010
```

访问 <http://127.0.0.1:8010/>。若 8000 未被占用，也可直接运行 `python manage.py runserver` 并访问 <http://127.0.0.1:8000/>。

没有本地 PostgreSQL 时，把 `.env` 中的 `DATABASE_URL` 留空或注释掉即可回退 SQLite；生产环境仍使用 PostgreSQL。

生成 SECRET_KEY（可选）：

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### 数据库说明

- 项目通过 `dj_database_url` 解析环境变量 `DATABASE_URL`。
- 生产 / Render 使用 PostgreSQL：

  ```env
  DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/loom
  ```

- 未设置 `DATABASE_URL` 时回退 SQLite，便于无数据库环境快速验收；Render 会自动注入 Postgres 连接串。

迁移相关命令：

```bash
python manage.py migrate
python manage.py showmigrations generator
# 如修改了 models.py：
python manage.py makemigrations generator
python manage.py migrate
```

## Mock 模式测试

Mock 是完整的演示兜底链路，不依赖外网。

### 方式一：不配置密钥

将 `.env` 中的 `LLM_API_KEY` 留空：

```env
LLM_API_KEY=
```

或者在启动命令里覆盖为空：

```bash
LLM_API_KEY= python manage.py runserver 8010
```

随后：

1. 打开首页，输入任意需求，点击「生成界面」。
2. 应看到五阶段进度走完，并出现可交互的加法计算器。
3. 刷新页面，右侧「生成记录」仍然保留，点击记录能重新打开预览。
4. 勾选「赛马模式」再生成，会得到庄重内敛 / 活泼艳丽两份计算器，选择后只保存所选版本。
5. Mock 模式不支持 AI 在线修改，点选修改会提示并保留原界面。

验证数据库记录：

```bash
python manage.py shell -c "from generator.models import GenerateHistory; print(GenerateHistory.objects.count()); print(GenerateHistory.objects.first().prompt)"
```

### 方式二：模拟接口异常

保留任意 `LLM_API_KEY`，把地址改成不可达服务，即可验证异常兜底：

```bash
LLM_BASE_URL=http://127.0.0.1:9 python manage.py runserver 8010
```

发起生成后会走 `requests` 异常分支，页面仍返回 Mock 加法计算器，记录仍正常入库。真实的 DeepSeek 接口超时时间固定为 30 秒。

## 大模型配置

DeepSeek 使用 OpenAI 兼容的 `POST /chat/completions` 接口：

```env
LLM_API_KEY=你的密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

密钥只从环境变量读取，源码中没有硬编码。修改变量和 `.env` 后需要重启 `runserver`。

固定前端 system prompt：

```text
你是前端工程师，只输出完整可独立运行的单文件HTML，里面JS可以交互，不要额外解释、不要markdown代码块标记。
```

后端代码使用独立的 system prompt 生成，以免破坏题设的前端固定提示词；用户提示词中出现 Python、Node.js、Go、PHP、C#、Ruby、Rust、Kotlin、Java 等后端语言时自动识别，否则默认 Java。

## 实现说明

- 模型 `GenerateHistory` 包含题设要求的 `prompt`、`html_code`、`created_at`；额外增加 `backend_language`、`backend_code`，用于源码面板展示和修改后持久化。
- 普通模式不拼接赛马风格提示词，原始需求会原样发送给模型；无 Key 或接口异常时使用不带风格标签的中性 Mock。
- 赛马模式以“庄重内敛 / 活泼艳丽”为两个独立的用户提示词并行生成候选，不立即入库；庄重内敛强调正式、沉稳且有成体系的色彩（如藏蓝、酒红、墨绿搭配米白和金色点缀），不是黑白灰风格。用户点选后调用 `/api/select/` 保存所选版本，因此刷新后历史只出现被保留的方案。
- 点选修改通过注入到预览 iframe 的桥接脚本，用 `postMessage` 与父页面通信；iframe 仍只有 `allow-scripts`，不开启 `allow-same-origin`。
- 沙箱会禁止访问真实 `localStorage`。预览在生成脚本执行前注入内存版 `localStorage` / `sessionStorage` 兼容层，避免依赖本地存储的页面因 `SecurityError` 整体失效；下载后的页面仍使用浏览器原生存储。
- 修改接口失败时不会破坏当前预览，界面保留原 HTML，并给出提示。
- 大模型请求 30 秒超时；超时、网络错误、非 OpenAI 格式响应、空内容、非完整 HTML 都会触发 Mock 兜底。
- 历史记录删除使用 POST + CSRF；下载接口按后端语言生成 `backend.py`、`backend.java` 等文件，与 `index.html` 一起打包为 ZIP。

## API 摘要

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/generate/` | 普通生成或赛马生成，入参 `{ prompt, race_mode }` |
| POST | `/api/select/` | 保存赛马模式选中的方案 |
| POST | `/api/revise/` | 按点选元素和自然语言建议修改 HTML |
| POST | `/api/history/<id>/delete/` | 删除一条生成记录 |
| GET | `/api/history/<id>/download/` | 下载 HTML + 后端代码 ZIP 包 |

页面提交使用 Django CSRF Token。

## 阿里云 ECS 部署（公网 IP 直连）

本项目只需要一台 ECS，不使用 Nginx、反向代理或负载均衡。直接由 Gunicorn 监听 `0.0.0.0:8000`，通过 `http://ECS公网IP:8000` 访问。

以下以 **Ubuntu 22.04 / 24.04 LTS** 为例。若选择 Alibaba Cloud Linux 3，可将 `apt` 换成 `dnf`，并确认系统 Python 版本为 3.10–3.12。

> 注意：这种方式会把生成接口直接暴露在公网。项目没有登录和限流，公网可访问时任何人都可以消耗你的 DeepSeek 额度。个人演示可在安全组中只放行自己的公网 IP；长期公开使用建议后续增加 Nginx、HTTPS 和访问控制。

### 1. ECS 安全组与系统准备

在阿里云控制台配置实例安全组入方向规则：

- `22`：仅允许你的公网 IP，用于 SSH
- `8000`：允许你的公网 IP，或设置为 `0.0.0.0/0` 允许所有人访问
- 不要对外开放 `5432`；PostgreSQL 只需本机访问

登录 ECS 后安装依赖：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip postgresql postgresql-contrib

sudo systemctl enable --now postgresql
python3 --version   # 建议 3.10–3.12
```

如果启用了 UFW，还需要放行 SSH 和应用端口：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp
sudo ufw enable
```

### 2. 上传项目与安装依赖

将项目放到 `/opt/loom`，使用 `www-data` 作为运行用户：

```bash
sudo mkdir -p /opt/loom
sudo chown -R www-data:www-data /opt/loom

# 方式一：服务器直接拉取 Git 仓库
sudo -u www-data git clone <YOUR_REPO_URL> /opt/loom

# 方式二：先在本地上传到服务器 /tmp/loom，再复制：
# sudo cp -a /tmp/loom/. /opt/loom/
# sudo chown -R www-data:www-data /opt/loom
```

创建虚拟环境并安装依赖：

```bash
sudo -u www-data python3 -m venv /opt/loom/.venv
sudo -u www-data /opt/loom/.venv/bin/pip install --upgrade pip
sudo -u www-data /opt/loom/.venv/bin/pip install -r /opt/loom/requirements.txt
```

### 3. 创建 PostgreSQL 数据库

```bash
sudo -u postgres psql
```

在 psql 中执行：

```sql
CREATE USER loom WITH PASSWORD '替换成强密码';
CREATE DATABASE loom OWNER loom;
\q
```

验证连接：

```bash
psql "postgresql://loom:替换成强密码@127.0.0.1:5432/loom" -c "SELECT 1;"
```

如果 ECS 使用阿里云 RDS PostgreSQL，可跳过本机安装和建库步骤，直接把 RDS 连接串填入 `DATABASE_URL`，并在 RDS 白名单中放行 ECS 内网 IP。

> 数据库密码中如果包含 `@`、`:`、`/`、`#` 等字符，写入 `DATABASE_URL` 前需要做 URL 编码。

### 4. 配置生产环境变量

```bash
sudo -u www-data cp /opt/loom/.env.example /opt/loom/.env
sudo -u www-data nano /opt/loom/.env
sudo chmod 600 /opt/loom/.env
```

生产 `.env` 示例（把 `47.0.0.1` 换成你的 ECS 公网 IP）：

```env
DEBUG=False
SECRET_KEY=替换成长随机字符串
ALLOWED_HOSTS=47.0.0.1,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=http://47.0.0.1:8000

DATABASE_URL=postgresql://loom:替换成强密码@127.0.0.1:5432/loom

LLM_API_KEY=你的DeepSeekKey
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

说明：

- `ALLOWED_HOSTS` 必须包含 ECS 公网 IP 和 `127.0.0.1`。
- `CSRF_TRUSTED_ORIGINS` 使用 `http://公网IP:8000` 格式；如果之后绑定域名，再追加对应域名。
- `.env` 权限设为 `600`，不要提交到 Git，也不要把密钥写入 systemd 配置。
- 生成 Django 密钥：

  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(50))"
  ```

### 5. 执行数据库迁移

```bash
sudo -u www-data /opt/loom/.venv/bin/python /opt/loom/manage.py migrate
sudo -u www-data /opt/loom/.venv/bin/python /opt/loom/manage.py check
```

本项目的页面样式和脚本全部内联在模板中，不需要配置静态文件服务，也不需要 Nginx。

### 6. 配置 Gunicorn systemd 服务

创建 `/etc/systemd/system/loom.service`：

```bash
sudo tee /etc/systemd/system/loom.service >/dev/null <<'EOF'
[Unit]
Description=Loom AI Page Studio
After=network.target postgresql.service

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/loom
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/loom/.venv/bin/gunicorn prompt_studio.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 90 --access-logfile - --error-logfile -
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

启动并设置开机自启：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now loom
sudo systemctl status loom --no-pager
```

本机验证：

```bash
curl -I http://127.0.0.1:8000/
```

`--timeout 90` 必须大于大模型接口的 30 秒超时，避免赛马模式并行请求时被 Gunicorn 提前杀掉。项目会通过 `python-dotenv` 读取 `/opt/loom/.env`。

### 7. 通过公网 IP 访问

确认安全组已放行 `8000` 后，浏览器访问：

```text
http://ECS公网IP:8000
```

例如：`http://47.0.0.1:8000`

如果访问失败，依次检查：

```bash
sudo systemctl status loom --no-pager
sudo journalctl -u loom -n 100 --no-pager
sudo ss -lntp | grep 8000
```

并确认阿里云安全组和 UFW 都放行了 TCP 8000。

> 这是便于个人演示的 HTTP 直连方案。如果需要使用 80/443 标准端口、域名或 HTTPS，再额外配置 Nginx 反向代理即可，当前项目不需要改动代码。

### 8. 更新版本与日志

拉取代码并更新依赖、迁移数据库：

```bash
sudo -u www-data git -C /opt/loom pull
sudo -u www-data /opt/loom/.venv/bin/pip install -r /opt/loom/requirements.txt
sudo -u www-data /opt/loom/.venv/bin/python /opt/loom/manage.py migrate
sudo systemctl restart loom
```

查看实时日志：

```bash
sudo journalctl -u loom -f
```

常见问题：

| 现象 | 排查方向 |
| --- | --- |
| 浏览器无法连接 | 检查安全组、UFW 是否放行 8000，以及 Gunicorn 是否监听 `0.0.0.0:8000` |
| 400 DisallowedHost | `ALLOWED_HOSTS` 未包含 ECS 公网 IP |
| CSRF 403 | 检查 `CSRF_TRUSTED_ORIGINS` 是否为 `http://公网IP:8000`，并刷新页面 |
| 页面能打开但 API 报错 | 执行 `journalctl -u loom -n 100` 查看日志，并检查数据库连接 |
| 生成请求超时 | 确认 Gunicorn 启动参数包含 `--timeout 90` |
| 数据库连接失败 | 检查 PostgreSQL 是否启动、`DATABASE_URL` 密码是否需要 URL 编码，以及 RDS 白名单 |

## Render 部署

### 方式一：Blueprint（推荐）

仓库已附带 `render.yaml`。在 Render 中选择 **New → Blueprint**，连接仓库后会自动创建：

- Web Service：`loom-ai-page-studio`
- PostgreSQL：`loom-postgres`

部署时按提示填写 `LLM_API_KEY`（`sync: false` 的变量不会写进仓库）。其余变量由 Blueprint 配置或由 Render 自动生成。

### 方式二：手动创建 Web Service

- **Build Command**

  ```bash
  pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate
  ```

- **Start Command**

  ```bash
  gunicorn prompt_studio.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 90
  ```

  `--timeout 90` 必须大于模型接口的 30 秒超时，避免 Gunicorn 在等待并行生成时杀掉 worker。

- **Python 版本**：3.12.5（与 `render.yaml` 一致）。

### Render 环境变量

| 变量 | 是否必填 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | 必填 | Render PostgreSQL 的 Connection String；Blueprint 会自动关联 |
| `SECRET_KEY` | 必填 | Django 密钥；Blueprint 会随机生成，手动部署请自行生成 |
| `DEBUG` | 必填 | 生产设为 `False` |
| `ALLOWED_HOSTS` | 必填 | 可填 `.onrender.com`；绑定自定义域名时追加域名 |
| `LLM_API_KEY` | 建议填写 | DeepSeek API Key；留空仍可运行 Mock 模式 |
| `LLM_BASE_URL` | 可选 | 默认 `https://api.deepseek.com` |
| `LLM_MODEL` | 可选 | 默认 `deepseek-chat` |
| `CSRF_TRUSTED_ORIGINS` | 可选 | 自定义域名时填 `https://你的域名`；Render 内置域名会自动处理 |
| `PYTHON_VERSION` | 可选 | 建议 `3.12.5` |

部署后首次访问前，确认迁移已在 Build Command 中成功执行，并检查 `/api/generate/` 是否可正常返回。

## 安全提醒

- `.env`、数据库文件和虚拟环境都已加入 `.gitignore`。
- 如果曾把真实 API Key 写进聊天、日志或公开仓库，建议立即在 DeepSeek 控制台轮换。
- 生成的 HTML 来自大模型，仅在 `sandbox="allow-scripts"` 中运行；不要把它当作可信代码直接部署。
