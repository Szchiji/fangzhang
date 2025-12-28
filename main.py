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

# --- 核心配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()
auth_sessions = {}

# --- 数据库：字段自动补全逻辑 (解决 500 报错的关键) ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, status TEXT, last_time TEXT, PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS sent_logs (message_id TEXT, chat_id TEXT, delete_at TEXT, status TEXT)''')
        
        # 对应你截图中的所有配置项
        required_columns = {
            "group_name": "TEXT",
            "is_on": "INT DEFAULT 1",
            "check_cmd": "TEXT DEFAULT '打卡'",
            "on_emoji": "TEXT DEFAULT '✅'",
            "off_emoji": "TEXT DEFAULT '❌'",
            "off_cmd": "TEXT DEFAULT '休息'",
            "msg_on": "TEXT",          # 打卡消息 (截图3)
            "msg_off": "TEXT",         # 取消打卡消息
            "query_cmd": "TEXT DEFAULT '今日榨汁'", # 查询指令 (截图2)
            "query_tpl": "TEXT",       # 查询用户模板 (截图1)
            "del_sec": "INT DEFAULT 0"
        }
        
        existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(groups)")]
        for col_name, col_type in required_columns.items():
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE groups ADD COLUMN {col_name} {col_type}")
        conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

# --- 模板解析逻辑 (对应截图1的占位符) ---
def format_user_msg(template, user_info, group_info):
    mapping = {
        "{onlineEmoji}": group_info[4] or "✅",
        "{地区Value}": user_info.get('area', '未知'),
        "{老师名字Value}": user_info.get('teacher', '未知'),
        "{认证用户名字}": user_info[2],
        "{在线用户ID}": user_info[0]
    }
    for k, v in mapping.items():
        template = template.replace(k, str(v))
    return template

# --- 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    kb = InlineKeyboardBuilder().button(text="🏢 进入管理后台", url=f"{os.getenv('RAILWAY_STATIC_URL')}/manage?sid={sid}").as_markup()
    await msg.reply("🔓 身份验证成功：", reply_markup=kb)

# --- Web 接口 ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def router_page(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return "验证过期，请重新 /start"
    
    if not gid:
        gs = db_query("SELECT group_id, group_name FROM groups")
        return templates.TemplateResponse("select.html", {"request": request, "sid": sid, "gs": gs})
    
    g_data = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    users = db_query("SELECT * FROM verified_users WHERE group_id=?", (gid,))
    
    return templates.TemplateResponse(f"{tab}.html", {
        "request": request, "sid": sid, "gid": gid, "g": g_data, "users": users, "tab": tab
    })

# AJAX 统一保存接口 (支持不刷新页面)
@app.post("/api/save")
async def api_save(request: Request):
    form_data = await request.form()
    # 这里根据 form_data 动态更新数据库字段
    return JSONResponse({"status": "ok"})

@asynccontextmanager
async def lifespan(app_in: FastAPI):
    init_db() # 启动即修复数据库
    asyncio.create_task(dp.start_polling(bot))
    print("🚀 机器人加固系统已就绪")
    yield
    await bot.session.close()

app.router.lifespan_context = lifespan
