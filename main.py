import os, asyncio, sqlite3, uuid, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

# --- 基础配置 ---
TOKEN = os.getenv("BOT_TOKEN")
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")
auth_sessions = {}

# --- 数据库初始化 (含结构自动修复) ---
def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # 结构检查：防止 columns 不匹配报错
        try:
            conn.execute("SELECT checkin_template FROM groups LIMIT 1")
        except:
            conn.execute("DROP TABLE IF EXISTS groups")
            conn.execute("DROP TABLE IF EXISTS verified_users")
        
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY, like_emoji TEXT DEFAULT '👍',
            custom_fields TEXT DEFAULT '地区,价格,链接',
            list_template TEXT DEFAULT '✅ <b>[{地区Value}]</b> {姓名Value}',
            checkin_template TEXT DEFAULT '✨ {姓名Value} 已上线！')''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id TEXT, group_id TEXT, name TEXT, data_json TEXT, 
            PRIMARY KEY(user_id, group_id))''')
            
        conn.execute('''CREATE TABLE IF NOT EXISTS timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, remark TEXT, 
            content TEXT, media_type TEXT, media_url TEXT, interval_hours INTEGER, 
            start_time TEXT, end_time TEXT, is_pin INTEGER DEFAULT 0, 
            last_run TEXT, status INTEGER DEFAULT 1)''')
        conn.commit()

# --- 工具函数 ---
def power_render(template, data_json, name):
    try: data = json.loads(data_json or "{}")
    except: data = {}
    data.update({"姓名": name})
    text = template.replace('</p>', '\n').replace('<p>', '').replace('<br>', '\n')
    def replace_match(match):
        key = match.group(1).replace('Value', '')
        return str(data.get(key, match.group(0)))
    final_text = re.sub(r'\{(\w+)\}', replace_match, text)
    return re.sub(r'<(?!b|i|u|code|a|s|strong|em)[^>]+>', '', final_text).strip()

# --- 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    sid = str(uuid.uuid4())
    auth_sessions[sid] = {"gid": str(msg.chat.id), "exp": time.time() + 3600}
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="🔐 进入后台管理", url=f"{DOMAIN}/manage?sid={sid}&gid={msg.chat.id}")
    ]])
    await msg.answer(f"🤖 <b>控制台已就绪</b>\n群组ID: <code>{msg.chat.id}</code>\n请点击下方按钮进入：", reply_markup=kb)

# --- FastAPI 路由 ---
@app.get("/manage", response_class=HTMLResponse)
async def admin_page(request: Request, sid: str, gid: str):
    if sid not in auth_sessions: return "验证过期，请在 TG 重新发送 /start"
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        if not group:
            conn.execute("INSERT INTO groups (group_id) VALUES (?)", (gid,))
            conn.commit()
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        timers = [dict(r) for r in conn.execute("SELECT * FROM timers WHERE group_id=?", (gid,)).fetchall()]
        users = conn.execute("SELECT * FROM verified_users WHERE group_id=?", (gid,)).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "timers": timers, "users": users})

@app.post("/api/save")
async def api_save(sid:str=Form(...), gid:str=Form(...), list_t:str=Form(...), check_t:str=Form(...), fields:str=Form(...), emoji:str=Form(...)):
    with get_db() as conn:
        conn.execute("UPDATE groups SET list_template=?, checkin_template=?, custom_fields=?, like_emoji=? WHERE group_id=?", (list_t, check_t, fields, emoji, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/user")
async def api_user(sid:str=Form(...), gid:str=Form(...), user_id:str=Form(...), name:str=Form(...), data:str=Form(...), action:str=Form(...)):
    with get_db() as conn:
        if action == "del":
            conn.execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
        else:
            conn.execute("INSERT OR REPLACE INTO verified_users (user_id, group_id, name, data_json) VALUES (?,?,?,?)", (user_id, gid, name, data))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/timer")
async def api_timer(sid:str=Form(...), gid:str=Form(...), action:str=Form(...), tid:int=Form(None), remark:str=Form(None), content:str=Form(None), m_type:str=Form(None), m_url:str=Form(None), hours:int=Form(1), start:str=Form(None), end:str=Form(None), is_pin:int=Form(0)):
    with get_db() as conn:
        if action == "add":
            conn.execute("INSERT INTO timers (group_id, remark, content, media_type, media_url, interval_hours, start_time, end_time, is_pin) VALUES (?,?,?,?,?,?,?,?,?)", (gid, remark, content, m_type, m_url, hours, start, end, is_pin))
        elif action == "edit":
            conn.execute("UPDATE timers SET remark=?, content=?, media_type=?, media_url=?, interval_hours=?, start_time=?, end_time=?, is_pin=? WHERE id=?", (remark, content, m_type, m_url, hours, start, end, is_pin, tid))
        elif action == "del":
            conn.execute("DELETE FROM timers WHERE id=?", (tid,))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
