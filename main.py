import os, asyncio
from fastapi import FastAPI
import uvicorn
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties

# --- 配置 ---
TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", 8080))

app = FastAPI()
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# --- 消息处理 ---
@dp.message()
async def echo_handler(msg: types.Message):
    # 只要收到消息，日志一定会跳出这一行
    print(f"!!! 成功收到消息: {msg.text} 来自: {msg.from_user.id}")
    await msg.answer(f"收到！你的 ID 是: {msg.from_user.id}")

@app.get("/")
async def health():
    return {"status": "ok", "message": "Bot is running"}

# --- 核心启动逻辑：彻底解决阻塞 ---
async def run_services():
    # 1. 强制清理 Webhook
    await bot.delete_webhook(drop_pending_updates=True)
    
    # 2. 验证机器人身份
    try:
        me = await bot.get_me()
        print(f"--- 机器人验证成功: @{me.username} ---")
    except Exception as e:
        print(f"--- 机器人连接失败: {e} ---")
        return

    # 3. 配置 Web 服务器
    # 注意：我们手动在 loop 中运行 uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, loop="asyncio")
    server = uvicorn.Server(config)

    # 4. 并行启动：这会让 Bot 和 Web 同时在后台运行
    print("--- 正在启动并行链路 ---")
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    # 直接运行并发主函数
    asyncio.run(run_services())
