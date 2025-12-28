import os, asyncio, sqlite3, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
import uvicorn

# 配置区
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',')]
DOMAIN = f"https://{os.getenv('RAILWAY_STATIC_URL', 'localhost:8080')}".rstrip('/')
DB_PATH = "/data/bot_pro.db"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
auth_sessions = {}

def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.execute(sql, params)
        return c.fetchone() if one else c.fetchall()

def init_db():
    os.makedirs("/data", exist_ok=True)
    db_exec('''CREATE TABLE IF NOT EXISTS groups (gid TEXT PRIMARY KEY, gname TEXT, is_on INT DEFAULT 1, cmd_on TEXT DEFAULT '打卡', cmd_off TEXT DEFAULT '下班', emo_on TEXT DEFAULT '✅', emo_off TEXT DEFAULT '❌', cmd_query TEXT DEFAULT '查询', tpl_query TEXT)''')
    db_exec('''CREATE TABLE IF NOT EXISTS users (uid TEXT, gid TEXT, name TEXT, area TEXT, teacher TEXT, expire TEXT, PRIMARY KEY(uid, gid))''')
    db_exec('''CREATE TABLE IF NOT EXISTS tasks (tid TEXT PRIMARY KEY, gid TEXT, content TEXT, cron INT, remark TEXT)''')

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    kb = InlineKeyboardBuilder().button(text="🖥️ 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}").as_markup()
    await msg.answer("<b>7哥，欢迎登录中控系统</b>\n后台链接已生成，请点击进入：", reply_markup=kb)

app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def page_manage(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return "验证过期，请重新在Bot发送/start"
    # 如果没选群组，先去选群
    if not gid:
        gs = db_query("SELECT gid, gname FROM groups")
        return templates.TemplateResponse("select.html", {"request":request, "sid":sid, "gs":gs})
    # 获取数据
    g = db_query("SELECT * FROM groups WHERE gid=?", (gid,), True)
    u = db_query("SELECT * FROM users WHERE gid=?", (gid,))
    t = db_query("SELECT * FROM tasks WHERE gid=?", (gid,))
    return templates.TemplateResponse(f"{tab}.html", {"request":request, "sid":sid, "gid":gid, "g":g, "users":u, "tasks":t, "tab":tab})

# --- 通用 API (全覆盖) ---
@app.post("/api/save_group")
async def save_group(sid:str=Form(...), gid:str=Form(...), field:str=Form(...), value:str=Form(...)):
    db_exec(f"UPDATE groups SET {field}=? WHERE gid=?", (value, gid))
    return {"status":"ok"}

@app.post("/api/user/save")
async def user_save(sid:str=Form(...), gid:str=Form(...), uid:str=Form(...), name:str=Form(...), area:str=Form(None), teacher:str=Form(None), expire:str=Form(None)):
    db_exec("INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?)", (uid, gid, name, area, teacher, expire))
    return {"status":"ok"}

@app.post("/api/user/del")
async def user_del(uid:str=Form(...), gid:str=Form(...)):
    db_exec("DELETE FROM users WHERE uid=? AND gid=?", (uid, gid))
    return {"status":"ok"}

@app.post("/api/task/add")
async def task_add(sid:str=Form(...), gid:str=Form(...), remark:str=Form(...), content:str=Form(...), cron:int=Form(...)):
    db_exec("INSERT INTO tasks VALUES (?,?,?,?,?)", (str(uuid.uuid4())[:8], gid, content, cron, remark))
    return {"status":"ok"}

@app.post("/api/task/del")
async def task_del(tid:str=Form(...)):
    db_exec("DELETE FROM tasks WHERE tid=?", (tid,))
    return {"status":"ok"}

@asynccontextmanager
async def lifespan(a: FastAPI):
    init_db()
    asyncio.create_task(dp.start_polling(bot))
    yield

app.router.lifespan_context = lifespan
if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=8080)
