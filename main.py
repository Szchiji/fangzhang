import os
import asyncio
import logging
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramConflictError

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("Missing TOKEN environment variable")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    if not (os.getenv("PGHOST") and os.getenv("PGUSER")):
        raise ValueError("Missing database configuration: set DATABASE_URL or PGHOST+PGUSER")

from db import get_pool
from schema import init_db
from bot import register_all
from web.routes import setup_routes

logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
register_all(dp)

app = FastAPI(title="CheBot Admin")
templates = Jinja2Templates(directory="templates")
setup_routes(app, bot, templates)

ENABLE_BOT_POLLING = os.getenv("ENABLE_BOT_POLLING", "true").lower() in {"1", "true", "yes", "on"}


async def run_bot_polling():
    try:
        await dp.start_polling(bot)
    except TelegramConflictError:
        logger.error(
            "Telegram polling conflict detected; disable polling on duplicate instances with ENABLE_BOT_POLLING=false (or 0/no/off)."
        )


@app.on_event("startup")
async def startup():
    init_db()
    if ENABLE_BOT_POLLING:
        asyncio.create_task(run_bot_polling())
    else:
        logger.info("Bot polling is disabled by ENABLE_BOT_POLLING=false")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
