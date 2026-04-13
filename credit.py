"""
月影车姬机器人 - 兰花信用生态（完整实现）
YueYingCheJiBot - Orchid Credit Ecosystem

模块职责：
  1. 六境信用等级系统
  2. 月影会话积分公式 v2.0
  3. 防刷机制（速率限制 + 行为指纹 + 动态阈值）
  4. 月影遮蔽系统（分级限制）
  5. 信用恢复机制（时间疗愈 + 任务修行）
  6. 格式化输出（供 Bot 消息展示）

依赖：纯 Python 标准库，无外部 I/O；由 models.py / bot.py 调用。
"""

import uuid
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# =============================================================================
# 1. 六境信用等级
# =============================================================================

CREDIT_TIERS = [
    {
        "min": 150,
        "name": "极光境",
        "emoji": "🌌",
        "perks": ["exclusive_realm", "priority_match", "lantern_glow_max"],
        "match_multiplier": 1.2,
    },
    {
        "min": 120,
        "name": "满月境",
        "emoji": "🌕",
        "perks": ["priority_match", "lantern_glow_high"],
        "match_multiplier": 1.1,
    },
    {
        "min": 90,
        "name": "弦月境",
        "emoji": "🌙",
        "perks": ["standard"],
        "match_multiplier": 1.0,
    },
    {
        "min": 60,
        "name": "残月境",
        "emoji": "🌛",
        "perks": ["match_penalty_minor"],
        "match_multiplier": 0.8,
    },
    {
        "min": 30,
        "name": "月蚀境",
        "emoji": "🌒",
        "perks": ["match_penalty", "no_session"],
        "match_multiplier": 0.5,
    },
    {
        "min": 0,
        "name": "黑月境",
        "emoji": "🌑",
        "perks": ["match_penalty", "no_session", "no_submit", "no_match"],
        "match_multiplier": 0.0,
    },
]

_PERK_LABELS = {
    "exclusive_realm":    "🌌 月下专属秘境",
    "priority_match":     "⬆️ 媒婆优先匹配",
    "lantern_glow_max":   "✨ 灯笼光芒MAX",
    "lantern_glow_high":  "💫 灯笼高亮展示",
    "standard":           "🌙 标准秘境权限",
    "match_penalty_minor":"🌛 匹配权重微降",
    "match_penalty":      "🌒 匹配权重降低",
    "no_session":         "🚫 暂停匿名会话",
    "no_submit":          "🚫 暂停灯笼投稿",
    "no_match":           "🚫 暂停媒婆匹配",
}


def get_credit_tier(credit_score: int) -> dict:
    """根据信用分返回对应等级 dict。"""
    for tier in CREDIT_TIERS:
        if credit_score >= tier["min"]:
            return tier
    return CREDIT_TIERS[-1]


def get_match_multiplier(credit_score: int) -> float:
    """返回媒婆匹配权重系数（影响灯笼排序）。"""
    return get_credit_tier(credit_score)["match_multiplier"]


# =============================================================================
# 2. 月影遮蔽系统
# =============================================================================

# 遮蔽等级按 credit_score 从低到高排列
ECLIPSE_LEVELS = [
    {
        "level": "deep",
        "label": "深度月蚀",
        "emoji": "🌑",
        "max_credit": 29,
        "restrictions": ["no_match", "no_submit", "no_session", "match_penalty"],
        "description": (
            "你的灯笼已彻底熄灭，秘境之门暂时关闭。\n"
            "请完成修行任务，方可重燃月光。"
        ),
    },
    {
        "level": "medium",
        "label": "月蚀境",
        "emoji": "🌒",
        "max_credit": 59,
        "restrictions": ["no_session", "match_penalty"],
        "description": (
            "月光被浓云遮住，你暂时无法开启匿名会话，\n"
            "媒婆匹配权重也有所降低。保持良好行为可逐步恢复。"
        ),
    },
    {
        "level": "light",
        "label": "残月境",
        "emoji": "🌛",
        "max_credit": 89,
        "restrictions": ["match_penalty_minor"],
        "description": "月光稍显暗淡，匹配优先级略有降低。",
    },
    {
        "level": "none",
        "label": "正常",
        "emoji": "",
        "max_credit": 9999,
        "restrictions": [],
        "description": "",
    },
]


def get_eclipse_level(credit_score: int) -> dict:
    """根据信用分返回遮蔽等级 dict（从严到宽找第一个匹配）。"""
    for lvl in ECLIPSE_LEVELS:
        if credit_score <= lvl["max_credit"]:
            return lvl
    return ECLIPSE_LEVELS[-1]


def has_restriction(credit_score: int, restriction: str) -> bool:
    """判断用户是否受某项限制。"""
    return restriction in get_eclipse_level(credit_score).get("restrictions", [])


def eclipse_message(credit_score: int, restriction: str) -> str:
    """返回遮蔽拦截提示文案。"""
    eclipse = get_eclipse_level(credit_score)
    tier = get_credit_tier(credit_score)
    action_labels = {
        "no_match":   "使用媒婆匹配",
        "no_submit":  "投稿灯笼",
        "no_session": "开启匿名会话",
    }
    action = action_labels.get(restriction, "执行此操作")
    return (
        f"{eclipse['emoji']} <b>月影遮蔽提醒</b>\n\n"
        f"你当前处于 <b>{eclipse['label']}</b>（信用分：{credit_score} · {tier['name']}）\n"
        f"<i>{eclipse['description']}</i>\n\n"
        f"⚠️ 当前信用状态无法<b>{action}</b>。\n"
        "发送 /credit 查看修行任务，重拾月光之路 🌙"
    )


# =============================================================================
# 3. 防刷机制 — 速率限制配置
# =============================================================================

# action_type → {max 次数, window_hours 时间窗口}
RATE_LIMITS = {
    "submit":  {"max": 3,  "window_hours": 24, "label": "24小时内投稿次数"},
    "report":  {"max": 5,  "window_hours": 24, "label": "24小时内举报次数"},
    "session": {"max": 5,  "window_hours": 24, "label": "24小时内开启会话次数"},
    "rate":    {"max": 3,  "window_hours": 1,  "label": "1小时内评分次数"},
    "match":   {"max": 20, "window_hours": 24, "label": "24小时内匹配请求次数"},
}

# 同一用户在短时间内触发限制的额外惩罚
RATE_LIMIT_PENALTY = -10  # 兰花令


def check_rate_limit(timestamps: list, action_type: str) -> tuple[bool, int]:
    """
    检查行为频率是否超限。
    timestamps: 该 action_type 的历史时间戳列表（datetime）。
    返回 (is_allowed: bool, remaining: int)。
    """
    config = RATE_LIMITS.get(action_type)
    if not config:
        return True, 999
    cutoff = datetime.utcnow() - timedelta(hours=config["window_hours"])
    recent = [ts for ts in timestamps if ts > cutoff]
    remaining = max(0, config["max"] - len(recent))
    return len(recent) < config["max"], remaining


def detect_session_gaming(
    duration_minutes: float,
    message_count: int,
    rating_self: int,
    rating_other: int,
) -> bool:
    """
    启发式会话刷分检测。
    条件：会话时长极短 且 消息极少 且 双方均给满分。
    返回 True 表示疑似刷分。
    """
    too_short = duration_minutes < 5
    too_few_messages = message_count < 5
    mutual_five_stars = rating_self == 5 and rating_other == 5
    # 任意两个条件同时成立即判定可疑
    red_flags = sum([too_short, too_few_messages, mutual_five_stars])
    return red_flags >= 2


# =============================================================================
# 4. 月影会话积分公式 v2.0
# =============================================================================

def calculate_session_credit(
    rating_received: int,        # 对方给出的评分 (1-5)
    duration_minutes: float,     # 会话时长（分钟）
    photos_both_shared: bool,    # 双方均发送过照片
    photo_one_shared: bool,      # 至少一方发送过照片
    completed_naturally: bool,   # 双方主动结束（非超时/举报）
    ai_quality_score: float,     # AI 质量分 0-100（消息丰富度/真诚度）
    fraud_complaint: bool,       # 被对方举报且核实成立
    gaming_detected: bool,       # AI/规则检测到刷分行为
) -> tuple[int, dict]:
    """
    计算单次月影会话的兰花令变化量。
    返回 (final_delta: int, breakdown: dict)。

    公式：
      base          = (rating_received / 5) × 20       [0-20]
      time_bonus    = min(duration, 60) / 60 × 8       [0-8]
      quality_bonus = ai_quality_score / 100 × 10      [0-10]
      photo_bonus   = 5(双方) / 2(单方) / 0            [0-5]
      completion    = 3 if 主动结束 else 0              [0-3]
      fraud_penalty = -25 if 被举报核实                 [0/-25]
      gaming_penalty= -50 if 刷分检测                   [0/-50]
      final = clamp(sum, -50, +30)
    """
    base = round((max(1, min(5, rating_received)) / 5) * 20)
    time_bonus = round(min(duration_minutes, 60) / 60 * 8)
    quality_bonus = round(max(0.0, min(100.0, ai_quality_score)) / 100 * 10)
    photo_bonus = 5 if photos_both_shared else (2 if photo_one_shared else 0)
    completion_bonus = 3 if completed_naturally else 0
    fraud_penalty = -25 if fraud_complaint else 0
    gaming_penalty = -50 if gaming_detected else 0

    raw = (
        base + time_bonus + quality_bonus
        + photo_bonus + completion_bonus
        + fraud_penalty + gaming_penalty
    )
    final = max(-50, min(30, raw))

    breakdown = {
        "base": base,
        "time_bonus": time_bonus,
        "quality_bonus": quality_bonus,
        "photo_bonus": photo_bonus,
        "completion_bonus": completion_bonus,
        "fraud_penalty": fraud_penalty,
        "gaming_penalty": gaming_penalty,
        "raw": raw,
        "final_delta": final,
    }
    return final, breakdown


def format_session_credit_summary(breakdown: dict, delta: int) -> str:
    """生成会话积分结算小票（供 Bot 消息展示）。"""
    sign = "+" if delta >= 0 else ""
    lines = [
        "📋 <b>月影会话结算</b>",
        "",
        f"  基础评分分：<b>+{breakdown['base']}</b>",
        f"  时长奖励：<b>+{breakdown['time_bonus']}</b>",
        f"  质量奖励：<b>+{breakdown['quality_bonus']}</b>",
        f"  照片互信：<b>+{breakdown['photo_bonus']}</b>",
        f"  完成奖励：<b>+{breakdown['completion_bonus']}</b>",
    ]
    if breakdown["fraud_penalty"]:
        lines.append(f"  举报惩罚：<b>{breakdown['fraud_penalty']}</b>")
    if breakdown["gaming_penalty"]:
        lines.append(f"  刷分检测：<b>{breakdown['gaming_penalty']}</b>")
    lines += [
        "",
        f"🌙 本次兰花令变化：<b>{sign}{delta}</b>",
    ]
    return "\n".join(lines)


# =============================================================================
# 5. 信用恢复机制 — 修行任务库
# =============================================================================

RECOVERY_TASK_TEMPLATES = [
    {
        "type": "report_fraud",
        "description": "举报一名骗子并通过核实",
        "reward": 15,
        "requirement": {"action": "report_verified", "count": 1},
    },
    {
        "type": "submit_lantern",
        "description": "投稿一个通过审核的真实灯笼",
        "reward": 15,
        "requirement": {"action": "lantern_approved", "count": 1},
    },
    {
        "type": "good_session",
        "description": "完成3次获得4星以上好评的月影会话",
        "reward": 20,
        "requirement": {"action": "session_good_4plus", "count": 3},
    },
    {
        "type": "no_violation_7days",
        "description": "连续7天无违规行为",
        "reward": 10,
        "requirement": {"action": "daily_clean", "count": 7},
    },
]

DAILY_RECOVERY_DELTA = 1          # 每日无违规恢复 1 枚兰花令
DAILY_RECOVERY_CAP = 89           # 日常恢复上限（弦月境底线）
MAX_DAILY_RECOVERY_STREAK = 30    # 最多连续30天自动恢复


def assign_recovery_tasks(credit_score: int) -> list:
    """
    根据遮蔽等级分配修行任务（已有任务则不重复分配）。
    黑月境：分配全部4个任务
    月蚀境：分配2个
    残月境：分配1个
    """
    eclipse = get_eclipse_level(credit_score)
    level = eclipse["level"]
    task_count = {"light": 1, "medium": 2, "deep": 4}.get(level, 0)
    tasks = []
    for template in RECOVERY_TASK_TEMPLATES[:task_count]:
        tasks.append({
            "task_id": str(uuid.uuid4()),
            "type": template["type"],
            "description": template["description"],
            "reward": template["reward"],
            "requirement": template["requirement"],
            "progress": 0,
            "completed": False,
            "assigned_at": datetime.utcnow(),
        })
    return tasks


def progress_task(task: dict, action: str) -> tuple[dict, bool]:
    """
    更新单个修行任务进度。
    返回 (updated_task, just_completed)。
    """
    if task.get("completed"):
        return task, False
    req = task.get("requirement", {})
    if req.get("action") != action:
        return task, False
    task = dict(task)
    task["progress"] = task.get("progress", 0) + 1
    if task["progress"] >= req.get("count", 1):
        task["completed"] = True
        task["completed_at"] = datetime.utcnow()
        return task, True
    return task, False


# =============================================================================
# 6. 格式化输出
# =============================================================================

def format_credit_report(user: dict) -> str:
    """
    生成完整兰花令信用报告（Telegram HTML 格式）。
    包含：当前分、等级、遮蔽状态、等级权益、最近变动、修行任务。
    """
    score = user.get("credit_score", 100)
    tier = get_credit_tier(score)
    eclipse = get_eclipse_level(score)
    history = user.get("credit_history", [])[-5:]
    recovery_tasks = user.get("recovery_tasks", [])

    lines = [
        f"{tier['emoji']} <b>兰花令信用报告</b>",
        "",
        f"📊 信用分：<b>{score}</b> 枚　境界：<b>{tier['name']}</b>",
    ]

    # 遮蔽状态
    if eclipse["level"] != "none":
        lines.append(f"{eclipse['emoji']} 遮蔽：<b>{eclipse['label']}</b>")
        lines.append(f"<i>{eclipse['description']}</i>")

    # 权益提示
    perk_line = " · ".join(
        _PERK_LABELS[p] for p in tier.get("perks", []) if p in _PERK_LABELS
    )
    if perk_line:
        lines += ["", f"🎁 <b>当前权益</b>：{perk_line}"]

    # 最近变动
    lines.append("")
    if history:
        lines.append("📜 <b>最近变动</b>：")
        for h in reversed(history):
            sign = "+" if h["delta"] > 0 else ""
            ts = h.get("timestamp")
            ts_str = ts.strftime("%m-%d") if isinstance(ts, datetime) else ""
            lines.append(f"  {ts_str} {sign}{h['delta']} — {h.get('reason', '')}")
    else:
        lines.append("暂无变动记录。投稿优质资源或举报骗子可获得兰花令！")

    # 修行任务
    active = [t for t in recovery_tasks if not t.get("completed")]
    if active:
        lines += ["", "🕯 <b>修行任务</b>（完成可恢复信用）："]
        for t in active:
            progress = t.get("progress", 0)
            total = t.get("requirement", {}).get("count", 1)
            lines.append(
                f"  • {t['description']}　({progress}/{total}) → +{t['reward']} 兰花令"
            )
    elif eclipse["level"] != "none":
        lines += ["", "✅ 所有修行任务已完成，等待信用自然恢复中…"]

    return "\n".join(lines)


def format_tier_badge(credit_score: int) -> str:
    """返回简短的等级徽章（用于内联展示，如欢迎语）。"""
    tier = get_credit_tier(credit_score)
    return f"{tier['emoji']} {tier['name']}（{credit_score} 枚）"
