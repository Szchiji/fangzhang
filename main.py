import os, asyncio, sqlite3, uuid, logging
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
import uvicorn

# --- 1. 基础配置 ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"
# 确保数据目录存在
os.makedirs("/data", exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 内存验证状态 {sid: {"code": "...", "verified": False}}
auth_states = {}

# --- 2. 数据库管理 ---
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # 群组表
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY, group_name TEXT, 
            page_size INTEGER DEFAULT 20, like_emoji TEXT DEFAULT '👍', 
            list_template TEXT DEFAULT '✅ {area} {name} 频道 胸{chest_size} {price}')''')
        # 老师/用户表
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id INTEGER, group_id INTEGER, name TEXT, area TEXT, 
            price TEXT, chest_size TEXT, sort_order INTEGER DEFAULT 0, 
            PRIMARY KEY(user_id, group_id))''')
        # 打卡记录表
        conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
            user_id INTEGER, group_id INTEGER, checkin_date TEXT, 
            PRIMARY KEY(user_id, group_id, checkin_date))''')

# --- 3. 机器人核心逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    sid = str(uuid.uuid4())
    code = "".join([str(os.urandom(1)[0] % 10) for _ in range(6)])
    auth_states[sid] = {"code": code, "verified": False}
    
    # 生成登录按钮
    kb = InlineKeyboardBuilder()
    kb.button(text="🔐 点击进入管理后台", url=f"{DOMAIN}/login?sid={sid}")
    await msg.answer(f"<b>管理系统验证</b>\n验证码: <code>{code}</code>\n请点击下方按钮，并在网页打开后将验证码发回给我。", reply_markup=kb.as_markup())

@dp.message(F.text.regexp(r'^\d{6}$'))
async def handle_code(msg: types.Message):
    # 处理验证码
    for sid, data in auth_states.items():
        if data["code"] == msg.text:
            data["verified"] = True
            await msg.answer("✅ 验证成功！网页即将跳转...")
            return
    await msg.answer("❌ 验证码无效")

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group(msg: types.Message):
    gid, uid = msg.chat.id, msg.from_user.id
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 自动记录新群组
    with get_db() as conn:
        exist = conn.execute("SELECT 1 FROM groups WHERE group_id=?", (gid,)).fetchone()
        if not exist:
            conn.execute("INSERT INTO groups (group_id, group_name) VALUES (?,?)", (gid, msg.chat.title))
            conn.commit()

    # 处理打卡
    if msg.text == "打卡":
        with get_db() as conn:
            user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()
            if user:
                try:
                    conn.execute("INSERT INTO checkins VALUES (?,?,?)", (uid, gid, today))
                    conn.commit()
                    await msg.reply(f"✅ {user['name']} 打卡成功！")
                except:
                    await msg.reply("ℹ️ 您今天已经打过卡了")
    
    # 处理查询列表
    if msg.text == "今日榨汁" or msg.text == "今日名单":
        await send_list(msg, gid, 1)

async def send_list(msg, gid, page):
    today = datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        # 获取群组设置
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        # 获取今日打卡用户
        users = conn.execute('''
            SELECT v.* FROM verified_users v 
            JOIN checkins c ON v.user_id = c.user_id AND v.group_id = c.group_id
            WHERE v.group_id=? AND c.checkin_date=? 
            ORDER BY v.sort_order DESC
        ''', (gid, today)).fetchall()

    if not users:
        return await msg.answer("📅 今日暂无老师打卡。")
    
    # 生成文本
    text = f"<b>📅 今日开课名单 ({len(users)}人)</b>\n\n"
    for u in users:
        # 使用模板格式化
        try:
            line = group['list_template'].format(name=u['name'], area=u['area'], price=u['price'], chest_size=u['chest_size'])
            text += line + "\n"
        except:
            text += f"✅ {u['name']}\n"
            
    await msg.answer(text)

# --- 4. 网页后端接口 ---
@app.get("/login", response_class=HTMLResponse)
async def web_login(request: Request, sid: str):
    if sid not in auth_states: return HTMLResponse("链接已失效，请重新 /start")
    return templates.TemplateResponse("login.html", {"request": request, "sid": sid, "code": auth_states[sid]["code"]})

@app.get("/check_status/{sid}")
async def check_status(sid: str):
    # 网页轮询接口
    if sid in auth_states and auth_states[sid]["verified"]:
        return {"status": "verified"}
    return {"status": "waiting"}

@app.get("/portal", response_class=HTMLResponse)
async def portal(request: Request, sid: str):
    if not auth_states.get(sid, {}).get("verified"): return RedirectResponse(f"/login?sid={sid}")
    with get_db() as conn:
        groups = conn.execute("SELECT * FROM groups").fetchall()
    return templates.TemplateResponse("portal.html", {"request": request, "sid": sid, "groups": groups})

@app.get("/manage", response_class=HTMLResponse)
async def manage(request: Request, sid: str, gid: int):
    if not auth_states.get(sid, {}).get("verified"): return RedirectResponse(f"/login?sid={sid}")
    with get_db() as conn:
        group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        users = conn.execute("SELECT * FROM verified_users WHERE group_id=? ORDER BY sort_order DESC", (gid,)).fetchall()
    return templates.TemplateResponse("manage.html", {"request": request, "sid": sid, "gid": gid, "group": group, "users": users})

@app.post("/api/save_user")
async def save_user(sid: str = Form(...), gid: int = Form(...), user_id: int = Form(...), name: str = Form(...), area: str = Form(""), price: str = Form(""), chest: str = Form(""), sort: int = Form(0)):
    if not auth_states.get(sid, {}).get("verified"): return JSONResponse({"error": "No Auth"}, 403)
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO verified_users VALUES (?,?,?,?,?,?,?)", (user_id, gid, name, area, price, chest, sort))
        conn.commit()
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.on_event("startup")
async def on_startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
