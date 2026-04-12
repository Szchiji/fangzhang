from datetime import date
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db import db_exec, db_query, db_query_one

router = Router()


def _award_points(uid: int, amount: int, reason: str):
    user = db_query_one("SELECT points FROM users WHERE uid = %s", (uid,))
    if not user:
        return
    new_balance = (user["points"] or 0) + amount
    db_exec("UPDATE users SET points = %s WHERE uid = %s", (new_balance, uid))
    db_exec(
        "INSERT INTO points_transactions (uid, amount, reason, balance_after) VALUES (%s, %s, %s, %s)",
        (uid, amount, reason, new_balance),
    )
    return new_balance


@router.message(Command("checkin"))
async def cmd_checkin(msg: types.Message):
    uid = msg.from_user.id
    today = date.today()

    existing = db_query_one(
        "SELECT * FROM checkins WHERE uid = %s AND checkin_date = %s",
        (uid, today),
    )
    if existing:
        await msg.answer(
            f"✅ 您今天已经签到过了！\n"
            f"当前连续签到: <b>{existing['streak']}</b> 天\n"
            "明天再来领取积分吧 😊"
        )
        return

    # Calculate streak
    yesterday = db_query_one(
        "SELECT streak FROM checkins WHERE uid = %s AND checkin_date = %s",
        (uid, date.fromordinal(today.toordinal() - 1)),
    )
    streak = (yesterday["streak"] + 1) if yesterday else 1

    # Bonus for streaks
    base_points = 10
    bonus = 0
    if streak >= 7:
        bonus = 10
    elif streak >= 3:
        bonus = 5
    total_points = base_points + bonus

    db_exec(
        "INSERT INTO checkins (uid, checkin_date, points_earned, streak) VALUES (%s, %s, %s, %s)",
        (uid, today, total_points, streak),
    )

    new_balance = _award_points(uid, total_points, "daily_checkin")

    # Update certified user activity score if linked
    cert = db_query_one(
        "SELECT id FROM certified_users WHERE uid = %s AND status = 'active'",
        (uid,),
    )
    if cert:
        db_exec(
            "UPDATE certified_users SET activity_score = activity_score + 1 WHERE id = %s",
            (cert["id"],),
        )
        # Update online status
        db_exec(
            """
            INSERT INTO online_status (uid, certified_user_id)
            VALUES (%s, %s)
            ON CONFLICT (uid) DO UPDATE SET last_seen = NOW(), expires_at = NOW() + INTERVAL '8 hours'
            """,
            (uid, cert["id"]),
        )

    # Mark task completed
    existing_task = db_query_one(
        "SELECT 1 FROM user_tasks WHERE uid = %s AND task_key = 'daily_checkin' AND completed_at::date = %s",
        (uid, today),
    )
    if not existing_task:
        db_exec(
            "INSERT INTO user_tasks (uid, task_key, points_earned) VALUES (%s, 'daily_checkin', %s)",
            (uid, total_points),
        )

    bonus_text = f"（含 {bonus} 连签奖励）" if bonus else ""
    balance_text = f"\n💰 当前积分余额: <b>{new_balance}</b>" if new_balance is not None else ""

    await msg.answer(
        f"🎉 签到成功！\n\n"
        f"📅 连续签到: <b>{streak}</b> 天\n"
        f"💎 获得积分: <b>+{total_points}</b> {bonus_text}"
        f"{balance_text}"
    )


@router.message(Command("online"))
async def cmd_online(msg: types.Message):
    rows = db_query(
        """
        SELECT cu.id, cu.display_name, cu.trust_score, cu.level, cu.region, cu.city,
               os.last_seen
        FROM online_status os
        JOIN certified_users cu ON cu.id = os.certified_user_id
        WHERE os.expires_at > NOW()
          AND cu.status = 'active'
          AND (cu.valid_until IS NULL OR cu.valid_until > NOW())
        ORDER BY cu.trust_score DESC
        LIMIT 20
        """,
    )

    if not rows:
        await msg.answer("📭 当前暂无在线认证用户")
        return

    text = f"<b>🟢 今日在线认证用户</b>（{len(rows)} 人）\n\n"
    for i, u in enumerate(rows, 1):
        region = f" · {u['city'] or u['region']}" if (u.get("city") or u.get("region")) else ""
        text += f"{i}. {u['display_name']}{region} — 信任分 {u['trust_score']:.1f}\n"

    await msg.answer(text)


@router.message(Command("ranking"))
async def cmd_ranking(msg: types.Message):
    rows = db_query(
        """
        SELECT uid, MAX(streak) as max_streak, COUNT(*) as total_days
        FROM checkins
        GROUP BY uid
        ORDER BY max_streak DESC, total_days DESC
        LIMIT 10
        """
    )

    if not rows:
        await msg.answer("📊 暂无签到记录")
        return

    text = "<b>🏆 签到排行榜</b>（最高连续签到）\n\n"
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 10
    for i, r in enumerate(rows):
        u = db_query_one("SELECT full_name, username FROM users WHERE uid = %s", (r["uid"],))
        name = (u["full_name"] if u else None) or str(r["uid"])
        text += f"{medals[i]} {name} — 连续 {r['max_streak']} 天，共 {r['total_days']} 天\n"

    await msg.answer(text)
