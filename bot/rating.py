from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db import db_exec, db_query_one, db_query

router = Router()


class RatingStates(StatesGroup):
    waiting_comment = State()


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


def _update_trust_score(cert_id: int):
    stats = db_query_one(
        "SELECT AVG(stars) as avg, COUNT(*) as cnt FROM ratings WHERE certified_user_id = %s AND status = 'approved'",
        (cert_id,),
    )
    if not stats or not stats["cnt"]:
        return
    cu = db_query_one("SELECT risk_score, activity_score FROM certified_users WHERE id = %s", (cert_id,))
    if not cu:
        return

    avg_rating = float(stats["avg"])
    risk_penalty = min(cu["risk_score"] * 0.1, 2.0)
    activity_bonus = min(cu["activity_score"] * 0.05, 1.0)
    trust = max(0, min(10, avg_rating * 2 - risk_penalty + activity_bonus))

    db_exec(
        "UPDATE certified_users SET trust_score = %s, updated_at = NOW() WHERE id = %s",
        (round(trust, 2), cert_id),
    )


@router.message(Command("rate"))
async def cmd_rate(msg: types.Message, state: FSMContext):
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.answer("用法：/rate [认证用户ID]")
        return
    try:
        cert_id = int(parts[1].strip())
    except ValueError:
        await msg.answer("请提供有效的用户 ID")
        return

    u = db_query_one(
        "SELECT * FROM certified_users WHERE id = %s AND status = 'active'",
        (cert_id,),
    )
    if not u:
        await msg.answer("❌ 未找到该认证用户")
        return

    existing = db_query_one(
        "SELECT 1 FROM ratings WHERE certified_user_id = %s AND rater_uid = %s AND created_at > NOW() - INTERVAL '7 days'",
        (cert_id, msg.from_user.id),
    )
    if existing:
        await msg.answer("⚠️ 您在最近 7 天内已评价过该用户，请稍后再试")
        return

    kb = InlineKeyboardBuilder()
    for stars in range(1, 6):
        kb.button(text="⭐" * stars, callback_data=f"rate:stars:{cert_id}:{stars}")
    kb.adjust(5)

    await msg.answer(
        f"请为 <b>{u['display_name']}</b> 打分：",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("rate:start:"))
async def on_rate_start(callback: types.CallbackQuery):
    cert_id = int(callback.data.split(":")[2])
    u = db_query_one(
        "SELECT * FROM certified_users WHERE id = %s AND status = 'active'",
        (cert_id,),
    )
    if not u:
        await callback.answer("用户不存在", show_alert=True)
        return

    existing = db_query_one(
        "SELECT 1 FROM ratings WHERE certified_user_id = %s AND rater_uid = %s AND created_at > NOW() - INTERVAL '7 days'",
        (cert_id, callback.from_user.id),
    )
    if existing:
        await callback.answer("您最近已评价过此用户", show_alert=True)
        return

    await callback.answer()
    kb = InlineKeyboardBuilder()
    for stars in range(1, 6):
        kb.button(text="⭐" * stars, callback_data=f"rate:stars:{cert_id}:{stars}")
    kb.adjust(5)

    await callback.message.answer(
        f"请为 <b>{u['display_name']}</b> 打分：",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("rate:stars:"))
async def on_rate_stars(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    cert_id, stars = int(parts[2]), int(parts[3])
    await callback.answer(f"您选择了 {'⭐' * stars}")
    await state.update_data(cert_id=cert_id, stars=stars, msg_id=callback.message.message_id)
    await state.set_state(RatingStates.waiting_comment)
    await callback.message.answer("💬 请输入评价内容（可选，直接发送 . 跳过）：")


@router.message(RatingStates.waiting_comment)
async def on_rating_comment(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    cert_id = data["cert_id"]
    stars = data["stars"]
    comment = msg.text.strip() if msg.text and msg.text.strip() != "." else None

    # Simple tag extraction
    tags = []
    if comment:
        tag_map = {
            "专业": ["专业", "技术"],
            "热情": ["热情", "友好", "nice"],
            "靠谱": ["靠谱", "可信", "reliable"],
            "推荐": ["推荐", "好评"],
        }
        for tag, keywords in tag_map.items():
            if any(kw in comment for kw in keywords):
                tags.append(tag)

    db_exec(
        """
        INSERT INTO ratings (certified_user_id, rater_uid, stars, comment, tags)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (cert_id, msg.from_user.id, stars, comment, tags),
    )

    # Check if auto-approve is enabled (no moderation)
    setting = db_query_one("SELECT value FROM settings WHERE gid='global' AND key='auto_approve_ratings'")
    if setting and setting["value"] == "1":
        db_exec(
            "UPDATE ratings SET status='approved' WHERE certified_user_id=%s AND rater_uid=%s ORDER BY id DESC LIMIT 1",
            (cert_id, msg.from_user.id),
        )
        _update_trust_score(cert_id)

    # Award points
    user = db_query_one("SELECT points FROM users WHERE uid = %s", (msg.from_user.id,))
    if user:
        new_bal = (user["points"] or 0) + 20
        db_exec("UPDATE users SET points = %s WHERE uid = %s", (new_bal, msg.from_user.id))
        db_exec(
            "INSERT INTO points_transactions (uid, amount, reason, balance_after) VALUES (%s, 20, 'rate_user', %s)",
            (msg.from_user.id, new_bal),
        )
        db_exec(
            "INSERT INTO user_tasks (uid, task_key, points_earned) VALUES (%s, 'rate_user', 20)",
            (msg.from_user.id,),
        )

    u = db_query_one("SELECT display_name FROM certified_users WHERE id = %s", (cert_id,))
    name = u["display_name"] if u else str(cert_id)
    await msg.answer(
        f"✅ 感谢您对 <b>{name}</b> 的评价！\n"
        f"您的评分：{'⭐' * stars}\n"
        "评价将在审核后显示，同时奖励 +20 积分"
    )


@router.message(Command("approve_rating"))
async def cmd_approve_rating(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/approve_rating [评价ID]")
        return
    rid = int(parts[1].strip())
    r = db_query_one("SELECT certified_user_id FROM ratings WHERE id = %s", (rid,))
    if not r:
        await msg.reply("❌ 未找到该评价")
        return
    db_exec("UPDATE ratings SET status='approved' WHERE id = %s", (rid,))
    _update_trust_score(r["certified_user_id"])
    await msg.reply(f"✅ 评价 #{rid} 已通过审核，信任分已更新")


@router.message(Command("reject_rating"))
async def cmd_reject_rating(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/reject_rating [评价ID]")
        return
    rid = int(parts[1].strip())
    db_exec("UPDATE ratings SET status='rejected' WHERE id = %s", (rid,))
    await msg.reply(f"❌ 评价 #{rid} 已被拒绝")
