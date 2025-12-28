import os, asyncio, sqlite3, uuid, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
import uvicorn

# --- 配置 ---
TOKEN = os.getenv("TOKEN")
# 这里的处理确保即使有空格也能匹配
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")
auth_sessions = {}

# --- 数据库 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, like_emoji TEXT DEFAULT "👍", custom_fields TEXT DEFAULT "地区,价格,链接", list_template TEXT, checkin_template TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, data_json TEXT, expire_date TEXT, PRIMARY KEY(user_id, group_id))')
        conn.execute('CREATE TABLE IF NOT EXISTS timers (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, remark TEXT, content TEXT, interval_hours INTEGER, last_run TEXT, status INTEGER DEFAULT 1)')
        conn.execute('CREATE TABLE IF NOT EXISTS checkins (user_id TEXT, group_id TEXT, checkin_date TEXT, PRIMARY KEY(user_id, group_id, checkin_date))')

# --- 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = str(msg.from_user.id)
    print(f">>> [收到指令] /start | 来自用户: {uid} | 名单内管理员: {ADMIN_IDS}")
    
    if uid not in ADMIN_IDS:
        print(f">>> [拒绝] 用户 {uid} 不在管理员名单里！")
        # 为了测试，如果你不是管理员，我们也回一句话，确认机器人活着
        await msg.reply(f"抱歉，你不是机器人创建者。你的 ID 是: {uid}")
        return

    # 如果是管理员
    sid = str(uuid.uuid4())
    gid = str(msg.chat.id)
    auth_sessions[sid] = {"gid": gid, "exp": time.time() + 7200}
    
    # 只要是管理员，无论私聊还是群聊，直接给链接
    url = f"{DOMAIN}/manage?sid={sid}&gid={gid}"
    await msg.reply(f"✅ 身份验证成功！\n\n点击进入管理后台：\n{url}")

@dp.message()
async def bot_handler(msg: types.Message):
    # 记录所有收到的消息到日志
    print(f">>> [收到消息] 来自: {msg.from_user.id} | 内容: {msg.text}")

# --- 启动逻辑 ---
async def main():
    init_db()
    # 强制清理
    await bot.delete_webhook(drop_pending_updates=True)
    
    # 打印测试：确认机器人身份
    me = await bot.get_me()
    print(f"*** 机器人 @{me.username} 启动成功，正在监听消息... ***")

    # 运行 Web 服务
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, loop="asyncio")
    server = uvicorn.Server(config)
    
    # 核心：将所有任务聚在一起跑
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except:
        pass
