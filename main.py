import os, asyncio, sqlite3, uuid, logging, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

# --- 基础配置 ---
TOKEN = os.getenv("BOT_TOKEN")
# Railway 提供的域名环境变量
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

# 数据库存储在 Volume 挂载点
DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

# 初始化机器人
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 后台简易 Session
auth_sessions = {}

# --- 数据库初始化 ---
def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # 群组基础配置表
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY, 
            like_emoji TEXT DEFAULT '👍',
            custom_fields TEXT DEFAULT '地区,价格,联系链接',
            list_template TEXT DEFAULT '✅ <b>[{地区Value}]</b> {姓名Value}',
            checkin_template TEXT DEFAULT '✨ {姓名Value} 已上线！')''')
        # 认证老师名单表
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id TEXT, group_id TEXT, name TEXT, data_json TEXT, 
            PRIMARY KEY(user_id, group_id))''')
        # 每日打卡记录表
        conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
            user_id TEXT, group_id TEXT, checkin_date TEXT, 
            PRIMARY KEY(user_id, group_id, checkin_date))''')
        # 定时发送任务表
        conn.execute('''CREATE TABLE IF NOT EXISTS timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, remark TEXT, 
            content TEXT, media_type TEXT, media_url TEXT, interval_hours INTEGER, 
            start_time TEXT, end_time TEXT, is_pin INTEGER DEFAULT 0, 
            last_run TEXT, status INTEGER DEFAULT 1)''')
        conn.commit()

# --- 名单占位符渲染解析器 ---
def power_render(template, data_json, name):
    try: data = json.loads(data_json or "{}")
    except: data = {}
    data.update({"姓名": name, "onlineEmoji": "✅"})
    
    # 转换富文本为 TG 兼容格式
    text = template.replace('</p>', '\n').replace('<p>', '').replace('<br>', '\n')
    
    # 正则替换 {变量名Value}
    def replace_match(match):
        key = match.group(1).replace('Value', '')
        return str(data.get(key, match.group(0)))
    
    final_text = re.sub(r'\{(\w+)\}', replace_match, text)
    # 过滤掉不支持的 HTML 标签防止机器人报错
    return re.sub(r'<(?!b|i|u|code|a|s|strong|em)[^>]+>', '', final_text).strip()

# --- 机器人事件处理 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    sid = str(uuid.uuid4())
    auth_sessions[sid] = {"gid": str(msg.chat.id), "exp": time.time() + 3600}
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="🔐 进入管理后台", url=f"{DOMAIN}/manage?sid={sid}&gid={msg.chat.id}")
    ]])
    await msg.answer(f"👤 您的 UID: <code>{msg.from_user.id}</code>\n点击下方按钮登录后台：", reply_markup=kb)

@dp.message()
async def bot_handler(msg: types.Message):
    if not msg.text: return
    gid, uid, today = str(msg.chat.id), str(msg.from_user.id), datetime.now().strftime('%Y-%m-%d')
    
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()
    
    if not group: return

    # 认证打卡逻辑
    if "打卡" in msg.text and user:
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO checkins VALUES (?,?,?)", (uid, gid, today))
            conn.commit()
        await msg.reply(power_render(group['checkin_template'], user['data_json'], user['name']))
        try: await bot.set_message_reaction(gid, msg.message_id, [types.ReactionTypeEmoji(emoji=group['like_emoji'])])
        except: pass

    # 生成在线名单逻辑
    elif any(k in msg.text for k in ["名单", "在线"]):
        with get_db() as conn:
            rows = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id 
                                AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=?''', (gid, today)).fetchall()
        if not rows: return await msg.answer("📅 今日暂时无人打卡上线")
        res = f"<b>📅 今日在线名单 ({today})</b>\n\n"
        for r in rows: res += power_render(group['list_template'], r['data_json'], r['name']) + "\n"
        await msg.answer(res, disable_web_page_preview=True)

# --- 定时发送异步守护进程 ---
async def timer_worker():
    while True:
        now_dt = datetime.now()
        now_str = now_dt.strftime('%Y-%m-%dT%H:%M')
        with get_db() as conn:
            tasks = conn.execute("SELECT * FROM timers WHERE status=1").fetchall()
            for t in tasks:
                # 检查有效期
                if (t['start_time'] and now_str < t['start_time']) or (t['end_time'] and now_str > t['end_time']): continue
                # 检查运行频率
                run = False
                if not t['last_run']: run = True
                else:
                    last = datetime.strptime(t['last_run'], '%Y-%m-%d %H:%M:%S')
                    if (now_dt - last).total_seconds() >= t['interval_hours'] * 3600: run = True
                
                if run:
                    try:
                        text = power_render(t['content'], "{}", "") # 定时消息暂不解析变量
                        msg = None
                        if t['media_type'] == "图片" and t['media_url']:
                            msg = await bot.send_photo(t['group_id'], t['media_url'], caption=text)
                        elif t['media_type'] == "视频" and t['media_url']:
                            msg = await bot.send_video(t['group_id'], t['media_url'], caption=text)
                        else:
                            msg = await bot.send_message(t['group_id'], text)
                        
                        if t['is_pin'] and msg: await bot.pin_chat_message(t['group_id'], msg.message_id)
                        conn.execute("UPDATE timers SET last_run=? WHERE id=?", (now_dt.strftime('%Y-%m-%d %H:%M:%S'), t['id']))
                        conn.commit()
                    except: pass
        await asyncio.sleep(60)

# --- Web 接口逻辑 ---
@app.get("/manage", response_class=HTMLResponse)
async def admin_page(request: Request, sid: str, gid: str):
    if sid not in auth_sessions: return "验证已过期，请在群组重新发送 /start"
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        if not group: 
            conn.execute("INSERT INTO groups (group_id) VALUES (?)", (gid,))
            conn.commit()
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        timers = conn.execute("SELECT * FROM timers WHERE group_id=?", (gid,)).fetchall()
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
        if action == "del": conn.execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
        else: conn.execute("INSERT OR REPLACE INTO verified_users VALUES (?,?,?,?)", (user_id, gid, name, data))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/timer")
async def api_timer(sid:str=Form(...), gid:str=Form(...), action:str=Form(...), tid:int=Form(None), remark:str=Form(None), content:str=Form(None), m_type:str=Form(None), m_url:str=Form(None), hours:int=Form(1), start:str=Form(None), end:str=Form(None), is_pin:int=Form(0)):
    with get_db() as conn:
        if action == "add":
            conn.execute("INSERT INTO timers (group_id, remark, content, media_type, media_url, interval_hours, start_time, end_time, is_pin) VALUES (?,?,?,?,?,?,?,?,?)", (gid, remark, content, m_type, m_url, hours, start, end, is_pin))
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
