import os
from aiogram import Router, types, Bot
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db import db_exec, db_query_one
from bot.roles import detect_role, cert_status_label, cert_expiry_text

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


def _build_normal_user_home(name: str) -> tuple[str, types.InlineKeyboardMarkup]:
    """Home screen for non-certified, non-admin users."""
    text = (
        f"<b>👋 你好，{name}！</b>\n"
        "欢迎使用 <b>CheBot</b>\n\n"
        "🔎 <b>快速查询</b>\n"
        "• /search [关键词] — 搜索认证用户\n"
        "• /list — 浏览全部认证用户\n"
        "• /nearby [城市] — 查找附近用户\n"
        "• /online — 今日在线用户\n\n"
        "📌 <b>我的账户</b>\n"
        "• /checkin — 每日签到领积分\n"
        "• /tasks — 积分任务\n"
        "• /points — 我的积分余额\n\n"
        "💡 认证用户可发布优惠券并获得更多曝光，联系管理员申请认证。"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 搜索用户", callback_data="menu:search_tip")
    kb.button(text="📋 用户列表", callback_data="menu:list")
    kb.button(text="📍 附近推荐", callback_data="menu:nearby")
    kb.button(text="🟢 今日在线", callback_data="menu:online")
    kb.button(text="✅ 每日签到", callback_data="menu:checkin")
    kb.button(text="💰 我的积分", callback_data="menu:points")
    kb.adjust(2, 2, 2)
    return text, kb.as_markup()


def _build_certified_user_home(
    name: str, cert: dict
) -> tuple[str, types.InlineKeyboardMarkup]:
    """Home screen for certified users."""
    status_label = cert_status_label(cert)
    expiry_text = cert_expiry_text(cert)
    trust = float(cert.get("trust_score") or 0)
    level_stars = "🔸" * int(cert.get("level") or 1)

    text = (
        f"<b>✅ 认证用户专区</b>\n\n"
        f"👤 {name}  {level_stars}\n"
        f"状态：{status_label}\n"
        f"{expiry_text}\n"
        f"信任分：<b>{trust:.1f}</b> / 10\n\n"
        "🚀 <b>快捷操作</b>\n"
        "• /coupon — 发布优惠券\n"
        "• /myprofile — 我的认证资料\n"
        "• /checkin — 每日签到（提升活跃分）\n"
        "• /points — 我的积分与成长值\n\n"
        "🔎 <b>用户查询</b>\n"
        "• /search [关键词] · /list · /nearby [城市]"
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="🎫 发布优惠券", callback_data="menu:coupon")
    kb.button(text="🌟 我的资料", callback_data="menu:myprofile")
    kb.button(text="⭐ 我的评价", callback_data="menu:my_ratings")
    kb.button(text="💰 我的积分", callback_data="menu:points")
    kb.button(text="✅ 每日签到", callback_data="menu:checkin")
    kb.button(text="🔍 搜索用户", callback_data="menu:search_tip")
    kb.adjust(2, 2, 2)
    return text, kb.as_markup()


def _build_admin_home(
    name: str, cert: dict | None
) -> tuple[str, types.InlineKeyboardMarkup]:
    """Home screen for admins (may also be certified)."""
    # Fetch quick stats
    pending_ratings = db_query_one(
        "SELECT COUNT(*) as cnt FROM ratings WHERE status='pending'"
    )
    pending_coupons = db_query_one(
        "SELECT COUNT(*) as cnt FROM coupons WHERE status='pending'"
    )
    expiring = db_query_one(
        """
        SELECT COUNT(*) as cnt FROM certified_users
        WHERE status='active'
          AND valid_until IS NOT NULL
          AND valid_until < NOW() + INTERVAL '7 days'
          AND valid_until > NOW()
        """
    )
    violations_today = db_query_one(
        "SELECT COUNT(*) as cnt FROM violations WHERE created_at::date = CURRENT_DATE"
    )

    pr = pending_ratings["cnt"] if pending_ratings else 0
    pc = pending_coupons["cnt"] if pending_coupons else 0
    ex = expiring["cnt"] if expiring else 0
    vt = violations_today["cnt"] if violations_today else 0

    alerts = []
    if pr > 0:
        alerts.append(f"⭐ {pr} 条评价待审核")
    if pc > 0:
        alerts.append(f"🎫 {pc} 张优惠券待审核")
    if ex > 0:
        alerts.append(f"⏰ {ex} 位用户即将到期")
    if vt > 0:
        alerts.append(f"⚠️ 今日 {vt} 条违规记录")
    alert_text = "\n".join(f"  • {a}" for a in alerts) if alerts else "  无待处理事项 ✅"

    cert_line = ""
    if cert:
        cert_line = f"认证身份：{cert_status_label(cert)}  {cert_expiry_text(cert)}\n"

    text = (
        f"<b>🖥️ 管理员控制台</b>\n\n"
        f"👤 {name}\n"
        f"{cert_line}\n"
        f"<b>📋 待处理事项</b>\n{alert_text}\n\n"
        "<b>⚡ 快捷命令</b>\n"
        "• /adduser — 添加认证用户\n"
        "• /dashboard — 完整数据看板\n"
        "• /stats — 群组统计\n"
        "• /push [ID] — 推送用户到频道\n"
        "• /freeze [ID] / /unfreeze [ID]\n"
        "• /blacklist [UID] — 加入黑名单"
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ 添加认证用户", callback_data="menu:adduser")
    kb.button(text="📊 数据看板", url=f"{BASE_URL}/dashboard")
    if pr > 0:
        kb.button(text=f"⭐ 审核评价（{pr}）", callback_data="menu:pending_ratings")
    if pc > 0:
        kb.button(text=f"🎫 审核优惠券（{pc}）", callback_data="menu:pending_coupons")
    kb.button(text="👥 用户管理", url=f"{BASE_URL}/users")
    kb.button(text="⚙️ 全局设置", url=f"{BASE_URL}/settings")
    kb.adjust(2)
    return text, kb.as_markup()


@router.message(Command("start"))
async def cmd_start(msg: types.Message, bot: Bot):
    uid = msg.from_user.id
    username = msg.from_user.username
    full_name = msg.from_user.full_name
    name = msg.from_user.first_name or full_name or username or "用户"

    _ensure_user(uid, username, full_name)

    if msg.chat.type != "private":
        gid = str(msg.chat.id)
        db_exec(
            "INSERT INTO groups (gid, gname) VALUES (%s, %s) ON CONFLICT (gid) DO NOTHING",
            (gid, msg.chat.title or "群组"),
        )

    role = await detect_role(bot, uid, msg.chat.id, msg.chat.type)

    if role["is_admin"]:
        text, markup = _build_admin_home(name, role["cert"])
    elif role["is_certified"]:
        text, markup = _build_certified_user_home(name, role["cert"])
    else:
        text, markup = _build_normal_user_home(name)

    await msg.answer(text, reply_markup=markup)


@router.message(Command("help"))
async def cmd_help(msg: types.Message, bot: Bot):
    uid = msg.from_user.id
    role = await detect_role(bot, uid, msg.chat.id, msg.chat.type)

    base = (
        "<b>📖 CheBot 帮助</b>\n\n"
        "<b>🔎 查询功能（所有用户）</b>\n"
        "/list — 浏览认证用户列表\n"
        "/search [关键词] — 搜索用户\n"
        "/user [ID] — 查看用户详情\n"
        "/nearby [城市] — 附近用户推荐\n"
        "/online — 今日在线用户\n\n"
        "<b>📌 账户功能（所有用户）</b>\n"
        "/checkin — 每日签到领积分\n"
        "/ranking — 签到排行榜\n"
        "/tasks — 积分任务\n"
        "/points — 我的积分\n"
        "/rate [ID] — 评价认证用户\n\n"
    )

    cert_section = ""
    if role["is_certified"]:
        cert_section = (
            "<b>✅ 认证用户专属</b>\n"
            "/myprofile — 我的认证资料与状态\n"
            "/coupon — 发布优惠券\n\n"
        )

    admin_section = ""
    if role["is_admin"]:
        admin_section = (
            "<b>🖥️ 管理员功能</b>\n"
            "/adduser — 添加认证用户\n"
            "/edituser [ID] — 编辑用户\n"
            "/expire [ID] [日期] — 设置到期日\n"
            "/freeze [ID] / /unfreeze [ID] — 冻结/解冻\n"
            "/blacklist [UID] — 加入黑名单\n"
            "/whitelist [UID] — 加入白名单\n"
            "/push [ID] — 推送用户到频道\n"
            "/dashboard — 数据统计\n"
            "/stats — 群组统计\n"
            "/approve_rating [ID] — 审核通过评价\n"
            "/reject_rating [ID] — 拒绝评价\n"
        )

    await msg.answer(base + cert_section + admin_section)


@router.callback_query(lambda c: c.data and c.data.startswith("menu:"))
async def menu_callback(callback: types.CallbackQuery, bot: Bot):
    action = callback.data.split(":")[1]
    await callback.answer()

    routing = {
        "list": "请使用 /list 查看认证用户列表",
        "search_tip": "请使用 /search [关键词] 进行搜索\n例如：/search 北京 摄影",
        "nearby": "请使用 /nearby [城市] 查找附近用户",
        "online": "请使用 /online 查看今日在线用户",
        "checkin": "请使用 /checkin 进行每日签到",
        "tasks": "请使用 /tasks 查看今日任务",
        "points": "请使用 /points 查看积分余额",
        "coupon": "请使用 /coupon 发布优惠券（仅认证用户）",
        "myprofile": "请使用 /myprofile 查看您的认证资料",
        "my_ratings": "请使用 /myratings 查看您收到的评价",
        "adduser": "请使用 /adduser 开始添加认证用户（仅管理员）",
        "pending_ratings": "请前往管理后台审核评价，或使用 /approve_rating [ID]",
        "pending_coupons": "请前往管理后台审核优惠券",
    }

    reply = routing.get(action, "请使用对应命令操作")
    await callback.message.answer(reply)
