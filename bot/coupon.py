import os
from datetime import date, timedelta
from aiogram import Router, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dateutil.parser import parse as parse_date
from db import db_exec, db_query_one

router = Router()

COUPON_COOLDOWN_DAYS = 7
DEFAULT_CHANNEL_ID = os.getenv("PUBLISH_CHANNEL_ID", "")


def _get_global_setting(key: str, default: str = "") -> str:
    row = db_query_one("SELECT value FROM settings WHERE gid='global' AND key=%s", (key,))
    return row["value"] if row and row.get("value") else default


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _render_push_template(template: str, context: dict) -> str:
    return template.format_map(_SafeFormatDict(context))


class CouponStates(StatesGroup):
    title = State()
    description = State()
    discount = State()
    valid_until = State()


@router.message(Command("coupon"))
async def cmd_coupon(msg: types.Message, state: FSMContext):
    uid = msg.from_user.id

    # Check if user is a certified user
    cert = db_query_one(
        "SELECT * FROM certified_users WHERE uid = %s AND status = 'active' AND (valid_until IS NULL OR valid_until > NOW())",
        (uid,),
    )
    if not cert:
        await msg.answer("❌ 只有认证用户才能发布优惠券\n请联系管理员申请认证")
        return

    # Check cooldown
    cooldown_cutoff = date.today() - timedelta(days=COUPON_COOLDOWN_DAYS)
    recent = db_query_one(
        "SELECT created_at FROM coupons WHERE certified_user_id = %s AND created_at::date > %s",
        (cert["id"], cooldown_cutoff),
    )
    if recent:
        next_date = (recent["created_at"].date() + timedelta(days=COUPON_COOLDOWN_DAYS))
        await msg.answer(
            f"⏳ 发布冷却中\n您可以在 <b>{next_date}</b> 后再次发布优惠券"
        )
        return

    await state.update_data(cert_id=cert["id"])
    await state.set_state(CouponStates.title)
    await msg.answer(
        "📣 开始发布优惠券\n\n"
        "第 1 步：请输入优惠券标题（例如：限时8折优惠）"
    )


@router.message(CouponStates.title)
async def on_coupon_title(msg: types.Message, state: FSMContext):
    await state.update_data(title=msg.text.strip())
    await state.set_state(CouponStates.description)
    await msg.answer("第 2 步：请输入优惠券描述（活动详情、使用条件等）")


@router.message(CouponStates.description)
async def on_coupon_description(msg: types.Message, state: FSMContext):
    await state.update_data(description=msg.text.strip())
    await state.set_state(CouponStates.discount)
    await msg.answer("第 3 步：请输入折扣内容（例如：8折、立减50元、买一送一）")


@router.message(CouponStates.discount)
async def on_coupon_discount(msg: types.Message, state: FSMContext):
    await state.update_data(discount=msg.text.strip())
    await state.set_state(CouponStates.valid_until)
    default_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
    await msg.answer(f"第 4 步：请输入有效期（格式：YYYY-MM-DD，默认 {default_date}）\n发送 . 使用默认值")


@router.message(CouponStates.valid_until)
async def on_coupon_valid_until(msg: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()

    text = msg.text.strip()
    if text == ".":
        valid_until = date.today() + timedelta(days=30)
    else:
        try:
            valid_until = parse_date(text).date()
        except Exception:
            await msg.answer("❌ 日期格式错误，请使用 YYYY-MM-DD 格式")
            return

    if valid_until <= date.today():
        await msg.answer("❌ 有效期必须是未来日期")
        return

    cert_id = data["cert_id"]

    # Check if direct publish or needs approval
    auto_publish = db_query_one("SELECT value FROM settings WHERE gid='global' AND key='auto_approve_coupons'")
    status = "approved" if (auto_publish and auto_publish["value"] == "1") else "pending"

    db_exec(
        """
        INSERT INTO coupons (certified_user_id, uid, title, description, discount, valid_until, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (cert_id, msg.from_user.id, data["title"], data["description"], data["discount"], valid_until, status),
    )

    channel_id = _get_global_setting("publish_channel_id", DEFAULT_CHANNEL_ID)
    if status == "approved" and channel_id:
        await _publish_coupon_to_channel(bot, cert_id, data, valid_until, msg.from_user.id)
        await msg.answer("✅ 优惠券已发布到频道！")
    else:
        await msg.answer(
            "✅ 优惠券已提交审核！\n\n"
            f"📋 标题: {data['title']}\n"
            f"💰 折扣: {data['discount']}\n"
            f"📅 有效期: {valid_until}\n\n"
            "审核通过后将自动发布到频道"
        )


async def _publish_coupon_to_channel(bot: Bot, cert_id: int, data: dict, valid_until, uid: int):
    channel_id = _get_global_setting("publish_channel_id", DEFAULT_CHANNEL_ID)
    if not channel_id:
        return
    cu = db_query_one("SELECT display_name FROM certified_users WHERE id = %s", (cert_id,))
    name = cu["display_name"] if cu else "认证用户"
    coupon_template = _get_global_setting(
        "coupon_push_template",
        "🎫 <b>优惠券</b>\n\n👤 发布者: {display_name}\n📌 {title}\n📝 {description}\n💰 折扣: {discount}\n📅 有效期至: {valid_until}\n\n详情: /user_{certified_user_id}",
    )
    text = _render_push_template(
        coupon_template,
        {
            "display_name": name,
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "discount": data.get("discount", ""),
            "valid_until": valid_until,
            "certified_user_id": cert_id,
        },
    )
    try:
        sent = await bot.send_message(channel_id, text)
        db_exec(
            "UPDATE coupons SET published_at = NOW(), channel_id = %s, message_id = %s WHERE certified_user_id = %s AND uid = %s ORDER BY id DESC LIMIT 1",
            (channel_id, sent.message_id, cert_id, uid),
        )
    except Exception as e:
        import logging
        logging.error(f"Failed to publish coupon: {e}")
