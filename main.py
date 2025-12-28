import os, asyncio, sqlite3, uuid, time, json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
import uvicorn

# --- 1. 配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"  # 建议 Railway 挂载 Volume 到 /data
os.makedirs("/data", exist_ok=True)

# --- 2. 实例 ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
auth_sessions = {}

# --- 3. 数据库 ---
def db_execute(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), fetchone=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if fetchone else cursor.fetchall()

def init_db():
    db_execute('CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, like_emoji TEXT DEFAULT "👍", custom_fields TEXT DEFAULT "地区,价格,链接", list_template TEXT, checkin_template TEXT)')
    db_execute('CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, data_json TEXT, expire_date TEXT, PRIMARY KEY(user_id, group_id))')
    db_execute('CREATE TABLE IF NOT EXISTS checkins (user_id TEXT, group_id TEXT, checkin_date TEXT, PRIMARY KEY(user_id, group_id, checkin_date))')

# --- 4. 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = str(msg.from_user.id)
    if uid not in ADMIN_IDS: return await msg.reply(f"❌ 无权限。ID: {uid}")
    sid = str(uuid.uuid4())
    auth_sessions[sid] = {"uid": uid, "exp": time.time() + 7200}
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="⚙️ 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}&gid={msg.chat.id}"))
    await msg.reply(f"🔐 验证通过！群组ID: <code>{msg.chat.id}</code>", reply_markup=builder.as_markup())

@dp.message(F.text == "打卡")
async def handle_checkin(msg: types.Message):
    gid, uid = str(msg.chat.id), str(msg.from_user.id)
    user = db_query("SELECT name, expire_date FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid), True)
    if not user: return await msg.reply("❌ 您尚未通过验证。")
    if datetime.now() > datetime.strptime(user[1], "%Y-%m-%d"): return await msg.reply("⚠️ 验证已过期。")
    try:
        db_execute("INSERT INTO checkins VALUES (?, ?, ?)", (uid, gid, datetime.now().strftime("%Y-%m-%d")))
        count = db_query("SELECT COUNT(*) FROM checkins WHERE user_id=? AND group_id=?", (uid, gid), True)[0]
        await msg.reply(f"✅ <b>{user[0]}</b> 打卡成功！累计: {count} 次")
    except sqlite3.IntegrityError: await msg.reply("📢 今日已打卡。")

# --- 5. Web 路由 ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def get_manage(request: Request, sid: str, gid: str):
    if sid not in auth_sessions or time.time() > auth_sessions[sid]["exp"]: return HTMLResponse("失效")
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True) or (gid, "👍", "地区,价格,链接", "", "")
    group_obj = {"group_id": g[0], "like_emoji": g[1], "custom_fields": g[2]}
    users = db_query("SELECT user_id, name, expire_date, data_json FROM verified_users WHERE group_id=?", (gid,))
    user_list = [{"id": u[0], "name": u[1], "expire": u[2], "data": json.loads(u[3])} for u in users]
    return templates.TemplateResponse("manage.html", {"request": request, "gid": gid, "sid": sid, "group": group_obj, "users": user_list, "fields": group_obj["custom_fields"].split(',')})

@app.get("/group_settings", response_class=HTMLResponse)
async def get_settings(request: Request, sid: str, gid: str):
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    group_obj = {"group_id": g[0], "like_emoji": g[1], "custom_fields": g[2], "list_template": g[3], "checkin_template": g[4]}
    return templates.TemplateResponse("group_settings.html", {"request": request, "gid": gid, "sid": sid, "group": group_obj})

@app.get("/checkin_logs", response_class=HTMLResponse)
async def get_logs(request: Request, sid: str, gid: str):
    logs = db_query("SELECT c.user_id, u.name, c.checkin_date FROM checkins c LEFT JOIN verified_users u ON c.user_id=u.user_id WHERE c.group_id=? ORDER BY c.checkin_date DESC LIMIT 100", (gid,))
    return templates.TemplateResponse("checkin_logs.html", {"request": request, "gid": gid, "sid": sid, "logs": [{"uid": l[0], "name": l[1] or "未知", "date": l[2]} for l in logs]})

@app.post("/update_settings")
@app.post("/save_group")
async def update_settings(sid: str=Form(...), gid: str=Form(...), fields: str=Form(None), emoji: str=Form(None), list_template: str=Form(None), checkin_template: str=Form(None)):
    db_execute("UPDATE groups SET custom_fields=?, like_emoji=?, list_template=?, checkin_template=? WHERE group_id=?", (fields, emoji, list_template, checkin_template, gid))
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/add_user")
async def add_user(sid: str=Form(...), gid: str=Form(...), uid: str=Form(...), name: str=Form(...), days: int=Form(...), custom_data: str=Form("{}")):
    expire = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    db_execute("REPLACE INTO verified_users VALUES (?, ?, ?, ?, ?)", (uid, gid, name, custom_data, expire))
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/delete_user")
async def delete_user(sid: str=Form(...), gid: str=Form(...), uid: str=Form(...)):
    db_execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid))
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@asynccontextmanager
async def lifespan(app_in: FastAPI):
    init_db(); await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me(); print(f"🚀 Bot @{me.username} Online")
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    task.cancel(); await bot.session.close()

app.router.lifespan_context = lifespan
if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=PORT)
