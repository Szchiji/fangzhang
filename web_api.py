"""
月影车姬机器人 - Web API 服务
YueYingCheJiBot - Web API for Mini App

提供 Mini App 所需的 REST API 端点：
  GET  /api/lanterns  — 获取已审核灯笼列表
  GET  /api/credit    — 获取用户信用分
  POST /api/collect   — 收藏灯笼到时光秘匣
  GET  /mini_app.html — 返回 Mini App 页面

使用 aiohttp 轻量 Web 框架，与 aiogram Bot Webhook 共存运行。
数据库操作通过 SQLAlchemy 异步 ORM 完成（PostgreSQL）。
"""

import os
import json
import hmac
import hashlib
import logging
from pathlib import Path
from aiohttp import web
from models import (
    get_or_create_user,
    collect_lantern,
    get_lanterns_by_city,
    get_approved_lanterns,
)

logger = logging.getLogger(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BASE_DIR = Path(__file__).parent


def verify_telegram_data(init_data: str) -> bool:
    """
    验证 Telegram WebApp initData 签名（防伪造请求）。
    参考：https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not BOT_TOKEN:
        return False
    try:
        pairs = dict(pair.split("=", 1) for pair in init_data.split("&"))
        received_hash = pairs.pop("hash", "")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()  # noqa: S324
        return hmac.compare_digest(expected, received_hash)
    except Exception as e:
        logger.warning("initData 验证失败: %s", e)
        return False


# ── 路由处理器 ──────────────────────────────────────────────────────────────

async def handle_mini_app(request: web.Request) -> web.Response:
    """返回 Mini App HTML 页面。"""
    html_path = BASE_DIR / "mini_app.html"
    return web.FileResponse(html_path)


async def handle_lanterns(request: web.Request) -> web.Response:
    """
    GET /api/lanterns?city=台北&init_data=...
    返回已审核灯笼列表（JSON）。
    """
    # 开发模式下跳过验证；生产环境始终验证
    if os.environ.get("ENV") != "dev":
        init_data = request.rel_url.query.get("init_data", "")
        if not verify_telegram_data(init_data):
            raise web.HTTPForbidden(reason="Invalid Telegram initData")

    city = request.rel_url.query.get("city", "")
    limit = min(int(request.rel_url.query.get("limit", "100")), 200)

    if city:
        lanterns = await get_lanterns_by_city(city, limit=limit)
    else:
        lanterns = await get_approved_lanterns(limit=limit)

    # 为每个灯笼添加模糊坐标（真实坐标不存储，前端负责模糊化）
    city_coords = {
        "台北": (25.04, 121.53),
        "香港": (22.32, 114.17),
        "深圳": (22.54, 114.06),
        "高雄": (22.63, 120.30),
        "台中": (24.15, 120.67),
        "上海": (31.23, 121.47),
        "广州": (23.13, 113.26),
        "新竹": (24.80, 120.97),
    }
    for l in lanterns:
        c = l.get("city", "")
        lat, lng = city_coords.get(c, (23.5, 121.0))
        l["lat"] = lat
        l["lng"] = lng
        # 转换 datetime 为字符串
        if "submitted_at" in l and l["submitted_at"] is not None:
            l["submitted_at"] = l["submitted_at"].isoformat()
        if "updated_at" in l and l["updated_at"] is not None:
            l["updated_at"] = l["updated_at"].isoformat()

    return web.Response(
        text=json.dumps(lanterns, ensure_ascii=False),
        content_type="application/json",
    )


async def handle_credit(request: web.Request) -> web.Response:
    """
    GET /api/credit?user_id=123&init_data=...
    返回用户兰花令信用分。
    """
    if os.environ.get("ENV") != "dev":
        init_data = request.rel_url.query.get("init_data", "")
        if not verify_telegram_data(init_data):
            raise web.HTTPForbidden(reason="Invalid Telegram initData")

    try:
        user_id = int(request.rel_url.query["user_id"])
    except (KeyError, ValueError):
        raise web.HTTPBadRequest(reason="Missing or invalid user_id")

    user = await get_or_create_user(user_id)
    return web.json_response({"credit": user.get("credit_score", 0)})


async def handle_collect(request: web.Request) -> web.Response:
    """
    POST /api/collect
    Body: {"user_id": 123, "lantern_id": "...", "init_data": "..."}
    收藏灯笼到用户时光秘匣。
    """
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON body")

    if os.environ.get("ENV") != "dev":
        if not verify_telegram_data(body.get("init_data", "")):
            raise web.HTTPForbidden(reason="Invalid Telegram initData")

    user_id = body.get("user_id")
    lantern_id = body.get("lantern_id")
    if not user_id or not lantern_id:
        raise web.HTTPBadRequest(reason="Missing user_id or lantern_id")

    await collect_lantern(int(user_id), lantern_id)
    return web.json_response({"ok": True, "message": "已收藏到你的时光秘匣 🕰"})


# ── App 工厂 ────────────────────────────────────────────────────────────────

def create_web_app() -> web.Application:
    """创建 aiohttp Web 应用，注册所有路由。"""
    app = web.Application()
    app.router.add_get("/", handle_mini_app)
    app.router.add_get("/mini_app.html", handle_mini_app)
    app.router.add_get("/api/lanterns", handle_lanterns)
    app.router.add_get("/api/credit", handle_credit)
    app.router.add_post("/api/collect", handle_collect)
    return app
