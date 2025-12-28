import os, asyncio, sqlite3, uuid, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ChatPermissions

# --- 1. 配置加载 ---
TOKEN = os.getenv("TOKEN", "您的默认TOKEN")
# 获取授权管理员列表
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(',')
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

auth_sessions = {}

# --- 2. 数据库初始化 ---
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
            expire_date TEXT, PRIMARY KEY(user_id, group_id))''')
            
        conn.execute('''CREATE TABLE IF NOT EXISTS timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, remark TEXT, 
            content TEXT, media_type TEXT, media_url TEXT, interval_hours INTEGER, 
            start_time TEXT, end_time TEXT, delete_last INTEGER DEFAULT 0, 
            last_msg_id INTEGER, last_run TEXT, status INTEGER DEFAULT 1)''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
            user_id TEXT, group_id TEXT, checkin_date TEXT, 
            PRIMARY KEY(user_id, group_id, checkin_date))''')

        # 修复列缺失
        try: conn.execute("ALTER TABLE verified_users ADD COLUMN expire_date TEXT")
        except: pass
        for col in ["start_time", "end_time", "delete_last", "last_msg_id"]:
            try: conn.execute(f"ALTER TABLE timers ADD COLUMN {col} TEXT")
            except: pass
        conn.commit()

# --- 3. 渲染引擎 ---
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

# --- 4. 机器人指令逻辑 ---

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    """
    只有在 ADMIN_IDS 里的用户才能触发
    """
    user_id = str(msg.from_user.id)
    if user_id not in ADMIN_IDS:
        return # 机器人直接无视非管理员的指令

    if msg.chat.type in ["group", "supergroup"]:
        # 生成唯一登录 Session
        sid = str(uuid.uuid4())
        auth_sessions[sid] = {"gid": str(msg.chat.id), "exp": time.time() + 7200}
        
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="📱 点击进入手机后台", url=f"{DOMAIN}/manage?sid={sid}&gid={msg.chat.id}")
        ]])
        
        try:
            # 必须通过私聊发送，防止链接泄露
            await bot.send_message(msg.from_user.id, f"🔑 <b>认证成功 (管理员: {user_id})</b>\n当前群组: {msg.chat.title}\n该链接有效期 2 小时。", reply_markup=kb)
            await msg.reply("🔐 权限验证通过，后台链接已私聊发给您。")
        except:
            await msg.reply("❌ 请先私聊机器人发送 /start，否则我无法给您发链接。")

@dp.message()
async def bot_handler(msg: types.Message):
    if not msg.text: return
    gid, uid, today = str(msg.chat.id), str(msg.from_user.id), datetime.now().strftime('%Y-%m-%d')
    text = msg.text.strip()

    # 打卡与名单 (对普通用户开放)
    if text == "打卡":
        with get_db() as conn:
            user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        
        if not user: return
        if user['expire_date'] and today > user['expire_date']:
            try: await bot.restrict_chat_member(gid, int(uid), permissions=ChatPermissions(can_send_messages=False))
            except: pass
            return

        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO checkins VALUES (?,?,?)", (uid, gid, today))
            conn.commit()
        
        await msg.reply(power_render(group['checkin_template'], user['data_json'], user['name']))
        try: await bot.set_message_reaction(gid, msg.message_id, [types.ReactionTypeEmoji(emoji=group['like_emoji'])])
        except: pass

    elif text in ["名单", "在线", "今日榨汁"]:
        with get_db() as conn:
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
            rows = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id 
                                AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=?''', (gid, today)).fetchall()
        if not rows: return await msg.answer("📅 今日暂无打卡数据。")
        res = f"<b>🍹 今日在线名单 ({today})</b>\n\n"
        for r in rows: res += power_render(group['list_template'], r['data_json'], r['name']) + "\n"
        await msg.answer(res, disable_web_page_preview=True)

# --- 5. 定时任务 ---
async def timer_worker():
    while True:
        now_ts = time.time()
        now_s = datetime.now().strftime('%H:%M')
        with get_db() as conn:
            tasks = conn.execute("SELECT * FROM timers WHERE status=1").fetchall()
            for t in tasks:
                # 检查时间段 (例如 09:00 - 23:00)
                if t['start_time'] and t['end_time']:
                    if not (t['start_time'] <= now_s <= t['end_time']): continue
                
                last_run = float(t['last_run'] or 0)
                if now_ts - last_run >= int(t['interval_hours']) * 3600:
                    try:
                        if int(t['delete_last'] or 0) == 1 and t['last_msg_id']:
                            try: await bot.delete_message(t['group_id'], t['last_msg_id'])
                            except: pass
                        m = await bot.send_message(t['group_id'], power_render(t['content'], "{}", ""))
                        conn.execute("UPDATE timers SET last_run=?, last_msg_id=? WHERE id=?", (now_ts, m.message_id, t['id']))
                        conn.commit()
                    except: pass
        await asyncio.sleep(60)

# --- 6. Web 后台接口 ---
@app.get("/manage", response_class=HTMLResponse)
async def admin_page(request: Request, sid: str, gid: str):
    if sid not in auth_sessions or auth_sessions[sid]["gid"] != gid:
        return "🚫 只有指定的机器人管理员可以访问"
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        timers = [dict(r) for r in conn.execute("SELECT * FROM timers WHERE group_id=?", (gid,)).fetchall()]
        users = [dict(r) for r in conn.execute("SELECT * FROM verified_users WHERE group_id=?", (gid,)).fetchall()]
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "timers": timers, "users": users, "today": datetime.now().strftime('%Y-%m-%d')})

# (API 保存逻辑同前，不再赘述，保持完整)
@app.post("/api/save")
async def api_save(sid:str=Form(...), gid:str=Form(...), list_t:str=Form(...), check_t:str=Form(...), fields:str=Form(...), emoji:str=Form(...)):
    with get_db() as conn:
        conn.execute("UPDATE groups SET list_template=?, checkin_template=?, custom_fields=?, like_emoji=? WHERE group_id=?", (list_t, check_t, fields, emoji, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/user")
async def api_user(sid:str=Form(...), gid:str=Form(...), user_id:str=Form(...), name:str=Form(...), data:str=Form(...), expire:str=Form(None), action:str=Form(...)):
    with get_db() as conn:
        if action == "del": conn.execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
        else: conn.execute("INSERT OR REPLACE INTO verified_users (user_id, group_id, name, data_json, expire_date) VALUES (?,?,?,?,?)", (user_id, gid, name, data, expire))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/timer")
async def api_timer(sid:str=Form(...), gid:str=Form(...), action:str=Form(...), tid:int=Form(None), remark:str=Form(None), content:str=Form(None), hours:int=Form(1), start:str=Form(None), end:str=Form(None), delete_last:int=Form(0)):
    with get_db() as conn:
        if action == "add": conn.execute("INSERT INTO timers (group_id, remark, content, interval_hours, start_time, end_time, delete_last) VALUES (?,?,?,?,?,?,?)", (gid, remark, content, hours, start, end, delete_last))
        elif action == "edit": conn.execute("UPDATE timers SET remark=?, content=?, interval_hours=?, start_time=?, end_time=?, delete_last=? WHERE id=?", (remark, content, hours, start, end, delete_last, tid))
        elif action == "del": conn.execute("DELETE FROM timers WHERE id=?", (tid,))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(timer_worker())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
