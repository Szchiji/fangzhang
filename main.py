import os, asyncio, sqlite3, uuid, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
import uvicorn

# --- 1. 配置加载 ---
TOKEN = os.getenv("TOKEN")
# 自动清理管理员ID前后的空格
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

# 核心：必须定义 app 供 Railway 加载
app = FastAPI()
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
auth_sessions = {}

# --- 2. 数据库初始化 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, like_emoji TEXT DEFAULT "👍", custom_fields TEXT DEFAULT "地区,价格,链接", list_template TEXT, checkin_template TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, data_json TEXT, expire_date TEXT, PRIMARY KEY(user_id, group_id))')
        conn.execute('CREATE TABLE IF NOT EXISTS timers (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, remark TEXT, content TEXT, interval_hours INTEGER, last_run TEXT, status INTEGER DEFAULT 1)')
        conn.execute('CREATE TABLE IF NOT EXISTS checkins (user_id TEXT, group_id TEXT, checkin_date TEXT, PRIMARY KEY(user_id, group_id, checkin_date))')

# --- 3. 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = str(msg.from_user.id)
    print(f">>> [日志] 收到 /start 来自: {uid}")
    
    if uid not in ADMIN_IDS:
        await msg.reply(f"❌ 权限不足。你的 ID 是: {uid} (已记录在日志)")
        print(f">>> [拒绝] {uid} 不在管理员名单 {ADMIN_IDS} 中")
        return

    sid = str(uuid.uuid4())
    gid = str(msg.chat.id)
    auth_sessions[sid] = {"gid": gid, "exp": time.time() + 7200}
    
    url = f"{DOMAIN}/manage?sid={sid}&gid={gid}"
    await msg.reply(f"✅ 认证成功！\n\n管理后台链接（2小时有效）：\n{url}")

@dp.message()
async def bot_handler(msg: types.Message):
    # 打印所有收到的消息，方便确认机器人是否“活着”
    print(f">>> [收到消息] 来自: {msg.from_user.id} | 内容: {msg.text}")

# --- 4. Web 路由 (最简版确保 app 正常) ---
@app.get("/", response_class=HTMLResponse)
async def index():
    return "<h1>Bot Server is Running</h1>"

@app.get("/manage", response_class=HTMLResponse)
async def admin_page(request: Request, sid: str, gid: str):
    if sid not in auth_sessions: return "链接失效，请在群里重发 /start"
    return "<h1>后台界面已连接 (请确保 templates 文件夹存在)</h1>"

# --- 5. 终极启动逻辑 ---
async def main():
    init_db()
    # 强制清理旧连接，解决不回话问题
    await bot.delete_webhook(drop_pending_updates=True)
    
    me = await bot.get_me()
    print(f"*** 机器人 @{me.username} 认证成功！ ***")

    # 启动 Web 服务
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, loop="asyncio")
    server = uvicorn.Server(config)
    
    # 同时运行
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    asyncio.run(main())
