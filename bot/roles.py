"""
Centralized role detection and status label helpers for CheBot.

Usage:
    from bot.roles import detect_role, cert_status_label, subscription_status_text
"""
import os
from aiogram import Bot, types
from db import db_query_one

ADMIN_IDS: list[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]


async def detect_role(bot: Bot, uid: int, chat_id: int, chat_type: str) -> dict:
    """
    Detect the roles of a Telegram user.

    Returns a dict with:
        is_admin       bool   — global admin (env) or group creator/admin
        is_certified   bool   — has an active, non-expired certified_users record
        cert           dict|None — the certified_users row (with expiring_soon flag)
        uid            int
    """
    is_admin = uid in ADMIN_IDS
    if not is_admin and chat_type != "private":
        try:
            member = await bot.get_chat_member(chat_id, uid)
            is_admin = member.status in ("creator", "administrator")
        except Exception:
            pass

    cert = db_query_one(
        """
        SELECT id, display_name, valid_until, status,
               trust_score, level, activity_score,
               (
                   valid_until IS NOT NULL
                   AND valid_until < NOW() + INTERVAL '7 days'
                   AND valid_until > NOW()
               ) AS expiring_soon
        FROM certified_users
        WHERE uid = %s
          AND status = 'active'
          AND (valid_until IS NULL OR valid_until > NOW())
        """,
        (uid,),
    )

    return {
        "is_admin": is_admin,
        "is_certified": bool(cert),
        "cert": cert,
        "uid": uid,
    }


def cert_status_label(cert: dict | None) -> str:
    """Return a short human-readable certification status badge."""
    if cert is None:
        return "❌ 未认证"
    if cert.get("expiring_soon"):
        return "⚠️ 认证即将到期"
    return "✅ 认证有效"


def cert_expiry_text(cert: dict | None) -> str:
    """Return expiry information string for a certified user."""
    if cert is None:
        return ""
    if cert.get("valid_until") is None:
        return "永久有效"
    vu = cert["valid_until"]
    # vu may be a date or datetime
    from datetime import date, datetime
    if isinstance(vu, datetime):
        vu_date = vu.date()
    else:
        vu_date = vu
    delta = (vu_date - date.today()).days
    if delta <= 0:
        return f"⛔ 已过期（{vu_date}）"
    if delta <= 7:
        return f"⚠️ 还剩 {delta} 天到期（{vu_date}）"
    return f"📅 有效期至 {vu_date}（剩余 {delta} 天）"


def subscription_status_text(unsubscribed: list[dict]) -> str:
    """Return a human-readable subscription gate message."""
    if not unsubscribed:
        return "✅ 已订阅所有必需频道"
    names = "、".join(
        ch.get("channel_name") or ch["channel_id"] for ch in unsubscribed
    )
    return f"🔒 尚未订阅：{names}"
