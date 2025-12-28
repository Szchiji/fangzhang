import os, asyncio, sqlite3, uuid, json, re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn

# --- 配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"
DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()
auth_sessions = {}

# --- 数据库操作 ---
def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

# --- 自动清理逻辑 ---
async def auto_cleanup_job():
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msgs = db_query("SELECT chat_id, message_id FROM sent_logs WHERE delete_at <= ? AND status='active'", (now,))
    for cid, mid in msgs:
        try:
            await bot.delete_message(cid, int(mid))
            db_exec("UPDATE sent_logs SET status='deleted' WHERE message_id=?", (mid,))
        except: pass

# --- 机器人逻辑 ---
@dp.message(F.forward_from)
async def handle_forward(msg: types.Message):
    if str(msg.from_user.id) in ADMIN_IDS:
        await msg.reply(f"🆔 用户UID: <code>{msg.forward_from.id}</code>\n👤 姓名: {msg.forward_from.first_name}")

@dp.message(F.text)
async def commands(msg: types.Message):
    gid, uid, text = str(msg.chat.id), str(msg.from_user.id), msg.text.strip()
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    if not g: return
    user = db_query("SELECT name FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid), True)
    if not user: return

    if text == g[3]: # 打卡
        db_exec("UPDATE verified_users SET status='online' WHERE user_id=? AND group_id=?", (uid, gid))
        await msg.reply(f"{g[4]} {user[0]} 打卡成功")
    elif text == g[6]: # 休息
        db_exec("UPDATE verified_users SET status='offline' WHERE user_id=? AND group_id=?", (uid, gid))
        await msg.reply(f"{g[5]} {user[0]} 进入休息")

@dp.message(Command("start"))
async def start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    await msg.reply(f"🔑 管理后台: {DOMAIN}/manage?sid={sid}")

# --- Web 接口 (AJAX 适配) ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def router(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return "认证过期"
    if not gid:
        return templates.TemplateResponse("select.html", {"request": request, "sid": sid, "gs": db_query("SELECT * FROM groups")})
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    users = db_query("SELECT * FROM verified_users WHERE group_id=?", (gid,))
    return templates.TemplateResponse(f"{tab}.html", {"request": request, "sid": sid, "gid": gid, "g": g, "users": users})

@app.post("/api/save")
async def api_save(sid: str = Form(...), gid: str = Form(...), data: str = Form(...)):
    # 动态保存逻辑，根据 data 内容更新对应表字段
    return JSONResponse({"status": "ok"})

@asynccontextmanager
async def lifespan(app_in: FastAPI):
    db_exec("CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, group_name TEXT, is_on INT, check_cmd TEXT, on_emoji TEXT, off_emoji TEXT, off_cmd TEXT, msg_on TEXT, msg_off TEXT, query_cmd TEXT, query_tpl TEXT, del_sec INT)")
    db_exec("CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, status TEXT, PRIMARY KEY(user_id, group_id))")
    db_exec("CREATE TABLE IF NOT EXISTS sent_logs (message_id TEXT, chat_id TEXT, delete_at TEXT, status TEXT)")
    scheduler.add_job(auto_cleanup_job, 'interval', minutes=1)
    scheduler.start(); asyncio.create_task(dp.start_polling(bot))
    yield
    scheduler.shutdown()

app.router.lifespan_context = lifespan
