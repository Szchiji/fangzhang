# 月影车姬机器人 · YueYingCheJiBot

> **月下寻花，影中见真** 🌙  
> 一款沉浸式 Telegram 修车资源机器人，将传统"列表式查询"彻底变革为"月影秘境探索体验"。

---

## 核心功能

| 功能 | 描述 |
|------|------|
| 🗺 月影秘境 Mini App | Leaflet 地图 + 灯笼资源，深色夜游风格 |
| 🔮 月影媒婆 AI | 自然语言匹配，AI 推荐 Top-5 灯笼 |
| 🏮 灯笼投稿 | FSM 流程引导投稿，AI 鉴真照片真实度 |
| 🌸 兰花信用生态 | 兰花令信用分，奖励优质投稿与举报 |
| 🕰 时光秘匣 | 收藏灯笼，支持提醒与订阅 |
| 🛡 车姬守护 | 可选群管模式，防骗防中介 |

---

## 技术栈

- **Bot 框架**：[aiogram](https://github.com/aiogram/aiogram) 3.x
- **Web 框架**：aiohttp（Webhook + Web API 合一）
- **Mini App**：纯 HTML/CSS/JS + [Leaflet.js](https://leafletjs.com/)
- **数据库**：PostgreSQL（SQLAlchemy 异步 ORM + asyncpg 驱动）
- **AI**：Grok API 或通义千问（中文理解，照片鉴真）
- **部署**：Railway（推荐）/ Docker + Docker Compose

---

## Railway 快速部署

### 1. Fork 仓库并连接 Railway

1. Fork 本仓库到你的 GitHub 账户
2. 登录 [Railway](https://railway.app)
3. 创建新项目 → 从 GitHub 部署

### 2. 添加 PostgreSQL 数据库

在 Railway 项目中点击 **New Service → PostgreSQL**。

Railway 会自动注入 `DATABASE_URL` 环境变量到你的 Bot 服务。

### 3. 配置环境变量

在 Railway 项目的 **Variables** 页签中添加：

| 变量 | 必需 | 说明 |
|------|------|------|
| `BOT_TOKEN` | ✅ | Telegram Bot Token（@BotFather 获取） |
| `DATABASE_URL` | ✅ | Railway 自动注入（格式：`postgresql://...`） |
| `ADMIN_IDS` | ✅ | 管理员 Telegram ID，逗号分隔 |
| `WEBHOOK_URL` | ✅ | 你的 Railway 公开域名，如 `https://your-service.up.railway.app` |
| `MINI_APP_URL` | ✅ | Mini App 的 HTTPS URL（如 `https://your-service.up.railway.app/mini_app.html`） |
| `GROK_API_KEY` | ⬜ | Grok AI API Key（二选一） |
| `TONGYI_API_KEY` | ⬜ | 通义千问 API Key（二选一） |
| `ENV` | ⬜ | `dev` 跳过签名验证（仅开发用） |

> **提示**：`WEBHOOK_URL` 和 `MINI_APP_URL` 通常使用同一个 Railway 域名，格式为
> `https://fangzhang-production.up.railway.app`（在 Railway 项目的 Settings → Domains 中找到）。

### 4. 部署

Railway 检测到 `Procfile` 后会自动启动：

```
web: python main.py
```

首次部署时，代码会自动创建所有 PostgreSQL 表结构（无需手动迁移）。

---

## 本地开发

### 1. 克隆仓库

```bash
git clone https://github.com/Szchiji/fangzhang.git
cd fangzhang
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 BOT_TOKEN、DATABASE_URL、ADMIN_IDS 等
```

### 4. 启动本地 PostgreSQL（Docker 方式）

```bash
docker run -d \
  --name yueyingcheji-pg \
  -e POSTGRES_USER=yueyinguser \
  -e POSTGRES_PASSWORD=yueyingpass \
  -e POSTGRES_DB=yueyingcheji \
  -p 5432:5432 \
  postgres:16-alpine
```

设置 `.env` 中的 `DATABASE_URL`：

```
DATABASE_URL=postgresql://yueyinguser:yueyingpass@localhost:5432/yueyingcheji
```

### 5. 启动（本地长轮询模式）

```bash
# 不设置 WEBHOOK_URL 时，自动降级为长轮询模式
ENV=dev python main.py
```

### 6. 使用 Docker Compose

```bash
docker compose up -d
```

---

## 项目结构

```
fangzhang/
├── main.py           # 应用入口（Webhook + Web API + DB 初始化）
├── bot.py            # Telegram Bot 主逻辑（aiogram 处理器）
├── ai.py             # AI 模块（媒婆匹配 + 兰花鉴真）
├── models.py         # PostgreSQL 数据模型（SQLAlchemy ORM）
├── credit.py         # 兰花信用计算（纯 Python，无外部依赖）
├── web_api.py        # aiohttp Web API（供 Mini App 调用）
├── mini_app.html     # Telegram Mini App 页面（Leaflet 地图）
├── requirements.txt  # Python 依赖
├── Procfile          # Railway 启动命令
├── Dockerfile        # Docker 镜像配置
├── docker-compose.yml # Docker Compose 编排（含 PostgreSQL）
├── .env.example      # 环境变量模板
└── .gitignore        # Git 忽略配置
```

---

## 数据库模型（PostgreSQL）

| 表 | 说明 |
|----|------|
| `users` | 用户、兰花令信用分、收藏灯笼、速率限制时间戳 |
| `lanterns` | 灯笼资源，含 AI 真实度评分和模糊位置 |
| `anonymous_chats` | 匿名月影会话，24 小时过期 |
| `chat_requests` | 会话申请，24 小时过期 |
| `metrics` | 运营指标 & 用户行为日志 |

所有表在首次启动时通过 SQLAlchemy `Base.metadata.create_all()` 自动创建。

---

## API 端点（Mini App）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/mini_app.html` 或 `/` | Mini App 页面 |
| `GET` | `/api/lanterns?city=台北` | 获取已审核灯笼列表 |
| `GET` | `/api/credit?user_id=123` | 获取用户兰花令信用分 |
| `POST` | `/api/collect` | 收藏灯笼到时光秘匣 |

所有 API（`ENV=dev` 除外）均校验 Telegram `initData` 签名。

---

## MVP 路线图

- [x] /start 欢迎菜单
- [x] 月影媒婆 AI 匹配
- [x] 灯笼投稿 FSM 流程
- [x] AI 照片鉴真（异步）
- [x] 兰花令信用分
- [x] 时光秘匣收藏
- [x] 管理员审核面板
- [x] Mini App Leaflet 地图
- [x] Web API（灯笼/信用/收藏）
- [x] 匿名月影聊天会话
- [x] **PostgreSQL + SQLAlchemy 迁移（Railway 适配）**
- [x] **Telegram Webhook 启动模式**
- [ ] 月影提醒订阅（APScheduler）
- [ ] 社区传奇频道推送
- [ ] 车姬守护群管高级规则

---

## 许可证

MIT License — 月影车姬，自由流转。
