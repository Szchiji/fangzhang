"""
月影车姬机器人 - 应用入口
YueYingCheJiBot - Application Entry Point

同时启动：
1. Telegram Bot（aiogram 长轮询）
2. Web API 服务（aiohttp，供 Mini App 调用）

环境变量（必需）：
  BOT_TOKEN     — Telegram Bot Token
  MONGO_URI     — MongoDB 连接 URI（默认 mongodb://localhost:27017/）
  ADMIN_IDS     — 管理员 Telegram ID，逗号分隔（如 123456,789012）

环境变量（可选）：
  MINI_APP_URL  — Mini App 的 HTTPS URL（默认 https://example.com/mini_app.html）
  GROK_API_KEY  — Grok AI API Key
  TONGYI_API_KEY — 通义千问 API Key
  WEB_PORT      — Web API 端口（默认 8080）
  ENV           — 运行环境，dev 时跳过 initData 验证
"""

import asyncio
import logging
import os

from aiohttp import web

from bot import bot, dp
from models import create_indexes
from web_api import create_web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("🌙 月影车姬机器人启动中…")

    # 初始化数据库索引
    create_indexes()
    logger.info("✅ 数据库索引初始化完成")

    # 启动 Web API（aiohttp）
    web_app = create_web_app()
    port = int(os.environ.get("WEB_PORT", 8080))
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("✅ Web API 已启动，端口 %d", port)

    # 启动 Telegram Bot 轮询
    logger.info("✅ Telegram Bot 开始轮询…")
    await dp.start_polling(bot)

    # 清理
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
