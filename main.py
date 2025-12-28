import os, asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties

# --- 1. 配置 ---
TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", 8080))

# --- 2. 机器人初始化 ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

@dp.message()
async def echo_handler(msg: types.Message):
    print(f"!!! 收到消息: {msg.text} 来自: {msg.from_user.id}")
    await msg.answer(f"✅ 收到消息！你的 ID 是: {msg.from_user.id}")

# --- 3. 核心：强制在 FastAPI 启动时启动机器人 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 这是 FastAPI 启动时会执行的代码
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    print(f"🚀 [重要] 机器人 @{me.username} 已经在后台启动！")
    
    # 在后台运行机器人轮询
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    yield  # 这里是分割线，上面是启动时执行，下面是关闭时执行
    
    # 关闭时停止机器人
    polling_task.cancel()
    await bot.session.close()

# --- 4. 创建 FastAPI 实例 ---
app = FastAPI(lifespan=lifespan)

@app.get("/")
async def health():
    return {"status": "ok", "detail": "Bot is running in background"}

# 这里的 main 块只是为了本地调试，Railway 会调用上面的 app
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
