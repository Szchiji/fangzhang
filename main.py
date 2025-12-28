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
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

# 核心变量：FastAPI 实例
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
        conn.commit()

# --- 3. 机器人指令逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = str(msg.from_user.id)
    print(f">>> [收到/start] 来自: {uid}")
    
    if uid not in ADMIN_IDS:
        print(f">>> [拒绝访问] {uid} 不在管理员名单 {ADMIN_IDS}")
        await msg.reply(f"❌ 权限不足。您的 ID 是: <code>{uid}</code>\n请将其添加到 Railway 的 ADMIN_IDS 变量中。")
        return

    sid = str(uuid.uuid4())
    gid = str(msg.chat.id)
    auth_sessions[sid] = {"gid": gid, "exp": time.time() + 7200}
    
    login_url = f"{DOMAIN}/manage?sid={sid}&gid={gid}"
    await msg.reply(f"✅ 认证成功！\n\n<b>管理后台链接：</b>\n{login_url}\n\n<i>链接有效期 2 小时</i>")

@dp.message()
async def all_msg_handler(msg: types.Message):
    # 调试日志：如果机器人在群里没反应，看这里有没有输出
    print(f">>> [收到消息] 来自: {msg.from_user.id} | 内容: {msg.text or '非文本消息'}")

# --- 4. 网页路由 ---
@app.get("/", response_class=HTMLResponse)
async def index():
    return "<h1>Bot Server is Running</h1><p>机器人正在后台轮询中...</p>"

@app.get("/manage", response_class=HTMLResponse)
async def admin_page(request: Request, sid: str, gid: str):
    if sid not in auth_sessions or auth_sessions[sid]["exp"] < time.time():
        return "链接已失效，请重新发送 /start"
    # 这里可以添加加载 templates 的逻辑
    return f"<h1>后台已激活</h1><p>群组ID: {gid}</p>"

# --- 5. 核心启动函数 (解决不响应的关键) ---
async def main():
    # A. 初始化数据库
    init_db()
    
    # B. 强制清理 Webhook（解决消息不达的问题）
    await bot.delete_webhook(drop_pending_updates=True)
    
    # C. 获取机器人身份并打印
    me = await bot.get_me()
    print(f"--- 机器人认证成功: @{me.username} ---")
    print(f"--- 管理员 ID 配置: {ADMIN_IDS} ---")

    # D. 配置 Web 服务器
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, loop="asyncio")
    server = uvicorn.Server(config)
    
    # E. 并行运行：Bot轮询 + Web服务器
    print("--- 正在启动并行服务 (Polling + Web) ---")
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
