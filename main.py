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

# --- 1. 基础配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"
DB_PATH = os.getenv("DB_PATH", "/data/bot_final.db")
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()
auth_sessions = {}

# --- 2. 数据库：增加有效期、多媒体、按钮字段 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        # 群组配置表
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY, group_name TEXT, is_on INT DEFAULT 1, 
            check_cmd TEXT DEFAULT '打卡', on_emoji TEXT DEFAULT '✅', 
            off_emoji TEXT DEFAULT '❌', off_cmd TEXT DEFAULT '休息', 
            msg_on TEXT, msg_off TEXT, query_cmd TEXT DEFAULT '查询', 
            query_tpl TEXT, del_sec INT DEFAULT 0)''')
        
        # 用户认证表 (含有效期)
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id TEXT, group_id TEXT, name TEXT, status TEXT, 
            area TEXT, teacher TEXT, last_time TEXT, expire_at TEXT, 
            PRIMARY KEY(user_id, group_id))''')
        
        # 定时任务表 (多媒体版)
        conn.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, gid TEXT, content TEXT, cron INT, 
            delete_after INT, remark TEXT, media_type TEXT, 
            media_url TEXT, buttons TEXT)''')
        
        conn.commit()

def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

# --- 3. 核心：万能占位符解析引擎 ---
def parse_msg(tpl, u, gname):
    """
    u 索引参考: 0:uid, 2:name, 4:area, 5:teacher, 6:last_time, 7:expire_at
    """
    if not tpl: return ""
    mapping = {
        "{名字}": u[2],
        "{地区}": u[4] or "未填",
        "{老师}": u[5] or "未填",
        "{时间}": u[6] or datetime.now().strftime("%H:%M"),
        "{群组}": gname,
        "{到期时间}": u[7] or "永久",
        "{用户ID}": u[0]
    }
    for k, v in mapping.items():
        tpl = tpl.replace(k, str(v))
    return tpl

# --- 4. 机器人指令逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS:
        return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    kb = InlineKeyboardBuilder().button(text="🏢 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}").as_markup()
    await msg.reply(f"<b>身份验证成功</b>\n当前域: {DOMAIN}", reply_markup=kb)

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group(msg: types.Message):
    gid, uid, text = str(msg.chat.id), str(msg.from_user.id), msg.text or ""
    # 自动注册新群组
    db_exec("INSERT OR IGNORE INTO groups (group_id, group_name) VALUES (?, ?)", (gid, msg.chat.title))
    
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    if not g or g[2] == 0: return # 机器人未开启

    # 1. 处理打卡
    if text == g[3]:
        u = db_query("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid), True)
        if not u: return
        
        # 校验有效期
        if u[7]: 
            try:
                if datetime.strptime(u[7], "%Y-%m-%d") < datetime.now():
                    return await msg.reply("⚠️ 您的认证已过期")
            except: pass

        t_now = datetime.now().strftime("%H:%M")
        db_exec("UPDATE verified_users SET status='online', last_time=? WHERE user_id=? AND group_id=?", (t_now, uid, gid))
        
        reply_body = parse_msg(g[7] or "{名字} 打卡成功", u, g[1])
        await msg.reply(f"{g[4]} {reply_body}")

    # 2. 处理下班
    elif text == g[6]:
        db_exec("UPDATE verified_users SET status='offline' WHERE user_id=? AND group_id=?", (uid, gid))
        await msg.reply(f"{g[5]} 已下班，休息愉快")

    # 3. 处理查询列表
    elif text == g[9]:
        online = db_query("SELECT * FROM verified_users WHERE group_id=? AND status='online'", (gid,))
        if not online:
            return await msg.reply("📊 目前无人在线")
        
        lines = [parse_msg(g[10] or "· {名字} ({地区})", u, g[1]) for u in online]
        await msg.reply(f"📊 <b>{g[1]} 在线列表</b>\n\n" + "\n".join(lines))

# --- 5. Web API 接口 ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def router_page(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return HTMLResponse("验证过期，请重新 /start")
    if not gid:
        gs = db_query("SELECT group_id, group_name FROM groups")
        return templates.TemplateResponse("select.html", {"request": request, "sid": sid, "gs": gs})
    
    g_data = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    users = db_query("SELECT * FROM verified_users WHERE group_id=?", (gid,))
    tasks = db_query("SELECT * FROM tasks WHERE gid=?", (gid,))
    return templates.TemplateResponse(f"{tab}.html", {
        "request": request, "sid": sid, "gid": gid, "g": g_data, "users": users, "tasks": tasks, "tab": tab
    })

@app.post("/api/save")
async def api_save(sid: str = Form(...), gid: str = Form(...), field: str = Form(...), value: str = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status":"err"}, 403)
    db_exec(f"UPDATE groups SET {field}=? WHERE group_id=?", (value, gid))
    return {"status": "ok"}

@app.post("/api/add_user")
async def api_add_user(sid: str = Form(...), gid: str = Form(...), user_id: str = Form(...), 
                       name: str = Form(...), area: str = Form(None), teacher: str = Form(None), 
                       expire_at: str = Form(None)):
    if sid not in auth_sessions: return JSONResponse({"status":"err"}, 403)
    db_exec("INSERT OR REPLACE INTO verified_users (user_id, group_id, name, status, area, teacher, expire_at) VALUES (?, ?, ?, 'offline', ?, ?, ?)", 
            (user_id, gid, name, area, teacher, expire_at))
    return {"status": "ok"}

@app.post("/api/del_user")
async def api_del_user(sid: str = Form(...), gid: str = Form(...), user_id: str = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status":"err"}, 403)
    db_exec("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
    return {"status": "ok"}

@app.post("/api/add_task")
async def api_add_task(sid: str = Form(...), gid: str = Form(...), remark: str = Form(...), 
                       content: str = Form(...), cron: int = Form(...), m_type: str = Form("text"), 
                       m_url: str = Form(None), btn: str = Form(None)):
    if sid not in auth_sessions: return JSONResponse({"status":"err"}, 403)
    tid = str(uuid.uuid4())[:8]
    db_exec("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)", (tid, gid, content, cron, 0, remark, m_type, m_url, btn))
    return {"status": "ok"}

@app.post("/api/del_task")
async def api_del_task(sid: str = Form(...), tid: str = Form(...)):
    if sid not in auth_sessions: return JSONResponse({"status":"err"}, 403)
    db_exec("DELETE FROM tasks WHERE id=?", (tid,))
    return {"status": "ok"}

# --- 6. 生命周期：强制重置 Webhook 解决无反应问题 ---
@asynccontextmanager
async def lifespan(app_in: FastAPI):
    init_db()
    # 核心修复：强制清除 Webhook
    await bot.delete_webhook(drop_pending_updates=True)
    if not scheduler.running:
        scheduler.start()
    
    polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
    print("🚀 系统已启动，正在监听 /start...")
    yield
    polling_task.cancel()
    await bot.session.close()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
