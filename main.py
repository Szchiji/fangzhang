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

# --- 核心渲染函数：支持 {xxxValue} 占位符 ---
def safe_format(template, data):
    render_data = {f"{k}Value": v for k, v in data.items()}
    render_data["onlineEmoji"] = data.get("onlineEmoji", "✅")
    render_data["老师名字Value"] = data.get("name", "")
    
    # 清理原生编辑器可能产生的标签和转义字符
    template = template.replace('&nbsp;', ' ').replace('&amp;', '&').replace('<div>', '').replace('</div>', '\n')
    
    def replace(match):
        key = match.group(1)
        return str(render_data.get(key, ""))
        
    return re.sub(r'\{(\w+)\}', replace, template)

def json_loads_filter(value):
    try: return json.loads(value) if value else {}
    except: return {}

# --- 初始化 ---
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
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY, group_name TEXT, 
            like_emoji TEXT DEFAULT '👍', 
            list_template TEXT DEFAULT '{onlineEmoji} {地区Value} <a href="{联系方式Value}">{老师名字Value}</a>',
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

    # 1. 自动点赞 (解决不点赞问题)
    if user and msg.text not in ["打卡", "今日名单"]:
        try: await msg.react([ReactionTypeEmoji(emoji=group['like_emoji'])])
        except: pass

    # 2. 名单展示 (解决占位符失效)
    if msg.text in ["今日名单", "今日榨汁"]:
        with get_db() as conn:
            users = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id 
                                 AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=? 
                                 ORDER BY v.sort_order DESC''', (gid, today)).fetchall()
        if not users: return await msg.answer("📅 暂时无人在线")
        res = f"<b>📅 {msg.chat.title} 名单</b>\n\n"
        for u in users:
            attr = json.loads(u['data_json']); attr['name'] = u['name']
            res += safe_format(group['list_template'], attr) + "\n"
        await msg.answer(res, parse_mode="HTML")

# --- 后台接口 ---
@app.get("/manage", response_class=HTMLResponse)
async def manage(request: Request, sid: str, gid: str):
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        users = conn.execute("SELECT * FROM verified_users WHERE group_id=?", (gid,)).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "users": users, "now": int(time.time())})

@app.post("/api/save_config")
async def save_config(sid: str=Form(...), gid: str=Form(...), like_emoji: str=Form(...), list_template: str=Form(...), custom_fields: str=Form(...)):
    with get_db() as conn:
        conn.execute("UPDATE groups SET like_emoji=?, list_template=?, custom_fields=? WHERE group_id=?", (like_emoji, list_template, custom_fields, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
