import os, asyncio, sqlite3, uuid, json
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn

# --- 1. 基础配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()
auth_sessions = {}

# --- 2. 数据库加固逻辑 (解决 no such column 问题) ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        # 创建/修复 groups 表
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY)''')
        group_cols = {
            "group_name": "TEXT", "is_on": "INT DEFAULT 1", "check_cmd": "TEXT DEFAULT '打卡'",
            "on_emoji": "TEXT DEFAULT '✅'", "off_emoji": "TEXT DEFAULT '❌'", "off_cmd": "TEXT DEFAULT '休息'",
            "msg_on": "TEXT", "msg_off": "TEXT", "query_cmd": "TEXT DEFAULT '今日榨汁'",
            "query_tpl": "TEXT", "del_sec": "INT DEFAULT 0"
        }
        existing_group = [row[1] for row in conn.execute("PRAGMA table_info(groups)")]
        for col, col_type in group_cols.items():
            if col not in existing_group:
                conn.execute(f"ALTER TABLE groups ADD COLUMN {col} {col_type}")

        # 创建/修复 tasks 表 (解决 gid 缺失问题)
        conn.execute('''CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY)''')
        task_cols = {"gid": "TEXT", "content": "TEXT", "cron": "INT", "delete_after": "INT", "remark": "TEXT"}
        existing_task = [row[1] for row in conn.execute("PRAGMA table_info(tasks)")]
        for col, col_type in task_cols.items():
            if col not in existing_task:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_type}")

        # 创建用户表
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id TEXT, group_id TEXT, name TEXT, status TEXT, 
            area TEXT, teacher TEXT, last_time TEXT, 
            PRIMARY KEY(user_id, group_id))''')
        
        # 自动清理脏数据 (没有名字的群组)
        conn.execute("DELETE FROM groups WHERE group_name IS NULL")
        conn.commit()

def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

# --- 3. 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    kb = InlineKeyboardBuilder().button(text="🏢 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}").as_markup()
    await msg.reply("🔓 身份验证成功，请点击进入：", reply_markup=kb)

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_handler(msg: types.Message):
    # 自动注册群组
    db_exec("INSERT OR IGNORE INTO groups (group_id, group_name) VALUES (?, ?)", 
            (str(msg.chat.id), msg.chat.title))

# --- 4. Web 管理后台 ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def router_page(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return HTMLResponse("会话过期，请重新 /start")
    
    if not gid:
        gs = db_query("SELECT group_id, group_name FROM groups")
        return templates.TemplateResponse("select.html", {"request": request, "sid": sid, "gs": gs})
    
    g_data = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    if not g_data: return HTMLResponse("未发现群组数据，请在群里发句话")
    
    users = db_query("SELECT * FROM verified_users WHERE group_id=?", (gid,))
    tasks = db_query("SELECT * FROM tasks WHERE gid=?", (gid,))
    
    return templates.TemplateResponse(f"{tab}.html", {
        "request": request, "sid": sid, "gid": gid, "g": g_data, "users": users, "tasks": tasks, "tab": tab
    })

# --- 5. AJAX API 接口组 ---

@app.post("/api/save")
async def api_save(sid: str = Form(...), gid: str = Form(...), field: str = Form(...), value: str = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status":"error"}, 403)
    db_exec(f"UPDATE groups SET {field}=? WHERE group_id=?", (value, gid))
    return {"status": "ok"}

@app.post("/api/add_user")
async def api_add_user(sid: str = Form(...), gid: str = Form(...), user_id: str = Form(...), 
                       name: str = Form(...), area: str = Form(None), teacher: str = Form(None)):
    if sid not in auth_sessions: return JSONResponse({"status": "error"}, 403)
    db_exec("INSERT OR REPLACE INTO verified_users (user_id, group_id, name, status, area, teacher) VALUES (?, ?, ?, 'offline', ?, ?)",
            (user_id, gid, name, area, teacher))
    return {"status": "ok"}

@app.post("/api/delete_user")
async def api_delete_user(sid: str = Form(...), gid: str = Form(...), user_id: str = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status": "error"}, 403)
    db_exec("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
    return {"status": "ok"}

@app.post("/api/add_task")
async def api_add_task(sid: str = Form(...), gid: str = Form(...), remark: str = Form(...), 
                       content_html: str = Form(...), cron: int = Form(...), delete_after: int = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status": "error"}, 403)
    tid = str(uuid.uuid4())[:8]
    db_exec("INSERT INTO tasks (id, gid, content, cron, delete_after, remark) VALUES (?, ?, ?, ?, ?, ?)", 
            (tid, gid, content_html, cron, delete_after, remark))
    return {"status": "ok"}

@app.post("/api/del_task")
async def api_del_task(sid: str = Form(...), tid: str = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status": "error"}, 403)
    db_exec("DELETE FROM tasks WHERE id=?", (tid,))
    return {"status": "ok"}

# --- 6. 生命周期管理 ---
@asynccontextmanager
async def lifespan(app_in: FastAPI):
    init_db()
    if not scheduler.running: scheduler.start()
    polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
    yield
    polling_task.cancel()
    await bot.session.close()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
