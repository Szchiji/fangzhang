import os, asyncio, sqlite3, uuid, json, re
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

# --- 基础配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"
DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

# 初始化组件
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()
auth_sessions = {}

# --- 数据库核心：自动修复与初始化 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        # 创建基础表
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id TEXT, group_id TEXT, name TEXT, status TEXT, 
            area TEXT, teacher TEXT, last_time TEXT, 
            PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS sent_logs (
            message_id TEXT, chat_id TEXT, delete_at TEXT, status TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, gid TEXT, content TEXT, cron INT, 
            delete_after INT, remark TEXT)''')
        
        # 自动补全 groups 表字段 (解决 no such column 报错)
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

# --- 逻辑功能：查询解析 ---
def get_query_response(gid):
    g = db_query("SELECT query_tpl, on_emoji, group_name FROM groups WHERE group_id=?", (gid,), True)
    if not g or not g[0]: return "⚠️ 请先在后台配置查询模板"
    
    tpl, on_emoji, gname = g[0], g[1] or "✅", g[2] or "未命名群组"
    online_users = db_query("SELECT user_id, name, area, teacher FROM verified_users WHERE group_id=? AND status='online'", (gid,))
    
    if not online_users:
        return f"📊 <b>{gname}</b>\n\n当前暂无在线成员。"

    user_list_str = ""
    for u in online_users:
        item = tpl.replace("{onlineEmoji}", on_emoji)\
                  .replace("{认证用户名字}", str(u[1]))\
                  .replace("{在线用户ID}", str(u[0]))\
                  .replace("{地区Value}", str(u[2] or "未设置"))\
                  .replace("{老师名字Value}", str(u[3] or "未设置"))
        user_list_str += f"{item}\n"

    return f"📊 <b>{gname} | 在线: {len(online_users)}</b>\n\n{user_list_str}"

# --- 机器人事件监听 ---
@dp.message(F.forward_from)
async def handle_forward(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    u = msg.forward_from
    await msg.reply(f"👤 <b>用户信息已解析</b>\nUID: <code>{u.id}</code>\n姓名: {u.first_name}")

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    kb = InlineKeyboardBuilder().button(text="🏢 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}").as_markup()
    await msg.reply("🔓 身份验证成功，请点击下方按钮：", reply_markup=kb)

@dp.message(F.text)
async def handle_text_commands(msg: types.Message):
    gid, uid, text = str(msg.chat.id), str(msg.from_user.id), msg.text.strip()
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    if not g or g[2] == 0: return # 机器人关闭或未配置群

    # 逻辑：查询
    if text == g[9]: # query_cmd
        await msg.reply(get_query_response(gid))
    
    # 逻辑：打卡
    user = db_query("SELECT name FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid), True)
    if not user: return

    if text == g[3]: # check_cmd
        db_exec("UPDATE verified_users SET status='online', last_time=? WHERE user_id=? AND group_id=?", 
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), uid, gid))
        await msg.reply(f"{g[4]} {user[0]} 打卡成功！")
    elif text == g[6]: # off_cmd
        db_exec("UPDATE verified_users SET status='offline' WHERE user_id=? AND group_id=?", (uid, gid))
        await msg.reply(f"{g[5]} {user[0]} 已进入休息状态。")

# --- Web 路由 ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def router_page(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return "验证过期，请重新 /start"
    if not gid:
        gs = db_query("SELECT group_id, group_name FROM groups")
        return templates.TemplateResponse("select.html", {"request": request, "sid": sid, "gs": gs})
    
    g_data = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    users = db_query("SELECT * FROM verified_users WHERE group_id=?", (gid,))
    tasks = db_query("SELECT * FROM tasks WHERE gid=?", (gid,))
    
    tpl_map = {"basic": "basic.html", "checkin": "checkin.html", "query": "query.html", "tasks": "tasks.html", "users": "users.html"}
    return templates.TemplateResponse(tpl_map.get(tab, "basic.html"), {
        "request": request, "sid": sid, "gid": gid, "g": g_data, "users": users, "tasks": tasks, "tab": tab
    })

# AJAX 统一保存接口
@app.post("/api/save")
async def api_save(sid: str = Form(...), gid: str = Form(...), field: str = Form(...), value: str = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status":"error"}, 403)
    db_exec(f"UPDATE groups SET {field}=? WHERE group_id=?", (value, gid))
    return {"status": "ok"}

@asynccontextmanager
async def lifespan(app_in: FastAPI):
    init_db()
    if not scheduler.running: scheduler.start()
    polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
    print("✅ 加固版系统已上线")
    yield
    polling_task.cancel()
    await bot.session.close()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
