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

# --- 核心：带 Value 后缀的占位符渲染逻辑 ---
def safe_format(template, data):
    """支持 {字段名Value} 格式的替换"""
    # 构造带 Value 后缀的字典
    render_data = {f"{k}Value": v for k, v in data.items()}
    render_data["onlineEmoji"] = data.get("onlineEmoji", "✅")
    render_data["老师名字Value"] = data.get("name", "")
    
    # 清理富文本编辑器产生的 HTML 转义字符
    template = template.replace('&nbsp;', ' ')
    
    def replace(match):
        key = match.group(1)
        # 如果找不到变量，返回空字符串，不显示大括号本身
        return str(render_data.get(key, ""))
        
    return re.sub(r'\{(\w+)\}', replace, template)

def json_loads_filter(value):
    try: return json.loads(value) if value else {}
    except: return {}

# --- 初始化组件 ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 关键修复：必须在渲染前注册过滤器解决 500 报错
templates.env.filters["json_loads"] = json_loads_filter

auth_states = {}

# --- 数据库操作 ---
def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # ID 统一使用 TEXT 解决大群 ID 匹配导致的“不点赞”问题
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY, group_name TEXT, 
            like_emoji TEXT DEFAULT '👍', 
            list_template TEXT DEFAULT '{onlineEmoji} {地区Value} {老师名字Value} {价位Value}',
            custom_fields TEXT DEFAULT '地区,价位,胸围,联系方式')''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id TEXT, group_id TEXT, name TEXT, 
            sort_order INTEGER DEFAULT 0, expire_at INTEGER DEFAULT 0,
            data_json TEXT DEFAULT "{}", PRIMARY KEY(user_id, group_id))''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
            user_id TEXT, group_id TEXT, checkin_date TEXT, 
            PRIMARY KEY(user_id, group_id, checkin_date))''')
        conn.commit()

# --- 机器人核心逻辑 ---
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

    # 1. 自动点赞 (排除特定指令)
    if user and msg.text not in ["打卡", "今日名单", "今日榨汁"]:
        if user['expire_at'] == 0 or user['expire_at'] > time.time():
            try:
                await msg.react([ReactionTypeEmoji(emoji=group['like_emoji'] or "👍")])
            except: pass

    # 2. 老师打卡
    if msg.text == "打卡" and user:
        with get_db() as conn:
            exist = conn.execute("SELECT 1 FROM checkins WHERE user_id=? AND group_id=? AND checkin_date=?", (uid, gid, today)).fetchone()
            if not exist:
                conn.execute("INSERT INTO checkins VALUES (?,?,?)", (uid, gid, today))
                conn.commit()
            await msg.reply(f"✅ 【{user['name']}】上线成功！")

    # 3. 名单展示 (支持 HTML 链接与占位符)
    if msg.text in ["今日名单", "今日榨汁"]:
        with get_db() as conn:
            users = conn.execute('''SELECT v.* FROM verified_users v JOIN checkins c ON v.user_id = c.user_id 
                                 AND v.group_id = c.group_id WHERE v.group_id=? AND c.checkin_date=? 
                                 ORDER BY v.sort_order DESC''', (gid, today)).fetchall()
        if not users: return await msg.answer("📅 暂时没有在线老师。")
        
        res = f"<b>📅 {msg.chat.title} 在线名单</b>\n\n"
        for u in users:
            attr = json.loads(u['data_json'])
            attr.update({"name": u['name']})
            # 通过 safe_format 渲染带链接的 HTML 模板
            res += safe_format(group['list_template'], attr) + "\n"
        
        await msg.answer(res, parse_mode="HTML")

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    sid = str(uuid.uuid4())
    auth_states[sid] = {"code": "".join([str(os.urandom(1)[0] % 10) for _ in range(6)]), "verified": False}
    kb = InlineKeyboardBuilder().button(text="🔐 后台管理", url=f"{DOMAIN}/login?sid={sid}").as_markup()
    await msg.answer(f"验证码: <code>{auth_states[sid]['code']}</code>", reply_markup=kb)

@dp.message(F.text.regexp(r'^\d{6}$'))
async def handle_code(msg: types.Message):
    for sid, data in auth_states.items():
        if data["code"] == msg.text:
            data["verified"] = True
            return await msg.answer("✅ 验证通过，请在浏览器操作。")

# --- Web 路由 ---
@app.get("/login", response_class=HTMLResponse)
async def login(request: Request, sid: str):
    return templates.TemplateResponse("login.html", {"request": request, "sid": sid, "code": auth_states.get(sid, {}).get("code")})

@app.get("/check_status/{sid}")
async def check_status(sid: str):
    return {"status": "verified" if auth_states.get(sid, {}).get("verified") else "waiting"}

@app.get("/portal", response_class=HTMLResponse)
async def portal(request: Request, sid: str):
    if not auth_states.get(sid,{}).get("verified"): return RedirectResponse(f"/login?sid={sid}")
    with get_db() as conn: groups = conn.execute("SELECT * FROM groups").fetchall()
    return templates.TemplateResponse("portal.html", {"request": request, "sid": sid, "groups": groups})

@app.get("/manage", response_class=HTMLResponse)
async def manage(request: Request, sid: str, gid: str):
    if not auth_states.get(sid,{}).get("verified"): return RedirectResponse(f"/login?sid={sid}")
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        users = conn.execute("SELECT * FROM verified_users WHERE group_id=? ORDER BY sort_order DESC", (gid,)).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "users": users, "now": int(time.time())})

@app.post("/api/save_user")
async def save_user(request: Request):
    form = await request.form()
    sid, gid, uid = form.get("sid"), form.get("gid"), form.get("user_id")
    name, days, sort = form.get("name"), int(form.get("days", 0)), int(form.get("sort", 0))
    custom = {k: v for k, v in form.items() if k not in ['sid', 'gid', 'user_id', 'name', 'days', 'sort']}
    expire_at = int(time.time() + days*86400) if days > 0 else 0
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO verified_users VALUES (?,?,?,?,?,?)", (uid, gid, name, sort, expire_at, json.dumps(custom, ensure_ascii=False)))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/save_config")
async def save_config(sid: str=Form(...), gid: str=Form(...), like_emoji: str=Form(...), list_template: str=Form(...), custom_fields: str=Form(...)):
    with get_db() as conn:
        conn.execute("UPDATE groups SET like_emoji=?, list_template=?, custom_fields=? WHERE group_id=?", (like_emoji, list_template, custom_fields, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/api/delete_user")
async def delete_user(sid: str=Form(...), gid: str=Form(...), user_id: str=Form(...)):
    with get_db() as conn:
        conn.execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (user_id, gid))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
