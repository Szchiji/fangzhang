import os, asyncio, sqlite3, uuid, time, json, re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
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

# --- 工具函数：MarkdownV2 转换 ---
def html_to_mdv2(html: str) -> str:
    if not html: return ""
    def escape(t): return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', t)
    tags = [(r'<b>(.*?)</b>', r'*\1*'), (r'<i>(.*?)</i>', r'_\1_'), (r'<u>(.*?)</u>', r'__\1__'),
            (r'<s>(.*?)</s>', r'~\1~'), (r'<a href="(.*?)">(.*?)</a>', r'[\2](\1)'), (r'<br>', r'\n'), (r'<p>(.*?)</p>', r'\1\n')]
    res = html
    for t_re, repl in tags: res = re.sub(t_re, repl, res, flags=re.IGNORECASE)
    res = re.sub(r'<[^>]+>', '', res)
    return escape(res).replace(r'\{', '{').replace(r'\}', '}')

def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

# --- 机器人逻辑 ---
@dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_bot_join(event: types.ChatMemberUpdated):
    db_exec("INSERT OR REPLACE INTO groups (group_id, group_name) VALUES (?, ?)", (str(event.chat.id), event.chat.title))

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = {"uid": msg.from_user.id}
    kb = InlineKeyboardBuilder().button(text="🏢 进入管理中枢", url=f"{DOMAIN}/manage?sid={sid}").as_markup()
    await msg.reply("💎 管理员验证成功，请点击下方按钮进入后台：", reply_markup=kb)

@dp.message(F.text == "打卡")
async def handle_checkin(msg: types.Message):
    gid, uid = str(msg.chat.id), str(msg.from_user.id)
    # 检查认证
    user = db_query("SELECT name FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid), True)
    if not user:
        return await msg.reply("⚠️ 您尚未获得本群认证，请联系管理员。")
    
    g = db_query("SELECT msg_checkin_success, online_emoji FROM groups WHERE group_id=?", (gid,), True)
    content = g[0] if g and g[0] else "<b>{name}</b> 打卡成功 {emoji}"
    final_msg = html_to_mdv2(content.replace("{name}", user[0]).replace("{emoji}", g[1] or "✅"))
    await msg.reply(final_msg, parse_mode="MarkdownV2")

# --- Web 路由 ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def list_groups(request: Request, sid: str):
    if sid not in auth_sessions: return "认证过期，请在 Telegram 重新发送 /start"
    gs = db_query("SELECT group_id, group_name FROM groups")
    return templates.TemplateResponse("select_group.html", {"request": request, "sid": sid, "groups": gs})

@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request, sid: str, gid: str):
    g = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "g": g})

@app.post("/update_settings")
async def update_settings(sid: str = Form(...), gid: str = Form(...), checkin_cmd: str = Form(...), msg_checkin_success: str = Form(...)):
    db_exec("UPDATE groups SET checkin_cmd=?, msg_checkin_success=? WHERE group_id=?", (checkin_cmd, msg_checkin_success, gid))
    return RedirectResponse(f"/settings?sid={sid}&gid={gid}", status_code=303)

@asynccontextmanager
async def lifespan(app_in: FastAPI):
    db_exec('''CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, group_name TEXT, is_checkin_enabled INTEGER DEFAULT 1, checkin_cmd TEXT DEFAULT "打卡", online_emoji TEXT DEFAULT "✅", offline_emoji TEXT DEFAULT "❌", msg_checkin_success TEXT, msg_uncheckin_success TEXT)''')
    db_exec('''CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, expire_date TEXT, PRIMARY KEY(user_id, group_id))''')
    db_exec('''CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, group_id TEXT, content_type TEXT, media_url TEXT, message_body TEXT, buttons_json TEXT, repeat_rule TEXT, delete_after TEXT, start_time TEXT)''')
    scheduler.start()
    asyncio.create_task(dp.start_polling(bot))
    yield
    scheduler.shutdown()

app.router.lifespan_context = lifespan
if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=PORT)
