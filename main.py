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
DB_PATH = os.getenv("DB_PATH", "/data/bot_perfect.db")
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()
auth_sessions = {}

# --- 2. 数据库引擎 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, group_name TEXT, is_on INT DEFAULT 1, check_cmd TEXT DEFAULT '打卡', on_emoji TEXT DEFAULT '✅', off_emoji TEXT DEFAULT '❌', off_cmd TEXT DEFAULT '休息', msg_on TEXT, msg_off TEXT, query_cmd TEXT DEFAULT '查询', query_tpl TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, status TEXT, area TEXT, teacher TEXT, last_time TEXT, expire_at TEXT, PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, gid TEXT, content TEXT, cron INT, remark TEXT, m_type TEXT, m_url TEXT, btn TEXT)''')
        conn.commit()

def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

# --- 3. 占位符解析引擎 ---
def parse_msg(tpl, u, gname):
    if not tpl: return ""
    mapping = {"{名字}": u[2], "{地区}": u[4] or "未填", "{老师}": u[5] or "未填", "{时间}": u[6] or datetime.now().strftime("%H:%M"), "{群组}": gname, "{到期时间}": u[7] or "永久", "{用户ID}": u[0]}
    for k, v in mapping.items(): tpl = tpl.replace(k, str(v))
    return tpl

# --- 4. 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    kb = InlineKeyboardBuilder().button(text="🏢 控制中枢", url=f"{DOMAIN}/manage?sid={sid}").as_markup()
    await msg.reply(f"<b>权限确认成功</b>", reply_markup=kb)

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_msg(msg: types.Message):
    gid, uid, text = str(msg.chat.id), str(msg.from_user.id), msg.text or ""
    db_exec("INSERT OR IGNORE INTO groups (group_id, group_name) VALUES (?, ?)", (gid, msg.chat.title))
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    if not g: return
    
    if text == g[3]: # 打卡
        u = db_query("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid), True)
        if not u: return
        if u[7] and datetime.strptime(u[7], "%Y-%m-%d") < datetime.now(): return await msg.reply("❌ 认证已过期")
        t = datetime.now().strftime("%H:%M")
        db_exec("UPDATE verified_users SET status='online', last_time=? WHERE user_id=? AND group_id=?", (t, uid, gid))
        await msg.reply(parse_msg(g[7] or "{名字} 已上岗", u, g[1]))
    elif text == g[9]: # 查询
        online = db_query("SELECT * FROM verified_users WHERE group_id=? AND status='online'", (gid,))
        if not online: return await msg.reply("📊 无人在线")
        res = [parse_msg(g[10] or "· {名字}", u, g[1]) for u in online]
        await msg.reply(f"📊 <b>{g[1]} 在线列表</b>\n\n" + "\n".join(res))

# --- 5. Web API 接口 ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def router_page(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return HTMLResponse("验证失效")
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
    db_exec("INSERT INTO tasks (id, gid, content, cron, remark) VALUES (?,?,?,?,?)", (tid, gid, content, cron, remark))
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
