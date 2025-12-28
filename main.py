import os, asyncio, sqlite3, uuid, logging, time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ReactionTypeEmoji, ChatPermissions
from aiogram.utils.keyboard import InlineKeyboardBuilder
import uvicorn

# --- 配置 ---
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

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS groups (group_id INTEGER PRIMARY KEY, group_name TEXT, like_emoji TEXT DEFAULT '👍', list_template TEXT DEFAULT '✅ {area} {name} [价格:{price}] [身材:{chest}]')")
        conn.execute("CREATE TABLE IF NOT EXISTS verified_users (user_id INTEGER, group_id INTEGER, name TEXT, area TEXT, price TEXT, chest_size TEXT, sort_order INTEGER DEFAULT 0, PRIMARY KEY(user_id, group_id))")
        conn.execute("CREATE TABLE IF NOT EXISTS checkins (user_id INTEGER, group_id INTEGER, checkin_date TEXT, PRIMARY KEY(user_id, group_id, checkin_date))")
        try: conn.execute("ALTER TABLE verified_users ADD COLUMN expire_at INTEGER DEFAULT 0")
        except: pass

async def auto_mute_task():
    while True:
        now_ts = int(time.time())
        with get_db() as conn:
            expired = conn.execute("SELECT * FROM verified_users WHERE expire_at > 0 AND expire_at < ?", (now_ts,)).fetchall()
            for u in expired:
                try: 
                    await bot.restrict_chat_member(u['group_id'], u['user_id'], permissions=ChatPermissions(can_send_messages=False))
                    logging.info(f"Muted expired user: {u['name']}")
                except: pass
        await asyncio.sleep(60)

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    sid = str(uuid.uuid4())
    auth_states[sid] = {"code": "".join([str(os.urandom(1)[0] % 10) for _ in range(6)]), "verified": False}
    kb = InlineKeyboardBuilder().button(text="🔐 进入管理后台", url=f"{DOMAIN}/login?sid={sid}").as_markup()
    await msg.answer(f"验证码: <code>{auth_states[sid]['code']}</code>", reply_markup=kb)

@dp.message(F.text.regexp(r'^\d{6}$'))
async def handle_code(msg: types.Message):
    for sid, data in auth_states.items():
        if data["code"] == msg.text:
            data["verified"] = True
            return await msg.answer("✅ 验证通过！")

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_handler(msg: types.Message):
    gid, uid, today = msg.chat.id, msg.from_user.id, datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO groups (group_id, group_name) VALUES (?,?)", (gid, msg.chat.title))
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()
    if user and (user['expire_at'] == 0 or user['expire_at'] > time.time()):
        try: await msg.react([ReactionTypeEmoji(emoji=group['like_emoji'])])
        except: pass
    if msg.text == "打卡" and user:
        with get_db() as conn:
            try:
                conn.execute("INSERT INTO checkins VALUES (?,?,?)", (uid, gid, today))
                conn.commit()
                await msg.reply(f"✅ {user['name']} 今日开课")
            except: await msg.reply("ℹ️ 今日已打卡")
    if msg.text in ["今日榨汁", "今日名单"]:
        with get_db() as conn:
            users = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=? ORDER BY v.sort_order DESC''', (gid, today)).fetchall()
        if not users: return await msg.answer("📅 今日暂无打卡。")
        res = f"<b>📅 今日名单 ({len(users)}人)</b>\n\n"
        for u in users: res += group['list_template'].format(name=u['name'], area=u['area'], price=u['price'], chest=u['chest_size']) + "\n"
        await msg.answer(res)

@app.get("/login", response_class=HTMLResponse)
async def web_login(request: Request, sid: str):
    return templates.TemplateResponse("login.html", {"request": request, "sid": sid, "code": auth_states.get(sid, {}).get("code")})

@app.get("/check_status/{sid}")
async def check_status(sid: str):
    return {"status": "verified" if auth_states.get(sid, {}).get("verified") else "waiting"}

@app.get("/portal", response_class=HTMLResponse)
async def portal(request: Request, sid: str):
    if not auth_states.get(sid, {}).get("verified"): return RedirectResponse(f"/login?sid={sid}")
    with get_db() as conn: groups = conn.execute("SELECT * FROM groups").fetchall()
    return templates.TemplateResponse("portal.html", {"request": request, "sid": sid, "groups": groups})

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
        users = conn.execute(sql + " ORDER BY sort_order DESC", params).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "users": users, "q": q, "now": int(time.time())})

@app.post("/api/save_user")
async def save_user(sid: str=Form(...), gid: int=Form(...), user_id: int=Form(...), name: str=Form(...), area: str=Form(""), price: str=Form(""), chest: str=Form(""), sort: int=Form(0), days: int=Form(0)):
    expire_at = int(time.time() + days*86400) if days > 0 else 0
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO verified_users VALUES (?,?,?,?,?,?,?,?)", (user_id, gid, name, area, price, chest, sort, expire_at))
        conn.commit()
    try: await bot.restrict_chat_member(gid, user_id, permissions=ChatPermissions(can_send_messages=True, can_send_other_messages=True, can_send_polls=True, can_send_photos=True, can_send_videos=True, can_send_audios=True, can_send_documents=True, can_send_video_notes=True, can_send_voice_notes=True))
    except: pass
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(auto_mute_task())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
