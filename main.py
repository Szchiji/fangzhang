"""
月影车姬机器人 - 应用入口（Railway 版）
YueYingCheJiBot - Application Entry Point

启动方式：Telegram Webhook（适配 Railway 部署，无需长轮询）
同时启动：
  1. Telegram Bot Webhook（aiogram + aiohttp）
  2. Web API 服务（aiohttp，供 Mini App 调用）

数据库：PostgreSQL（通过 SQLAlchemy 异步 ORM，asyncpg 驱动）

环境变量（必需）：
  BOT_TOKEN     — Telegram Bot Token（@BotFather 获取）
  DATABASE_URL  — PostgreSQL 连接 URL（Railway 自动注入，或手动配置）
                  格式：postgresql://user:pass@host:port/db
                  或：postgresql+asyncpg://user:pass@host:port/db
  ADMIN_IDS     — 管理员 Telegram ID，逗号分隔（如 123456,789012）

环境变量（可选）：
  WEBHOOK_URL   — Telegram Webhook 公开 URL
                  示例：https://fangzhang-production.up.railway.app
                  若未设置则降级为长轮询模式（仅用于本地开发）
  MINI_APP_URL  — Mini App 的 HTTPS URL（默认 https://example.com/mini_app.html）
  GROK_API_KEY  — Grok AI API Key
  TONGYI_API_KEY — 通义千问 API Key
  PORT          — 监听端口（Railway 自动注入 $PORT，默认 8080）
  ENV           — 运行环境，dev 时跳过 initData 验证

Railway 部署说明：
  - Railway 会自动注入 DATABASE_URL 和 PORT 环境变量
  - 在 Railway 项目 Variables 中添加：BOT_TOKEN, ADMIN_IDS, WEBHOOK_URL, MINI_APP_URL
  - WEBHOOK_URL 填写 Railway 分配的公开域名，格式：https://your-service.up.railway.app
"""

import asyncio
import logging
import os

from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from bot import bot, dp
from models import create_tables
from web_api import create_web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Webhook 路径（固定，与 Telegram 注册的路径一致）
WEBHOOK_PATH = "/webhook"


async def main():
    logger.info("🌙 月影车姬机器人启动中…")

    # 1. 初始化 PostgreSQL 表结构（首次启动自动建表）
    logger.info("🗄 初始化 PostgreSQL 数据库表…")
    await create_tables()
    logger.info("✅ 数据库表初始化完成")

    # 2. 确定运行模式（Webhook 或 长轮询）
    webhook_base = os.environ.get("WEBHOOK_URL", "").rstrip("/")
    port = int(os.environ.get("PORT", os.environ.get("WEB_PORT", 8080)))

    if webhook_base:
        # ── Webhook 模式（Railway 生产环境推荐）────────────────────────────
        webhook_url = f"{webhook_base}{WEBHOOK_PATH}"
        logger.info("🔗 设置 Telegram Webhook: %s", webhook_url)
        await bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,   # 丢弃启动前积压的更新，避免重复处理
        )

        # 创建 aiohttp 应用：Webhook 处理器 + Web API 路由
        web_app = create_web_app()
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(
            web_app, path=WEBHOOK_PATH
        )
        setup_application(web_app, dp, bot=bot)

        logger.info("✅ Web 服务（Webhook + API）启动，端口 %d", port)
        await web._run_app(web_app, host="0.0.0.0", port=port)  # noqa: SLF001

    else:
        # ── 长轮询模式（本地开发，无需公开 URL）──────────────────────────
        logger.info("📡 未配置 WEBHOOK_URL，使用长轮询模式（仅适合本地开发）")

        # 删除已有 Webhook（确保长轮询正常运行）
        await bot.delete_webhook(drop_pending_updates=True)

        # 同时启动 Web API（供 Mini App 本地测试）
        web_app = create_web_app()
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info("✅ Web API 已启动，端口 %d（长轮询模式）", port)

        # 启动长轮询
        logger.info("✅ Telegram Bot 开始长轮询…")
        await dp.start_polling(bot)

        # 清理
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
