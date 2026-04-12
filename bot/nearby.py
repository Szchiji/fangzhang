from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from db import db_query, db_query_one

router = Router()

ACTIVE_FILTER = "cu.status = 'active' AND (cu.valid_until IS NULL OR cu.valid_until > NOW())"


class NearbyStates(StatesGroup):
    waiting_city = State()


@router.message(Command("nearby"))
async def cmd_nearby(msg: types.Message, state: FSMContext):
    parts = msg.text.split(None, 1)
    if len(parts) >= 2:
        city = parts[1].strip()
        await _show_nearby(msg, city)
    else:
        await msg.answer("📍 请输入要查找的城市或地区名称：")
        await state.set_state(NearbyStates.waiting_city)


@router.message(NearbyStates.waiting_city)
async def on_city_input(msg: types.Message, state: FSMContext):
    await state.clear()
    city = msg.text.strip()
    if not city:
        await msg.answer("❌ 城市名称不能为空")
        return
    await _show_nearby(msg, city)


async def _show_nearby(msg: types.Message, city: str):
    query_str = f"%{city}%"

    # Online users in city first
    online_rows = db_query(
        f"""
        SELECT cu.id, cu.display_name, cu.trust_score, cu.level, cu.region, cu.city,
               cu.tags, cu.category, TRUE as is_online
        FROM certified_users cu
        JOIN online_status os ON os.certified_user_id = cu.id
        WHERE {ACTIVE_FILTER}
          AND os.expires_at > NOW()
          AND (cu.city ILIKE %s OR cu.region ILIKE %s)
        ORDER BY cu.trust_score DESC
        LIMIT 5
        """,
        (query_str, query_str),
    )

    # Offline users in city
    offline_rows = db_query(
        f"""
        SELECT cu.id, cu.display_name, cu.trust_score, cu.level, cu.region, cu.city,
               cu.tags, cu.category, FALSE as is_online
        FROM certified_users cu
        LEFT JOIN online_status os ON os.certified_user_id = cu.id AND os.expires_at > NOW()
        WHERE {ACTIVE_FILTER}
          AND os.uid IS NULL
          AND (cu.city ILIKE %s OR cu.region ILIKE %s)
        ORDER BY cu.trust_score DESC
        LIMIT 10
        """,
        (query_str, query_str),
    )

    rows = list(online_rows) + list(offline_rows)

    if not rows:
        # Offer nearby region suggestions
        suggestions = db_query(
            f"""
            SELECT DISTINCT city, region FROM certified_users
            WHERE {ACTIVE_FILTER.replace('cu.', '')}
              AND city IS NOT NULL
            LIMIT 8
            """
        )
        suggestion_text = ""
        if suggestions:
            cities = [s["city"] or s["region"] for s in suggestions if s.get("city") or s.get("region")]
            suggestion_text = f"\n\n💡 当前有用户的城市：{' · '.join(cities[:8])}"
        await msg.answer(f"📍 未找到「{city}」的认证用户{suggestion_text}")
        return

    text = f"<b>📍 {city} 附近推荐</b>（共 {len(rows)} 人）\n\n"

    for u in rows:
        online_badge = "🟢" if u.get("is_online") else "⚫"
        stars = "⭐" if u.get("trust_score", 0) >= 8 else ""
        region_text = u.get("city") or u.get("region") or ""
        tags = " ".join(f"#{t}" for t in (u.get("tags") or [])[:3])
        text += (
            f"{online_badge} <b>{u['display_name']}</b> {stars}\n"
            f"   {region_text} | 信任分 {u.get('trust_score', 0):.1f} {tags}\n"
            f"   /user_{u['id']}\n\n"
        )

    # Smart combination recommendations
    if len(online_rows) > 0 and len(offline_rows) > 0:
        top = online_rows[0]
        text += (
            f"✨ <b>今日推荐</b>：<b>{top['display_name']}</b> 当前在线，信任分 {top.get('trust_score', 0):.1f} ⭐\n"
        )

    await msg.answer(text)
