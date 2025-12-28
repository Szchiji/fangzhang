import os, asyncio, sqlite3, uuid, logging, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ReactionTypeEmoji, InlineKeyboardMarkup, InlineKeyboardButton
import uvicorn

# --- 配置 ---
TOKEN = os.getenv("BOT_TOKEN")
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"
DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)
logging.basicConfig(level=logging.INFO)

auth_sessions = {}

# --- 消息渲染引擎 ---
def power_render(template, data_json, name):
    try: data = json.loads(data_json or "{}")
    except: data = {}
    data.update({"姓名": name, "onlineEmoji": "✅", "老师名字": name})
    
    # 清理编辑器生成的标签
    t = template.replace('<div>', '').replace('</div>', '\n').replace('<br>', '\n').replace('&nbsp;', ' ')
    def repl(m):
        key = m.group(1).replace('Value', '')
        return str(data.get(key, m.group(0)))
    return re.sub(r'\{(\w+)\}', repl, t).strip()

# --- 初始化 ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY, like_emoji TEXT DEFAULT '👍',
            custom_fields TEXT DEFAULT '地区,价格,联系链接',
            list_template TEXT DEFAULT '{onlineEmoji} <b>[{地区Value}]</b> {姓名Value} - {价格Value}',
            checkin_template TEXT DEFAULT '✨ {姓名Value} 已上线！')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id TEXT, group_id TEXT, name TEXT, data_json TEXT, PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
            user_id TEXT, group_id TEXT, checkin_date TEXT, PRIMARY KEY(user_id, group_id, checkin_date))''')
        conn.commit()

# --- 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    sid = str(uuid.uuid4())
    auth_sessions[sid] = {"gid": str(msg.chat.id), "exp": time.time() + 3600}
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔐 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}&gid={msg.chat.id}")]])
    await msg.answer(f"👤 您的 UID: <code>{msg.from_user.id}</code>\n点击下方链接登录后台：", reply_markup=kb)

@dp.message()
async def bot_handler(msg: types.Message):
    if not msg.text: return
    gid, uid, today = str(msg.chat.id), str(msg.from_user.id), datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()

    if "打卡" in msg.text and user:
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO checkins VALUES (?,?,?)", (uid, gid, today)); conn.commit()
        data = json.loads(user['data_json'] or "{}")
        kb = None
        if "联系链接" in data and str(data["联系链接"]).startswith("http"):
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"💬 联系 {user['name']}", url=data['联系链接'])]])
        await msg.reply(power_render(group['checkin_template'], user['data_json'], user['name']), reply_markup=kb)
    elif any(k in msg.text for k in ["名单", "今日", "在线"]) and group:
        with get_db() as conn:
            rows = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id 
                                AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=?''', (gid, today)).fetchall()
        if not rows: return await msg.answer("📅 暂时无人上线")
        res = f"<b>📅 {msg.chat.title or '群聊'} 名单</b>\n\n"
        for r in rows: res += power_render(group['list_template'], r['data_json'], r['name']) + "\n"
        await msg.answer(res, disable_web_page_preview=True)

# --- Web 接口 (核心修复点) ---
@app.get("/manage", response_class=HTMLResponse)
async def admin_page(request: Request, sid: str, gid: str):
    if sid not in auth_sessions or auth_sessions[sid]['exp'] < time.time():
        return "登录失效，请在群聊重新发送 /start"
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        if not group: # 修复 500 报错的关键
            conn.execute("INSERT INTO groups (group_id) VALUES (?)", (gid,))
            conn.commit()
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        users = conn.execute("SELECT * FROM verified_users WHERE group_id=?", (gid,)).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "users": users})

@app.post("/api/save")
async def api_save(sid:str=Form(...), gid:str=Form(...), list_t:str=Form(...), check_t:str=Form(...), fields:str=Form(...), emoji:str=Form(...)):
    with get_db() as conn:
        conn.execute("UPDATE groups SET list_template=?, checkin_template=?, custom_fields=?, like_emoji=? WHERE group_id=?", (list_t, check_t, fields, emoji, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/user")
async def api_user(sid:str=Form(...), gid:str=Form(...), user_id:str=Form(...), name:str=Form(...), data:str=Form(...), action:str=Form(...)):
    with get_db() as conn:
        if action == "del": conn.execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
        else: conn.execute("INSERT OR REPLACE INTO verified_users VALUES (?,?,?,?)", (user_id, gid, name, data))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
