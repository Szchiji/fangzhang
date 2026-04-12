import os, asyncio, json
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
import psycopg2
import psycopg2.extras

# --- 1. 从环境变量读取配置 ---
TOKEN = os.getenv("TOKEN")
# 检查 Token 是否存在，不存在则抛出友好提示
if not TOKEN:
    raise ValueError("错误：未在环境变量中检测到 TOKEN。请在 Railway 的 Variables 页面添加！")

# 支持 Railway 提供的 DATABASE_URL 或独立的 PG 环境变量
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    _pghost = os.getenv("PGHOST")
    _pguser = os.getenv("PGUSER")
    if not (_pghost and _pguser):
        raise ValueError(
            "错误：未检测到数据库配置。"
            "请在 Railway 的 Variables 页面添加 DATABASE_URL，"
            "或同时添加 PGHOST / PGUSER / PGPASSWORD / PGDATABASE！"
        )

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- 2. 数据库工具函数 ---
def _get_conn():
    """创建并返回 PostgreSQL 连接（支持 DATABASE_URL 或独立 PG 环境变量）。"""
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "railway"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def db_exec(sql, params=()):
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)

def db_query(sql, params=()):
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

def init_db():
    db_exec(
        "CREATE TABLE IF NOT EXISTS settings "
        "(gid TEXT, key TEXT, value TEXT, PRIMARY KEY(gid, key))"
    )
    db_exec("CREATE TABLE IF NOT EXISTS groups (gid TEXT PRIMARY KEY, gname TEXT)")

# --- 3. 机器人逻辑 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    gid = str(msg.chat.id)
    db_exec(
        "INSERT INTO groups (gid, gname) VALUES (%s, %s) ON CONFLICT (gid) DO NOTHING",
        (gid, msg.chat.title or "私聊"),
    )
    
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
    rows = db_query("SELECT key, value FROM settings WHERE gid=%s", (gid,))
    conf = {row['key']: row['value'] for row in rows if row['value']}
    return templates.TemplateResponse(f"{tab}.html", {"request": request, "gid": gid, "tab": tab, "conf": conf})

@app.post("/api/set")
async def api_set(gid: str = Form(...), key: str = Form(...), value: str = Form(None)):
    if value is None or value.strip() == "":
        db_exec("DELETE FROM settings WHERE gid=%s AND key=%s", (gid, key))
    else:
        db_exec(
            "INSERT INTO settings (gid, key, value) VALUES (%s, %s, %s) "
            "ON CONFLICT (gid, key) DO UPDATE SET value = EXCLUDED.value",
            (gid, key, value),
        )
    return {"status": "ok"}

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
