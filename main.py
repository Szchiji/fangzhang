import os, asyncio, sqlite3, uuid, json
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

# --- 1. 基础配置 (Railway 变量名请确保一致) ---
TOKEN = os.getenv("TOKEN")
DB_PATH = "/data/bot_pro.db"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()

# 确保你的 templates 文件夹下有 layout.html, users.html, config.html 等
templates = Jinja2Templates(directory="templates")

# --- 2. 数据库工具函数 ---
def db_exec(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params)
        conn.commit()

def db_query(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()

def init_db():
    os.makedirs("/data", exist_ok=True)
    # settings 表存所有侧边工具的配置 (K-V 结构)
    db_exec("CREATE TABLE IF NOT EXISTS settings (gid TEXT, key TEXT, value TEXT, PRIMARY KEY(gid, key))")
    # groups 表记录机器人加入的群
    db_exec("CREATE TABLE IF NOT EXISTS groups (gid TEXT PRIMARY KEY, gname TEXT)")

# --- 3. 机器人逻辑：自动识别群组 ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    # 生成一个临时的进入链接
    gid = str(msg.chat.id)
    # 记录群组信息
    db_exec("INSERT OR IGNORE INTO groups VALUES (?, ?)", (gid, msg.chat.title or "私聊"))
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    # 这里的链接你可以根据你的域名微调
    kb = InlineKeyboardBuilder().button(text="🖥️ 打开管理后台", url=f"https://{os.getenv('RAILWAY_STATIC_URL')}/manage?gid={gid}&tab=users").as_markup()
    await msg.answer("<b>7哥，中控系统已就绪</b>\n请点击下方按钮进入：", reply_markup=kb)

@dp.message()
async def handle_msg(msg: types.Message):
    # 机器人实时读取网页设置的示例逻辑
    gid = str(msg.chat.id)
    uid = str(msg.from_user.id)
    
    # 比如：检查该用户是否在网页的“认证用户”里
    user_key = f"u_{uid}"
    res = db_query("SELECT value FROM settings WHERE gid=? AND key=?", (gid, user_id))
    
    if res and "打卡" in (msg.text or ""):
        user_name = res[0]['value']
        await msg.reply(f"✅ 认证用户【{user_name}】打卡成功！")

# --- 4. Web 管理后台逻辑 ---

# 动态路由：根据 tab 参数加载对应的 HTML
@app.get("/manage", response_class=HTMLResponse)
async def page_manage(request: Request, gid: str, tab: str = "users"):
    # 1. 从数据库取出该群组的所有配置，转成字典供前端使用
    rows = db_query("SELECT key, value FROM settings WHERE gid=?", (gid,))
    conf = {row['key']: row['value'] for row in rows if row['value']} # 过滤掉空值
    
    # 2. 返回对应的 HTML 页面 (比如 tab=users 就返回 users.html)
    return templates.TemplateResponse(f"{tab}.html", {
        "request": request, 
        "gid": gid, 
        "tab": tab, 
        "conf": conf
    })

# 万能同步接口：网页点保存，直接调这个
@app.post("/api/set")
async def api_set(gid: str = Form(...), key: str = Form(...), value: str = Form(None)):
    if not value or value.strip() == "":
        db_exec("DELETE FROM settings WHERE gid=? AND key=?", (gid, key))
    else:
        db_exec("INSERT OR REPLACE INTO settings VALUES (?, ?, ?)", (gid, key, value))
    return {"status": "ok"}

# --- 5. 生命周期管理 ---
@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
