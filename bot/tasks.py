from datetime import date
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db import db_exec, db_query, db_query_one

router = Router()


def _get_task_status(uid: int, task_key: str, frequency: str) -> bool:
    """Return True if task already completed for the current period."""
    if frequency == "daily":
        row = db_query_one(
            "SELECT 1 FROM user_tasks WHERE uid=%s AND task_key=%s AND completed_at::date=%s",
            (uid, task_key, date.today()),
        )
    elif frequency == "weekly":
        row = db_query_one(
            "SELECT 1 FROM user_tasks WHERE uid=%s AND task_key=%s AND completed_at > NOW() - INTERVAL '7 days'",
            (uid, task_key),
        )
    else:  # once
        row = db_query_one(
            "SELECT 1 FROM user_tasks WHERE uid=%s AND task_key=%s",
            (uid, task_key),
        )
    return bool(row)


@router.message(Command("tasks"))
async def cmd_tasks(msg: types.Message):
    uid = msg.from_user.id
    tasks = db_query("SELECT * FROM task_definitions WHERE is_active = TRUE ORDER BY id")

    if not tasks:
        await msg.answer("📭 暂无可用任务")
        return

    user = db_query_one("SELECT points FROM users WHERE uid = %s", (uid,))
    points_bal = user["points"] if user else 0

    text = f"<b>📋 今日任务</b>  💰 余额: <b>{points_bal}</b> 积分\n\n"
    kb = InlineKeyboardBuilder()

    for t in tasks:
        done = _get_task_status(uid, t["task_key"], t["frequency"])
        status_icon = "✅" if done else "⭕"
        freq_text = {"daily": "每日", "weekly": "每周", "once": "一次性"}.get(t["frequency"], "")
        text += (
            f"{status_icon} <b>{t['title']}</b> [{freq_text}]\n"
            f"   {t['description']}\n"
            f"   奖励: +{t['points']} 积分\n\n"
        )
        if not done:
            kb.button(text=f"🎯 {t['title']} (+{t['points']})", callback_data=f"task:do:{t['task_key']}")

    kb.adjust(1)
    await msg.answer(text, reply_markup=kb.as_markup() if kb.buttons else None)


@router.callback_query(lambda c: c.data and c.data.startswith("task:do:"))
async def on_task_do(callback: types.CallbackQuery):
    task_key = callback.data.split(":")[2]
    uid = callback.from_user.id

    task = db_query_one("SELECT * FROM task_definitions WHERE task_key = %s AND is_active = TRUE", (task_key,))
    if not task:
        await callback.answer("任务不存在", show_alert=True)
        return

    if _get_task_status(uid, task_key, task["frequency"]):
        await callback.answer("该任务今日已完成", show_alert=True)
        return

    await callback.answer()

    # Route to proper handler
    if task_key == "daily_checkin":
        await callback.message.answer("请使用 /checkin 完成签到任务")
    elif task_key == "rate_user":
        await callback.message.answer("请使用 /rate [用户ID] 评价一位认证用户")
    elif task_key == "share_bot":
        await _complete_task(uid, task)
        await callback.message.answer(
            f"✅ 分享任务完成！获得 +{task['points']} 积分\n\n"
            "🔗 分享链接已生成，请分享给您的好友"
        )
    elif task_key == "invite_user":
        await callback.message.answer("邀请好友注册后，系统将自动为您发放积分")
    else:
        await _complete_task(uid, task)
        await callback.message.answer(f"✅ 任务完成！获得 +{task['points']} 积分")


async def _complete_task(uid: int, task: dict):
    user = db_query_one("SELECT points FROM users WHERE uid = %s", (uid,))
    if not user:
        return
    new_balance = (user["points"] or 0) + task["points"]
    db_exec("UPDATE users SET points = %s WHERE uid = %s", (new_balance, uid))
    db_exec(
        "INSERT INTO points_transactions (uid, amount, reason, balance_after) VALUES (%s, %s, %s, %s)",
        (uid, task["points"], task["task_key"], new_balance),
    )
    db_exec(
        "INSERT INTO user_tasks (uid, task_key, points_earned) VALUES (%s, %s, %s)",
        (uid, task["task_key"], task["points"]),
    )


@router.message(Command("points"))
async def cmd_points(msg: types.Message):
    uid = msg.from_user.id
    user = db_query_one("SELECT points, membership_level FROM users WHERE uid = %s", (uid,))

    if not user:
        await msg.answer("❌ 未找到您的账户，请先发送 /start")
        return

    history = db_query(
        "SELECT amount, reason, created_at FROM points_transactions WHERE uid = %s ORDER BY created_at DESC LIMIT 5",
        (uid,),
    )

    level_icons = {"free": "🆓", "silver": "🥈", "gold": "🥇", "vip": "💎"}
    level = user["membership_level"] or "free"
    text = (
        f"<b>💰 我的积分</b>\n\n"
        f"余额: <b>{user['points']}</b> 积分\n"
        f"会员等级: {level_icons.get(level, '🆓')} {level.upper()}\n\n"
    )

    if history:
        text += "<b>最近记录：</b>\n"
        for h in history:
            sign = "+" if h["amount"] > 0 else ""
            reason_map = {
                "daily_checkin": "每日签到",
                "rate_user": "评价用户",
                "share_bot": "分享机器人",
            }
            reason = reason_map.get(h["reason"], h["reason"])
            text += f"  {sign}{h['amount']} — {reason}\n"

    # Membership upgrade info
    upgrades = {
        "free": ("silver", 100),
        "silver": ("gold", 500),
        "gold": ("vip", 2000),
    }
    if level in upgrades:
        next_level, needed = upgrades[level]
        text += f"\n升级到 {next_level.upper()} 需要 <b>{needed}</b> 积分"

    kb = InlineKeyboardBuilder()
    current_points = user["points"] or 0
    if level == "free" and current_points >= 100:
        kb.button(text="🥈 升级 Silver（100积分）", callback_data="upgrade:silver:100")
    elif level == "silver" and current_points >= 500:
        kb.button(text="🥇 升级 Gold（500积分）", callback_data="upgrade:gold:500")
    elif level == "gold" and current_points >= 2000:
        kb.button(text="💎 升级 VIP（2000积分）", callback_data="upgrade:vip:2000")

    await msg.answer(text, reply_markup=kb.as_markup() if kb.buttons else None)


@router.callback_query(lambda c: c.data and c.data.startswith("upgrade:"))
async def on_upgrade(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    target_level, cost = parts[1], int(parts[2])
    uid = callback.from_user.id

    user = db_query_one("SELECT points, membership_level FROM users WHERE uid = %s", (uid,))
    if not user or (user["points"] or 0) < cost:
        await callback.answer("积分不足", show_alert=True)
        return

    await callback.answer()
    new_balance = user["points"] - cost
    db_exec(
        "UPDATE users SET points = %s, membership_level = %s WHERE uid = %s",
        (new_balance, target_level, uid),
    )
    db_exec(
        "INSERT INTO points_transactions (uid, amount, reason, balance_after) VALUES (%s, %s, %s, %s)",
        (uid, -cost, f"upgrade_to_{target_level}", new_balance),
    )

    await callback.message.edit_text(
        f"🎉 恭喜升级到 <b>{target_level.upper()}</b>！\n"
        f"消耗 {cost} 积分，余额 {new_balance} 积分"
    )
