import os, asyncio, sqlite3, uuid, time, json, re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
import uvicorn

# --- 配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"

DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

# --- 实例 ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
auth_sessions = {}

# --- 数据库操作 ---
def db_query(sql, params=(), fetchone=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if fetchone else cursor.fetchall()

def db_execute(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params)
        conn.commit()

def init_db():
    db_execute('''CREATE TABLE IF NOT EXISTS groups (
        group_id TEXT PRIMARY KEY, 
        like_emoji TEXT DEFAULT "👍", 
        custom_fields TEXT DEFAULT "地区,价格,链接", 
        list_template TEXT, 
        checkin_template TEXT)''')
    db_execute('''CREATE TABLE IF NOT EXISTS verified_users (
        user_id TEXT, group_id TEXT, name TEXT, data_json TEXT, 
        expire_date TEXT, PRIMARY KEY(user_id, group_id))''')
    db_execute('''CREATE TABLE IF NOT EXISTS checkins (
        user_id TEXT, group_id TEXT, checkin_date TEXT, 
        PRIMARY KEY(user_id, group_id, checkin_date))''')

# --- 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = str(msg.from_user.id)
    if uid not in ADMIN_IDS:
        return await msg.reply(f"❌ 无权限。ID: {uid}")
    
    sid = str(uuid.uuid4())
    auth_sessions[sid] = {"uid": uid, "exp": time.time() + 7200}
    url = f"{DOMAIN}/manage?sid={sid}&gid={msg.chat.id}"
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="⚙️ 点击进入后台管理", url=url))
    await msg.reply("🔐 管理员验证成功，请点击下方按钮管理当前群组：", reply_markup=builder.as_markup())

@dp.message(F.text == "打卡")
async def handle_checkin(msg: types.Message):
    gid, uid = str(msg.chat.id), str(msg.from_user.id)
    user = db_query("SELECT name, expire_date FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid), True)
    
    if not user:
        return await msg.reply("❌ 您尚未通过验证，请联系管理员。")
    
    name, expire_str = user
    if datetime.now() > datetime.strptime(expire_str, "%Y-%m-%d"):
        return await msg.reply(f"⚠️ 您的验证已过期 (到期日: {expire_str})")

    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db_execute("INSERT INTO checkins VALUES (?, ?, ?)", (uid, gid, today))
        # 统计打卡次数
        count = db_query("SELECT COUNT(*) FROM checkins WHERE user_id=? AND group_id=?", (uid, gid), True)[0]
        await msg.reply(f"✅ <b>{name}</b> 打卡成功！\n📅 日期：{today}\n🔥 累计打卡：{count} 次")
    except sqlite3.IntegrityError:
        await msg.reply("📢 您今天已经打过卡了，明天再来吧！")

# --- Web 路由 ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def get_manage(request: Request, sid: str, gid: str):
    if sid not in auth_sessions or time.time() > auth_sessions[sid]["exp"]:
        return "链接已失效，请在 Telegram 重新发送 /start"
    
    # 获取或初始化群组设置
    group = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    if not group:
        db_execute("INSERT INTO groups (group_id) VALUES (?)", (gid,))
        group = (gid, "👍", "地区,价格,链接", "", "")

    # 获取已验证用户列表
    users = db_query("SELECT user_id, name, expire_date, data_json FROM verified_users WHERE group_id=?", (gid,))
    user_list = []
    for u in users:
        user_list.append({"id": u[0], "name": u[1], "expire": u[2], "data": json.loads(u[3])})

    return templates.TemplateResponse("manage.html", {
        "request": request, "gid": gid, "sid": sid,
        "fields": group[2].split(','),
        "users": user_list,
        "like_emoji": group[1]
    })

@app.post("/add_user")
async def add_user(sid: str = Form(...), gid: str = Form(...), 
                   uid: str = Form(...), name: str = Form(...), 
                   days: int = Form(...), custom_data: str = Form(...)):
    if sid not in auth_sessions: raise HTTPException(403)
    
    expire_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    db_execute("REPLACE INTO verified_users VALUES (?, ?, ?, ?, ?)", 
               (uid, gid, name, custom_data, expire_date))
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

@app.post("/delete_user")
async def delete_user(sid: str = Form(...), gid: str = Form(...), uid: str = Form(...)):
    if sid not in auth_sessions: raise HTTPException(403)
    db_execute("DELETE FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid))
    return RedirectResponse(f"/manage?sid={sid}&gid={gid}", status_code=303)

# --- 生命周期 ---
@asynccontextmanager
async def lifespan(app_in: FastAPI):
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    print(f"🚀 机器人 @{me.username} 完整功能版已就绪")
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    task.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
