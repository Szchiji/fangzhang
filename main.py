import os, asyncio, sqlite3, uuid, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ChatPermissions
import uvicorn

# --- 1. 配置加载 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(',')
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

# --- 2. 数据库初始化 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS groups (
        group_id TEXT PRIMARY KEY, like_emoji TEXT DEFAULT '👍',
        custom_fields TEXT DEFAULT '地区,价格,链接',
        list_template TEXT DEFAULT '✅ <b>[{地区Value}]</b> {姓名Value}',
        checkin_template TEXT DEFAULT '✨ {姓名Value} 已上线！')''')
    conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
        user_id TEXT, group_id TEXT, name TEXT, data_json TEXT, expire_date TEXT, PRIMARY KEY(user_id, group_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS timers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, remark TEXT, content TEXT, interval_hours INTEGER, 
        start_time TEXT, end_time TEXT, delete_last INTEGER DEFAULT 0, last_msg_id INTEGER, last_run TEXT, status INTEGER DEFAULT 1)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
        user_id TEXT, group_id TEXT, checkin_date TEXT, PRIMARY KEY(user_id, group_id, checkin_date))''')
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

# --- 3. 机器人核心逻辑 ---

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = str(msg.from_user.id)
    print(f"--- [收到/start指令] --- 发送者ID: {uid}")
    
    if uid not in ADMIN_IDS:
        print(f"🚨 拒绝访问: UID {uid} 不在 ADMIN_IDS 名单中!")
        return

    if msg.chat.type in ["group", "supergroup"]:
        sid = str(uuid.uuid4())
        auth_sessions[sid] = {"gid": str(msg.chat.id), "exp": time.time() + 7200}
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="📱 点击进入手机后台", url=f"{DOMAIN}/manage?sid={sid}&gid={msg.chat.id}")
        ]])
        try:
            await bot.send_message(msg.from_user.id, f"🔑 <b>认证成功</b>\n当前群组: {msg.chat.title}\n链接2小时内有效。", reply_markup=kb)
            await msg.reply("🔐 权限验证通过，后台链接已私聊发给你。")
        except Exception as e:
            await msg.reply("❌ 请先【私聊】机器人点击开始，否则我无法给你发私信。")
            print(f"发送私信失败: {e}")

@dp.message()
async def bot_handler(msg: types.Message):
    if not msg.text: return
    uid, gid, text = str(msg.from_user.id), str(msg.chat.id), msg.text.strip()
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 打印每条消息到日志，方便调试
    print(f"💬 消息日志: [UID:{uid}] [群:{gid}] 内容: {text}")

    if text == "打卡":
        with get_db() as conn:
            user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        if not user: return
        # 记录打卡
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO checkins VALUES (?,?,?)", (uid, gid, today))
            conn.commit()
        await msg.reply(f"✅ {user['name']} 打卡成功！")

# --- 4. Web 路由与 API ---
@app.get("/manage", response_class=HTMLResponse)
async def admin_page(request: Request, sid: str, gid: str):
    if sid not in auth_sessions: return "链接已过期，请重新在群里发送 /start"
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        users = conn.execute("SELECT * FROM verified_users WHERE group_id=?", (gid,)).fetchall()
        timers = conn.execute("SELECT * FROM timers WHERE group_id=?", (gid,)).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "users": users, "timers": timers, "today": datetime.now().strftime('%Y-%m-%d')})

@app.post("/api/user")
async def api_user(sid:str=Form(...), gid:str=Form(...), user_id:str=Form(...), name:str=Form(...), action:str=Form(...)):
    with get_db() as conn:
        if action == "add":
            conn.execute("INSERT OR REPLACE INTO verified_users (user_id, group_id, name) VALUES (?,?,?)", (user_id, gid, name))
        elif action == "del":
            conn.execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

# --- 5. 启动逻辑 ---
async def main():
    init_db()
    # 强制删除旧的 Webhook，防止冲突
    await bot.delete_webhook(drop_pending_updates=True)
    print("🚀 机器人正在启动 (Polling模式)...")
    
    # 同时运行 FastAPI 和 Bot
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, loop="asyncio")
    server = uvicorn.Server(config)
    
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    asyncio.run(main())
