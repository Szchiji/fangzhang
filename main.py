import os, asyncio, sqlite3, uuid, json
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn

# --- 配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"
DB_PATH = os.getenv("DB_PATH", "/data/bot_v8.db")
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()
auth_sessions = {}

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, group_name TEXT, is_on INT DEFAULT 1, check_cmd TEXT DEFAULT '打卡', on_emoji TEXT DEFAULT '✅', off_emoji TEXT DEFAULT '❌', off_cmd TEXT DEFAULT '休息', msg_on TEXT, msg_off TEXT, query_cmd TEXT DEFAULT '查询', query_tpl TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, status TEXT, area TEXT, teacher TEXT, last_time TEXT, expire_at TEXT, PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, gid TEXT, content TEXT, cron INT, remark TEXT)''')
        conn.commit()

def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

# --- 机器人管理权限 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    kb = InlineKeyboardBuilder().button(text="🖥️ 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}").as_markup()
    await msg.reply(f"<b>7哥，欢迎回来！</b>\n管理链接已生成，点击下方按钮进入：", reply_markup=kb)

# --- 核心 API ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def router_page(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return HTMLResponse("验证超时，请重新在机器人发送 /start")
    if not gid:
        gs = db_query("SELECT group_id, group_name FROM groups")
        return templates.TemplateResponse("select.html", {"request": request, "sid": sid, "gs": gs})
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    u = db_query("SELECT * FROM verified_users WHERE group_id=?", (gid,))
    t = db_query("SELECT * FROM tasks WHERE gid=?", (gid,))
    return templates.TemplateResponse(f"{tab}.html", {"request": request, "sid": sid, "gid": gid, "g": g, "users": u, "tasks": t, "tab": tab})

@app.post("/api/save")
async def api_save(sid: str = Form(...), gid: str = Form(...), field: str = Form(...), value: str = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status":"err"}, 403)
    db_exec(f"UPDATE groups SET {field}=? WHERE group_id=?", (value, gid))
    return {"status": "ok"}

@app.post("/api/add_user")
async def api_add_user(sid: str = Form(...), gid: str = Form(...), user_id: str = Form(...), name: str = Form(...), area: str = Form(None), teacher: str = Form(None), expire_at: str = Form(None)):
    if sid not in auth_sessions: return JSONResponse({"status":"err"}, 403)
    db_exec("INSERT OR REPLACE INTO verified_users VALUES (?,?,?, 'offline', ?, ?, '', ?)", (user_id, gid, name, area, teacher, expire_at))
    return {"status": "ok"}

@app.post("/api/add_task")
async def api_add_task(sid: str = Form(...), gid: str = Form(...), remark: str = Form(...), content: str = Form(...), cron: int = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status":"err"}, 403)
    tid = str(uuid.uuid4())[:8]
    db_exec("INSERT INTO tasks VALUES (?,?,?,?,?)", (tid, gid, content, cron, remark))
    return {"status": "ok"}

@app.post("/api/del_task")
async def api_del_task(sid: str = Form(...), tid: str = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status":"err"}, 403)
    db_exec("DELETE FROM tasks WHERE id=?", (tid,))
    return {"status": "ok"}

@asynccontextmanager
async def lifespan(app_in: FastAPI):
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))
    yield
    await bot.session.close()

app.router.lifespan_context = lifespan
if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=PORT)
