"""
月影车姬机器人 - 数据库模型（PostgreSQL / SQLAlchemy Async）
YueYingCheJiBot - Database Models

迁移自 MongoDB，改用 PostgreSQL + SQLAlchemy 异步 ORM（asyncpg 驱动）。
适配 Railway 部署环境。

表结构：
  - users           — 用户信息、兰花令信用分、收藏灯笼
  - lanterns        — 灯笼资源（车姬资源信息、真实度评分）
  - anonymous_chats — 匿名月影会话（24小时TTL，由应用层清理）
  - chat_requests   — 会话申请（24小时后过期）
  - metrics         — 运营指标 & 用户行为日志
"""

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float,
    Integer, String, Text, select, update as sa_update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

# ---------------------------------------------------------------------------
# 数据库连接（Railway 的 DATABASE_URL 可能以 postgres:// 开头）
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/yueyingcheji",
)
# 将 postgres:// 或 postgresql:// 统一转换为 postgresql+asyncpg://（asyncpg 驱动要求）
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,          # 生产环境关闭 SQL 日志
    pool_pre_ping=True,  # 连接健康检查（Railway 容器环境推荐）
    pool_size=5,
    max_overflow=10,
)

# AsyncSession 工厂，expire_on_commit=False 避免懒加载问题
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# ORM 基类
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# =============================================================================
# 用户表
# =============================================================================
class User(Base):
    """用户信息、兰花令信用分、收藏灯笼、速率限制时间戳等。"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), default="")
    full_name = Column(String(255), default="")
    credit_score = Column(Integer, default=100)          # 兰花令初始值
    collected_lanterns = Column(JSONB, default=list)     # 收藏灯笼 ID 列表
    subscriptions = Column(JSONB, default=dict)          # 订阅设置
    guard_enabled = Column(Boolean, default=False)       # 车姬守护群管模式
    action_timestamps = Column(JSONB, default=dict)      # 各动作历史时间戳（速率限制）
    recovery_tasks = Column(JSONB, default=list)         # 修行任务列表
    last_preferences = Column(JSONB, default=dict)       # 媒婆匹配偏好
    last_daily_recovery = Column(DateTime, nullable=True)
    daily_clean_streak = Column(Integer, default=0)      # 连续无违规天数
    credit_history = Column(JSONB, default=list)         # 信用变动记录
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)


# =============================================================================
# 灯笼资源表
# =============================================================================
class Lantern(Base):
    """灯笼（车姬）资源信息，含真实度评分和模糊位置。"""
    __tablename__ = "lanterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lantern_id = Column(String(36), unique=True, nullable=False, index=True)
    city = Column(String(100), default="", index=True)
    type = Column(String(100), default="", index=True)
    price_range = Column(String(50), default="")
    description = Column(Text, default="")
    authenticity_score = Column(Float, nullable=True)          # AI 鉴真分，0-100
    authenticity_labels = Column(JSONB, default=list)          # 可疑标签列表
    location_blur = Column(String(255), default="")            # 模糊位置描述
    photo_file_ids = Column(JSONB, default=list)               # Telegram file_id 列表
    submitted_by = Column(BigInteger, nullable=True, index=True)
    submitted_at = Column(DateTime, default=datetime.utcnow, index=True)
    status = Column(String(20), default="pending", index=True) # pending/approved/rejected
    reports = Column(JSONB, default=list)                      # 举报记录
    views = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)
    needs_human_review = Column(Boolean, default=False)


# =============================================================================
# 匿名会话表
# =============================================================================
class AnonymousChat(Base):
    """双方同意后创建的临时匿名会话，应用层负责 24 小时过期清理。"""
    __tablename__ = "anonymous_chats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String(36), unique=True, nullable=False, index=True)
    user1 = Column(BigInteger, nullable=False)
    user2 = Column(BigInteger, nullable=False)
    messages = Column(JSONB, default=list)       # [{sender_id, text, sent_at}, ...]
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True) # 24小时后过期
    revealed = Column(Boolean, default=False)    # 是否已互揭真身
    completed_naturally = Column(Boolean, default=False)
    ended_at = Column(DateTime, nullable=True)
    ratings = Column(JSONB, default=dict)        # {user_id: {stars, rated_at}}
    photos_shared = Column(JSONB, default=dict)  # {user_id: True/False}


# =============================================================================
# 会话申请表
# =============================================================================
class ChatRequest(Base):
    """匿名会话申请，24 小时后自动过期。"""
    __tablename__ = "chat_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(36), unique=True, nullable=False, index=True)
    requester_id = Column(BigInteger, nullable=False)
    lantern_id = Column(String(36), nullable=False)
    lantern_owner_id = Column(BigInteger, nullable=False)
    status = Column(String(20), default="pending")  # pending/accepted/declined
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    declined_at = Column(DateTime, nullable=True)


# =============================================================================
# 运营指标 & 行为日志表
# =============================================================================
class Metric(Base):
    """运营指标与用户行为日志（事件 + 数据 JSON）。"""
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(100), nullable=False, index=True)
    data = Column(JSONB, default=dict)
    user_id = Column(BigInteger, nullable=True)
    action = Column(String(100), default="")
    lantern_id = Column(String(36), default="")
    metadata = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# =============================================================================
# 建表（首次启动调用）
# =============================================================================
async def create_tables():
    """使用 SQLAlchemy 元数据建立所有表（若已存在则跳过）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# =============================================================================
# 内部辅助：ORM 对象 → 字典
# =============================================================================

def _parse_iso_dt(val) -> Optional[datetime]:
    """将 ISO 8601 字符串或 datetime 统一转为 datetime，None 则返回 None。"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        return None


def _parse_history_timestamps(history: list) -> list:
    """将 credit_history 中的 timestamp 字符串转为 datetime，保持向后兼容。"""
    result = []
    for entry in (history or []):
        h = dict(entry)
        if "timestamp" in h:
            h["timestamp"] = _parse_iso_dt(h["timestamp"])
        result.append(h)
    return result


def _parse_task_timestamps(tasks: list) -> list:
    """将 recovery_tasks 中的日期字段转为 datetime。"""
    result = []
    for t in (tasks or []):
        task = dict(t)
        for field in ("assigned_at", "completed_at"):
            if field in task:
                task[field] = _parse_iso_dt(task[field])
        result.append(task)
    return result


def _user_to_dict(user: User) -> dict:
    """将 User ORM 对象转为与原 MongoDB 文档兼容的字典。"""
    return {
        "user_id": user.user_id,
        "username": user.username or "",
        "full_name": user.full_name or "",
        "credit_score": user.credit_score if user.credit_score is not None else 100,
        "collected_lanterns": user.collected_lanterns or [],
        "subscriptions": user.subscriptions or {},
        "guard_enabled": bool(user.guard_enabled),
        "action_timestamps": user.action_timestamps or {},
        "recovery_tasks": _parse_task_timestamps(user.recovery_tasks),
        "last_preferences": user.last_preferences or {},
        "last_daily_recovery": user.last_daily_recovery,
        "daily_clean_streak": user.daily_clean_streak or 0,
        "credit_history": _parse_history_timestamps(user.credit_history),
        "created_at": user.created_at,
        "last_active": user.last_active,
    }


def _lantern_to_dict(lantern: Lantern) -> dict:
    """将 Lantern ORM 对象转为字典。"""
    return {
        "lantern_id": lantern.lantern_id,
        "city": lantern.city or "",
        "type": lantern.type or "",
        "price_range": lantern.price_range or "",
        "description": lantern.description or "",
        "authenticity_score": lantern.authenticity_score,
        "authenticity_labels": lantern.authenticity_labels or [],
        "location_blur": lantern.location_blur or "",
        "photo_file_ids": lantern.photo_file_ids or [],
        "submitted_by": lantern.submitted_by,
        "submitted_at": lantern.submitted_at,
        "status": lantern.status or "pending",
        "reports": lantern.reports or [],
        "views": lantern.views or 0,
        "updated_at": lantern.updated_at,
        "needs_human_review": bool(lantern.needs_human_review),
    }


def _chat_to_dict(chat: AnonymousChat) -> dict:
    """将 AnonymousChat ORM 对象转为字典。"""
    return {
        "chat_id": chat.chat_id,
        "user1": chat.user1,
        "user2": chat.user2,
        "messages": chat.messages or [],
        "created_at": chat.created_at,
        "expires_at": chat.expires_at,
        "revealed": bool(chat.revealed),
        "completed_naturally": bool(chat.completed_naturally),
        "ended_at": chat.ended_at,
        "ratings": chat.ratings or {},
        "photos_shared": chat.photos_shared or {},
    }


def _request_to_dict(req: ChatRequest) -> dict:
    """将 ChatRequest ORM 对象转为字典。"""
    return {
        "request_id": req.request_id,
        "requester_id": req.requester_id,
        "lantern_id": req.lantern_id,
        "lantern_owner_id": req.lantern_owner_id,
        "status": req.status,
        "created_at": req.created_at,
        "expires_at": req.expires_at,
        "accepted_at": req.accepted_at,
        "declined_at": req.declined_at,
    }


# =============================================================================
# 用户模型（异步）
# =============================================================================

async def get_or_create_user(
    user_id: int,
    username: str = "",
    full_name: str = "",
) -> dict:
    """
    获取用户记录，若不存在则创建（初始赠送 100 兰花令）。
    返回与原 MongoDB 文档兼容的字典。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                user_id=user_id,
                username=username,
                full_name=full_name,
                credit_score=100,
                collected_lanterns=[],
                subscriptions={},
                guard_enabled=False,
                action_timestamps={
                    "submit": [], "report": [], "session": [], "rate": [], "match": [],
                },
                recovery_tasks=[],
                last_preferences={},
                last_daily_recovery=None,
                daily_clean_streak=0,
                credit_history=[],
                created_at=datetime.utcnow(),
                last_active=datetime.utcnow(),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        else:
            user.last_active = datetime.utcnow()
            await session.commit()
        return _user_to_dict(user)


async def update_credit(user_id: int, delta: int, reason: str = ""):
    """
    修改用户兰花令信用分。
    delta 为正表示增加，负表示扣减。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.credit_score = (user.credit_score or 100) + delta
            history = list(user.credit_history or [])
            history.append({
                "delta": delta,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            })
            user.credit_history = history
            flag_modified(user, "credit_history")
            await session.commit()


async def collect_lantern(user_id: int, lantern_id: str):
    """将灯笼添加到用户的时光秘匣（收藏列表），已收藏则忽略。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if user:
            collected = list(user.collected_lanterns or [])
            if lantern_id not in collected:
                collected.append(lantern_id)
                user.collected_lanterns = collected
                flag_modified(user, "collected_lanterns")
                await session.commit()


async def save_user_preferences(user_id: int, prefs: dict):
    """保存用户上次的查询偏好（城市、类型、预算等）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.last_preferences = prefs
            user.last_active = datetime.utcnow()
            await session.commit()


async def get_user_preferences(user_id: int) -> dict:
    """读取用户上次保存的查询偏好。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User.last_preferences).where(User.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        return row or {}


# =============================================================================
# 速率限制 / 防刷
# =============================================================================

async def record_action_timestamp(user_id: int, action_type: str):
    """记录用户某类动作的时间戳（用于速率限制）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if user:
            timestamps = dict(user.action_timestamps or {})
            action_list = list(timestamps.get(action_type, []))
            action_list.append(datetime.utcnow().isoformat())
            timestamps[action_type] = action_list
            user.action_timestamps = timestamps
            flag_modified(user, "action_timestamps")
            await session.commit()


async def get_action_timestamps(user_id: int, action_type: str) -> list:
    """
    获取用户某类动作的历史时间戳列表（返回 datetime 对象，便于 check_rate_limit 使用）。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User.action_timestamps).where(User.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return []
        raw_list = (row or {}).get(action_type, [])
        # 将 ISO 字符串转为 datetime 对象（check_rate_limit 需要与 datetime.utcnow() 比较）
        parsed = []
        for ts in raw_list:
            dt = _parse_iso_dt(ts)
            if dt:
                parsed.append(dt)
        return parsed


# =============================================================================
# 运营指标 & 行为日志
# =============================================================================

async def log_metric(event_type: str, data: dict = None):
    """记录运营指标事件。"""
    async with AsyncSessionLocal() as session:
        metric = Metric(
            event_type=event_type,
            data=data or {},
            created_at=datetime.utcnow(),
        )
        session.add(metric)
        await session.commit()


async def log_behavior(
    user_id: int,
    action: str,
    lantern_id: str = "",
    metadata: dict = None,
):
    """记录用户行为（浏览/收藏/举报/聊天等）。"""
    async with AsyncSessionLocal() as session:
        metric = Metric(
            event_type="behavior",
            data={},
            user_id=user_id,
            action=action,
            lantern_id=lantern_id,
            metadata=metadata or {},
            created_at=datetime.utcnow(),
        )
        session.add(metric)
        await session.commit()


# =============================================================================
# 灯笼资源模型（异步）
# =============================================================================

async def create_lantern(
    city: str,
    resource_type: str,
    price_range: str,
    description: str,
    photo_file_ids: list,
    submitted_by: int,
    location_blur: str = "",
) -> str:
    """
    创建新灯笼资源（待审核状态）。
    返回新建灯笼的 UUID 字符串 ID。
    """
    lantern_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        lantern = Lantern(
            lantern_id=lantern_id,
            city=city,
            type=resource_type,
            price_range=price_range,
            description=description,
            authenticity_score=None,
            authenticity_labels=[],
            location_blur=location_blur,
            photo_file_ids=photo_file_ids,
            submitted_by=submitted_by,
            submitted_at=datetime.utcnow(),
            status="pending",
            reports=[],
            views=0,
            updated_at=datetime.utcnow(),
            needs_human_review=False,
        )
        session.add(lantern)
        await session.commit()
    return lantern_id


async def get_lantern_by_id(lantern_id: str) -> Optional[dict]:
    """根据 lantern_id 获取灯笼文档，不存在返回 None。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lantern).where(Lantern.lantern_id == lantern_id)
        )
        lantern = result.scalar_one_or_none()
        return _lantern_to_dict(lantern) if lantern else None


async def update_lantern_fields(lantern_id: str, fields: dict):
    """更新灯笼任意字段（供 AI 鉴真回写和管理员操作使用）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lantern).where(Lantern.lantern_id == lantern_id)
        )
        lantern = result.scalar_one_or_none()
        if lantern:
            for key, value in fields.items():
                if hasattr(lantern, key):
                    setattr(lantern, key, value)
                    # JSONB 字段需手动标记为已修改
                    if isinstance(value, (dict, list)):
                        flag_modified(lantern, key)
            await session.commit()


async def approve_lantern(lantern_id: str, authenticity_score: float = None):
    """管理员审核通过灯笼，可附带 AI 真实度分数。"""
    fields = {"status": "approved", "updated_at": datetime.utcnow()}
    if authenticity_score is not None:
        fields["authenticity_score"] = authenticity_score
    await update_lantern_fields(lantern_id, fields)


async def reject_lantern(lantern_id: str):
    """管理员拒绝灯笼。"""
    await update_lantern_fields(
        lantern_id, {"status": "rejected", "updated_at": datetime.utcnow()}
    )


async def report_lantern(
    lantern_id: str,
    reporter_id: int,
    reason: str,
    evidence: str = "",
):
    """举报灯笼资源（附证据）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lantern).where(Lantern.lantern_id == lantern_id)
        )
        lantern = result.scalar_one_or_none()
        if lantern:
            reports = list(lantern.reports or [])
            reports.append({
                "reporter_id": reporter_id,
                "reason": reason,
                "evidence": evidence,
                "reported_at": datetime.utcnow().isoformat(),
            })
            lantern.reports = reports
            flag_modified(lantern, "reports")
            await session.commit()


async def get_lanterns_by_city(city: str, limit: int = 20) -> list:
    """获取指定城市已审核通过的灯笼列表（按提交时间倒序）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lantern)
            .where(Lantern.city == city, Lantern.status == "approved")
            .order_by(Lantern.submitted_at.desc())
            .limit(limit)
        )
        return [_lantern_to_dict(l) for l in result.scalars().all()]


async def get_pending_lanterns(limit: int = 50) -> list:
    """获取待审核灯笼列表（供管理员使用，按提交时间正序）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lantern)
            .where(Lantern.status == "pending")
            .order_by(Lantern.submitted_at.asc())
            .limit(limit)
        )
        return [_lantern_to_dict(l) for l in result.scalars().all()]


async def get_approved_lanterns(limit: int = 100) -> list:
    """获取全部已审核通过的灯笼（按提交时间倒序）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lantern)
            .where(Lantern.status == "approved")
            .order_by(Lantern.submitted_at.desc())
            .limit(limit)
        )
        return [_lantern_to_dict(l) for l in result.scalars().all()]


async def get_lanterns_multi_filter(
    city: str = "",
    resource_type: str = "",
    limit: int = 50,
) -> list:
    """多路召回：城市 + 类型过滤，返回已审核灯笼（按提交时间倒序）。"""
    async with AsyncSessionLocal() as session:
        query = select(Lantern).where(Lantern.status == "approved")
        if city:
            query = query.where(Lantern.city == city)
        if resource_type:
            query = query.where(Lantern.type == resource_type)
        query = query.order_by(Lantern.submitted_at.desc()).limit(limit)
        result = await session.execute(query)
        return [_lantern_to_dict(l) for l in result.scalars().all()]


async def get_high_trust_lanterns(limit: int = 30) -> list:
    """冷启动兜底：返回全局高可信灯笼（真实度 ≥ 70）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lantern)
            .where(Lantern.status == "approved", Lantern.authenticity_score >= 70)
            .order_by(Lantern.authenticity_score.desc())
            .limit(limit)
        )
        return [_lantern_to_dict(l) for l in result.scalars().all()]


async def increment_lantern_views(lantern_id: str):
    """增加灯笼浏览次数。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lantern).where(Lantern.lantern_id == lantern_id)
        )
        lantern = result.scalar_one_or_none()
        if lantern:
            lantern.views = (lantern.views or 0) + 1
            await session.commit()


# =============================================================================
# 匿名会话模型（异步）
# =============================================================================

async def create_anonymous_chat(
    user1: int,
    user2: int,
    ttl_hours: int = 24,
) -> str:
    """
    双方同意后创建临时匿名会话，TTL 默认 24 小时。
    返回会话 UUID。
    """
    chat_id = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
    async with AsyncSessionLocal() as session:
        chat = AnonymousChat(
            chat_id=chat_id,
            user1=user1,
            user2=user2,
            messages=[],
            created_at=datetime.utcnow(),
            expires_at=expires_at,
            revealed=False,
            completed_naturally=False,
            ended_at=None,
            ratings={},
            photos_shared={},
        )
        session.add(chat)
        await session.commit()
    return chat_id


async def get_chat_by_id(chat_id: str) -> Optional[dict]:
    """根据 chat_id 获取匿名会话，不存在返回 None。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AnonymousChat).where(AnonymousChat.chat_id == chat_id)
        )
        chat = result.scalar_one_or_none()
        return _chat_to_dict(chat) if chat else None


async def append_message(chat_id: str, sender_id: int, text: str):
    """在匿名会话中追加消息记录。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AnonymousChat).where(AnonymousChat.chat_id == chat_id)
        )
        chat = result.scalar_one_or_none()
        if chat:
            messages = list(chat.messages or [])
            messages.append({
                "sender_id": sender_id,
                "text": text,
                "sent_at": datetime.utcnow().isoformat(),
            })
            chat.messages = messages
            flag_modified(chat, "messages")
            await session.commit()


async def mark_photo_shared(chat_id: str, sender_id: int):
    """记录该用户在匿名会话中已发送照片（用于积分结算）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AnonymousChat).where(AnonymousChat.chat_id == chat_id)
        )
        chat = result.scalar_one_or_none()
        if chat:
            photos = dict(chat.photos_shared or {})
            photos[str(sender_id)] = True
            chat.photos_shared = photos
            flag_modified(chat, "photos_shared")
            await session.commit()


async def end_chat_naturally(chat_id: str):
    """标记会话为双方主动结束（非超时）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AnonymousChat).where(AnonymousChat.chat_id == chat_id)
        )
        chat = result.scalar_one_or_none()
        if chat:
            chat.completed_naturally = True
            chat.ended_at = datetime.utcnow()
            await session.commit()


async def rate_session(chat_id: str, rater_id: int, stars: int) -> Optional[dict]:
    """
    记录用户对会话的评分（1-5 星）。
    返回更新后的会话字典（双方均已评分时可触发结算）。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AnonymousChat).where(AnonymousChat.chat_id == chat_id)
        )
        chat = result.scalar_one_or_none()
        if not chat:
            return None
        ratings = dict(chat.ratings or {})
        ratings[str(rater_id)] = {
            "stars": stars,
            "rated_at": datetime.utcnow().isoformat(),
        }
        chat.ratings = ratings
        flag_modified(chat, "ratings")
        await session.commit()
        return _chat_to_dict(chat)


# =============================================================================
# 信用恢复 / 遮蔽
# =============================================================================

async def assign_recovery_tasks_to_user(user_id: int, tasks: list):
    """将修行任务列表写入用户文档（追加，不覆盖已有任务）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return
        existing = list(user.recovery_tasks or [])
        existing_types = {t["type"] for t in existing}
        new_tasks = [t for t in tasks if t["type"] not in existing_types]
        if new_tasks:
            # 序列化任务中的 datetime 字段为 ISO 字符串（JSONB 存储要求）
            serialized = []
            for task in new_tasks:
                t = dict(task)
                for field in ("assigned_at", "completed_at"):
                    if field in t and isinstance(t[field], datetime):
                        t[field] = t[field].isoformat()
                serialized.append(t)
            existing.extend(serialized)
            user.recovery_tasks = existing
            flag_modified(user, "recovery_tasks")
            await session.commit()


async def update_recovery_task_progress(user_id: int, action: str) -> list:
    """
    根据用户行为推进修行任务进度，返回本次新完成的任务列表。
    """
    from credit import progress_task  # 避免循环导入

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return []

        tasks = list(user.recovery_tasks or [])
        newly_completed = []
        updated_tasks = []
        for task in tasks:
            updated, just_done = progress_task(task, action)
            # 序列化 datetime 字段
            t = dict(updated)
            for field in ("assigned_at", "completed_at"):
                if field in t and isinstance(t[field], datetime):
                    t[field] = t[field].isoformat()
            updated_tasks.append(t)
            if just_done:
                newly_completed.append(updated)

        user.recovery_tasks = updated_tasks
        flag_modified(user, "recovery_tasks")
        await session.commit()
        return newly_completed


async def try_daily_recovery(user_id: int) -> int:
    """
    日常无违规恢复：每天最多 +1 兰花令（仅对信用分 < 90 用户有效）。
    返回实际增加量（0 表示无需或今日已恢复）。
    """
    from credit import DAILY_RECOVERY_CAP  # 避免循环导入

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return 0

        score = user.credit_score if user.credit_score is not None else 100
        if score > DAILY_RECOVERY_CAP:
            return 0

        now = datetime.utcnow()
        if user.last_daily_recovery and (now - user.last_daily_recovery).days < 1:
            return 0

        delta = 1
        user.credit_score = score + delta
        user.last_daily_recovery = now
        history = list(user.credit_history or [])
        history.append({
            "delta": delta,
            "reason": "每日无违规恢复",
            "timestamp": now.isoformat(),
        })
        user.credit_history = history
        flag_modified(user, "credit_history")
        await session.commit()
        return delta


# =============================================================================
# 匿名会话申请（chat_requests）
# =============================================================================

async def create_chat_request(
    requester_id: int,
    lantern_id: str,
    lantern_owner_id: int,
) -> str:
    """
    创建匿名会话申请（24 小时后过期）。
    返回 request_id。
    """
    request_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        req = ChatRequest(
            request_id=request_id,
            requester_id=requester_id,
            lantern_id=lantern_id,
            lantern_owner_id=lantern_owner_id,
            status="pending",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=24),
        )
        session.add(req)
        await session.commit()
    return request_id


async def get_chat_request(request_id: str) -> Optional[dict]:
    """获取会话申请文档，不存在返回 None。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ChatRequest).where(ChatRequest.request_id == request_id)
        )
        req = result.scalar_one_or_none()
        return _request_to_dict(req) if req else None


async def accept_chat_request(request_id: str) -> Optional[dict]:
    """接受会话申请，标记为 accepted。返回申请文档。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ChatRequest).where(
                ChatRequest.request_id == request_id,
                ChatRequest.status == "pending",
            )
        )
        req = result.scalar_one_or_none()
        if not req:
            return None
        req.status = "accepted"
        req.accepted_at = datetime.utcnow()
        await session.commit()
        return _request_to_dict(req)


async def decline_chat_request(request_id: str):
    """拒绝会话申请，标记为 declined。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ChatRequest).where(
                ChatRequest.request_id == request_id,
                ChatRequest.status == "pending",
            )
        )
        req = result.scalar_one_or_none()
        if req:
            req.status = "declined"
            req.declined_at = datetime.utcnow()
            await session.commit()


# ---------------------------------------------------------------------------
# 兼容占位：旧 MongoDB create_indexes 已由 create_tables 替代
# ---------------------------------------------------------------------------
def create_indexes():
    """
    兼容旧调用（已废弃）。
    PostgreSQL 索引通过 SQLAlchemy index=True 声明，建表时自动创建。
    """
    pass

