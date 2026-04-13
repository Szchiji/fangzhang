import os
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot
from db import db_exec, db_query, db_query_one

DEFAULT_CHANNEL_ID = os.getenv("PUBLISH_CHANNEL_ID", "")
GLOBAL_SETTINGS_GROUP = {"gid": "global", "gname": "🌐 全局配置"}


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _get_global_setting(key: str, default: str = "") -> str:
    row = db_query_one("SELECT value FROM settings WHERE gid='global' AND key=%s", (key,))
    return row["value"] if row and row.get("value") else default


def _render_push_template(template: str, context: dict) -> str:
    return template.format_map(_SafeFormatDict(context))


def setup_routes(app: FastAPI, bot: Bot, templates: Jinja2Templates):

    @app.get("/", response_class=RedirectResponse)
    async def root():
        return RedirectResponse("/dashboard")

    @app.get("/dashboard", response_class=HTMLResponse)
    async def page_dashboard(request: Request):
        stats = _get_stats()
        recent_users = db_query(
            "SELECT id, display_name, status, trust_score, created_at FROM certified_users ORDER BY created_at DESC LIMIT 10"
        )
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"stats": stats, "recent_users": recent_users},
        )

    @app.get("/users", response_class=HTMLResponse)
    async def page_users(request: Request, status: str = "", region: str = "", level: str = ""):
        from datetime import date as _date
        conditions = []
        params: list = []
        if status:
            conditions.append("status = %s")
            params.append(status)
        if region:
            conditions.append("(region ILIKE %s OR city ILIKE %s)")
            params.extend([f"%{region}%", f"%{region}%"])
        if level and level.isdigit():
            conditions.append("level = %s")
            params.append(int(level))
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        users = db_query(
            f"SELECT * FROM certified_users {where} ORDER BY created_at DESC",
            params,
        )
        return templates.TemplateResponse(
            request,
            "users.html",
            {
                "users": users,
                "today": _date.today(),
                "filter_status": status,
                "filter_region": region,
                "filter_level": level,
            },
        )

    @app.get("/user/new", response_class=HTMLResponse)
    async def page_user_new(request: Request):
        return templates.TemplateResponse(request, "user_form.html", {"user": None})

    @app.get("/user/{cert_id}/edit", response_class=HTMLResponse)
    async def page_user_edit(request: Request, cert_id: int):
        u = db_query_one("SELECT * FROM certified_users WHERE id = %s", (cert_id,))
        if not u:
            raise HTTPException(404, "User not found")
        return templates.TemplateResponse(request, "user_form.html", {"user": u})

    @app.get("/settings", response_class=HTMLResponse)
    async def page_settings(request: Request, gid: str = ""):
        groups = [GLOBAL_SETTINGS_GROUP] + db_query("SELECT * FROM groups ORDER BY created_at DESC")
        conf = {}
        sub_rules = []
        if gid:
            rows = db_query("SELECT key, value FROM settings WHERE gid=%s", (gid,))
            conf = {r["key"]: r["value"] for r in rows}
            sub_rules = db_query("SELECT * FROM subscription_rules WHERE gid=%s ORDER BY id", (gid,))
        return templates.TemplateResponse(
            request,
            "settings.html",
            {"groups": groups, "gid": gid, "conf": conf, "sub_rules": sub_rules},
        )

    @app.get("/manage", response_class=HTMLResponse)
    async def page_manage(request: Request, gid: str = "", tab: str = "users"):
        rows = db_query("SELECT key, value FROM settings WHERE gid=%s", (gid,)) if gid else []
        conf = {r["key"]: r["value"] for r in rows}
        return templates.TemplateResponse(
            request,
            "settings.html",
            {"gid": gid, "tab": tab, "conf": conf, "groups": [], "sub_rules": []},
        )

    @app.get("/ratings", response_class=HTMLResponse)
    async def page_ratings(request: Request):
        ratings = db_query(
            """
            SELECT r.*, cu.display_name as user_name, u.full_name as rater_name
            FROM ratings r
            LEFT JOIN certified_users cu ON cu.id = r.certified_user_id
            LEFT JOIN users u ON u.uid = r.rater_uid
            ORDER BY r.created_at DESC
            LIMIT 100
            """
        )
        return templates.TemplateResponse(request, "ratings.html", {"ratings": ratings})

    @app.get("/coupons", response_class=HTMLResponse)
    async def page_coupons(request: Request):
        coupons = db_query(
            """
            SELECT c.*, cu.display_name as user_name
            FROM coupons c
            LEFT JOIN certified_users cu ON cu.id = c.certified_user_id
            ORDER BY c.created_at DESC
            LIMIT 100
            """
        )
        return templates.TemplateResponse(request, "coupons.html", {"coupons": coupons})

    @app.get("/risk", response_class=HTMLResponse)
    async def page_risk(request: Request):
        logs = db_query("SELECT * FROM risk_logs ORDER BY created_at DESC LIMIT 100")
        blacklisted = db_query("SELECT * FROM users WHERE risk_status = 'blacklisted' ORDER BY uid")
        watchlist = db_query("SELECT * FROM users WHERE risk_status = 'watchlist' ORDER BY uid")
        return templates.TemplateResponse(
            request,
            "risk.html",
            {"logs": logs, "blacklisted": blacklisted, "watchlist": watchlist},
        )

    # ── Existing API route preserved ──────────────────────────────────────────
    @app.post("/api/set")
    async def api_set(gid: str = Form(...), key: str = Form(...), value: str = Form(None)):
        if value is None or value.strip() == "":
            db_exec("DELETE FROM settings WHERE gid=%s AND key=%s", (gid, key))
        else:
            db_exec(
                "INSERT INTO settings (gid, key, value) VALUES (%s, %s, %s) ON CONFLICT (gid, key) DO UPDATE SET value=EXCLUDED.value",
                (gid, key, value),
            )
        return {"status": "ok"}

    # ── Certified Users API ────────────────────────────────────────────────────
    @app.get("/api/users")
    async def api_users(status: str = "", region: str = ""):
        conditions = []
        params: list = []
        if status:
            conditions.append("status = %s")
            params.append(status)
        if region:
            conditions.append("(region ILIKE %s OR city ILIKE %s)")
            params.extend([f"%{region}%", f"%{region}%"])
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        rows = db_query(f"SELECT * FROM certified_users {where} ORDER BY id DESC", params)
        return JSONResponse([dict(r) for r in rows])

    @app.post("/api/users")
    async def api_add_user(request: Request):
        body = await request.json()
        db_exec(
            """
            INSERT INTO certified_users
              (display_name, username, category, tags, level, region, city, bio, contact, valid_from, valid_until, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')
            """,
            (
                body.get("display_name"), body.get("username"), body.get("category", "general"),
                body.get("tags", []), body.get("level", 1), body.get("region"),
                body.get("city"), body.get("bio"), body.get("contact"),
                body.get("valid_from"), body.get("valid_until"),
            ),
        )
        return {"status": "ok"}

    @app.put("/api/users/{cert_id}")
    async def api_update_user(cert_id: int, request: Request):
        body = await request.json()
        db_exec(
            """
            UPDATE certified_users SET
              display_name=%s, username=%s, category=%s, tags=%s, level=%s,
              region=%s, city=%s, bio=%s, contact=%s, valid_from=%s, valid_until=%s,
              status=%s, updated_at=NOW()
            WHERE id=%s
            """,
            (
                body.get("display_name"), body.get("username"), body.get("category"),
                body.get("tags", []), body.get("level", 1), body.get("region"),
                body.get("city"), body.get("bio"), body.get("contact"),
                body.get("valid_from"), body.get("valid_until"),
                body.get("status", "active"), cert_id,
            ),
        )
        return {"status": "ok"}

    @app.delete("/api/users/{cert_id}")
    async def api_delete_user(cert_id: int):
        db_exec("DELETE FROM certified_users WHERE id = %s", (cert_id,))
        return {"status": "ok"}

    @app.get("/api/stats")
    async def api_stats():
        return JSONResponse(_get_stats())

    # ── Ratings API ────────────────────────────────────────────────────────────
    @app.get("/api/ratings")
    async def api_ratings(status: str = "pending"):
        rows = db_query(
            "SELECT r.*, cu.display_name FROM ratings r LEFT JOIN certified_users cu ON cu.id=r.certified_user_id WHERE r.status=%s ORDER BY r.created_at DESC",
            (status,),
        )
        return JSONResponse([dict(r) for r in rows])

    @app.post("/api/ratings/{rid}/approve")
    async def api_approve_rating(rid: int):
        r = db_query_one("SELECT certified_user_id FROM ratings WHERE id = %s", (rid,))
        if not r:
            raise HTTPException(404)
        db_exec("UPDATE ratings SET status='approved' WHERE id=%s", (rid,))
        _update_trust_score(r["certified_user_id"])
        return {"status": "ok"}

    @app.post("/api/ratings/{rid}/reject")
    async def api_reject_rating(rid: int):
        db_exec("UPDATE ratings SET status='rejected' WHERE id=%s", (rid,))
        return {"status": "ok"}

    # ── Coupons API ────────────────────────────────────────────────────────────
    @app.get("/api/coupons")
    async def api_coupons(status: str = "pending"):
        rows = db_query(
            "SELECT c.*, cu.display_name FROM coupons c LEFT JOIN certified_users cu ON cu.id=c.certified_user_id WHERE c.status=%s ORDER BY c.created_at DESC",
            (status,),
        )
        return JSONResponse([dict(r) for r in rows])

    @app.post("/api/coupons/{cid}/approve")
    async def api_approve_coupon(cid: int):
        c = db_query_one("SELECT * FROM coupons WHERE id = %s", (cid,))
        if not c:
            raise HTTPException(404)
        channel_id = _get_global_setting("publish_channel_id", DEFAULT_CHANNEL_ID)
        if not channel_id:
            # Approve without publishing — no channel configured
            db_exec("UPDATE coupons SET status='approved' WHERE id=%s", (cid,))
            return {"status": "ok", "published": False, "note": "publish_channel_id not set"}
        cu = db_query_one("SELECT display_name FROM certified_users WHERE id = %s", (c["certified_user_id"],))
        coupon_template = _get_global_setting(
            "coupon_push_template",
            "🎫 <b>优惠券</b>\n\n👤 发布者: {display_name}\n📌 {title}\n📝 {description}\n💰 折扣: {discount}\n📅 有效期至: {valid_until}\n\n详情: /user_{certified_user_id}",
        )
        text = _render_push_template(
            coupon_template,
            {
                "title": c.get("title", ""),
                "description": c.get("description", ""),
                "discount": c.get("discount", ""),
                "valid_until": c.get("valid_until", ""),
                "certified_user_id": c.get("certified_user_id", ""),
                "display_name": cu["display_name"] if cu else "认证用户",
            },
        )
        try:
            await bot.send_message(channel_id, text)
            db_exec("UPDATE coupons SET status='published', published_at=NOW() WHERE id=%s", (cid,))
        except Exception as exc:
            db_exec("UPDATE coupons SET status='approved' WHERE id=%s", (cid,))
            return {"status": "ok", "published": False, "note": str(exc)}
        return {"status": "ok", "published": True}

    @app.post("/api/coupons/{cid}/reject")
    async def api_reject_coupon(cid: int):
        db_exec("UPDATE coupons SET status='rejected' WHERE id=%s", (cid,))
        return {"status": "ok"}

    # ── Risk API ───────────────────────────────────────────────────────────────
    @app.get("/api/risk")
    async def api_risk():
        logs = db_query("SELECT * FROM risk_logs ORDER BY created_at DESC LIMIT 50")
        return JSONResponse([dict(r) for r in logs])

    @app.post("/api/risk/action")
    async def api_risk_action(request: Request):
        body = await request.json()
        action = body.get("action")
        uid = body.get("uid")
        cert_id = body.get("cert_id")

        if action == "blacklist" and uid:
            db_exec("UPDATE users SET risk_status='blacklisted' WHERE uid=%s", (uid,))
            if cert_id:
                db_exec("UPDATE certified_users SET status='blacklisted' WHERE id=%s", (cert_id,))
        elif action == "whitelist" and uid:
            db_exec("UPDATE users SET risk_status='whitelisted' WHERE uid=%s", (uid,))
        elif action == "freeze" and cert_id:
            db_exec("UPDATE certified_users SET status='frozen' WHERE id=%s", (cert_id,))
        elif action == "unfreeze" and cert_id:
            db_exec("UPDATE certified_users SET status='active' WHERE id=%s", (cert_id,))

        db_exec(
            "INSERT INTO risk_logs (uid, certified_user_id, action, reason) VALUES (%s,%s,%s,%s)",
            (uid, cert_id, action, body.get("reason")),
        )
        return {"status": "ok"}

    # ── Subscription Rules API ─────────────────────────────────────────────────
    @app.get("/api/subscriptions")
    async def api_subscriptions(gid: str = ""):
        if gid:
            rows = db_query("SELECT * FROM subscription_rules WHERE gid=%s ORDER BY id", (gid,))
        else:
            rows = db_query("SELECT * FROM subscription_rules ORDER BY id")
        return JSONResponse([dict(r) for r in rows])

    @app.post("/api/subscriptions")
    async def api_add_subscription(request: Request):
        body = await request.json()
        db_exec(
            "INSERT INTO subscription_rules (gid, channel_id, channel_name, channel_url, feature) VALUES (%s,%s,%s,%s,%s)",
            (body["gid"], body["channel_id"], body.get("channel_name"), body.get("channel_url"), body.get("feature", "all")),
        )
        return {"status": "ok"}

    @app.delete("/api/subscriptions/{sub_id}")
    async def api_delete_subscription(sub_id: int):
        db_exec("DELETE FROM subscription_rules WHERE id=%s", (sub_id,))
        return {"status": "ok"}

    # ── Form POST handlers ─────────────────────────────────────────────────────
    @app.post("/users/save")
    async def save_user(
        request: Request,
        cert_id: str = Form(None),
        display_name: str = Form(...),
        username: str = Form(None),
        category: str = Form("general"),
        region: str = Form(None),
        city: str = Form(None),
        bio: str = Form(None),
        contact: str = Form(None),
        level: int = Form(1),
        valid_from: str = Form(None),
        valid_until: str = Form(None),
        status: str = Form("active"),
    ):
        if cert_id:
            db_exec(
                "UPDATE certified_users SET display_name=%s,username=%s,category=%s,region=%s,city=%s,bio=%s,contact=%s,level=%s,valid_from=%s,valid_until=%s,status=%s,updated_at=NOW() WHERE id=%s",
                (display_name, username, category, region, city, bio, contact, level, valid_from or None, valid_until or None, status, int(cert_id)),
            )
        else:
            db_exec(
                "INSERT INTO certified_users (display_name,username,category,region,city,bio,contact,level,valid_from,valid_until,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (display_name, username, category, region, city, bio, contact, level, valid_from or None, valid_until or None, status),
            )
        return RedirectResponse("/users", status_code=303)


def _get_stats() -> dict:
    def cnt(sql, params=()):
        r = db_query_one(sql, params)
        return r["cnt"] if r else 0

    return {
        "total_users": cnt("SELECT COUNT(*) as cnt FROM certified_users"),
        "active_users": cnt("SELECT COUNT(*) as cnt FROM certified_users WHERE status='active' AND (valid_until IS NULL OR valid_until > NOW())"),
        "expired_users": cnt("SELECT COUNT(*) as cnt FROM certified_users WHERE valid_until < NOW()"),
        "frozen_users": cnt("SELECT COUNT(*) as cnt FROM certified_users WHERE status='frozen'"),
        "checkins_today": cnt("SELECT COUNT(*) as cnt FROM checkins WHERE checkin_date=CURRENT_DATE"),
        "new_tg_users_today": cnt("SELECT COUNT(*) as cnt FROM users WHERE created_at::date=CURRENT_DATE"),
        "pending_ratings": cnt("SELECT COUNT(*) as cnt FROM ratings WHERE status='pending'"),
        "pending_coupons": cnt("SELECT COUNT(*) as cnt FROM coupons WHERE status='pending'"),
        "online_now": cnt("SELECT COUNT(*) as cnt FROM online_status WHERE expires_at > NOW()"),
        "total_tg_users": cnt("SELECT COUNT(*) as cnt FROM users"),
    }


def _update_trust_score(cert_id: int):
    stats = db_query_one(
        "SELECT AVG(stars) as avg, COUNT(*) as cnt FROM ratings WHERE certified_user_id=%s AND status='approved'",
        (cert_id,),
    )
    if not stats or not stats["cnt"]:
        return
    cu = db_query_one("SELECT risk_score, activity_score FROM certified_users WHERE id=%s", (cert_id,))
    if not cu:
        return
    avg_rating = float(stats["avg"])
    risk_penalty = min(cu["risk_score"] * 0.1, 2.0)
    activity_bonus = min(cu["activity_score"] * 0.05, 1.0)
    trust = max(0, min(10, avg_rating * 2 - risk_penalty + activity_bonus))
    db_exec("UPDATE certified_users SET trust_score=%s, updated_at=NOW() WHERE id=%s", (round(trust, 2), cert_id))
