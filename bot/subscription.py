from aiogram import Router, types, Bot
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db import db_query, db_query_one

router = Router()


async def check_subscriptions(bot: Bot, uid: int, gid: str, feature: str = "all") -> list[dict]:
    """
    Check if user is subscribed to all required channels for the given group+feature.
    Returns list of unsubscribed channel dicts (empty means all OK).
    """
    rules = db_query(
        """
        SELECT * FROM subscription_rules
        WHERE gid = %s AND (feature = 'all' OR feature = %s)
        """,
        (gid, feature),
    )
    unsubscribed = []
    for rule in rules:
        try:
            member = await bot.get_chat_member(rule["channel_id"], uid)
            if member.status in ("left", "kicked", "banned"):
                unsubscribed.append(rule)
        except Exception:
            unsubscribed.append(rule)
    return unsubscribed


def build_subscription_keyboard(unsubscribed: list[dict]) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ch in unsubscribed:
        label = ch.get("channel_name") or ch["channel_id"]
        url = ch.get("channel_url") or f"https://t.me/{ch['channel_id'].lstrip('@')}"
        kb.button(text=f"📢 加入 {label}", url=url)
    kb.button(text="✅ 我已订阅，重新验证", callback_data="sub:recheck")
    kb.adjust(1)
    return kb.as_markup()


@router.message(Command("subscribe"))
async def cmd_subscribe(msg: types.Message, bot: Bot):
    gid = str(msg.chat.id) if msg.chat.type != "private" else "private"
    uid = msg.from_user.id

    if gid == "private":
        await msg.answer("ℹ️ 订阅验证仅在群组中有效")
        return

    unsubscribed = await check_subscriptions(bot, uid, gid)
    if not unsubscribed:
        await msg.reply("✅ 您已订阅所有必需频道，可使用全部功能！")
        return

    await msg.reply(
        f"📋 使用本群功能需先订阅以下 {len(unsubscribed)} 个频道：",
        reply_markup=build_subscription_keyboard(unsubscribed),
    )


@router.callback_query(lambda c: c.data == "sub:recheck")
async def on_recheck(callback: types.CallbackQuery, bot: Bot):
    await callback.answer("正在验证...")
    gid = str(callback.message.chat.id)
    uid = callback.from_user.id

    unsubscribed = await check_subscriptions(bot, uid, gid)
    if not unsubscribed:
        await callback.message.edit_text("✅ 验证通过！您已订阅所有必需频道，可使用全部功能。")
    else:
        await callback.message.edit_reply_markup(
            reply_markup=build_subscription_keyboard(unsubscribed)
        )
        await callback.answer(f"还有 {len(unsubscribed)} 个频道未订阅", show_alert=True)
