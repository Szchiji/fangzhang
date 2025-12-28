import os, asyncio, sqlite3, uuid, json
from datetime import datetime
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

# --- 配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"
DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()
auth_sessions = {}

# --- 数据库加固逻辑 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        # 基础建表
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, status TEXT, last_time TEXT, PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS sent_logs (message_id TEXT, chat_id TEXT, delete_at TEXT, status TEXT)''')
        
        # 自动补全缺失字段 (解决 sqlite3.OperationalError: no such column)
        required_columns = {
            "group_name": "TEXT",
            "is_on": "INT DEFAULT 1",
            "check_cmd": "TEXT DEFAULT '打卡'",
            "on_emoji": "TEXT DEFAULT '✅'",
            "off_emoji": "TEXT DEFAULT '❌'",
            "off_cmd": "TEXT DEFAULT '休息'",
            "msg_on": "TEXT",
            "msg_off": "TEXT",
            "query_cmd": "TEXT DEFAULT '今日榨汁'",
            "query_tpl": "TEXT",
            "del_sec": "INT DEFAULT 0"
        }
        existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(groups)")]
        for col_name, col_type in required_columns.items():
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE groups ADD COLUMN {col_name} {col_type}")
        conn.commit()

def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

# --- 机器人逻辑 ---
@dp.message(F.forward_from)
async def handle_forward(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    await msg.reply(f"🆔 用户UID: <code>{msg.forward_from.id}</code>\n👤 姓名: {msg.forward_from.first_name}")

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    kb = InlineKeyboardBuilder().button(text="🏢 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}").as_markup()
    await msg.reply("🔓 身份验证成功，点击进入后台：", reply_markup=kb)

# --- Web 路由 (修复 500 错误) ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def router_page(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return "Session Expired. Please /start in Bot."
    
    if not gid:
        gs = db_query("SELECT group_id, group_name FROM groups")
        return templates.TemplateResponse("select.html", {"request": request, "sid": sid, "gs": gs})
    
    g_data = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    users_list = db_query("SELECT * FROM verified_users WHERE group_id=?", (gid,))
    
    # 动态映射模板
    tpl_map = {"basic": "basic.html", "checkin": "checkin.html", "query": "query.html", "tasks": "tasks.html", "users": "users.html"}
    return templates.TemplateResponse(tpl_map.get(tab, "basic.html"), {
        "request": request, "sid": sid, "gid": gid, "g": g_data, "users": users_list, "tab": tab
    })

@app.post("/api/save")
async def api_save(sid: str = Form(...), gid: str = Form(...)):
    # 接收 AJAX 提交
    return JSONResponse({"status": "ok"})

# --- 生命周期 ---
@asynccontextmanager
async def lifespan(app_in: FastAPI):
    init_db() # 每次启动自动修复数据库
    scheduler.start()
    polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
    print("🚀 系统已上线")
    yield
    polling_task.cancel()
    await bot.session.close()

app.router.lifespan_context = lifespan
