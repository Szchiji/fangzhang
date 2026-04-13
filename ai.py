"""
月影车姬机器人 - AI 模块（v2）
YueYingCheJiBot - AI Integration

核心功能：
1. parse_query_intent(query)  — NLU：解析自然语言为结构化槽位
2. match_lanterns(query, ...) — 月影媒婆：多路召回 + 两阶段排序 + 可解释推荐
3. analyze_authenticity(...)  — 兰花鉴真：照片真实度（返回 score + labels）
4. score_session_quality(...) — 会话质量评分（用于积分结算）
5. check_anti_fraud(text)     — 反诈检测：识别高危关键词

支持 Grok / 通义千问 双后端，无 AI Key 时降级为规则模式。
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import Optional

import aiohttp

from models import (
    get_lanterns_multi_filter,
    get_high_trust_lanterns,
    lanterns_col,
    log_metric,
)
from credit import get_match_multiplier
from pymongo import DESCENDING

logger = logging.getLogger(__name__)

# --- API 配置 ---
GROK_API_KEY = os.environ.get("GROK_API_KEY", "")
TONGYI_API_KEY = os.environ.get("TONGYI_API_KEY", "")

GROK_ENDPOINT = "https://api.x.ai/v1/chat/completions"
TONGYI_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

AI_API_KEY = GROK_API_KEY or TONGYI_API_KEY
AI_ENDPOINT = GROK_ENDPOINT if GROK_API_KEY else TONGYI_ENDPOINT
AI_MODEL = "grok-3-mini" if GROK_API_KEY else "qwen-turbo"

# --- 知识库常量 ---
KNOWN_CITIES = ["台北", "香港", "深圳", "上海", "广州", "高雄", "台中", "新竹"]
KNOWN_TYPES = ["大学生", "KH", "兼职", "全职", "外籍", "熟女"]

NEARBY_CITIES: dict[str, list] = {
    "台北": ["台中", "新竹", "高雄"],
    "高雄": ["台北", "台中"],
    "台中": ["台北", "新竹", "高雄"],
    "新竹": ["台北", "台中"],
    "香港": ["深圳", "广州"],
    "深圳": ["香港", "广州"],
    "广州": ["深圳", "香港"],
    "上海": [],
}

# 反诈高危关键词
FRAUD_KEYWORDS = [
    "先付", "定金", "预付", "转账", "汇款", "代付", "充值", "礼品卡",
    "买码", "比特币", "USDT", "虚拟货币", "红包", "跑路", "骗局", "黑中介",
]


# =============================================================================
# 通用 AI 调用
# =============================================================================

async def _call_ai(messages: list, temperature: float = 0.3, max_tokens: int = 1024) -> str:
    """通用 AI 接口调用（兼容 OpenAI 格式）。失败时抛出异常。"""
    if not AI_API_KEY:
        raise RuntimeError("未配置 AI API Key（GROK_API_KEY 或 TONGYI_API_KEY）")

    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            AI_ENDPOINT, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            choices = data.get("choices")
            if not choices or not isinstance(choices, list):
                raise ValueError(f"Unexpected AI response format: {data}")
            return choices[0]["message"]["content"].strip()


# =============================================================================
# 1. NLU — 意图理解
# =============================================================================

def _rule_parse_intent(query: str) -> dict:
    """规则降级解析：从查询中提取城市、类型、预算。"""
    city = next((c for c in KNOWN_CITIES if c in query), "")
    resource_type = next((t for t in KNOWN_TYPES if t in query), "")

    budget_min: Optional[int] = None
    budget_max: Optional[int] = None
    range_m = re.search(r"(\d+)[kK千]?\s*[-~到至]\s*(\d+)[kK千]?", query)
    single_m = re.search(r"(\d{3,5})[kK千]?", query)
    if range_m:
        lo, hi = int(range_m.group(1)), int(range_m.group(2))
        budget_min = lo * 1000 if lo < 500 else lo
        budget_max = hi * 1000 if hi < 500 else hi
    elif single_m:
        mid = int(single_m.group(1))
        mid = mid * 1000 if mid < 500 else mid
        budget_min, budget_max = int(mid * 0.8), int(mid * 1.2)

    need_real_photos = any(kw in query for kw in ["真实", "本人", "无修", "素颜", "自拍"])

    missing_slots = []
    if not city and len(query) < 15:
        missing_slots.append("city")

    return {
        "city": city,
        "type": resource_type,
        "budget_min": budget_min,
        "budget_max": budget_max,
        "need_real_photos": need_real_photos,
        "time_hint": "",
        "missing_slots": missing_slots,
    }


async def parse_query_intent(query: str) -> dict:
    """
    NLU：将用户自然语言解析为结构化槽位。
    有 AI 时调用 LLM，否则降级为规则解析。
    返回: {city, type, budget_min, budget_max, need_real_photos, time_hint, missing_slots}
    """
    if not AI_API_KEY:
        return _rule_parse_intent(query)

    system_prompt = (
        "你是月影车姬的意图解析引擎。将用户的自然语言需求解析为如下 JSON，只返回 JSON，不要其他内容：\n"
        '{"city":"城市名或空字符串","type":"资源类型或空字符串","budget_min":数字或null,'
        '"budget_max":数字或null,"need_real_photos":true或false,"time_hint":"时间描述或空字符串",'
        '"missing_slots":["city"等缺失的必要槽位列表"]}\n'
        f"可选城市：{KNOWN_CITIES}\n可选类型：{KNOWN_TYPES}\n"
        "预算单位为整数（台币/港币/人民币）；若用户提供了城市则 missing_slots 不含 city。"
    )
    try:
        raw = await _call_ai(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            temperature=0.1,
            max_tokens=256,
        )
        intent = json.loads(raw)
        for key, default in [
            ("city", ""), ("type", ""), ("budget_min", None), ("budget_max", None),
            ("need_real_photos", False), ("time_hint", ""), ("missing_slots", []),
        ]:
            intent.setdefault(key, default)
        return intent
    except Exception as e:
        logger.warning("NLU 解析失败，降级为规则模式: %s", e)
        return _rule_parse_intent(query)


# =============================================================================
# 2. 反诈检测
# =============================================================================

def check_anti_fraud(text: str) -> list:
    """
    反诈检测：返回文本中触发的高危关键词列表。
    空列表表示安全。
    """
    return [kw for kw in FRAUD_KEYWORDS if kw in text]


# =============================================================================
# 3. 规则评分（Stage 1）
# =============================================================================

def _compute_rule_score(lantern: dict, owner_credit: int = 100) -> float:
    """
    综合真实度、举报数、时效性、灯笼主信用分计算规则基础分（0-100）。
    """
    auth_score = float(lantern.get("authenticity_score") or 50.0)
    auth_factor = auth_score / 100.0

    reports_count = len(lantern.get("reports", []))
    report_factor = max(0.0, 1.0 - reports_count / 5.0)

    submitted_at = lantern.get("submitted_at")
    if submitted_at and isinstance(submitted_at, datetime):
        days_old = (datetime.utcnow() - submitted_at).days
        recency_factor = max(0.0, 1.0 - days_old / 90.0)
    else:
        recency_factor = 0.5

    credit_multiplier = get_match_multiplier(owner_credit)

    rule_score = (
        auth_factor * 35.0
        + report_factor * 30.0
        + recency_factor * 20.0
        + 15.0
    ) * credit_multiplier

    return round(min(100.0, rule_score), 1)


# =============================================================================
# 4. LLM 重排（Stage 2）
# =============================================================================

async def _llm_rerank(query: str, candidates: list) -> list:
    """
    LLM 重排：生成 match_reason 和 risk_tip，返回 Top-5。
    LLM 不可用时降级为规则分排序。
    """
    if not candidates:
        return []

    summaries = []
    for l in candidates:
        summaries.append(
            f"ID={l['lantern_id'][:8]} | 城市={l.get('city')} | 类型={l.get('type')} | "
            f"价位={l.get('price_range')} | 真实度={l.get('authenticity_score', '未知')}% | "
            f"规则分={l.get('_rule_score', 50)} | 描述={l.get('description', '')[:80]}"
        )

    system_prompt = (
        "你是月影媒婆，温柔专业的月下红娘。根据用户需求从候选资源中选出最多5个最匹配的。\n"
        "以 JSON 数组格式返回，每项包含：\n"
        '{"id":"8位ID","match_score":0-100整数,"reason":"推荐理由不超过20字",'
        '"risk":"风险提示不超过15字或空字符串"}\n'
        "只返回 JSON，不要任何其他内容。"
    )
    user_msg = f"用户需求：{query}\n\n候选资源：\n" + "\n".join(summaries)

    matched = None
    try:
        raw = await _call_ai(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]
        )
        matched = json.loads(raw)
    except Exception as e:
        logger.error("LLM 重排失败，使用规则分兜底: %s", e)
        log_metric("llm_rerank_failure", {"error": str(e)})

    id_map = {l["lantern_id"][:8]: l for l in candidates}

    if matched is None:
        results = sorted(candidates, key=lambda x: x.get("_rule_score", 0), reverse=True)[:5]
        for l in results:
            l.pop("_rule_score", None)
            l.setdefault("match_score", 50)
            l.setdefault("match_reason", "综合评分推荐")
            l.setdefault("risk_tip", "")
        return results

    results = []
    for item in matched:
        short_id = str(item.get("id", ""))[:8]
        lantern = id_map.get(short_id)
        if lantern:
            lantern = dict(lantern)
            lantern.pop("_rule_score", None)
            lantern["match_score"] = int(item.get("match_score", 0))
            lantern["match_reason"] = item.get("reason", "")
            lantern["risk_tip"] = item.get("risk", "")
            results.append(lantern)

    results.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return results[:5]


# =============================================================================
# 5. 月影媒婆主流程
# =============================================================================

async def match_lanterns(
    query: str,
    city_hint: str = "",
    user_prefs: dict = None,
) -> dict:
    """
    月影媒婆主流程：多路召回 + 两阶段排序 + 可解释推荐。

    返回:
    {
        results: list,           # 推荐灯笼（含 match_score / match_reason / risk_tip）
        parsed_intent: dict,     # 解析出的意图槽位
        missing_slots: list,     # 尚需追问的槽位
        anti_fraud_warning: str, # 反诈警告（若触发）
        is_cold_start: bool,     # 是否触发全局兜底
    }
    """
    log_metric("match_request", {"query_len": len(query)})

    # 1. NLU
    intent = await parse_query_intent(query)
    if city_hint and not intent.get("city"):
        intent["city"] = city_hint
    if user_prefs:
        for k in ("city", "type"):
            if not intent.get(k) and user_prefs.get(k):
                intent[k] = user_prefs[k]

    city = intent.get("city", "")
    resource_type = intent.get("type", "")

    # 2. 多路召回
    candidates: list = []
    is_cold_start = False

    if city:
        candidates = get_lanterns_multi_filter(city=city, resource_type=resource_type, limit=50)

    # 周边城市回退
    if not candidates and city:
        for nearby in NEARBY_CITIES.get(city, []):
            candidates = get_lanterns_multi_filter(city=nearby, resource_type=resource_type, limit=30)
            if candidates:
                break

    # 全局高可信兜底
    if not candidates:
        candidates = get_high_trust_lanterns(limit=30)
        if not candidates:
            candidates = list(
                lanterns_col.find({"status": "approved"}, {"_id": 0})
                .sort("submitted_at", DESCENDING)
                .limit(30)
            )
        is_cold_start = True

    # 去重
    seen: set = set()
    unique: list = []
    for l in candidates:
        lid = l.get("lantern_id", "")
        if lid not in seen:
            seen.add(lid)
            unique.append(l)
    candidates = unique

    if not candidates:
        log_metric("match_empty_result", {"city": city, "type": resource_type})
        return {
            "results": [],
            "parsed_intent": intent,
            "missing_slots": intent.get("missing_slots", []),
            "anti_fraud_warning": "",
            "is_cold_start": False,
        }

    # 3. 规则评分（Stage 1）：高风险资源排到末尾
    for l in candidates:
        l["_rule_score"] = _compute_rule_score(l)

    candidates.sort(
        key=lambda l: (
            0 if (
                (l.get("authenticity_score") or 50) < 30
                or len(l.get("reports", [])) >= 3
            ) else 1,
            l["_rule_score"],
        ),
        reverse=True,
    )
    top_candidates = candidates[:20]

    # 4. 反诈检测
    triggered = check_anti_fraud(query)
    anti_fraud_warning = ""
    if triggered:
        kws = "、".join(triggered)
        anti_fraud_warning = (
            f"🚨 <b>月影守护提醒</b>\n"
            f"检测到高危词汇「{kws}」\n"
            "⚠️ 请务必当面验证身份，切勿提前转账、充值或付定金！"
        )
        log_metric("anti_fraud_triggered", {"keywords": triggered})

    # 5. LLM 重排（Stage 2）
    results = await _llm_rerank(query, top_candidates)

    if not results:
        results = sorted(top_candidates, key=lambda x: x.get("_rule_score", 0), reverse=True)[:5]
        for l in results:
            l.pop("_rule_score", None)
            l.setdefault("match_score", 50)
            l.setdefault("match_reason", "综合评分推荐")
            l.setdefault("risk_tip", "")

    log_metric("match_success", {"result_count": len(results), "is_cold_start": is_cold_start})

    return {
        "results": results,
        "parsed_intent": intent,
        "missing_slots": intent.get("missing_slots", []),
        "anti_fraud_warning": anti_fraud_warning,
        "is_cold_start": is_cold_start,
    }


# =============================================================================
# 6. 兰花鉴真（照片真实度分析）
# =============================================================================

async def analyze_authenticity(photo_file_ids: list) -> dict:
    """
    兰花鉴真：分析照片真实度。
    返回:
    {
        score: float,          # 0-100，100 最真实
        labels: list,          # 可疑标签: ai_generated / heavy_edit / stolen
        needs_review: bool,    # 是否需要人工复核
    }
    """
    default = {"score": 50.0, "labels": [], "needs_review": False}

    if not AI_API_KEY:
        logger.warning("未配置 AI API Key，跳过鉴真，返回默认分 50")
        return default

    bot_token = os.environ.get("BOT_TOKEN", "")
    photo_descriptions = []
    if bot_token:
        async with aiohttp.ClientSession() as session:
            for file_id in photo_file_ids[:3]:
                try:
                    url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
                    async with session.get(url) as resp:
                        data = await resp.json()
                        file_path = data["result"]["file_path"]
                    photo_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                    photo_descriptions.append(f"照片链接：{photo_url}")
                except Exception as e:
                    logger.warning("获取照片链接失败 %s: %s", file_id, e)

    if not photo_descriptions:
        photo_descriptions = [f"共 {len(photo_file_ids)} 张照片（无法获取链接）"]

    system_prompt = (
        "你是专业照片真实度鉴定师。分析给定照片，检测 PS 修图、AI 生成、盗图迹象。\n"
        "以 JSON 格式返回（只返回 JSON）：\n"
        '{"score":0-100整数,"labels":["ai_generated","heavy_edit","stolen"中实际存在的可疑类型]}\n'
        "100 为完全真实，0 为完全造假；labels 只列出确实存在的可疑类型。"
    )
    user_msg = "请鉴定以下照片的真实度：\n" + "\n".join(photo_descriptions)

    try:
        raw = await _call_ai(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]
        )
        data = json.loads(raw)
        score = max(0.0, min(100.0, float(data.get("score", 50))))
        valid_labels = {"ai_generated", "heavy_edit", "stolen"}
        labels = [lb for lb in data.get("labels", []) if lb in valid_labels]
        needs_review = score < 40 or len(labels) >= 2
        return {"score": score, "labels": labels, "needs_review": needs_review}
    except Exception as e:
        logger.error("AI 鉴真调用失败: %s", e)
        return default


# =============================================================================
# 7. 月影会话质量评分
# =============================================================================

async def score_session_quality(messages: list) -> float:
    """
    对匿名会话消息列表进行 AI 质量评分（0-100）。
    评估维度：消息丰富度、真诚度、互动深度。
    无 AI 时返回默认 60.0。
    """
    if not AI_API_KEY or not messages:
        return 60.0

    sample = messages[-30:]
    text_sample = "\n".join(
        f"[{'A' if i % 2 == 0 else 'B'}]: {m.get('text', '')[:100]}"
        for i, m in enumerate(sample)
    )

    system_prompt = (
        "你是月影会话质量评估引擎。根据以下双方对话内容，评估其真诚度和互动质量（0-100）。\n"
        "高分（>70）：双方积极互动、内容真诚具体。\n"
        "低分（<40）：内容敷衍、明显机器人对话、刷分行为。\n"
        "只返回一个 0-100 的整数，不要其他内容。"
    )
    try:
        raw = await _call_ai(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"对话内容：\n{text_sample}"},
            ],
            temperature=0.1,
            max_tokens=10,
        )
        return max(0.0, min(100.0, float(raw.strip())))
    except Exception as e:
        logger.error("会话质量评分失败: %s", e)
        return 60.0
