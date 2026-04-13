"""
月影车姬机器人 - AI 模块
YueYingCheJiBot - AI Integration

提供两个核心 AI 功能：
1. match_lanterns(query)   — 月影媒婆：根据自然语言查询匹配灯笼资源
2. analyze_authenticity(photo_file_ids) — 兰花鉴真：AI 分析照片真实度

默认使用 Tongyi Qianwen（通义千问）API（兼容 OpenAI 接口格式）。
若配置了 GROK_API_KEY 则优先使用 Grok。
"""

import os
import json
import logging
from typing import Optional
import aiohttp

from models import get_lanterns_by_city

logger = logging.getLogger(__name__)

# --- API 配置 ---
GROK_API_KEY = os.environ.get("GROK_API_KEY", "")
TONGYI_API_KEY = os.environ.get("TONGYI_API_KEY", "")

GROK_ENDPOINT = "https://api.x.ai/v1/chat/completions"
TONGYI_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

AI_API_KEY = GROK_API_KEY or TONGYI_API_KEY
AI_ENDPOINT = GROK_ENDPOINT if GROK_API_KEY else TONGYI_ENDPOINT
AI_MODEL = "grok-3-mini" if GROK_API_KEY else "qwen-turbo"


async def _call_ai(messages: list, temperature: float = 0.3) -> str:
    """
    通用 AI 接口调用（兼容 OpenAI 格式）。
    返回模型的文本回复，失败时抛出异常。
    """
    if not AI_API_KEY:
        raise RuntimeError("未配置 AI API Key（GROK_API_KEY 或 TONGYI_API_KEY）")

    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 1024,
    }
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(AI_ENDPOINT, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()


async def match_lanterns(query: str, city_hint: str = "") -> list:
    """
    月影媒婆：根据用户自然语言查询匹配灯笼资源。

    策略：
    1. 从查询中提取城市关键词。
    2. 从数据库拉取该城市的已审核灯笼。
    3. 调用 AI 按匹配度排序，返回 Top-5。

    返回包含匹配度的灯笼列表。
    """
    # 简单城市提取（可扩展为 NLP）
    known_cities = ["台北", "香港", "深圳", "上海", "广州", "高雄", "台中", "新竹"]
    city = city_hint
    for c in known_cities:
        if c in query:
            city = c
            break

    # 若未识别城市，尝试取全库（限 50 条）
    if city:
        candidates = get_lanterns_by_city(city, limit=50)
    else:
        from models import lanterns_col
        from pymongo import DESCENDING
        candidates = list(
            lanterns_col.find({"status": "approved"}, {"_id": 0})
            .sort("submitted_at", DESCENDING)
            .limit(50)
        )

    if not candidates:
        return []

    # 构建 AI 提示词
    lantern_summaries = []
    for l in candidates:
        lantern_summaries.append(
            f"ID={l['lantern_id'][:8]} | 城市={l.get('city')} | 类型={l.get('type')} | "
            f"价位={l.get('price_range')} | 真实度={l.get('authenticity_score', '未知')}% | "
            f"描述={l.get('description', '')[:80]}"
        )

    system_prompt = (
        "你是月影车姬机器人的专属 AI 媒婆，擅长根据用户需求精准匹配修车资源。"
        "资源信息来自数据库，以 ID|城市|类型|价位|真实度|描述 格式提供。"
        "请根据用户查询，从候选资源中选出最多 5 个最匹配的，"
        "以 JSON 数组格式返回，每项包含字段：id、match_score（0-100整数）、reason（简短中文推荐理由，不超过20字）。"
        "只返回 JSON，不要其他内容。"
    )
    user_message = (
        f"用户查询：{query}\n\n候选资源：\n" + "\n".join(lantern_summaries)
    )

    try:
        raw = await _call_ai(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )
        # 解析 JSON
        matched_ids = json.loads(raw)
    except Exception as e:
        logger.error("AI 媒婆解析失败: %s | 原始回复: %s", e, locals().get("raw", ""))
        return []

    # 将 AI 结果与原始灯笼数据合并
    id_map = {l["lantern_id"][:8]: l for l in candidates}
    results = []
    for item in matched_ids:
        short_id = str(item.get("id", ""))[:8]
        lantern = id_map.get(short_id)
        if lantern:
            lantern = dict(lantern)
            lantern["match_score"] = item.get("match_score", 0)
            lantern["match_reason"] = item.get("reason", "")
            results.append(lantern)

    results.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return results[:5]


async def analyze_authenticity(photo_file_ids: list) -> float:
    """
    兰花鉴真：分析照片真实度，返回 0-100 分。

    注意：Telegram file_id 需先通过 Bot API 下载或获取链接。
    此处接受 file_id 列表，若配置了 BOT_TOKEN 则自动获取链接。

    当 AI 不可用时返回默认分 50.0（中立）。
    """
    if not AI_API_KEY:
        logger.warning("未配置 AI API Key，跳过鉴真，返回默认分 50")
        return 50.0

    bot_token = os.environ.get("BOT_TOKEN", "")
    photo_descriptions = []
    if bot_token:
        async with aiohttp.ClientSession() as session:
            for file_id in photo_file_ids[:3]:  # 最多分析 3 张
                try:
                    # 获取文件路径
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
        "你是一位专业的照片真实度鉴定师。请分析给定照片，检测是否存在 PS 修图、AI 生成、或盗图的迹象。"
        "综合所有照片，给出一个 0-100 的真实度分数（100 为完全真实，0 为完全造假）。"
        "只返回一个整数，不要其他内容。"
    )
    user_message = "请鉴定以下照片的真实度：\n" + "\n".join(photo_descriptions)

    try:
        raw = await _call_ai(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )
        score = float(raw.strip())
        return max(0.0, min(100.0, score))
    except Exception as e:
        logger.error("AI 鉴真调用失败: %s", e)
        return 50.0
