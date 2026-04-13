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
    # TTL index: MongoDB automatically deletes documents once their `expires_at`
    # timestamp is reached (expireAfterSeconds=0 means "expire at the field's value",
    # not "expire immediately"). This powers the 24-hour auto-destroy for anonymous chats.
    chats_col.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)


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
            "subscriptions": {},           # 订阅设置，如 {"weekly_taipei": True}
            "guard_enabled": False,        # 车姬守护模式（群管）
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
