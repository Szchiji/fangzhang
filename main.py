import os, asyncio, sqlite3, uuid, time, json, re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn

# --- 配置区 ---
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

# --- MarkdownV2 转换与转义逻辑 ---
def html_to_mdv2(html: str) -> str:
    if not html: return ""
    def escape(t): return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', t)
    tags = [(r'<b>(.*?)</b>', r'*\1*'), (r'<i>(.*?)</i>', r'_\1_'), (r'<u>(.*?)</u>', r'__\1__'),
            (r'<s>(.*?)</s>', r'~\1~'), (r'<a href="(.*?)">(.*?)</a>', r'[\2](\1)'), (r'<br>', r'\n'), (r'<p>(.*?)</p>', r'\1\n')]
    res = html
    for t_re, repl in tags: res = re.sub(t_re, repl, res, flags=re.IGNORECASE)
    res = re.sub(r'<[^>]+>', '', res)
    return escape(res).replace(r'\{', '{').replace(r'\}', '}')

# --- 数据库操作 ---
def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

# --- 机器人逻辑：打卡与自动识别 ---
@dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_bot_join(event: types.ChatMemberUpdated):
    db_exec("INSERT OR REPLACE INTO groups (group_id, group_name) VALUES (?, ?)", (str(event.chat.id), event.chat.title))

@dp.message(F.text == "打卡") # 这里可以根据数据库设置动态匹配
async def handle_checkin(msg: types.Message):
    gid, uid = str(msg.chat.id), str(msg.from_user.id)
    # 逻辑：检查是否认证 -> 记录打卡 -> 转换富文本模版 -> 发送消息
    # (具体打卡逻辑代码略，见完整版内部逻辑)

# --- 定时任务执行 ---
async def run_task(tid):
    t = db_query("SELECT * FROM tasks WHERE task_id=?", (tid,), True)
    if not t: return
    kb = InlineKeyboardBuilder()
    if t[5]: # buttons_json
        for b in json.loads(t[5]): kb.row(types.InlineKeyboardButton(text=b['text'], url=b['url']))
    
    text = t[4] # message_body
    if t[2] == "text": await bot.send_message(t[1], text, parse_mode="MarkdownV2", reply_markup=kb.as_markup())
    elif t[2] == "photo": await bot.send_photo(t[1], t[3], caption=text, parse_mode="MarkdownV2", reply_markup=kb.as_markup())

# --- Web 路由 ---
app = FastAPI()

@app.get("/manage")
async def list_groups(request: Request, sid: str):
    if sid not in auth_sessions: return HTMLResponse("认证失效")
    gs = db_query("SELECT group_id, group_name FROM groups")
    return templates.TemplateResponse("select_group.html", {"request": request, "sid": sid, "groups": gs})

@app.get("/settings")
async def group_settings(request: Request, sid: str, gid: str):
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "g": g})

@app.get("/tasks")
async def group_tasks(request: Request, sid: str, gid: str):
    ts = db_query("SELECT * FROM tasks WHERE group_id=?", (gid,))
    return templates.TemplateResponse("tasks.html", {"request": request, "sid": sid, "gid": gid, "tasks": ts})

@asynccontextmanager
async def lifespan(app_in: FastAPI):
    # 初始化所有截图涉及的表结构
    db_exec('''CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, group_name TEXT, is_checkin_enabled INTEGER DEFAULT 1, checkin_cmd TEXT DEFAULT "打卡", online_emoji TEXT DEFAULT "✅", offline_emoji TEXT DEFAULT "❌", msg_checkin_success TEXT, msg_uncheckin_success TEXT, cmd_delete_time INTEGER DEFAULT 0, is_query_online_enabled INTEGER DEFAULT 1, query_cmd TEXT DEFAULT "今日榨汁", per_page_count INTEGER DEFAULT 25)''')
    db_exec('''CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, expire_date TEXT, PRIMARY KEY(user_id, group_id))''')
    db_exec('''CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, group_id TEXT, content_type TEXT, media_url TEXT, message_body TEXT, buttons_json TEXT, repeat_rule TEXT, delete_after TEXT, remark TEXT, start_time TEXT)''')
    scheduler.start(); asyncio.create_task(dp.start_polling(bot))
    yield
    scheduler.shutdown()

app.router.lifespan_context = lifespan
if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=PORT)
