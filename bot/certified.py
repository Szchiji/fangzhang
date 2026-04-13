from aiogram import Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db import db_query, db_query_one

router = Router()

ACTIVE_FILTER = "status = 'active' AND (valid_until IS NULL OR valid_until > NOW())"
PAGE_SIZE = 8


def _format_user_card(u: dict, short: bool = False) -> str:
    badges = "✅"
    if u.get("trust_score", 0) >= 8:
        badges += "⭐"
    level_stars = "🔸" * int(u.get("level", 1))
    region = f"📍 {u['region']}" if u.get("region") else ""
    city = f" · {u['city']}" if u.get("city") else ""
    tags = " ".join(f"#{t}" for t in (u.get("tags") or [])) if u.get("tags") else ""

    if short:
        return (
            f"{badges} <b>{u['display_name']}</b> {level_stars}\n"
            f"{region}{city}  信任分: {u.get('trust_score', 0):.1f}\n"
            f"/user_{u['id']}"
        )

    bio = f"\n📝 {u['bio']}" if u.get("bio") else ""
    contact = f"\n📞 {u['contact']}" if u.get("contact") else ""
    valid = f"\n📅 有效期至: {u['valid_until']}" if u.get("valid_until") else ""
    return (
        f"{badges} <b>{u['display_name']}</b> {level_stars}\n"
        f"分类: {u.get('category', 'general')}\n"
        f"{region}{city}\n"
        f"信任分: <b>{u.get('trust_score', 0):.1f}</b> / 10{bio}{contact}"
        f"{valid}\n{tags}"
    )


@router.message(Command("list"))
async def cmd_list(msg: types.Message):
    page = 0
    rows = db_query(
        f"SELECT * FROM certified_users WHERE {ACTIVE_FILTER} ORDER BY trust_score DESC LIMIT %s OFFSET %s",
        (PAGE_SIZE, page * PAGE_SIZE),
    )
    total = db_query_one(f"SELECT COUNT(*) as cnt FROM certified_users WHERE {ACTIVE_FILTER}")
    total_count = total["cnt"] if total else 0

    if not rows:
        await msg.answer("📭 暂无认证用户")
        return

    text = f"<b>📋 认证用户列表</b>（共 {total_count} 人）\n\n"
    text += "\n\n".join(_format_user_card(u, short=True) for u in rows)

    kb = InlineKeyboardBuilder()
    if total_count > PAGE_SIZE:
        kb.button(text="下一页 ➡️", callback_data="list:1")
    kb.button(text="🔍 搜索", callback_data="list:search")
    kb.adjust(2)

    await msg.answer(text, reply_markup=kb.as_markup() if kb.buttons else None)


@router.callback_query(lambda c: c.data and c.data.startswith("list:"))
async def on_list_page(callback: types.CallbackQuery):
    action = callback.data.split(":")[1]
    if action == "search":
        await callback.answer()
        await callback.message.answer("请使用 /search [关键词] 进行搜索")
        return

    await callback.answer()
    page = int(action)
    rows = db_query(
        f"SELECT * FROM certified_users WHERE {ACTIVE_FILTER} ORDER BY trust_score DESC LIMIT %s OFFSET %s",
        (PAGE_SIZE, page * PAGE_SIZE),
    )
    total = db_query_one(f"SELECT COUNT(*) as cnt FROM certified_users WHERE {ACTIVE_FILTER}")
    total_count = total["cnt"] if total else 0

    text = f"<b>📋 认证用户列表</b>（第 {page + 1} 页）\n\n"
    text += "\n\n".join(_format_user_card(u, short=True) for u in rows)

    kb = InlineKeyboardBuilder()
    if page > 0:
        kb.button(text="⬅️ 上一页", callback_data=f"list:{page - 1}")
    if (page + 1) * PAGE_SIZE < total_count:
        kb.button(text="下一页 ➡️", callback_data=f"list:{page + 1}")
    kb.adjust(2)

    await callback.message.edit_text(text, reply_markup=kb.as_markup() if kb.buttons else None)


@router.message(Command("search"))
async def cmd_search(msg: types.Message):
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.answer(
            "🔍 <b>搜索认证用户</b>\n\n"
            "用法：/search [关键词]\n"
            "例如：/search 北京 摄影\n\n"
            "支持按名称、简介、地区、城市、分类搜索。"
        )
        return

    query = f"%{parts[1].strip()}%"
    rows = db_query(
        f"""
        SELECT * FROM certified_users
        WHERE {ACTIVE_FILTER}
          AND (display_name ILIKE %s OR bio ILIKE %s OR region ILIKE %s OR city ILIKE %s
               OR category ILIKE %s OR %s = ANY(tags))
        ORDER BY trust_score DESC
        LIMIT 10
        """,
        (query, query, query, query, query, parts[1].strip()),
    )

    if not rows:
        kb = InlineKeyboardBuilder()
        kb.button(text="📋 浏览全部用户", callback_data="list:0")
        kb.button(text="📍 按城市查找", callback_data="list:search")
        kb.adjust(2)
        await msg.answer(
            f"🔍 未找到匹配「{parts[1].strip()}」的认证用户\n\n"
            "💡 试试其他关键词，或浏览全部用户列表。",
            reply_markup=kb.as_markup(),
        )
        return

    text = f"<b>🔍 搜索结果</b>：{parts[1].strip()}（{len(rows)} 人）\n\n"
    text += "\n\n".join(_format_user_card(u, short=True) for u in rows)

    kb = InlineKeyboardBuilder()
    kb.button(text="📋 浏览全部", callback_data="list:0")
    kb.button(text="📍 附近推荐", callback_data="list:search")
    kb.adjust(2)

    await msg.answer(text, reply_markup=kb.as_markup())


@router.message(lambda m: m.text and m.text.startswith("/user_"))
async def cmd_user_by_id(msg: types.Message):
    try:
        uid = int(msg.text.split("_")[1])
    except (IndexError, ValueError):
        await msg.answer("用法：/user_[ID]")
        return
    await _show_user(msg, uid)


@router.message(Command("user"))
async def cmd_user(msg: types.Message):
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.answer("用法：/user [ID]")
        return
    try:
        uid = int(parts[1].strip())
    except ValueError:
        await msg.answer("请提供有效的用户 ID")
        return
    await _show_user(msg, uid)


async def _show_user(msg: types.Message, cert_id: int):
    u = db_query_one(
        f"SELECT * FROM certified_users WHERE id = %s AND {ACTIVE_FILTER}",
        (cert_id,),
    )
    if not u:
        # Check if user exists but is expired/frozen
        any_u = db_query_one(
            "SELECT status, valid_until FROM certified_users WHERE id = %s",
            (cert_id,),
        )
        if any_u:
            if any_u["status"] == "frozen":
                detail = "该认证用户账号已被冻结，暂时无法查看。"
            elif any_u["status"] == "blacklisted":
                detail = "该认证用户已被列入黑名单。"
            elif any_u.get("valid_until"):
                detail = f"该认证用户的认证已于 {any_u['valid_until']} 到期，已从列表中隐藏。"
            else:
                detail = "该认证用户当前不可见。"
            await msg.answer(f"🔒 {detail}")
        else:
            await msg.answer("❌ 未找到该认证用户（ID 不存在）")
        return

    online = db_query_one(
        "SELECT 1 FROM online_status WHERE certified_user_id = %s AND expires_at > NOW()",
        (cert_id,),
    )
    online_badge = "🟢 在线  " if online else ""

    ratings = db_query_one(
        "SELECT COUNT(*) as cnt, AVG(stars) as avg FROM ratings WHERE certified_user_id = %s AND status='approved'",
        (cert_id,),
    )
    rating_text = ""
    if ratings and ratings["cnt"]:
        rating_text = f"\n⭐ 评分: {float(ratings['avg']):.1f} ({ratings['cnt']} 条评价)"
    else:
        rating_text = "\n⭐ 暂无评价"

    text = f"{online_badge}{_format_user_card(u)}{rating_text}"

    kb = InlineKeyboardBuilder()
    kb.button(text="⭐ 评价此用户", callback_data=f"rate:start:{cert_id}")
    kb.button(text="📋 返回列表", callback_data="list:0")
    kb.button(text="🔍 继续搜索", callback_data="list:search")
    kb.adjust(1, 2)

    await msg.answer(text, reply_markup=kb.as_markup())
