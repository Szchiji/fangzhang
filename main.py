import os, asyncio, sqlite3, uuid, logging, time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ReactionTypeEmoji, ChatPermissions
from aiogram.utils.keyboard import InlineKeyboardBuilder
import uvicorn

# --- 配置区 ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")
auth_states = {}

# --- 数据库核心：包含到期时间 ---
def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY, group_name TEXT, 
            like_emoji TEXT DEFAULT '👍', 
            list_template TEXT DEFAULT '✅ {area} {name} [价格:{price}] [身材:{chest}]')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id INTEGER, group_id INTEGER, name TEXT, area TEXT, 
            price TEXT, chest_size TEXT, sort_order INTEGER DEFAULT 0, 
            expire_at INTEGER DEFAULT 0, PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
            user_id INTEGER, group_id INTEGER, checkin_date TEXT, 
            PRIMARY KEY(user_id, group_id, checkin_date))''')

# --- 自动巡逻：每分钟检查过期并禁言 ---
async def auto_mute_task():
    while True:
        now_ts = int(time.time())
        with get_db() as conn:
            expired = conn.execute("SELECT * FROM verified_users WHERE expire_at > 0 AND expire_at < ?", (now_ts,)).fetchall()
            for u in expired:
                try:
                    await bot.restrict_chat_member(u['group_id'], u['user_id'], permissions=ChatPermissions(can_send_messages=False))
                    logging.info(f"已禁言过期用户: {u['name']}")
                except: pass
        await asyncio.sleep(60)

# --- 机器人群组逻辑 ---
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_handler(msg: types.Message):
    gid, uid, today = msg.chat.id, msg.from_user.id, datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO groups (group_id, group_name) VALUES (?,?)", (gid, msg.chat.title))
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()

    if user:
        # 只要认证未过期，发言就点赞
        if user['expire_at'] == 0 or user['expire_at'] > time.time():
            try: await msg.react([ReactionTypeEmoji(emoji=group['like_emoji'])])
            except: pass

    if msg.text == "打卡" and user:
        with get_db() as conn:
            try:
                conn.execute("INSERT INTO checkins VALUES (?,?,?)", (uid, gid, today))
                conn.commit()
                await msg.reply(f"✅ {user['name']} 今日已开课")
            except: await msg.reply("ℹ️ 您今日已打过卡")

    if msg.text in ["今日榨汁", "今日名单"]:
        with get_db() as conn:
            users = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id 
                                 AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=? 
                                 ORDER BY v.sort_order DESC''', (gid, today)).fetchall()
        if not users: return await msg.answer("📅 今日暂无打卡。")
        res = f"<b>📅 今日名单 ({len(users)}人)</b>\n\n"
        for u in users:
            res += group['list_template'].format(name=u['name'], area=u['area'], price=u['price'], chest=u['chest_size']) + "\n"
        await msg.answer(res)

# --- Web 管理路由 ---
@app.get("/manage", response_class=HTMLResponse)
async def manage(request: Request, sid: str, gid: int, q: str = ""):
    if not auth_states.get(sid, {}).get("verified"): return RedirectResponse(f"/login?sid={sid}")
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        sql = "SELECT * FROM verified_users WHERE group_id=?"
        params = [gid]
        if q:
            sql += " AND (name LIKE ? OR area LIKE ? OR user_id LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        sql += " ORDER BY sort_order DESC"
        users = conn.execute(sql, params).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "users": users, "q": q, "now": int(time.time())})

@app.post("/api/save_user")
async def save_user(sid: str=Form(...), gid: int=Form(...), user_id: int=Form(...), name: str=Form(...), area: str=Form(""), price: str=Form(""), chest: str=Form(""), sort: int=Form(0), days: int=Form(0)):
    expire_at = int(time.time() + days*86400) if days > 0 else 0
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO verified_users VALUES (?,?,?,?,?,?,?,?)", (user_id, gid, name, area, price, chest, sort, expire_at))
        conn.commit()
    # 尝试解禁（如果是新设有效期）
    try: await bot.restrict_chat_member(gid, user_id, permissions=ChatPermissions(can_send_messages=True, can_send_other_messages=True, can_send_polls=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True))
    except: pass
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(auto_mute_task())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
