import os
import asyncio
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

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

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
register_all(dp)

app = FastAPI(title="CheBot Admin")
templates = Jinja2Templates(directory="templates")
setup_routes(app, bot, templates)


@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
