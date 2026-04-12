import os
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db import db_exec, db_query_one

router = Router()

BASE_URL = os.getenv("RAILWAY_STATIC_URL", "")
if BASE_URL:
    BASE_URL = f"https://{BASE_URL.rstrip('/')}"
else:
    BASE_URL = f"http://localhost:{os.getenv('PORT', '8080')}"


def _ensure_user(uid: int, username: str | None, full_name: str):
    db_exec(
        """
        INSERT INTO users (uid, username, full_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (uid) DO UPDATE
          SET username = EXCLUDED.username,
              full_name = EXCLUDED.full_name,
              last_seen = NOW()
        """,
        (uid, username, full_name),
    )


@router.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = msg.from_user.id
    username = msg.from_user.username
    full_name = msg.from_user.full_name

    _ensure_user(uid, username, full_name)

    if msg.chat.type != "private":
        gid = str(msg.chat.id)
        db_exec(
            "INSERT INTO groups (gid, gname) VALUES (%s, %s) ON CONFLICT (gid) DO NOTHING",
            (gid, msg.chat.title or "群组"),
        )

    kb = InlineKeyboardBuilder()
    kb.button(text="📋 认证用户列表", callback_data="menu:list")
    kb.button(text="✅ 每日签到", callback_data="menu:checkin")
    kb.button(text="📍 附近推荐", callback_data="menu:nearby")
    kb.button(text="⭐ 积分任务", callback_data="menu:tasks")
    kb.button(text="🖥️ 管理后台", url=f"{BASE_URL}/dashboard")
    kb.adjust(2, 2, 1)

    await msg.answer(
        "<b>🤖 CheBot — 认证用户管理平台</b>\n\n"
        "功能菜单：\n"
        "• /list — 浏览认证用户\n"
        "• /search — 搜索用户\n"
        "• /nearby — 附近推荐\n"
        "• /checkin — 每日签到\n"
        "• /tasks — 积分任务\n"
        "• /points — 我的积分\n"
        "• /coupon — 发布优惠\n"
        "• /help — 帮助说明\n\n"
        "管理员命令：/dashboard /adduser /admin",
        reply_markup=kb.as_markup(),
    )


@router.message(Command("help"))
async def cmd_help(msg: types.Message):
    await msg.answer(
        "<b>📖 CheBot 功能说明</b>\n\n"
        "<b>用户功能</b>\n"
        "/list — 浏览所有认证用户\n"
        "/search [关键词] — 搜索用户\n"
        "/user [ID] — 查看用户详情\n"
        "/nearby [城市] — 附近用户推荐\n"
        "/checkin — 每日签到领积分\n"
        "/online — 今日在线用户\n"
        "/ranking — 签到排行榜\n"
        "/rate [ID] — 评价认证用户\n"
        "/coupon — 发布优惠券\n"
        "/tasks — 查看任务列表\n"
        "/points — 我的积分余额\n\n"
        "<b>管理员功能</b>\n"
        "/adduser — 添加认证用户\n"
        "/edituser [ID] — 编辑用户\n"
        "/freeze [ID] — 冻结用户\n"
        "/expire [ID] [日期] — 设置到期\n"
        "/blacklist [UID] — 加入黑名单\n"
        "/dashboard — 数据统计\n"
        "/push [ID] — 推送用户到频道\n"
    )


@router.callback_query(lambda c: c.data and c.data.startswith("menu:"))
async def menu_callback(callback: types.CallbackQuery):
    action = callback.data.split(":")[1]
    await callback.answer()
    if action == "list":
        await callback.message.answer("请使用 /list 查看认证用户列表")
    elif action == "checkin":
        await callback.message.answer("请使用 /checkin 进行每日签到")
    elif action == "nearby":
        await callback.message.answer("请使用 /nearby [城市] 查找附近用户")
    elif action == "tasks":
        await callback.message.answer("请使用 /tasks 查看今日任务")
