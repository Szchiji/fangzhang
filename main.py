import os, asyncio, sqlite3, uuid, logging, time, json
from datetime import datetime
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
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")
auth_states = {}

# --- 数据库整合 ---
def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # 【警告】首次部署解决 500 报错用，正式运行后请注释掉下面两行
        conn.execute("DROP TABLE IF EXISTS groups")
        conn.execute("DROP TABLE IF EXISTS verified_users")
        
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY, group_name TEXT, 
            like_emoji TEXT DEFAULT '👍', 
            list_template TEXT DEFAULT '✅ {地区} {name} [价格:{价位}]',
            custom_fields TEXT DEFAULT '地区,价位,胸围,联系方式')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id INTEGER, group_id INTEGER, name TEXT, 
            sort_order INTEGER DEFAULT 0, expire_at INTEGER DEFAULT 0,
            data_json TEXT DEFAULT "{}", PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
            user_id INTEGER, group_id INTEGER, checkin_date TEXT, 
            PRIMARY KEY(user_id, group_id, checkin_date))''')
        conn.commit()

# --- 自动任务：到期禁言 ---
async def auto_mute_task():
    while True:
        now_ts = int(time.time())
        with get_db() as conn:
            expired = conn.execute("SELECT * FROM verified_users WHERE expire_at > 0 AND expire_at < ?", (now_ts,)).fetchall()
            for u in expired:
                try: await bot.restrict_chat_member(u['group_id'], u['user_id'], permissions=ChatPermissions(can_send_messages=False))
                except: pass
        await asyncio.sleep(60)

# --- 机器人交互逻辑 ---
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_handler(msg: types.Message):
    gid, uid, today = msg.chat.id, msg.from_user.id, datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        if not group:
            conn.execute("INSERT INTO groups (group_id, group_name) VALUES (?,?)", (gid, msg.chat.title))
            conn.commit()
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()

    # 1. 自动点赞
    if user and msg.text != "打卡" and (user['expire_at'] == 0 or user['expire_at'] > time.time()):
        try: await msg.react([ReactionTypeEmoji(emoji=group['like_emoji'])])
        except: pass

    # 2. 打卡功能
    if msg.text == "打卡" and user:
        with get_db() as conn:
            exist = conn.execute("SELECT 1 FROM checkins WHERE user_id=? AND group_id=? AND checkin_date=?", (uid, gid, today)).fetchone()
            if exist: return await msg.reply("ℹ️ 您今日已打过卡。")
            conn.execute("INSERT INTO checkins VALUES (?,?,?)", (uid, gid, today))
            conn.commit()
            await msg.reply(f"✅ 【{user['name']}】今日打卡成功！")

    # 3. 动态模板名单展示
    if msg.text in ["今日榨汁", "今日名单"]:
        with get_db() as conn:
            users = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id 
                                 AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=? 
                                 ORDER BY v.sort_order DESC''', (gid, today)).fetchall()
        if not users: return await msg.answer("📅 暂时没有在线老师")
        res = f"<b>📅 今日在线名单 ({len(users)}人)</b>\n\n"
        for u in users:
            attr = json.loads(u['data_json']); attr['name'] = u['name']; attr['onlineEmoji'] = "✅"
            try: res += group['list_template'].format(**attr) + "\n"
            except KeyError as e: res += f"✅ {u['name']} (缺少字段: {e})\n"
        await msg.answer(res)

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
            return await msg.answer("✅ 身份验证通过！")

# --- Web 接口整合 ---
@app.get("/login", response_class=HTMLResponse)
async def login(request: Request, sid: str):
    return templates.TemplateResponse("login.html", {"request": request, "sid": sid, "code": auth_states.get(sid, {}).get("code")})

@app.get("/check_status/{sid}")
async def check_status(sid: str):
    return {"status": "verified" if auth_states.get(sid, {}).get("verified") else "waiting"}

@app.get("/portal", response_class=HTMLResponse)
async def portal(request: Request, sid: str):
    if not auth_states.get(sid,{}).get("verified"): return RedirectResponse(f"/login?sid={sid}")
    with get_db() as conn: groups = conn.execute("SELECT * FROM groups").fetchall()
    return templates.TemplateResponse("portal.html", {"request": request, "sid": sid, "groups": groups})

@app.get("/manage", response_class=HTMLResponse)
async def manage(request: Request, sid: str, gid: int, q: str = ""):
    if not auth_states.get(sid,{}).get("verified"): return RedirectResponse(f"/login?sid={sid}")
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        sql = "SELECT * FROM verified_users WHERE group_id=?"
        params = [gid]
        if q:
            sql += " AND (name LIKE ? OR user_id LIKE ? OR data_json LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        users = conn.execute(sql + " ORDER BY sort_order DESC", params).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "users": users, "q": q, "now": int(time.time())})

@app.post("/api/save_user")
async def save_user(request: Request):
    form = await request.form()
    sid, gid, uid = form.get("sid"), int(form.get("gid")), int(form.get("user_id"))
    name, days, sort = form.get("name"), int(form.get("days", 0)), int(form.get("sort", 0))
    # 动态抓取所有非固定字段存入 JSON
    custom = {k: v for k, v in form.items() if k not in ['sid', 'gid', 'user_id', 'name', 'days', 'sort']}
    expire_at = int(time.time() + days*86400) if days > 0 else 0
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO verified_users VALUES (?,?,?,?,?,?)", (uid, gid, name, sort, expire_at, json.dumps(custom, ensure_ascii=False)))
        conn.commit()
    try: await bot.restrict_chat_member(gid, uid, permissions=ChatPermissions(can_send_messages=True, can_send_other_messages=True, can_send_photos=True, can_send_videos=True))
    except: pass
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/delete_user")
async def delete_user(sid: str=Form(...), gid: int=Form(...), user_id: int=Form(...)):
    with get_db() as conn:
        conn.execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/save_config")
async def save_config(sid: str=Form(...), gid: int=Form(...), like_emoji: str=Form(...), list_template: str=Form(...), custom_fields: str=Form(...)):
    with get_db() as conn:
        conn.execute("UPDATE groups SET like_emoji=?, list_template=?, custom_fields=? WHERE group_id=?", (like_emoji, list_template, custom_fields, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(auto_mute_task())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
