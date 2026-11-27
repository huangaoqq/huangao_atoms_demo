# PromptForge · AI 应用生成工作台

基于 Django 4.2 的 AI 应用生成工作台。用户输入需求或点击模板后进入独立生成工作台，实时查看生成阶段和模型处理进度；生成完成后可预览、查看源码、在线修改。所有新项目默认生成完整的前后端网站应用（默认 Django），也支持用户在需求中指定后端语言和框架，并附带可独立部署的 DEPLOY.md。

- **生成前选择模型模式**：点击「开始生成」或模板后弹出模式选择框，可用历史胜出率更高的单模型、另一个单模型，或 DeepSeek + 千问双模型赛马
- **历史赛马统计与推荐**：弹窗汇总历史赛马项目双方胜场、胜率、平均分和逐项记录，并给胜出率更高的模型标记推荐；用户可接受推荐或改选
- **后台生成任务**：离开工作台返回项目主页后生成继续执行，主页实时显示任务进度和完成状态
- **在线修改使用优胜模型**：DeepSeek 方案显示 DP 机器人，千问方案显示 QW 机器人
- **在线修改后台执行**：提交后离开工作台也会继续，主页任务栏显示“修改「项目名」vN”，完成后自动生成新版本并刷新项目列表
- **组件点选/框选修改**：可以点击单个组件，也可以按住拖拽框选多个组件；助手列出选区中的所有组件，并询问是否只修改这些组件。选区出现后两个范围按钮会高亮闪烁，提醒先做选择；选“否”时不限制选区，按自然语言指令针对性修改
- **完整 Django 项目生成**：所有新需求都生成前后端分层、按功能模块组织目录的完整网站项目；默认 Django，用户指定后端语言/框架时优先采用
- **发布**：每个版本可部署到当前域名的独立路由 `/p/<slug>/`（全栈项目发布的是静态预览）
- **兜底**：任一模型 Key 为空或调用失败时使用内置加法计算器，接口不崩溃

## 项目文档

- [说明文档](docs/说明文档.md)：项目定位、架构、功能、数据模型、发布现状、与 Atoms.dev 的对比、正式发布方案和已知限制
- [测试文档](docs/测试文档.md)：测试范围、环境、策略、用例、缺陷回归和未覆盖项
- [验收清单](docs/验收清单.md)：演示版验收项和生产版 Gap

## 页面与流程

### 1. 项目主页 `/`

- 顶部保留需求输入框和「开始生成」；提交需求（或点击模板）后弹出历史赛马统计和模式选择，必须确认单模型或双模型赛马后才进入生成工作台，关闭弹窗则取消本次提交
- 下方分左右两栏：
  - 左侧：我的项目列表默认只展示项目级信息；点击项目标题直接打开最新版本预览，点击右侧箭头才展开缩进的版本列表；项目较多时列表独立滚动、卡片不会被压缩，展开后会自动滚动到该项目，确保版本行和下载/发布/删除操作完整可见；已有版本发布的项目卡片使用绿色背景，未发布项目保持白色
  - 右侧：五个带界面缩略图的模板，点击即直接开始生成
- 模板：2048 游戏、扫雷游戏、番茄钟、待办清单、个人作品集
- 项目版本操作：
  - 打开：进入工作台预览指定版本
  - 下载：新项目下载完整 ZIP（含前后端源码、依赖清单、部署文档和静态预览）；旧版单文件项目下载 HTML
  - 删除：删除版本；最后一个版本删除后项目一并删除
  - 发布：在当前域名生成独立访问路由
  - 取消发布：立即下线已发布页面
- 所有项目和发布状态均持久化数据库

### 2. 生成工作台 `/workspace/`

- 左侧实时生成进度：
  - 当前阶段列表（解析需求、模型生成、质量评审、保存项目）
  - 实时进度条和状态
  - DeepSeek / 千问模型状态
  - “模型生成”阶段会显示每个模型当前的内部处理阶段（结构规划、样式编写、交互实现、自检收尾）和已生成字符数，不展示原始代码流
- 右侧生成结果：
  - 预览 / 查看源码切换；源码区左侧展示完整目录树，点击文件在右侧显示内容
  - 在线修改：点击后在左侧打开 AI 助手对话面板，不遮挡右侧预览
  - 助手名称跟随项目优胜模型：DeepSeek 为 DP 机器人，千问为 QW 机器人
  - 组件点选/框选：在线修改开启后，可点击单个组件，或按住拖拽框选多个组件；助手会展示选区中的所有组件，并询问是否只修改选区组件。选“否”时不限制修改范围，按语言指令执行
  - 修改在后台任务中执行：提交后可以立即返回项目主页，主页「生成任务」会显示“修改「项目名」vN”，任务继续执行并自动生成新版本；点击任务可回到工作台查看实时修改进度
  - 修改结果以新版本链接返回，所有修改会话按版本历史保留；点击任意版本链接立即切换预览
  - 版本下拉切换
  - 已发布版本显示访问链接
  - 双模型赛马时展示评分结果和十维评分详情
- 生成完成后 URL 自动更新为 `/workspace/?history=<id>&version=<id>`；修改进行中会额外带有 `refineJob=<uuid>`，刷新或从主页任务回到工作台可恢复进度显示

## 项目结构

```text
.
├── manage.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── DEPLOY_ALIYUN_ECS.md
├── docs/
│   ├── 说明文档.md
│   ├── 测试文档.md
│   └── 验收清单.md
├── webgen/
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
└── generator/
    ├── models.py                 # GenerateHistory / GenerateVersion / PublishedPage / GenerationJob
    ├── views.py                  # 页面、后台任务、解析、版本操作、发布路由
    ├── scoring.py                # 100 分制十维网页质量评分引擎
    ├── tests.py                  # 评分/解析/任务/统计回归测试
    ├── urls.py
    ├── admin.py
    ├── migrations/
    │   ├── 0001_initial.py
    │   ├── 0002_publishedpage.py
    │   ├── 0003_generatehistory_backend_label_and_more.py
    │   └── 0004_generationjob.py
    └── templates/
        └── index.html            # 主页 + 工作台（原生 HTML/CSS/JS）
```

## 本地运行

### 1. 环境要求

- Python 3.10+
- PostgreSQL 14+（本地演示可退回 SQLite）

### 2. 安装依赖

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Windows Git Bash / Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate        # Git Bash: source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

### 3. 环境变量

不存在 `.env` 时复制模板：

```bash
cp .env.example .env
```

核心配置：

```dotenv
DJANGO_SECRET_KEY=<长随机字符串>
DJANGO_DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=

LLM_API_KEY=<DeepSeek Key>
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_TIMEOUT=30

QWEN_API_KEY=<千问 Token Plan Key>
QWEN_BASE_URL=https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1
QWEN_MODEL=qwen3.6-flash
QWEN_TIMEOUT=60
BACKEND_TIMEOUT=90
QWEN_BACKEND_TIMEOUT=180

# PostgreSQL；本地可留空使用 SQLite
DATABASE_URL=
```

说明：

- 千问 `sk-sp-` 开头的 Token Plan Key 必须搭配 `token-plan.maas.qianwenaiapi.com` 兼容端点
- 标准 DashScope Key 应使用 `https://dashscope.aliyuncs.com/compatible-mode/v1`，同时把 `QWEN_MODEL` 改为对应模型
- 千问复杂页面生成耗时可能超过 30 秒，因此单独设置 60 秒超时
- 全栈项目代码量较大，DeepSeek 后端项目生成使用 `BACKEND_TIMEOUT=90`
- 千问生成完整项目明显更慢，使用独立的 `QWEN_BACKEND_TIMEOUT=180`；后台任务不会因 90 秒读超时误判为结构不完整
- `.env` 已被 `.gitignore` 忽略，禁止提交真实 Key

### 4. 数据库与迁移

```bash
python manage.py migrate
```

PostgreSQL 示例：

```dotenv
DATABASE_URL=postgresql://webgen:change-me@127.0.0.1:5432/webgen
```

`DJANGO_DEBUG=False` 时必须配置 PostgreSQL 的 `DATABASE_URL`；调试模式下未配置则使用项目根目录的 SQLite。

### 5. 启动

```bash
python manage.py runserver
```

访问 <http://127.0.0.1:8000/>。

## Mock 兜底测试

后台生成由 `GenerationJob` + `_run_generation_job()` 执行；对应模型 Key 为空、超时、接口报错或返回内容无法解析时，会自动返回内置可交互加法计算器。修改接口使用 `_call_model()`。

### 正常链路

1. 输入需求 → 自动跳转 `/workspace/`，后台任务立即开始
2. 左侧通过轮询显示阶段、进度和模型处理阶段；此时可以返回项目主页，任务仍继续
3. 主页「生成任务」面板实时显示进行中和失败的任务，并支持删除；删除运行中任务会立即停止后台生成并清理任务数据，已完成任务会自动从任务栏移除并刷新项目列表
4. 完成后右侧显示预览，可切换源码；点击「在线修改」在左侧与优胜模型对应的助手对话，点击或框选预览中的组件后，助手会询问是否限制在该选区

### 测试一：全部 Mock

将 `.env` 中两个 Key 清空并重启服务：

```dotenv
LLM_API_KEY=
QWEN_API_KEY=
```

- 顶部状态显示 `Mock 模式 · 未配置模型 Key`
- 生成工作台仍会走完整阶段，最终显示 Mock 加法计算器
- 项目正常以完整 Mock Django 项目入库，可下载 ZIP、发布静态预览、删除

### 测试二：单模型失败

只清空 `QWEN_API_KEY` 并在模式选择框中选择「双模型赛马」：

- 千问显示“调用失败，未参与评分”，评分 0 分
- 系统自动选用 DeepSeek 的真实结果
- 项目备注包含「千问调用失败」（双方都失败时才是「双方均调用失败，Mock 兜底」）

两个 Key 都清空时，赛马仍会展示双方 Mock 结果并正常保存。全栈模型输出失败时也会生成一套完整的 Mock Django 计算器项目（含 DEPLOY.md）作为兜底。

### 验证数据库

```bash
python manage.py shell -c "from generator.models import GenerateHistory, GenerateVersion, PublishedPage; print(GenerateHistory.objects.count(), GenerateVersion.objects.count(), PublishedPage.objects.count())"
```

### 恢复真实调用

把有效 Key 写回 `.env`，重启 `runserver`。修改 `.env` 后必须重启进程。

## 数据模型

### GenerateHistory

| 字段 | 说明 |
| --- | --- |
| `prompt` | 用户提示词 / 模板提示词 |
| `html_code` | 当前选中版本的 HTML / 静态预览 |
| `model_provider` | 项目使用模型：`deepseek` / `qwen`，后续在线修改沿用此模型 |
| `project_type` | `frontend` 或 `backend` |
| `backend_label` | 生成的后端技术栈 |
| `created_at` | 项目创建时间 |

### GenerateVersion

| 字段 | 说明 |
| --- | --- |
| `history` | 所属项目 |
| `version_no` | 从 1 递增的版本号 |
| `html_code` | 该版本 HTML 快照 |
| `note` | 初始生成 / 赛马评分结果 / 修改建议 |
| `files` | 完整项目源码文件列表（路径、内容，含 DEPLOY.md） |
| `created_at` | 版本创建时间 |

### GenerationJob

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 任务 ID |
| `client_id` | 浏览器标识，用于隔离不同浏览器/用户的任务状态 |
| `prompt` / `requested_mode` | 用户提示词和请求模式 |
| `status` / `progress` | queued / running / completed / failed 和进度 |
| `state` | 阶段、模型状态、评分结果等实时快照 |
| `history` | 生成任务完成后关联新项目；修改任务完成后关联目标项目 |
| `error` | 失败信息 |

修改任务还会在 `state` 中保存：

| 字段 | 说明 |
| --- | --- |
| `kind` | `generate` / `refine` |
| `target_history_id` | 被修改的项目 ID |
| `target_version_id` / `target_version_no` | 被修改的版本 |
| `target_project_prompt` | 项目需求，用于主页任务栏标题 |
| `instruction` | 本次修改指令 |
| `target_context` / `target_label` | 点选/框选组件上下文 |

### PublishedPage

| 字段 | 说明 |
| --- | --- |
| `version` | 被发布版本，一对一 |
| `slug` | 随机访问标识 |
| `created_at` | 发布时间 |

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/` | 项目主页 |
| GET | `/workspace/` | 生成 / 项目预览工作台 |
| GET | `/api/history/` | 项目与版本列表（不含 HTML） |
| GET | `/api/history/<id>/` | 项目详情（含 HTML） |
| POST | `/api/jobs/start/` | 创建后台生成任务，立即返回任务 ID；单模型可传 `provider=deepseek/qwen` 指定模型 |
| GET | `/api/race/stats/` | 历史赛马胜场、胜率、平均分、逐项记录与单模型推荐 |
| GET | `/api/jobs/?client_id=<id>` | 当前浏览器的最近生成任务 |
| GET | `/api/jobs/<uuid>/` | 任务进度、状态与完成后的项目详情 |
| POST | `/api/jobs/<uuid>/delete/` | 删除任务；运行中任务会停止后台生成并清理数据 |
| POST | `/api/generate/stream/` | 兼容保留的 NDJSON 即时生成接口 |
| POST | `/api/history/<id>/refine/` | 基于指定版本修改；传 `background: true` 创建后台修改任务并返回 202 任务 ID，否则同步返回新版本；支持 `target_context` / `target_label` |
| GET | `/api/history/<id>/versions/<vid>/download/` | 下载 HTML 或全栈项目 ZIP |
| POST | `/api/history/<id>/versions/<vid>/delete/` | 删除版本 |
| POST | `/api/history/<id>/versions/<vid>/publish/` | 发布版本 |
| POST | `/api/history/<id>/versions/<vid>/unpublish/` | 取消发布 |
| GET | `/p/<slug>/` | 已发布页面 |

### 全栈项目解析与完整性校验

- 模型文件包同时兼容严格的 `===FILE: path=== ... ===END_FILE===` 和简化的 `===FILE: path===` 分隔格式；即使模型漏掉部分 `===END_FILE===` 或 `===FILES===` 标记，也会按下一个文件/预览标记正确拆分，避免把整个项目误塞进 `requirements.txt`
- 保存前校验项目至少包含 6 个文件，并同时具备依赖清单、后端入口/目录和前端模板/静态资源；不满足时不会当成完整项目保存
- 单模型模式遇到不完整包会使用完整的 Mock Django 项目兜底；赛马模式会优先在“结构完整”的候选方案中按预览评分选优，不会让只有两三个文件的高分预览胜出
- 空的 `__init__.py` 等文件也会保留，下载 ZIP 和源码树不会再缺少后端模块

### 双模型赛马评分

- 两个模型使用同一份用户提示词和同一套确定性规则评分，评分代码不接收模型名，不存在 DeepSeek/千问权重差异
- 十维 100 分制：页面结构与完整性 10、需求覆盖 15、交互能力 15、状态管理与持久化 10、视觉设计 10、响应式适配 10、可访问性 10、内容质量 8、代码与安全 7、性能与资源 5
- “需求覆盖”会将提示词中的 2048、计算器、待办、扫雷、作品集、番茄钟等需求映射为具体功能检查；未知需求则检查控件、逻辑、内容、状态和关键词呼应
- 评分检查项、双方得分和失分原因都可在「评分详情」中展开查看；同分时综合比较需求覆盖、交互能力、状态管理和内容质量
- 历史赛马统计读取所有已完成的赛马任务（包括项目后来被删除的任务），因此能看到双方完整胜负记录，而不是只看当前仍存在的项目

### 后台任务与并发

- 每个生成任务使用独立 UUID、独立线程和独立 `GenerationJob` 记录，多个浏览器可以同时生成
- 在线修改同样使用 `GenerationJob`：`state.kind = "refine"`，记录目标项目、目标版本号、修改指令和点选上下文；修改任务不会把目标项目当作“生成结果”覆盖
- 工作台和主页都通过轮询 `/api/jobs/<uuid>/` / `/api/jobs/?client_id=...` 获取状态；离开页面不会中止后台线程
- 主页「生成任务」中的修改任务显示为“修改「项目名」vN”；完成后自动刷新项目列表并出现新版本。删除运行中的修改任务只会停止修改，不会删除项目已有版本
- 修改任务完成后 `job.history` 关联目标项目，任务详情接口会返回含新版本的完整项目，便于工作台恢复
- 任务状态持久化到数据库；主页「生成任务」面板只展示进行中和失败的任务，已完成任务会自动移除并更新项目列表
- 进行中和失败任务卡片均提供「删除」按钮：删除运行中任务会先确认，前端立即移除卡片，后端设置取消事件并异步关闭模型 HTTP 响应，工作线程在下次状态写入时退出，不会再创建项目或“复活”任务行；失败任务会直接删除任务记录。删除请求不等待套接字关闭，因此一次点击即可完成
- 任务保存使用 `QuerySet.update()` 并检查受影响行数，任务行被删除后旧线程无法重新插入数据
- 本地 SQLite 已增加 20 秒写锁等待时间；生产环境推荐 PostgreSQL，可避免多任务写入争用
- 生产部署建议保持多个 Gunicorn worker，并让数据库连接池承担并发

### 兼容的流式事件（可选）

`/api/generate/stream/` 返回 `application/x-ndjson`，主要用于外部调试；当前页面使用后台任务轮询：

```text
{"type":"start","mode":"single|race|backend","providers":[...]}
{"type":"stage","key":"prepare|generate|score|backend|files|save|fallback","progress":18,...}
{"type":"provider","provider":"deepseek","status":"connecting|streaming|done|fallback",...}
{"type":"delta","provider":"deepseek","chars":80}
{"type":"progress","progress":45,"chars":12000}
{"type":"heartbeat","elapsed":10}
{"type":"done","history":{...},...}
{"type":"error","message":"..."}
```

## 发布与安全

- 发布会生成 `/p/<随机slug>/` 路由，返回当前版本完整 HTML
- 已发布页面带 CSP `sandbox` 响应头，脚本可以运行，但生成页面无法读取主站 Cookie / localStorage
- 取消发布会立即删除路由；删除版本也会级联删除其发布记录，原 URL 返回 404
- 下载接口对完整项目返回 ZIP，包含全部前后端文件、依赖清单、`DEPLOY.md` 与静态预览；旧版单文件项目仍返回 HTML
- 生成预览 iframe 使用 `sandbox="allow-scripts"`
- 如果生成结果确实使用 React JSX，后端会自动注入 Babel Standalone 并把内联脚本转换为 `text/babel`，避免“预览空白”；普通 DOM 脚本（即使包含 `createElement` 和 HTML 模板字符串）不会被误判为 JSX。历史上被误转换的预览会在读取时自动还原，避免 Babel 延迟执行导致 `DOMContentLoaded` 处理器和入场动画不运行
- 预览通过 iframe 加载时，`DOMContentLoaded` 处理器必须正常注册；如果页面使用 `.fade-in { opacity: 0 }` 等入场动画，动画脚本未执行会让内容整体不可见，相关 JSX 误判问题已修复
- 预览 iframe 和临时发布页处于沙箱化 opaque origin，`localStorage` 会抛出 `SecurityError`；系统会自动注入存储兜底，保证依赖本地存储的页面仍能正常初始化与交互
- 已发布页面的存储兜底会把 `localStorage` / `sessionStorage` 镜像到 URL 的 `__pf_store` 查询参数（通过 `history.replaceState`），因此刷新、前进后退或收藏后可以恢复最高分等游戏状态；不授予同源权限，也不会读写主站 Cookie / localStorage。状态上限约 32KB，超出后仅保留当前页面内存状态
- 生成页面只有在自身把状态写入 `localStorage` / `sessionStorage` 时才能被恢复；如果游戏代码没有保存棋盘等中间状态，刷新后仍会按代码逻辑重新开始
- 所有新生成项目默认使用 Django 4.2，并按前后端 / 业务模块划分目录；用户指定的后端语言或框架优先。生成的 `DEPLOY.md` 说明如何独立安装、配置数据库和启动服务
- 发布时仅将生成的静态 `PREVIEW_HTML` 部署为当前域名的临时路由；不会执行生成的后端代码。完整前后端项目请下载 ZIP 后按包内 `DEPLOY.md` 独立部署。取消发布会立即下线路由

## 阿里云 ECS 部署

完整步骤见 [DEPLOY_ALIYUN_ECS.md](DEPLOY_ALIYUN_ECS.md)，包括 PostgreSQL、Gunicorn、systemd、Nginx、HTTPS、后台任务并发、流式代理设置与发布路由。
