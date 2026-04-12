import random
from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from db import db_exec, db_query_one, db_query

router = Router()

SPAM_KEYWORDS = ["广告", "代理", "招募", "加微", "加V", "赚钱", "兼职", "http://", "https://t.me/joinchat"]


def _get_spam_keywords(gid: str) -> list[str]:
    row = db_query_one("SELECT value FROM settings WHERE gid=%s AND key='spam_keywords'", (gid,))
    if row and row["value"]:
        custom = [k.strip() for k in row["value"].split(",") if k.strip()]
        return SPAM_KEYWORDS + custom
    return SPAM_KEYWORDS


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


def _make_challenge() -> tuple[str, str]:
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    return f"{a} + {b} = ?", str(a + b)


@router.chat_member()
async def on_new_member(event: types.ChatMemberUpdated, bot: Bot):
    if event.new_chat_member.status not in ("member", "restricted"):
        return
    if event.old_chat_member.status in ("member", "administrator", "creator"):
        return

    uid = event.new_chat_member.user.id
    gid = str(event.chat.id)
    name = event.new_chat_member.user.full_name

    row = db_query_one("SELECT value FROM settings WHERE gid=%s AND key='verify_enabled'", (gid,))
    if not row or row["value"] != "1":
        row_welcome = db_query_one("SELECT value FROM settings WHERE gid=%s AND key='welcome_msg'", (gid,))
        if row_welcome and row_welcome["value"]:
            await bot.send_message(event.chat.id, row_welcome["value"].replace("{name}", name))
        return

    challenge, answer = _make_challenge()

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    for opt in sorted({int(answer), random.randint(1, 18), random.randint(1, 18)}):
        kb.button(text=str(opt), callback_data=f"verify:{uid}:{opt}:{answer}")
    kb.adjust(3)

    try:
        await bot.restrict_chat_member(
            event.chat.id, uid,
            permissions=types.ChatPermissions(can_send_messages=False),
        )
    except Exception:
        pass

    sent = await bot.send_message(
        event.chat.id,
        f"👋 欢迎 <b>{name}</b>！\n\n请在 5 分钟内完成验证：\n<b>{challenge}</b>",
    )

    db_exec(
        """
        INSERT INTO verifications (uid, gid, challenge, answer, message_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (uid, gid, challenge, answer, sent.message_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("verify:"))
async def on_verify(callback: types.CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    _, target_uid, chosen, correct = parts

    if callback.from_user.id != int(target_uid):
        await callback.answer("这不是你的验证题！", show_alert=True)
        return

    await callback.answer()
    gid = str(callback.message.chat.id)

    if chosen == correct:
        try:
            await bot.restrict_chat_member(
                callback.message.chat.id,
                int(target_uid),
                permissions=types.ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
        except Exception:
            pass

        db_exec(
            "UPDATE verifications SET status='passed' WHERE uid=%s AND gid=%s AND status='pending'",
            (int(target_uid), gid),
        )

        row_welcome = db_query_one("SELECT value FROM settings WHERE gid=%s AND key='welcome_msg'", (gid,))
        welcome = row_welcome["value"] if row_welcome and row_welcome["value"] else "🎉 验证成功，欢迎加入！"
        await callback.message.edit_text(welcome.replace("{name}", callback.from_user.full_name))
    else:
        await callback.message.edit_text("❌ 验证失败，请联系管理员。")
        db_exec(
            "UPDATE verifications SET status='failed' WHERE uid=%s AND gid=%s AND status='pending'",
            (int(target_uid), gid),
        )


@router.message(F.text)
async def on_message(msg: types.Message, bot: Bot):
    if msg.chat.type == "private":
        return
    if not msg.text:
        return

    uid = msg.from_user.id
    gid = str(msg.chat.id)

    if await _is_admin(bot, msg.chat.id, uid):
        return

    keywords = _get_spam_keywords(gid)
    text_lower = msg.text.lower()
    if any(kw.lower() in text_lower for kw in keywords):
        try:
            await msg.delete()
            await bot.restrict_chat_member(
                msg.chat.id, uid,
                permissions=types.ChatPermissions(can_send_messages=False),
            )
            db_exec(
                "INSERT INTO violations (uid, gid, violation_type, details) VALUES (%s, %s, %s, %s)",
                (uid, gid, "spam", msg.text[:200]),
            )
            await bot.send_message(
                msg.chat.id,
                f"⚠️ 用户 <a href='tg://user?id={uid}'>{msg.from_user.full_name}</a> 因发送违规内容已被禁言。",
            )
        except Exception:
            pass


@router.message(Command("welcome"))
async def cmd_welcome(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/welcome 欢迎消息内容（可用 {name} 代替用户名）")
        return
    gid = str(msg.chat.id)
    db_exec(
        "INSERT INTO settings (gid, key, value) VALUES (%s, 'welcome_msg', %s) ON CONFLICT (gid, key) DO UPDATE SET value=EXCLUDED.value",
        (gid, parts[1]),
    )
    await msg.reply("✅ 欢迎消息已保存")


@router.message(Command("mute"))
async def cmd_mute(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    if not msg.reply_to_message:
        await msg.reply("请回复要禁言的用户消息")
        return
    target = msg.reply_to_message.from_user.id
    try:
        await bot.restrict_chat_member(
            msg.chat.id, target,
            permissions=types.ChatPermissions(can_send_messages=False),
        )
        await msg.reply(f"🔇 已禁言 {msg.reply_to_message.from_user.full_name}")
    except Exception as e:
        await msg.reply(f"操作失败: {e}")


@router.message(Command("kick"))
async def cmd_kick(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    if not msg.reply_to_message:
        await msg.reply("请回复要踢出的用户消息")
        return
    target = msg.reply_to_message.from_user.id
    try:
        await bot.ban_chat_member(msg.chat.id, target)
        await bot.unban_chat_member(msg.chat.id, target)
        await msg.reply(f"👢 已踢出 {msg.reply_to_message.from_user.full_name}")
    except Exception as e:
        await msg.reply(f"操作失败: {e}")


@router.message(Command("settings"))
async def cmd_settings(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    gid = str(msg.chat.id)
    rows = db_query("SELECT key, value FROM settings WHERE gid=%s", (gid,))
    conf = "\n".join(f"• {r['key']}: <code>{r['value']}</code>" for r in rows) or "（无配置）"
    await msg.reply(f"<b>当前群组设置</b>\n\n{conf}")
