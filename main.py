import os, asyncio, sqlite3, uuid, json
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

# --- 1. 从环境变量读取配置 ---
TOKEN = os.getenv("TOKEN")
# 检查 Token 是否存在，不存在则抛出友好提示
if not TOKEN:
    raise ValueError("错误：未在环境变量中检测到 TOKEN。请在 Railway 的 Variables 页面添加！")

DB_PATH = "/data/bot_pro.db"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- 2. 数据库工具函数 ---
def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params); conn.commit()

def db_query(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()

def init_db():
    os.makedirs("/data", exist_ok=True)
    db_exec("CREATE TABLE IF NOT EXISTS settings (gid TEXT, key TEXT, value TEXT, PRIMARY KEY(gid, key))")
    db_exec("CREATE TABLE IF NOT EXISTS groups (gid TEXT PRIMARY KEY, gname TEXT)")

# --- 3. 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    gid = str(msg.chat.id)
    # 显式指定列名写入，兼容任何版本的旧表
    db_exec("INSERT OR IGNORE INTO groups (gid, gname) VALUES (?, ?)", (gid, msg.chat.title or "私聊"))
    
    # 自动获取 Railway 分配的静态域名
    raw_url = os.getenv('RAILWAY_STATIC_URL')
    if raw_url:
        domain = f"https://{raw_url.rstrip('/')}"
    else:
        domain = "http://localhost:8080" # 本地调试用
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    url = f"{domain}/manage?gid={gid}&tab=users"
    kb = InlineKeyboardBuilder().button(text="🖥️ 进入管理后台", url=url).as_markup()
    
    await msg.answer(
        f"<b>7哥，中控系统运行中</b>\n"
        f"当前群组: <code>{msg.chat.title}</code>\n"
        f"环境变量检测: <code>TOKEN 已就绪 ✅</code>", 
        reply_markup=kb
    )

# --- 4. Web 管理后台逻辑 ---
@app.get("/manage", response_class=HTMLResponse)
async def page_manage(request: Request, gid: str, tab: str = "users"):
    rows = db_query("SELECT key, value FROM settings WHERE gid=?", (gid,))
    conf = {row['key']: row['value'] for row in rows if row['value']}
    return templates.TemplateResponse(f"{tab}.html", {"request": request, "gid": gid, "tab": tab, "conf": conf})

@app.post("/api/set")
async def api_set(gid: str = Form(...), key: str = Form(...), value: str = Form(None)):
    if value is None or value.strip() == "":
        db_exec("DELETE FROM settings WHERE gid=? AND key=?", (gid, key))
    else:
        db_exec("INSERT OR REPLACE INTO settings VALUES (?, ?, ?)", (gid, key, value))
    return {"status": "ok"}

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
