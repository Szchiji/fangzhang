import os, asyncio
from fastapi import FastAPI
import uvicorn
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties

# --- 极简配置 ---
TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", 8080))

app = FastAPI()
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# --- 强制打印所有收到的消息 ---
@dp.message()
async def echo_handler(msg: types.Message):
    print(f"!!! [重大进展] 收到消息: {msg.text} 来自: {msg.from_user.id}")
    await msg.answer(f"机器人已收到消息！你的 ID 是: {msg.from_user.id}")

@app.get("/")
async def health():
    return {"status": "ok", "info": "bot server is alive"}

# --- 核心修复：手动管理循环 ---
async def start_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    print(f"*** 机器人已在线: @{me.username} ***")
    await dp.start_polling(bot)

async def start_web():
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, loop="asyncio")
    server = uvicorn.Server(config)
    await server.serve()

async def run_all():
    # 使用 gather 同时启动，互不阻塞
    print("正在并行启动 Bot 和 Web...")
    await asyncio.gather(start_bot(), start_web())

if __name__ == "__main__":
    asyncio.run(run_all())
