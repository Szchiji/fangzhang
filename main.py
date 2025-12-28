import os, asyncio, sqlite3, uuid, logging, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ReactionTypeEmoji
from aiogram.utils.keyboard import InlineKeyboardBuilder
import uvicorn

# --- 基础配置 ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)
logging.basicConfig(level=logging.INFO)

# --- 核心渲染引擎：处理占位符与 HTML 清理 ---
def safe_format(template, data):
    # 构建带 Value 后缀的字典
    render_data = {f"{k}Value": v for k, v in data.items()}
    render_data["onlineEmoji"] = data.get("onlineEmoji", "✅")
    render_data["老师名字Value"] = data.get("name", "")
    
    # 清理原生编辑器 HTML 标签，确保 Telegram 兼容
    # 将 <div> 替换为换行，清理多余转义
    template = template.replace('&nbsp;', ' ').replace('&amp;', '&')
    template = template.replace('<div>', '').replace('</div>', '\n').replace('<br>', '\n').replace('<p>', '').replace('</p>', '\n')
    
    def replace(match):
        key = match.group(1)
        return str(render_data.get(key, ""))
        
    return re.sub(r'\{(\w+)\}', replace, template).strip()

def json_loads_filter(value):
    try: return json.loads(value) if value else {}
    except: return {}

# --- 初始化组件 ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")
templates.env.filters["json_loads"] = json_loads_filter

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # 核心数据库表结构
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY, group_name TEXT, 
            like_emoji TEXT DEFAULT '👍', 
            list_template TEXT DEFAULT '{onlineEmoji} {地区Value} <a href="{联系方式Value}">{老师名字Value}</a>',
            checkin_template TEXT DEFAULT '✅ 【{老师名字Value}】上线成功！',
            custom_fields TEXT DEFAULT '地区,价位,联系方式')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id TEXT, group_id TEXT, name TEXT, 
            sort_order INTEGER DEFAULT 0, expire_at INTEGER DEFAULT 0,
            data_json TEXT DEFAULT "{}", PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
            user_id TEXT, group_id TEXT, checkin_date TEXT, 
            PRIMARY KEY(user_id, group_id, checkin_date))''')
        conn.commit()

# --- 机器人逻辑 ---
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_handler(msg: types.Message):
    gid, uid, today = str(msg.chat.id), str(msg.from_user.id), datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        if not group:
            conn.execute("INSERT INTO groups (group_id, group_name) VALUES (?,?)", (gid, msg.chat.title))
            conn.commit()
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()

    # 1. 自动点赞
    if user and msg.text not in ["打卡", "今日名单", "今日榨汁"]:
        if user['expire_at'] == 0 or user['expire_at'] > time.time():
            try: await msg.react([ReactionTypeEmoji(emoji=group['like_emoji'] or "👍")])
            except: pass

    # 2. 老师打卡 (自定义回复模板)
    if msg.text == "打卡" and user:
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO checkins VALUES (?,?,?)", (uid, gid, today))
            conn.commit()
        attr = json.loads(user['data_json']); attr['name'] = user['name']
        welcome_text = safe_format(group['checkin_template'], attr)
        await msg.reply(welcome_text)

    # 3. 今日名单展示 (支持超链接跳转)
    if msg.text in ["今日名单", "今日榨汁"]:
        with get_db() as conn:
            users = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id 
                                 AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=? 
                                 ORDER BY v.sort_order DESC''', (gid, today)).fetchall()
        if not users: return await msg.answer("📅 暂时没有在线老师。")
        
        res = f"<b>📅 {msg.chat.title} 在线名单</b>\n\n"
        for u in users:
            attr = json.loads(u['data_json']); attr['name'] = u['name']
            res += safe_format(group['list_template'], attr) + "\n"
        await msg.answer(res, disable_web_page_preview=True)

# --- 后台接口 (仅保留核心逻辑) ---
@app.get("/manage", response_class=HTMLResponse)
async def manage(request: Request, sid: str, gid: str):
    # 这里应有鉴权逻辑
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        users = conn.execute("SELECT * FROM verified_users WHERE group_id=? ORDER BY sort_order DESC", (gid,)).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "users": users, "now": int(time.time())})

@app.post("/api/save_config")
async def save_config(sid: str=Form(...), gid: str=Form(...), like_emoji: str=Form(...), list_template: str=Form(...), checkin_template: str=Form(...), custom_fields: str=Form(...)):
    with get_db() as conn:
        conn.execute("UPDATE groups SET like_emoji=?, list_template=?, checkin_template=?, custom_fields=? WHERE group_id=?", (like_emoji, list_template, checkin_template, custom_fields, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
