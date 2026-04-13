import os
from datetime import date
from aiogram import Router, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from db import db_exec, db_query, db_query_one

router = Router()

DEFAULT_CHANNEL_ID = os.getenv("PUBLISH_CHANNEL_ID", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


def _get_global_setting(key: str, default: str = "") -> str:
    row = db_query_one("SELECT value FROM settings WHERE gid='global' AND key=%s", (key,))
    return row["value"] if row and row.get("value") else default


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _render_push_template(template: str, context: dict) -> str:
    return template.replace("\\n", "\n").format_map(_SafeFormatDict(context))


class AddUserStates(StatesGroup):
    display_name = State()
    category = State()
    region = State()
    city = State()
    bio = State()
    contact = State()
    level = State()
    valid_until = State()


@router.message(Command("adduser"))
async def cmd_adduser(msg: types.Message, state: FSMContext, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    await state.set_state(AddUserStates.display_name)
    await msg.answer("➕ 添加认证用户\n\n第 1 步：请输入显示名称")


@router.message(AddUserStates.display_name)
async def on_add_name(msg: types.Message, state: FSMContext):
    await state.update_data(display_name=msg.text.strip())
    await state.set_state(AddUserStates.category)
    await msg.answer("第 2 步：分类（如：美食、健身、摄影，或直接发送 . 跳过）")


@router.message(AddUserStates.category)
async def on_add_category(msg: types.Message, state: FSMContext):
    val = msg.text.strip()
    await state.update_data(category=val if val != "." else "general")
    await state.set_state(AddUserStates.region)
    await msg.answer("第 3 步：地区（省/市，或发送 . 跳过）")


@router.message(AddUserStates.region)
async def on_add_region(msg: types.Message, state: FSMContext):
    val = msg.text.strip()
    await state.update_data(region=val if val != "." else None)
    await state.set_state(AddUserStates.city)
    await msg.answer("第 4 步：城市（或发送 . 跳过）")


@router.message(AddUserStates.city)
async def on_add_city(msg: types.Message, state: FSMContext):
    val = msg.text.strip()
    await state.update_data(city=val if val != "." else None)
    await state.set_state(AddUserStates.bio)
    await msg.answer("第 5 步：简介（或发送 . 跳过）")


@router.message(AddUserStates.bio)
async def on_add_bio(msg: types.Message, state: FSMContext):
    val = msg.text.strip()
    await state.update_data(bio=val if val != "." else None)
    await state.set_state(AddUserStates.contact)
    await msg.answer("第 6 步：联系方式（或发送 . 跳过）")


@router.message(AddUserStates.contact)
async def on_add_contact(msg: types.Message, state: FSMContext):
    val = msg.text.strip()
    await state.update_data(contact=val if val != "." else None)
    await state.set_state(AddUserStates.level)
    await msg.answer("第 7 步：等级（1-5，默认 1）")


@router.message(AddUserStates.level)
async def on_add_level(msg: types.Message, state: FSMContext):
    try:
        level = max(1, min(5, int(msg.text.strip())))
    except ValueError:
        level = 1
    await state.update_data(level=level)
    await state.set_state(AddUserStates.valid_until)
    await msg.answer("第 8 步：有效期（YYYY-MM-DD，或发送 . 表示永久）")


@router.message(AddUserStates.valid_until)
async def on_add_valid_until(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    val = msg.text.strip()
    valid_until = None
    if val != ".":
        try:
            from dateutil.parser import parse as parse_date
            valid_until = parse_date(val).date()
        except Exception:
            await msg.answer("❌ 日期格式错误，已设为永久有效")

    db_exec(
        """
        INSERT INTO certified_users
          (display_name, category, region, city, bio, contact, level, valid_until, valid_from, added_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            data["display_name"], data["category"], data.get("region"),
            data.get("city"), data.get("bio"), data.get("contact"),
            data["level"], valid_until, date.today(), msg.from_user.id,
        ),
    )

    await msg.answer(
        f"✅ 认证用户 <b>{data['display_name']}</b> 添加成功！\n"
        f"使用 /list 查看所有认证用户"
    )


@router.message(Command("edituser"))
async def cmd_edituser(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/edituser [ID]")
        return
    try:
        cert_id = int(parts[1])
    except ValueError:
        await msg.reply("请提供有效 ID")
        return
    u = db_query_one("SELECT * FROM certified_users WHERE id = %s", (cert_id,))
    if not u:
        await msg.reply("❌ 用户不存在")
        return
    await msg.reply(
        f"编辑用户 ID:{cert_id} <b>{u['display_name']}</b>\n\n"
        "请通过 Web 管理后台编辑详细信息，或使用以下命令：\n"
        "/expire [ID] [日期] — 设置到期\n"
        "/freeze [ID] — 冻结用户\n"
        "/unfreeze [ID] — 解冻用户"
    )


@router.message(Command("deleteuser"))
async def cmd_deleteuser(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/deleteuser [ID]")
        return
    try:
        cert_id = int(parts[1])
    except ValueError:
        await msg.reply("请提供有效 ID")
        return
    u = db_query_one("SELECT display_name FROM certified_users WHERE id = %s", (cert_id,))
    if not u:
        await msg.reply("❌ 用户不存在")
        return
    db_exec("DELETE FROM certified_users WHERE id = %s", (cert_id,))
    db_exec(
        "INSERT INTO risk_logs (certified_user_id, action, performed_by) VALUES (%s, 'deleted', %s)",
        (cert_id, msg.from_user.id),
    )
    await msg.reply(f"🗑️ 认证用户 <b>{u['display_name']}</b> 已删除")


@router.message(Command("expire"))
async def cmd_expire(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.reply("用法：/expire [ID] [YYYY-MM-DD]")
        return
    try:
        cert_id = int(parts[1])
        from dateutil.parser import parse as parse_date
        exp_date = parse_date(parts[2]).date()
    except (ValueError, Exception):
        await msg.reply("参数格式错误")
        return
    db_exec(
        "UPDATE certified_users SET valid_until = %s, updated_at = NOW() WHERE id = %s",
        (exp_date, cert_id),
    )
    await msg.reply(f"📅 用户 #{cert_id} 到期日已设为 {exp_date}")


@router.message(Command("freeze"))
async def cmd_freeze(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/freeze [ID]")
        return
    cert_id = int(parts[1])
    db_exec("UPDATE certified_users SET status = 'frozen', updated_at = NOW() WHERE id = %s", (cert_id,))
    db_exec(
        "INSERT INTO risk_logs (certified_user_id, action, performed_by) VALUES (%s, 'frozen', %s)",
        (cert_id, msg.from_user.id),
    )
    await msg.reply(f"🔒 认证用户 #{cert_id} 已冻结")


@router.message(Command("unfreeze"))
async def cmd_unfreeze(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/unfreeze [ID]")
        return
    cert_id = int(parts[1])
    db_exec("UPDATE certified_users SET status = 'active', updated_at = NOW() WHERE id = %s", (cert_id,))
    db_exec(
        "INSERT INTO risk_logs (certified_user_id, action, performed_by) VALUES (%s, 'unfrozen', %s)",
        (cert_id, msg.from_user.id),
    )
    await msg.reply(f"🔓 认证用户 #{cert_id} 已解冻")


@router.message(Command("blacklist"))
async def cmd_blacklist(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/blacklist [UID]")
        return
    uid = int(parts[1])
    db_exec("UPDATE users SET risk_status = 'blacklisted' WHERE uid = %s", (uid,))
    db_exec("UPDATE certified_users SET status = 'blacklisted' WHERE uid = %s", (uid,))
    db_exec(
        "INSERT INTO risk_logs (uid, action, performed_by) VALUES (%s, 'blacklisted', %s)",
        (uid, msg.from_user.id),
    )
    await msg.reply(f"🚫 用户 {uid} 已加入黑名单")


@router.message(Command("whitelist"))
async def cmd_whitelist(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/whitelist [UID]")
        return
    uid = int(parts[1])
    db_exec("UPDATE users SET risk_status = 'whitelisted' WHERE uid = %s", (uid,))
    db_exec(
        "INSERT INTO risk_logs (uid, action, performed_by) VALUES (%s, 'whitelisted', %s)",
        (uid, msg.from_user.id),
    )
    await msg.reply(f"✅ 用户 {uid} 已加入白名单")


@router.message(Command("watch"))
async def cmd_watch(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/watch [UID]")
        return
    uid = int(parts[1])
    db_exec("UPDATE users SET risk_status = 'watchlist' WHERE uid = %s", (uid,))
    db_exec(
        "INSERT INTO risk_logs (uid, action, performed_by) VALUES (%s, 'watchlist', %s)",
        (uid, msg.from_user.id),
    )
    await msg.reply(f"👁️ 用户 {uid} 已加入监控名单")


@router.message(Command("dashboard"))
async def cmd_dashboard(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 此命令仅限管理员使用")
        return

    total_users = db_query_one("SELECT COUNT(*) as cnt FROM certified_users")
    active = db_query_one("SELECT COUNT(*) as cnt FROM certified_users WHERE status='active' AND (valid_until IS NULL OR valid_until > NOW())")
    expired = db_query_one("SELECT COUNT(*) as cnt FROM certified_users WHERE valid_until < NOW()")
    expiring_soon = db_query_one(
        """
        SELECT COUNT(*) as cnt FROM certified_users
        WHERE status='active' AND valid_until IS NOT NULL
          AND valid_until < NOW() + INTERVAL '7 days' AND valid_until > NOW()
        """
    )
    checkins_today = db_query_one("SELECT COUNT(*) as cnt FROM checkins WHERE checkin_date = CURRENT_DATE")
    pending_ratings = db_query_one("SELECT COUNT(*) as cnt FROM ratings WHERE status='pending'")
    pending_coupons = db_query_one("SELECT COUNT(*) as cnt FROM coupons WHERE status='pending'")
    new_users_today = db_query_one("SELECT COUNT(*) as cnt FROM users WHERE created_at::date = CURRENT_DATE")
    violations_today = db_query_one("SELECT COUNT(*) as cnt FROM violations WHERE created_at::date = CURRENT_DATE")

    ex = expiring_soon["cnt"] if expiring_soon else 0
    pr = pending_ratings["cnt"] if pending_ratings else 0
    pc = pending_coupons["cnt"] if pending_coupons else 0
    vt = violations_today["cnt"] if violations_today else 0

    alert_lines = []
    if ex > 0:
        alert_lines.append(f"⏰ {ex} 位用户认证即将到期（7天内）")
    if pr > 0:
        alert_lines.append(f"⭐ {pr} 条评价待审核")
    if pc > 0:
        alert_lines.append(f"🎫 {pc} 张优惠券待审核")
    if vt > 0:
        alert_lines.append(f"⚠️ 今日 {vt} 条违规记录")
    alert_block = "\n".join(f"  • {a}" for a in alert_lines) if alert_lines else "  ✅ 无待处理事项"

    base_url = _get_base_url()

    await msg.answer(
        "<b>📊 管理员数据看板</b>\n\n"
        "<b>👥 认证用户</b>\n"
        f"  总计：<b>{total_users['cnt']}</b>  活跃：<b>{active['cnt']}</b>  "
        f"已过期：<b>{expired['cnt']}</b>\n\n"
        "<b>📅 今日动态</b>\n"
        f"  新增用户：<b>{new_users_today['cnt']}</b>  签到：<b>{checkins_today['cnt']}</b>\n\n"
        "<b>🔔 需要处理</b>\n"
        f"{alert_block}\n\n"
        f"🖥️ <a href='{base_url}/dashboard'>打开完整管理后台</a>\n"
        f"👥 <a href='{base_url}/users'>用户管理</a>  "
        f"⚙️ <a href='{base_url}/settings'>全局配置</a>"
    )


@router.message(Command("push"))
async def cmd_push(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return
    parts = msg.text.split(None, 1)
    if len(parts) < 2:
        await msg.reply("用法：/push [认证用户ID]")
        return

    cert_id = int(parts[1])
    u = db_query_one(
        "SELECT * FROM certified_users WHERE id = %s AND status = 'active'",
        (cert_id,),
    )
    if not u:
        await msg.reply("❌ 用户不存在或已非活跃状态")
        return

    channel_id = _get_global_setting("publish_channel_id", DEFAULT_CHANNEL_ID)
    if not channel_id:
        await msg.reply("❌ 未配置发布频道（请在后台全局配置 publish_channel_id）")
        return

    profile_template = _get_global_setting(
        "profile_push_template",
        "✅ <b>{display_name}</b> {level_stars}\n{region_line}\n信任分: <b>{trust_score}</b>\n{bio}\n{tags}\n\n详情: /user_{certified_user_id}",
    )
    level_stars = "🔸" * int(u.get("level", 1))
    region_line = f"📍 {u['city'] or u['region']}" if (u.get("city") or u.get("region")) else ""
    tags = " ".join(f"#{t}" for t in (u.get("tags") or []))
    text = _render_push_template(
        profile_template,
        {
            "display_name": u.get("display_name", ""),
            "level_stars": level_stars,
            "region_line": region_line,
            "trust_score": float(u.get("trust_score", 0)),
            "bio": u.get("bio", ""),
            "tags": tags,
            "certified_user_id": cert_id,
            "region": u.get("region", ""),
            "city": u.get("city", ""),
            "contact": u.get("contact", ""),
        },
    )

    try:
        sent = await bot.send_message(channel_id, text)
        db_exec(
            "INSERT INTO channel_pushes (certified_user_id, channel_id, message_id) VALUES (%s, %s, %s)",
            (cert_id, channel_id, sent.message_id),
        )
        await msg.reply(f"✅ 已将 <b>{u['display_name']}</b> 推送到频道")
    except Exception as e:
        await msg.reply(f"❌ 推送失败: {e}")


@router.message(Command("stats"))
async def cmd_stats(msg: types.Message, bot: Bot):
    if not await _is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("❌ 仅管理员可用")
        return

    regions = db_query(
        """
        SELECT region, COUNT(*) as cnt FROM certified_users
        WHERE status = 'active' AND region IS NOT NULL
        GROUP BY region ORDER BY cnt DESC LIMIT 5
        """
    )
    region_text = "\n".join(f"  {r['region']}: {r['cnt']}" for r in regions) or "  暂无数据"

    violations = db_query_one("SELECT COUNT(*) as cnt FROM violations WHERE created_at > NOW() - INTERVAL '7 days'")

    await msg.answer(
        "<b>📈 群组统计</b>\n\n"
        "<b>热门地区：</b>\n" + region_text +
        f"\n\n<b>近 7 日违规记录：</b> {violations['cnt']} 条"
    )


def _get_base_url() -> str:
    raw = os.getenv("RAILWAY_STATIC_URL", "")
    if raw:
        return f"https://{raw.rstrip('/')}"
    return f"http://localhost:{os.getenv('PORT', '8080')}"
