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
- **Web API**：aiohttp（供 Mini App 调用）
- **Mini App**：纯 HTML/CSS/JS + [Leaflet.js](https://leafletjs.com/)
- **数据库**：MongoDB（灯笼资源、信用分、匿名会话）
- **AI**：Grok API 或通义千问（中文理解，照片鉴真）
- **部署**：Docker + Docker Compose（推荐香港/台湾 VPS）

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/Szchiji/fangzhang.git
cd fangzhang
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 BOT_TOKEN、MONGO_URI、ADMIN_IDS 等
```

### 3. 使用 Docker Compose 启动

```bash
docker compose up -d
```

### 4. 本地开发（不使用 Docker）

```bash
# 安装依赖
pip install -r requirements.txt

# 确保 MongoDB 已启动
mongod --dbpath ./data/db

# 启动
ENV=dev python main.py
```

---

## 项目结构

```
fangzhang/
├── main.py           # 应用入口（Bot + Web API 同时启动）
├── bot.py            # Telegram Bot 主逻辑（aiogram 处理器）
├── ai.py             # AI 模块（媒婆匹配 + 兰花鉴真）
├── models.py         # MongoDB 数据模型
├── web_api.py        # aiohttp Web API（供 Mini App 调用）
├── mini_app.html     # Telegram Mini App 页面（Leaflet 地图）
├── requirements.txt  # Python 依赖
├── Dockerfile        # Docker 镜像配置
├── docker-compose.yml # Docker Compose 编排
├── .env.example      # 环境变量模板
└── .gitignore        # Git 忽略配置
```

---

## 环境变量说明

| 变量 | 必需 | 说明 |
|------|------|------|
| `BOT_TOKEN` | ✅ | Telegram Bot Token（@BotFather 获取） |
| `MONGO_URI` | ✅ | MongoDB 连接 URI |
| `ADMIN_IDS` | ✅ | 管理员 Telegram ID，逗号分隔 |
| `MINI_APP_URL` | ✅ | Mini App 的 HTTPS URL |
| `GROK_API_KEY` | ⬜ | Grok AI API Key（二选一） |
| `TONGYI_API_KEY` | ⬜ | 通义千问 API Key（二选一） |
| `WEB_PORT` | ⬜ | Web API 端口（默认 8080） |
| `ENV` | ⬜ | `dev` 跳过签名验证（仅开发用） |

---

## Mini App 部署

Mini App 需要 HTTPS URL。推荐使用 Nginx 反代：

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    location / {
        proxy_pass http://localhost:8080;
    }

    # SSL 配置（Let's Encrypt）
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
}
```

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
- [ ] 匿名月影聊天会话
- [ ] 月影提醒订阅（APScheduler）
- [ ] 社区传奇频道推送
- [ ] 车姬守护群管高级规则

---

## 许可证

MIT License — 月影车姬，自由流转。
