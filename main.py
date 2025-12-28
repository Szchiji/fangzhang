import os, asyncio, sqlite3, uuid, logging, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
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

# --- 数据库初始化 ---
def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
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

# --- 渲染逻辑 ---
def power_render(template, data_json, name):
    try: data = json.loads(data_json or "{}")
    except: data = {}
    data.update({"姓名": name, "onlineEmoji": "✅"})
    text = template.replace('</p>', '\n').replace('<p>', '').replace('<br>', '\n')
    def replace_match(match):
        key = match.group(1).replace('Value', '')
        return str(data.get(key, match.group(0)))
    final_text = re.sub(r'\{(\w+)\}', replace_match, text)
    return re.sub(r'<(?!b|i|u|code|a|s|strong|em)[^>]+>', '', final_text).strip()

# --- 机器人事件 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    sid = str(uuid.uuid4())
    auth_sessions[sid] = {"gid": str(msg.chat.id), "exp": time.time() + 3600}
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="🔐 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}&gid={msg.chat.id}")
    ]])
    await msg.answer(f"👤 UID: <code>{msg.from_user.id}</code>\n点击登录后台：", reply_markup=kb)

@dp.message()
async def bot_handler(msg: types.Message):
    if not msg.text: return
    gid, uid, today = str(msg.chat.id), str(msg.from_user.id), datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()
    if not group: return
    if "打卡" in msg.text and user:
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO checkins (user_id, group_id, checkin_date) VALUES (?,?,?)", (uid, gid, today))
            conn.commit()
        await msg.reply(power_render(group['checkin_template'], user['data_json'], user['name']))
        try: await bot.set_message_reaction(gid, msg.message_id, [types.ReactionTypeEmoji(emoji=group['like_emoji'])])
        except: pass
    elif any(k in msg.text for k in ["名单", "在线"]):
        with get_db() as conn:
            rows = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id 
                                AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=?''', (gid, today)).fetchall()
        if not rows: return await msg.answer("📅 今日无人上线")
        res = f"<b>📅 在线名单</b>\n\n"
        for r in rows: res += power_render(group['list_template'], r['data_json'], r['name']) + "\n"
        await msg.answer(res, disable_web_page_preview=True)

async def timer_worker():
    while True:
        now_dt = datetime.now()
        now_str = now_dt.strftime('%Y-%m-%dT%H:%M')
        with get_db() as conn:
            tasks = conn.execute("SELECT * FROM timers WHERE status=1").fetchall()
            for t in tasks:
                if (t['start_time'] and now_str < t['start_time']) or (t['end_time'] and now_str > t['end_time']): continue
                run = False
                if not t['last_run']: run = True
                else:
                    last = datetime.strptime(t['last_run'], '%Y-%m-%d %H:%M:%S')
                    if (now_dt - last).total_seconds() >= t['interval_hours'] * 3600: run = True
                if run:
                    try:
                        text = power_render(t['content'], "{}", "")
                        if t['media_type'] == "图片" and t['media_url']: m = await bot.send_photo(t['group_id'], t['media_url'], caption=text)
                        elif t['media_type'] == "视频" and t['media_url']: m = await bot.send_video(t['group_id'], t['media_url'], caption=text)
                        else: m = await bot.send_message(t['group_id'], text)
                        if t['is_pin'] and m: await bot.pin_chat_message(t['group_id'], m.message_id)
                        conn.execute("UPDATE timers SET last_run=? WHERE id=?", (now_dt.strftime('%Y-%m-%d %H:%M:%S'), t['id']))
                        conn.commit()
                    except: pass
        await asyncio.sleep(60)

# --- FastAPI 接口 ---
@app.get("/manage", response_class=HTMLResponse)
async def admin_page(request: Request, sid: str, gid: str):
    if sid not in auth_sessions: return "Session Expired"
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        if not group: 
            conn.execute("INSERT INTO groups (group_id) VALUES (?)", (gid,))
            conn.commit()
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        timers = [dict(row) for row in conn.execute("SELECT * FROM timers WHERE group_id=?", (gid,)).fetchall()]
        users = conn.execute("SELECT * FROM verified_users WHERE group_id=?", (gid,)).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "timers": timers, "users": users})

@app.post("/api/save")
async def api_save(sid:str=Form(...), gid:str=Form(...), list_t:str=Form(...), check_t:str=Form(...), fields:str=Form(...), emoji:str=Form(...)):
    try:
        with get_db() as conn:
            conn.execute("UPDATE groups SET list_template=?, checkin_template=?, custom_fields=?, like_emoji=? WHERE group_id=?", (list_t, check_t, fields, emoji, gid))
            conn.commit()
        return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/user")
async def api_user(sid:str=Form(...), gid:str=Form(...), user_id:str=Form(...), name:str=Form(...), data:str=Form(...), action:str=Form(...)):
    with get_db() as conn:
        if action == "del": conn.execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
        else: conn.execute("INSERT OR REPLACE INTO verified_users VALUES (?,?,?,?)", (user_id, gid, name, data))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/timer")
async def api_timer(sid:str=Form(...), gid:str=Form(...), action:str=Form(...), tid:int=Form(None), remark:str=Form(None), content:str=Form(None), m_type:str=Form(None), m_url:str=Form(None), hours:int=Form(1), start:str=Form(None), end:str=Form(None), is_pin:int=Form(0)):
    with get_db() as conn:
        if action == "add": conn.execute("INSERT INTO timers (group_id, remark, content, media_type, media_url, interval_hours, start_time, end_time, is_pin) VALUES (?,?,?,?,?,?,?,?,?)", (gid, remark, content, m_type, m_url, hours, start, end, is_pin))
        elif action == "edit": conn.execute("UPDATE timers SET remark=?, content=?, media_type=?, media_url=?, interval_hours=?, start_time=?, end_time=?, is_pin=? WHERE id=?", (remark, content, m_type, m_url, hours, start, end, is_pin, tid))
        elif action == "del": conn.execute("DELETE FROM timers WHERE id=?", (tid,))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(timer_worker())
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
