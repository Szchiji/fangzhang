import os, asyncio, sqlite3, uuid, time, json, re
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ChatPermissions
import uvicorn

# --- 1. 配置加载 ---
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(',')
PORT = int(os.getenv("PORT", 8080))
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
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY, like_emoji TEXT DEFAULT '👍',
            custom_fields TEXT DEFAULT '地区,价格,链接',
            list_template TEXT DEFAULT '✅ <b>[{地区Value}]</b> {姓名Value}',
            checkin_template TEXT DEFAULT '✨ {姓名Value} 已上线！')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS verified_users (
            user_id TEXT, group_id TEXT, name TEXT, data_json TEXT, expire_date TEXT, PRIMARY KEY(user_id, group_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, remark TEXT, content TEXT, interval_hours INTEGER, 
            start_time TEXT, end_time TEXT, delete_last INTEGER DEFAULT 0, last_msg_id INTEGER, last_run TEXT, status INTEGER DEFAULT 1)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
            user_id TEXT, group_id TEXT, checkin_date TEXT, PRIMARY KEY(user_id, group_id, checkin_date))''')
        # 补齐字段
        try: conn.execute("ALTER TABLE verified_users ADD COLUMN expire_date TEXT")
        except: pass
        conn.commit()

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

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

# --- 3. 机器人核心指令 ---

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = str(msg.from_user.id)
    print(f"DEBUG: 收到 /start 指令，来自 UID: {uid}")
    
    if uid not in ADMIN_IDS:
        print(f"DEBUG: 拒绝访问，{uid} 不在 ADMIN_IDS {ADMIN_IDS} 中")
        return

    if msg.chat.type in ["group", "supergroup"]:
        sid = str(uuid.uuid4())
        auth_sessions[sid] = {"gid": str(msg.chat.id), "exp": time.time() + 7200}
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="🔐 进入手机后台", url=f"{DOMAIN}/manage?sid={sid}&gid={msg.chat.id}")
        ]])
        try:
            await bot.send_message(msg.from_user.id, f"🔑 <b>后台登录成功</b>\n群组: {msg.chat.title}", reply_markup=kb)
            await msg.reply("🔐 权限已确认，后台链接已发至您的私聊。")
        except Exception as e:
            await msg.reply("❌ 请先私聊机器人发送 /start 激活。")

@dp.message()
async def bot_handler(msg: types.Message):
    if not msg.text: return
    uid, gid, text = str(msg.from_user.id), str(msg.chat.id), msg.text.strip()
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"DEBUG: 收到消息 [{text}] 来自 UID: {uid} 在群: {gid}")

    if text == "打卡":
        with get_db() as conn:
            user = conn.execute("SELECT * FROM verified_users WHERE user_id=? AND group_id=?", (uid, gid)).fetchone()
            group = conn.execute("SELECT * FROM groups WHERE group_id=?", (gid,)).fetchone()
        if not user: return
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO checkins VALUES (?,?,?)", (uid, gid, today))
            conn.commit()
        await msg.reply(power_render(group['checkin_template'], user['data_json'], user['name']))
        try: await bot.set_message_reaction(gid, msg.message_id, [types.ReactionTypeEmoji(emoji=group['like_emoji'])])
        except: pass

# --- 4. 启动逻辑整合 ---
async def timer_worker():
    while True:
        # 定时广告逻辑 (保持原有逻辑)
        await asyncio.sleep(60)

async def main():
    init_db()
    # 强制清理旧 Webhook，解决不回应问题的核心
    await bot.delete_webhook(drop_pending_updates=True)
    print(f"🚀 机器人已启动！监听端口: {PORT}")

    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, loop="asyncio")
    server = uvicorn.Server(config)
    
    # 协同运行：Bot轮询 + Web服务器 + 定时器
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve(),
        timer_worker()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
