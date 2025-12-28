import os, asyncio, sqlite3, uuid, json, re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn

# --- 核心配置 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("RAILWAY_STATIC_URL", "localhost:8080").rstrip('/')
if not DOMAIN.startswith('http'): DOMAIN = f"https://{DOMAIN}"
DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

# 初始化 Bot 和 调度器
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()
auth_sessions = {}

# --- 数据库辅助函数 ---
def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone() if one else cursor.fetchall()

# --- 自动清理逻辑 ---
async def auto_cleanup_job():
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msgs = db_query("SELECT chat_id, message_id FROM sent_logs WHERE delete_at <= ? AND status='active'", (now,))
    for cid, mid in msgs:
        try:
            await bot.delete_message(chat_id=cid, message_id=int(mid))
            db_exec("UPDATE sent_logs SET status='deleted' WHERE message_id=?", (mid,))
        except: pass

# --- 机器人事件处理 ---

# 1. 转发获取 UID (仅管理员)
@dp.message(F.forward_from)
async def handle_forward(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    u = msg.forward_from
    res = [
        f"👤 <b>用户信息已解析</b>",
        f"数字 UID: <code>{u.id}</code>",
        f"名: {u.first_name}",
        f"用户名: @{u.username or '未设置'}"
    ]
    await msg.reply("\n".join(res))

# 2. 打卡逻辑 (静默拦截非认证用户)
@dp.message(F.text == "打卡")
async def handle_checkin(msg: types.Message):
    gid, uid = str(msg.chat.id), str(msg.from_user.id)
    user = db_query("SELECT name FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid), True)
    if user:
        g = db_query("SELECT on_emoji, msg_on FROM groups WHERE group_id=?", (gid,), True)
        emoji = g[0] if g and g[0] else "✅"
        await msg.reply(f"{emoji} {user[0]} 打卡成功！")
    # 非认证用户不回复，保持静默

# 3. 入口指令
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if str(msg.from_user.id) not in ADMIN_IDS: return
    sid = str(uuid.uuid4())
    auth_sessions[sid] = msg.from_user.id
    kb = InlineKeyboardBuilder().button(text="🏢 进入管理中枢", url=f"{DOMAIN}/manage?sid={sid}").as_markup()
    await msg.reply("🔓 身份已识别，点击下方按钮进入后台：", reply_markup=kb)

# --- Web 路由 ---
app = FastAPI()

@app.get("/manage", response_class=HTMLResponse)
async def router_page(request: Request, sid: str, gid: str = None, tab: str = "basic"):
    if sid not in auth_sessions: return "认证过期，请重新在机器人发送 /start"
    if not gid:
        gs = db_query("SELECT group_id, group_name FROM groups")
        return templates.TemplateResponse("select.html", {"request": request, "sid": sid, "gs": gs})
    
    g_data = db_query("SELECT * FROM groups WHERE group_id=?", (gid,), True)
    users_list = db_query("SELECT * FROM verified_users WHERE group_id=?", (gid,))
    return templates.TemplateResponse(f"{tab}.html", {"request": request, "sid": sid, "gid": gid, "g": g_data, "users": users_list, "tab": tab})

# AJAX 保存接口
@app.post("/api/save")
async def api_save(sid: str = Form(...), gid: str = Form(...), data: str = Form(None)):
    # 实际开发中根据 Form 字段更新数据库
    return JSONResponse({"status": "ok"})

# --- 生命周期加固 ---
@asynccontextmanager
async def lifespan(app_in: FastAPI):
    # 1. 数据库表结构对齐
    db_exec("CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY, group_name TEXT, is_on INT, check_cmd TEXT, on_emoji TEXT, off_emoji TEXT, off_cmd TEXT, msg_on TEXT, msg_off TEXT, query_cmd TEXT, query_tpl TEXT, del_sec INT)")
    db_exec("CREATE TABLE IF NOT EXISTS verified_users (user_id TEXT, group_id TEXT, name TEXT, status TEXT, last_time TEXT, PRIMARY KEY(user_id, group_id))")
    db_exec("CREATE TABLE IF NOT EXISTS sent_logs (message_id TEXT, chat_id TEXT, delete_at TEXT, status TEXT)")
    
    # 2. 定时任务启动
    if not scheduler.running:
        scheduler.add_job(auto_cleanup_job, 'interval', minutes=1)
        scheduler.start()

    # 3. 机器人启动预检
    polling_task = None
    try:
        bot_user = await bot.get_me()
        print(f"✅ Bot 连接成功: @{bot_user.username}")
        
        # 清除积压消息并启动轮询
        await bot.delete_webhook(drop_pending_updates=True)
        polling_task = asyncio.create_task(dp.start_polling(bot))
        
        # 上线通知
        if ADMIN_IDS:
            await bot.send_message(ADMIN_IDS[0], "🚀 机器人加固系统启动成功\n网页后台已同步上线。")
    except Exception as e:
        print(f"❌ 机器人启动失败: {e}")

    yield
    
    # 4. 优雅关闭
    if polling_task: polling_task.cancel()
    if scheduler.running: scheduler.shutdown()
    await bot.session.close()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
