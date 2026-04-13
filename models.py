"""
月影车姬机器人 - 数据库模型
YueYingCheJiBot - Database Models

使用 MongoDB 存储以下数据：
- 用户（User）：Telegram 用户信息、兰花令信用分、收藏灯笼
- 灯笼资源（Lantern）：车姬资源信息，含真实度评分和模糊位置
- 匿名会话（AnonymousChat）：双方同意后创建，24小时自动销毁
"""

import os
from datetime import datetime, timedelta
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure

# MongoDB 连接
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["yueyingcheji"]

# --- 集合引用 ---
users_col = db["users"]
lanterns_col = db["lanterns"]
chats_col = db["anonymous_chats"]
metrics_col = db["metrics"]
chat_requests_col = db["chat_requests"]

# --- 索引（首次启动时建立） ---
def create_indexes():
    """建立 MongoDB 索引，提升查询性能。"""
    users_col.create_index([("user_id", ASCENDING)], unique=True)
    lanterns_col.create_index([("city", ASCENDING), ("status", ASCENDING)])
    lanterns_col.create_index([("submitted_at", DESCENDING)])
    lanterns_col.create_index([("type", ASCENDING)])
    lanterns_col.create_index([("authenticity_score", DESCENDING)])
    # TTL index: MongoDB automatically deletes documents once their `expires_at`
    # timestamp is reached (expireAfterSeconds=0 means "expire at the field's value",
    # not "expire immediately"). This powers the 24-hour auto-destroy for anonymous chats.
    chats_col.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    chat_requests_col.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    chat_requests_col.create_index([("request_id", ASCENDING)], unique=True)
    metrics_col.create_index([("event_type", ASCENDING), ("created_at", DESCENDING)])


# =============================================================================
# 用户模型
# =============================================================================

def get_or_create_user(user_id: int, username: str = "", full_name: str = "") -> dict:
    """
    获取用户文档，若不存在则创建（初始赠送 100 兰花令）。
    返回用户文档 dict。
    """
    user = users_col.find_one({"user_id": user_id})
    if user is None:
        user = {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "credit_score": 100,          # 兰花令初始值
            "collected_lanterns": [],      # 收藏灯笼 ID 列表
            "subscriptions": {},           # 订阅设置
            "guard_enabled": False,        # 车姬守护模式（群管）
            # 防刷：各动作历史时间戳
            "action_timestamps": {
                "submit": [],
                "report": [],
                "session": [],
                "rate": [],
                "match": [],
            },
            # 信用恢复：修行任务列表
            "recovery_tasks": [],
            # 用户偏好（媒婆匹配历史槽位）
            "last_preferences": {},
            # 每日恢复追踪
            "last_daily_recovery": None,
            "daily_clean_streak": 0,       # 连续无违规天数（用于修行任务）
            "created_at": datetime.utcnow(),
            "last_active": datetime.utcnow(),
        }
        users_col.insert_one(user)
    return user


def update_credit(user_id: int, delta: int, reason: str = ""):
    """
    修改用户兰花令信用分。
    delta 为正数表示增加，负数表示扣减。
    """
    users_col.update_one(
        {"user_id": user_id},
        {
            "$inc": {"credit_score": delta},
            "$push": {
                "credit_history": {
                    "delta": delta,
                    "reason": reason,
                    "timestamp": datetime.utcnow(),
                }
            },
        },
    )


def collect_lantern(user_id: int, lantern_id: str):
    """将灯笼添加到用户的时光秘匣（收藏列表）。"""
    users_col.update_one(
        {"user_id": user_id},
        {"$addToSet": {"collected_lanterns": lantern_id}},
    )


# =============================================================================
# 灯笼资源模型
# =============================================================================

def create_lantern(
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
    返回新建灯笼的字符串 ID。
    """
    import uuid

    lantern = {
        "lantern_id": str(uuid.uuid4()),
        "city": city,
        "type": resource_type,           # 如 "大学生", "KH", "兼职" 等
        "price_range": price_range,      # 如 "5000-8000"
        "description": description,
        "authenticity_score": None,      # AI 鉴真后填入，0-100
        "location_blur": location_blur,  # 模糊位置描述，如 "台北市中心 5km 内"
        "photo_file_ids": photo_file_ids,
        "submitted_by": submitted_by,
        "submitted_at": datetime.utcnow(),
        "status": "pending",             # pending / approved / rejected
        "reports": [],                   # [{"reporter_id": int, "reason": str, "evidence": str}]
        "views": 0,
        "updated_at": datetime.utcnow(),
    }
    lanterns_col.insert_one(lantern)
    return lantern["lantern_id"]


def approve_lantern(lantern_id: str, authenticity_score: float = None):
    """管理员审核通过灯笼，可附带 AI 真实度分数。"""
    update = {
        "$set": {
            "status": "approved",
            "updated_at": datetime.utcnow(),
        }
    }
    if authenticity_score is not None:
        update["$set"]["authenticity_score"] = authenticity_score
    lanterns_col.update_one({"lantern_id": lantern_id}, update)


def reject_lantern(lantern_id: str):
    """管理员拒绝灯笼。"""
    lanterns_col.update_one(
        {"lantern_id": lantern_id},
        {"$set": {"status": "rejected", "updated_at": datetime.utcnow()}},
    )


def report_lantern(lantern_id: str, reporter_id: int, reason: str, evidence: str = ""):
    """举报灯笼资源（附证据）。"""
    lanterns_col.update_one(
        {"lantern_id": lantern_id},
        {
            "$push": {
                "reports": {
                    "reporter_id": reporter_id,
                    "reason": reason,
                    "evidence": evidence,
                    "reported_at": datetime.utcnow(),
                }
            }
        },
    )


def get_lanterns_by_city(city: str, limit: int = 20) -> list:
    """获取指定城市已审核通过的灯笼列表（按提交时间倒序）。"""
    return list(
        lanterns_col.find(
            {"city": city, "status": "approved"},
            {"_id": 0},
        )
        .sort("submitted_at", DESCENDING)
        .limit(limit)
    )


def get_pending_lanterns(limit: int = 50) -> list:
    """获取待审核灯笼列表（供管理员使用）。"""
    return list(
        lanterns_col.find({"status": "pending"}, {"_id": 0}).sort("submitted_at", ASCENDING).limit(limit)
    )


# =============================================================================
# 匿名会话模型
# =============================================================================

def create_anonymous_chat(user1: int, user2: int, ttl_hours: int = 24) -> str:
    """
    双方同意后创建临时匿名会话，TTL 默认 24 小时后自动销毁。
    返回会话 ID。
    """
    import uuid

    expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
    chat = {
        "chat_id": str(uuid.uuid4()),
        "user1": user1,
        "user2": user2,
        "messages": [],
        "created_at": datetime.utcnow(),
        "expires_at": expires_at,        # MongoDB TTL 索引自动删除
        "revealed": False,               # 是否已互相揭开真身
    }
    chats_col.insert_one(chat)
    return chat["chat_id"]


def get_chat_by_id(chat_id: str) -> dict:
    """根据 chat_id 获取匿名会话。"""
    return chats_col.find_one({"chat_id": chat_id}, {"_id": 0})


def append_message(chat_id: str, sender_id: int, text: str):
    """在匿名会话中追加消息记录。"""
    chats_col.update_one(
        {"chat_id": chat_id},
        {
            "$push": {
                "messages": {
                    "sender_id": sender_id,
                    "text": text,
                    "sent_at": datetime.utcnow(),
                }
            }
        },
    )


def mark_photo_shared(chat_id: str, sender_id: int):
    """记录该用户在匿名会话中已发送照片（用于积分结算）。"""
    chats_col.update_one(
        {"chat_id": chat_id},
        {"$set": {f"photos_shared.{sender_id}": True}},
    )


def end_chat_naturally(chat_id: str):
    """标记会话为双方主动结束（非超时）。"""
    chats_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"completed_naturally": True, "ended_at": datetime.utcnow()}},
    )


def rate_session(chat_id: str, rater_id: int, stars: int) -> dict | None:
    """
    记录用户对会话的评分（1-5星）。
    返回更新后的会话文档（若双方均已评分则可触发结算）。
    """
    chats_col.update_one(
        {"chat_id": chat_id},
        {"$set": {f"ratings.{rater_id}": {"stars": stars, "rated_at": datetime.utcnow()}}},
    )
    return chats_col.find_one({"chat_id": chat_id}, {"_id": 0})


# =============================================================================
# 速率限制 / 防刷
# =============================================================================

def record_action_timestamp(user_id: int, action_type: str):
    """记录用户某类动作的时间戳（用于速率限制）。"""
    users_col.update_one(
        {"user_id": user_id},
        {"$push": {f"action_timestamps.{action_type}": datetime.utcnow()}},
    )


def get_action_timestamps(user_id: int, action_type: str) -> list:
    """获取用户某类动作的历史时间戳列表。"""
    user = users_col.find_one({"user_id": user_id}, {"action_timestamps": 1})
    if not user:
        return []
    return (user.get("action_timestamps") or {}).get(action_type, [])


def log_metric(event_type: str, data: dict = None):
    """记录运营指标事件。"""
    metrics_col.insert_one({
        "event_type": event_type,
        "data": data or {},
        "created_at": datetime.utcnow(),
    })


def log_behavior(user_id: int, action: str, lantern_id: str = "", metadata: dict = None):
    """记录用户行为（浏览/收藏/举报/聊天等）。"""
    metrics_col.insert_one({
        "event_type": "behavior",
        "user_id": user_id,
        "action": action,
        "lantern_id": lantern_id,
        "metadata": metadata or {},
        "created_at": datetime.utcnow(),
    })


def increment_lantern_views(lantern_id: str):
    """增加灯笼浏览次数。"""
    lanterns_col.update_one(
        {"lantern_id": lantern_id},
        {"$inc": {"views": 1}},
    )


# =============================================================================
# 多路召回（供 AI 媒婆检索）
# =============================================================================

def get_lanterns_multi_filter(
    city: str = "",
    resource_type: str = "",
    limit: int = 50,
) -> list:
    """多路召回：城市 + 类型过滤，返回已审核灯笼（按提交时间倒序）。"""
    query: dict = {"status": "approved"}
    if city:
        query["city"] = city
    if resource_type:
        query["type"] = resource_type
    return list(
        lanterns_col.find(query, {"_id": 0})
        .sort("submitted_at", DESCENDING)
        .limit(limit)
    )


def get_high_trust_lanterns(limit: int = 30) -> list:
    """冷启动兜底：返回全局高可信灯笼（真实度 ≥ 70）。"""
    return list(
        lanterns_col.find(
            {"status": "approved", "authenticity_score": {"$gte": 70}},
            {"_id": 0},
        )
        .sort("authenticity_score", DESCENDING)
        .limit(limit)
    )


# =============================================================================
# 用户偏好存储
# =============================================================================

def save_user_preferences(user_id: int, prefs: dict):
    """保存用户上次的查询偏好（城市、类型、预算等）。"""
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"last_preferences": prefs, "last_active": datetime.utcnow()}},
    )


def get_user_preferences(user_id: int) -> dict:
    """读取用户上次保存的查询偏好。"""
    user = users_col.find_one({"user_id": user_id}, {"last_preferences": 1})
    return (user or {}).get("last_preferences") or {}


# =============================================================================
# 信用恢复 / 遮蔽
# =============================================================================

def assign_recovery_tasks_to_user(user_id: int, tasks: list):
    """将修行任务列表写入用户文档（追加，不覆盖已有任务）。"""
    user = users_col.find_one({"user_id": user_id}, {"recovery_tasks": 1})
    existing_types = {t["type"] for t in (user or {}).get("recovery_tasks", [])}
    new_tasks = [t for t in tasks if t["type"] not in existing_types]
    if new_tasks:
        users_col.update_one(
            {"user_id": user_id},
            {"$push": {"recovery_tasks": {"$each": new_tasks}}},
        )


def update_recovery_task_progress(user_id: int, action: str) -> list[dict]:
    """
    根据用户行为推进修行任务进度，返回本次新完成的任务列表。
    """
    from credit import progress_task

    user = users_col.find_one({"user_id": user_id}, {"recovery_tasks": 1, "credit_score": 1})
    if not user:
        return []

    tasks = user.get("recovery_tasks", [])
    newly_completed = []
    updated_tasks = []
    for task in tasks:
        updated, just_done = progress_task(task, action)
        updated_tasks.append(updated)
        if just_done:
            newly_completed.append(updated)

    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"recovery_tasks": updated_tasks}},
    )
    return newly_completed


def try_daily_recovery(user_id: int) -> int:
    """
    日常无违规恢复：每天最多 +1 兰花令。
    仅对信用分 < 90（弦月境起始线）的用户生效；
    恢复后分数可自然升入 90（弦月境），届时自动停止。
    返回实际增加量（0 表示今日已恢复或无需恢复）。
    """
    from credit import DAILY_RECOVERY_CAP  # 89 = 弦月境底线（恢复目标上限）

    user = users_col.find_one(
        {"user_id": user_id},
        {"credit_score": 1, "last_daily_recovery": 1},
    )
    if not user:
        return 0

    score = user.get("credit_score", 100)
    last_recovery = user.get("last_daily_recovery")

    # 只对尚未达到弦月境（<90）的用户执行恢复
    # DAILY_RECOVERY_CAP == 89，score > 89 即 score >= 90 时停止
    if score > DAILY_RECOVERY_CAP:
        return 0

    now = datetime.utcnow()
    if last_recovery and (now - last_recovery).days < 1:
        return 0

    delta = 1
    users_col.update_one(
        {"user_id": user_id},
        {
            "$inc": {"credit_score": delta},
            "$set": {"last_daily_recovery": now},
            "$push": {
                "credit_history": {
                    "delta": delta,
                    "reason": "每日无违规恢复",
                    "timestamp": now,
                }
            },
        },
    )
    return delta


# =============================================================================
# 匿名会话请求（chat_requests）
# =============================================================================

def create_chat_request(requester_id: int, lantern_id: str, lantern_owner_id: int) -> str:
    """
    创建匿名会话申请（TTL 24 小时后自动过期）。
    返回 request_id。
    """
    import uuid as _uuid
    request_id = str(_uuid.uuid4())
    chat_requests_col.insert_one({
        "request_id": request_id,
        "requester_id": requester_id,
        "lantern_id": lantern_id,
        "lantern_owner_id": lantern_owner_id,
        "status": "pending",
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(hours=24),
    })
    return request_id


def get_chat_request(request_id: str) -> dict | None:
    """获取会话申请文档。"""
    return chat_requests_col.find_one({"request_id": request_id}, {"_id": 0})


def accept_chat_request(request_id: str) -> dict | None:
    """接受会话申请，标记为 accepted。返回申请文档。"""
    doc = chat_requests_col.find_one_and_update(
        {"request_id": request_id, "status": "pending"},
        {"$set": {"status": "accepted", "accepted_at": datetime.utcnow()}},
        return_document=True,
    )
    return doc


def decline_chat_request(request_id: str):
    """拒绝会话申请，标记为 declined。"""
    chat_requests_col.update_one(
        {"request_id": request_id, "status": "pending"},
        {"$set": {"status": "declined", "declined_at": datetime.utcnow()}},
    )

