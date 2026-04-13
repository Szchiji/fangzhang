"""
Certified user self-service profile commands.

/myprofile — view own certification status, expiry, ratings, and quick actions
/myratings — view ratings received by the certified user
"""
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db import db_query, db_query_one
from bot.roles import cert_status_label, cert_expiry_text

router = Router()


@router.message(Command("myprofile"))
async def cmd_myprofile(msg: types.Message):
    uid = msg.from_user.id

    cert = db_query_one(
        """
        SELECT id, display_name, valid_until, status,
               trust_score, level, activity_score, category, region, city, bio,
               (
                   valid_until IS NOT NULL
                   AND valid_until < NOW() + INTERVAL '7 days'
                   AND valid_until > NOW()
               ) AS expiring_soon
        FROM certified_users
        WHERE uid = %s AND (valid_until IS NULL OR valid_until > NOW())
        """,
        (uid,),
    )

    if not cert:
        # Check if the user was ever certified but has since expired
        expired = db_query_one(
            "SELECT display_name, valid_until FROM certified_users WHERE uid = %s ORDER BY id DESC LIMIT 1",
            (uid,),
        )
        if expired:
            await msg.answer(
                "⛔ <b>您的认证已过期</b>\n\n"
                f"认证名称：{expired['display_name']}\n"
                f"到期日期：{expired['valid_until']}\n\n"
                "如需续期，请联系管理员办理续期手续。\n"
                "续期后即可继续享有发布优惠券、评价曝光等认证用户权益。"
            )
        else:
            await msg.answer(
                "❌ <b>您目前没有认证身份</b>\n\n"
                "认证用户可享有以下专属权益：\n"
                "• 🎫 发布优惠券到推广频道\n"
                "• 📢 认证名片展示与推荐曝光\n"
                "• ⭐ 收集用户评价、积累信任分\n"
                "• 🔸 等级与成长值体系\n\n"
                "如需申请认证，请联系管理员。"
            )
        return

    user = db_query_one("SELECT points, membership_level FROM users WHERE uid = %s", (uid,))
    points = user["points"] if user else 0
    mem_level = (user["membership_level"] or "free").upper() if user else "FREE"
    level_icons = {"FREE": "🆓", "SILVER": "🥈", "GOLD": "🥇", "VIP": "💎"}
    mem_icon = level_icons.get(mem_level, "🆓")

    ratings_stats = db_query_one(
        "SELECT COUNT(*) as cnt, AVG(stars) as avg FROM ratings WHERE certified_user_id = %s AND status='approved'",
        (cert["id"],),
    )
    rating_line = ""
    if ratings_stats and ratings_stats["cnt"]:
        rating_line = f"⭐ 评分：<b>{float(ratings_stats['avg']):.1f}</b>（{ratings_stats['cnt']} 条评价）\n"

    online = db_query_one(
        "SELECT 1 FROM online_status WHERE certified_user_id = %s AND expires_at > NOW()",
        (cert["id"],),
    )
    online_badge = "🟢 当前在线  " if online else "⚫ 离线  "

    level_stars = "🔸" * int(cert.get("level") or 1)
    status_label = cert_status_label(cert)
    expiry_text = cert_expiry_text(cert)
    region_city = " · ".join(
        filter(None, [cert.get("region"), cert.get("city")])
    )
    bio_line = f"\n📝 {cert['bio']}" if cert.get("bio") else ""

    text = (
        f"<b>🌟 我的认证资料</b>\n\n"
        f"{online_badge}<b>{cert['display_name']}</b> {level_stars}\n"
        f"状态：{status_label}\n"
        f"{expiry_text}\n"
        f"分类：{cert.get('category', 'general')}\n"
        f"地区：{region_city or '未填写'}\n"
        f"信任分：<b>{float(cert.get('trust_score') or 0):.1f}</b> / 10  "
        f"活跃分：{cert.get('activity_score', 0)}\n"
        f"{rating_line}"
        f"积分余额：<b>{points}</b>  会员：{mem_icon} {mem_level}"
        f"{bio_line}"
    )

    if cert.get("expiring_soon"):
        text += (
            "\n\n🔔 <b>续期提醒</b>\n"
            "您的认证即将到期，请及时联系管理员续期，"
            "避免失去认证用户权益（发优惠券、曝光展示等）。"
        )

    kb = InlineKeyboardBuilder()
    kb.button(text="🎫 发布优惠券", callback_data="menu:coupon")
    kb.button(text="⭐ 查看我的评价", callback_data="myprofile:ratings")
    kb.button(text="✅ 每日签到", callback_data="menu:checkin")
    kb.button(text="💰 我的积分", callback_data="menu:points")
    kb.adjust(2, 2)

    await msg.answer(text, reply_markup=kb.as_markup())


@router.message(Command("myratings"))
async def cmd_myratings(msg: types.Message):
    uid = msg.from_user.id

    cert = db_query_one(
        "SELECT id, display_name FROM certified_users WHERE uid = %s AND status='active' AND (valid_until IS NULL OR valid_until > NOW())",
        (uid,),
    )
    if not cert:
        await msg.answer(
            "❌ 此功能仅限认证用户使用。\n"
            "如果您的认证已过期，请联系管理员续期后再查看。"
        )
        return

    ratings = db_query(
        """
        SELECT stars, comment, tags, created_at
        FROM ratings
        WHERE certified_user_id = %s AND status = 'approved'
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (cert["id"],),
    )
    total = db_query_one(
        "SELECT COUNT(*) as cnt, AVG(stars) as avg FROM ratings WHERE certified_user_id = %s AND status='approved'",
        (cert["id"],),
    )

    if not ratings:
        await msg.answer(
            f"<b>⭐ {cert['display_name']} 的评价</b>\n\n"
            "暂无已审核的评价记录。\n\n"
            "💡 鼓励您的用户通过 /rate 为您留下好评，有助于提升信任分。"
        )
        return

    avg = float(total["avg"]) if total and total["avg"] else 0
    text = (
        f"<b>⭐ 我的评价</b>（{total['cnt']} 条，平均 {avg:.1f} 分）\n\n"
    )
    for r in ratings:
        stars_str = "⭐" * int(r["stars"])
        comment = r["comment"] or "（无文字评价）"
        tags_str = " ".join(f"#{t}" for t in (r.get("tags") or [])) or ""
        date_str = r["created_at"].strftime("%m-%d") if r.get("created_at") else ""
        text += f"{stars_str}  {comment} {tags_str}  <i>{date_str}</i>\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="🌟 我的资料", callback_data="menu:myprofile")
    kb.button(text="🔙 返回首页", callback_data="myprofile:home")
    kb.adjust(2)

    await msg.answer(text, reply_markup=kb.as_markup())


@router.callback_query(lambda c: c.data and c.data.startswith("myprofile:"))
async def on_myprofile_action(callback: types.CallbackQuery):
    action = callback.data.split(":")[1]
    await callback.answer()
    if action == "ratings":
        await callback.message.answer("请使用 /myratings 查看您收到的评价")
    elif action == "home":
        await callback.message.answer("请使用 /start 返回首页")
