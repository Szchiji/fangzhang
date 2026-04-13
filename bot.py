"""
月影车姬机器人 - 主程序（v2）
YueYingCheJiBot - Main Bot Logic

框架：aiogram 3.x
新增功能：
  - 兰花信用六境等级 + 遮蔽限制
  - 月影媒婆 NLU 多路召回 + 两阶段排序
  - 月影会话全流程（申请 → 同意 → 中继 → 评分 → 结算）
  - 信用恢复修行任务追踪
  - 反诈检测 + 速率限制
"""

import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from models import (
    create_indexes,
    get_or_create_user,
    update_credit,
    collect_lantern,
    create_lantern,
    approve_lantern,
    reject_lantern,
    report_lantern,
    get_pending_lanterns,
    create_anonymous_chat,
    get_chat_by_id,
    append_message,
    mark_photo_shared,
    end_chat_naturally,
    rate_session,
    record_action_timestamp,
    get_action_timestamps,
    log_behavior,
    log_metric,
    save_user_preferences,
    get_user_preferences,
    assign_recovery_tasks_to_user,
    update_recovery_task_progress,
    try_daily_recovery,
    create_chat_request,
    get_chat_request,
    accept_chat_request,
    decline_chat_request,
    get_lantern_by_id,
    update_lantern_fields,
)
from ai import match_lanterns, analyze_authenticity, score_session_quality, check_anti_fraud
from credit import (
    get_credit_tier,
    get_eclipse_level,
    has_restriction,
    eclipse_message,
    check_rate_limit,
    detect_session_gaming,
    calculate_session_credit,
    format_session_credit_summary,
    assign_recovery_tasks,
    format_credit_report,
    format_tier_badge,
    RATE_LIMIT_PENALTY,
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
MINI_APP_URL = os.environ.get("MINI_APP_URL", "https://example.com/mini_app.html")
ADMIN_IDS: list = [
    int(uid) for uid in os.environ.get("ADMIN_IDS", "").split(",") if uid.strip()
]

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# 缓存 bot_id（首次 get_me 后填充）
_BOT_ID: int = 0


# ---------------------------------------------------------------------------
# FSM 状态组
# ---------------------------------------------------------------------------

class SubmitLantern(StatesGroup):
    city = State()
    resource_type = State()
    price_range = State()
    description = State()
    photos = State()


class MatchQuery(StatesGroup):
    waiting_query = State()
    waiting_city_followup = State()   # 追问城市时使用


class AnonChat(StatesGroup):
    active = State()       # 正在中继消息
    rating = State()       # 等待用户提交评分


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗺 进入秘境", web_app=WebAppInfo(url=MINI_APP_URL))],
            [InlineKeyboardButton(text="🔮 媒婆匹配", callback_data="cmd:match")],
            [InlineKeyboardButton(text="🏮 投稿灯笼", callback_data="cmd:submit")],
            [InlineKeyboardButton(text="🌸 兰花令牌", callback_data="cmd:credit")],
            [InlineKeyboardButton(text="🕰 时光秘匣", callback_data="cmd:collection")],
            [InlineKeyboardButton(text="🛡 车姬守护", callback_data="cmd:guard")],
        ]
    )


def stars_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    """会话结束后的评分键盘。"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text=f"{'⭐' * i}", callback_data=f"rate:{chat_id}:{i}")
            for i in range(1, 6)
        ]]
    )


def anon_chat_action_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    """匿名聊天中的操作键盘。"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎭 申请互揭真身", callback_data=f"anon:reveal:{chat_id}")],
            [InlineKeyboardButton(text="🚪 结束会话", callback_data=f"anon:end:{chat_id}")],
        ]
    )


async def _get_bot_id() -> int:
    global _BOT_ID
    if not _BOT_ID:
        me = await bot.get_me()
        _BOT_ID = me.id
    return _BOT_ID


async def _check_eclipse(user_id: int, restriction: str, target) -> bool:
    """
    检查遮蔽限制。受限则发送提示并返回 True（调用方应 return）。
    target: Message 或 CallbackQuery。
    """
    user = await get_or_create_user(user_id)
    score = user.get("credit_score", 100)
    if not has_restriction(score, restriction):
        return False

    msg = eclipse_message(score, restriction)
    if isinstance(target, CallbackQuery):
        await target.answer()
        await target.message.answer(msg)
    else:
        await target.answer(msg)
    return True


async def _check_rate_limit(user_id: int, action_type: str, target) -> bool:
    """
    检查速率限制。超限则发提示、扣分并返回 True。
    """
    timestamps = await get_action_timestamps(user_id, action_type)
    allowed, remaining = check_rate_limit(timestamps, action_type)
    if allowed:
        await record_action_timestamp(user_id, action_type)
        return False

    from credit import RATE_LIMITS
    cfg = RATE_LIMITS.get(action_type, {})
    msg = (
        f"⚡ <b>月影节流提醒</b>\n\n"
        f"你在 {cfg.get('window_hours', 24)} 小时内操作过于频繁（{cfg.get('label', '')}）。\n"
        "稍后再试，或保持良好节奏维护兰花信用 🌙"
    )
    if isinstance(target, CallbackQuery):
        await target.answer()
        await target.message.answer(msg)
    else:
        await target.answer(msg)

    # 超限扣分
    await update_credit(user_id, RATE_LIMIT_PENALTY, "触发速率限制")
    await log_metric("rate_limit_hit", {"user_id": user_id, "action": action_type})
    return True


async def _apply_eclipse_if_needed(user_id: int):
    """若信用分进入遮蔽区间，自动分配修行任务。"""
    user = await get_or_create_user(user_id)
    score = user.get("credit_score", 100)
    tasks = assign_recovery_tasks(score)
    if tasks:
        await assign_recovery_tasks_to_user(user_id, tasks)


def _notify_recovery_completions(newly_completed: list) -> str:
    """将新完成的修行任务格式化为通知文本。"""
    if not newly_completed:
        return ""
    lines = ["\n🎊 <b>修行任务完成！</b>"]
    for t in newly_completed:
        lines.append(f"  ✅ {t['description']} → +{t['reward']} 兰花令")
    return "\n".join(lines)


def _format_match_result(results: list, intent: dict, is_cold_start: bool) -> str:
    """将媒婆匹配结果格式化为 Telegram HTML 消息。"""
    city = intent.get("city", "")
    cold_note = "（未找到精准匹配，为你呈现全局精选）" if is_cold_start else ""
    city_note = f"于 <b>{city}</b>" if city else "于全境秘境"

    lines = [f"🏮 <b>月影媒婆为你寻得 {len(results)} 盏灯笼</b>{cold_note}\n"]
    for i, r in enumerate(results[:5], 1):
        auth_val = r.get("authenticity_score")
        auth = f"{auth_val}%" if auth_val is not None else "未鉴定"
        label_map = {"ai_generated": "疑似AI生成", "heavy_edit": "重度修图", "stolen": "疑似盗图"}
        auth_labels = r.get("authenticity_labels", [])
        label_str = "、".join(label_map.get(lb, lb) for lb in auth_labels)
        risk_note = f"  ⚠️ {label_str}" if label_str else ""
        risk_tip = r.get("risk_tip", "")
        risk_tip_str = f"\n  💡 风险提示：{risk_tip}" if risk_tip else ""

        lines.append(
            f"{i}. 🌙 <b>{r.get('city', '?')} · {r.get('type', '?')}</b>\n"
            f"  💰 价位：{r.get('price_range', '?')} | 真实度：{auth}{risk_note}\n"
            f"  📝 {r.get('description', '')[:60]}…\n"
            f"  ✨ 匹配度：<b>{r.get('match_score', '?')}%</b>　{r.get('match_reason', '')}"
            f"{risk_tip_str}"
        )
    return "\n".join(lines)


def _result_actions_keyboard(results: list) -> InlineKeyboardMarkup:
    """为每个匹配结果提供收藏 + 申请月影聊天的快捷按钮。"""
    rows = []
    for i, r in enumerate(results[:5], 1):
        lid = r.get("lantern_id", "")
        rows.append([
            InlineKeyboardButton(text=f"🕰 收藏#{i}", callback_data=f"collect:{lid}"),
            InlineKeyboardButton(text=f"💌 聊#{i}", callback_data=f"anon:req:{lid}"),
        ])
    rows.append([InlineKeyboardButton(text="🔮 再次匹配", callback_data="cmd:match")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# /start 命令
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name or "",
    )
    credit = user.get("credit_score", 100)

    # 日常无违规恢复
    recovered = await try_daily_recovery(message.from_user.id)
    recovery_note = f"\n🌱 今日恢复 <b>+{recovered}</b> 兰花令" if recovered else ""

    badge = format_tier_badge(credit)
    await message.answer(
        "🌙 <b>月影车姬欢迎你，老司机！</b> 🌙\n\n"
        "在月下秘境中，每一盏灯笼都藏着真实的邂逅。\n"
        "让月影媒婆为你牵线，兰花会守护你的信任。\n\n"
        f"✨ 兰花令：{badge}{recovery_note}\n\n"
        "<i>月下寻花，影中见真</i> — 选择你的探索之路：",
        reply_markup=main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# 月影媒婆 AI 匹配
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:match")
async def cb_match(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if await _check_eclipse(callback.from_user.id, "no_match", callback):
        return

    # 展示上次偏好提示
    prefs = await get_user_preferences(callback.from_user.id)
    hint = ""
    if prefs.get("city") or prefs.get("type"):
        parts = []
        if prefs.get("city"):
            parts.append(f"城市：{prefs['city']}")
        if prefs.get("type"):
            parts.append(f"类型：{prefs['type']}")
        hint = f"\n\n<i>💡 上次偏好：{' · '.join(parts)}（可直接描述新需求）</i>"

    await callback.message.answer(
        "🔮 <b>月影媒婆已燃起红烛，恭候老司机驾临。</b>\n\n"
        "请用自然语言描述你的心仪之选，例如：\n"
        "<i>「台北 大学生 KH 6000左右，需要真实照」</i>\n\n"
        "媒婆将为你在月影秘境中寻访最合适的灯笼 ✨"
        + hint
    )
    await state.set_state(MatchQuery.waiting_query)


@router.message(MatchQuery.waiting_query)
async def handle_match_query(message: Message, state: FSMContext):
    if await _check_rate_limit(message.from_user.id, "match", message):
        await state.clear()
        return

    query = message.text.strip() if message.text else ""
    if not query:
        await message.answer("请输入有效的描述文字。")
        return

    await state.clear()
    await message.answer("🌙 月影媒婆正在月下为你寻灯……请稍候 ✨")

    user_prefs = await get_user_preferences(message.from_user.id)

    try:
        result = await match_lanterns(query, user_prefs=user_prefs)
    except Exception as e:
        logger.error("AI 匹配失败: %s", e)
        await message.answer("抱歉，媒婆暂时不在线，请稍后再试。")
        return

    # 反诈警告优先展示
    if result.get("anti_fraud_warning"):
        await message.answer(result["anti_fraud_warning"])

    results = result.get("results", [])
    intent = result.get("parsed_intent", {})
    missing = result.get("missing_slots", [])

    if not results:
        # 若城市为缺失槽位，追问
        if "city" in missing:
            await message.answer(
                "🕯 媒婆在月影秘境中转了一圈，暂未找到匹配的灯笼。\n\n"
                "请告诉媒婆你在哪座城市？（如：台北、香港、深圳）"
            )
            await state.set_state(MatchQuery.waiting_city_followup)
            await state.update_data(original_query=query)
            return

        await message.answer(
            "🕯 月光照遍秘境，暂未寻得心仪灯笼。\n"
            "请换个描述（如具体城市、价位）再试，或稍后再来。",
            reply_markup=main_menu_keyboard(),
        )
        return

    # 保存偏好
    await save_user_preferences(message.from_user.id, intent)
    await log_behavior(message.from_user.id, "match", metadata={"city": intent.get("city"), "type": intent.get("type")})

    cold_note = result.get("is_cold_start", False)
    text = _format_match_result(results, intent, cold_note)
    kb = _result_actions_keyboard(results)
    await message.answer(text, reply_markup=kb)


@router.message(MatchQuery.waiting_city_followup)
async def handle_city_followup(message: Message, state: FSMContext):
    """用户回复了城市后，重新执行匹配。"""
    data = await state.get_data()
    await state.clear()

    city = message.text.strip() if message.text else ""
    original_query = data.get("original_query", "")
    combined_query = f"{city} {original_query}".strip()

    await message.answer("🌙 媒婆已记下，重新为你寻灯……")

    user_prefs = await get_user_preferences(message.from_user.id)
    try:
        result = await match_lanterns(combined_query, city_hint=city, user_prefs=user_prefs)
    except Exception as e:
        logger.error("AI 匹配失败（追问后）: %s", e)
        await message.answer("抱歉，媒婆暂时不在线，请稍后再试。")
        return

    results = result.get("results", [])
    if not results:
        await message.answer(
            "🕯 月光照遍秘境，暂未寻得心仪灯笼。\n换个描述或稍后再试。",
            reply_markup=main_menu_keyboard(),
        )
        return

    intent = result.get("parsed_intent", {})
    await save_user_preferences(message.from_user.id, intent)
    text = _format_match_result(results, intent, result.get("is_cold_start", False))
    await message.answer(text, reply_markup=_result_actions_keyboard(results))


# ---------------------------------------------------------------------------
# 结果动作：收藏灯笼
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("collect:"))
async def cb_collect(callback: CallbackQuery):
    await callback.answer()
    lantern_id = callback.data.split(":", 1)[1]
    await collect_lantern(callback.from_user.id, lantern_id)
    await log_behavior(callback.from_user.id, "collect", lantern_id)
    await callback.message.answer("🕰 已收藏到你的时光秘匣！")


# ---------------------------------------------------------------------------
# 灯笼投稿流程
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:submit")
async def cb_submit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if await _check_eclipse(callback.from_user.id, "no_submit", callback):
        return
    if await _check_rate_limit(callback.from_user.id, "submit", callback):
        return

    await callback.message.answer(
        "🏮 <b>投稿灯笼资源</b>\n\n"
        "第 1 步：请输入资源所在的<b>城市</b>（如：台北、香港、深圳）"
    )
    await state.set_state(SubmitLantern.city)


@router.message(SubmitLantern.city)
async def submit_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await message.answer("第 2 步：请输入资源<b>类型</b>（如：大学生、KH、兼职、全职）")
    await state.set_state(SubmitLantern.resource_type)


@router.message(SubmitLantern.resource_type)
async def submit_type(message: Message, state: FSMContext):
    await state.update_data(resource_type=message.text.strip())
    await message.answer("第 3 步：请输入<b>价位范围</b>（如：5000-8000）")
    await state.set_state(SubmitLantern.price_range)


@router.message(SubmitLantern.price_range)
async def submit_price(message: Message, state: FSMContext):
    await state.update_data(price_range=message.text.strip())
    await message.answer("第 4 步：请输入<b>详细描述</b>（外貌、服务、注意事项等，不超过 500 字）")
    await state.set_state(SubmitLantern.description)


@router.message(SubmitLantern.description)
async def submit_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip()[:500])
    await message.answer(
        "第 5 步：请发送<b>真实照片</b>（可发多张，发完后请回复「完成」）\n\n"
        "⚠️ 照片将经过 AI 兰花鉴真分析，确保真实度。"
    )
    await state.update_data(photo_file_ids=[])
    await state.set_state(SubmitLantern.photos)


@router.message(SubmitLantern.photos, F.photo)
async def submit_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photo_file_ids", [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(photo_file_ids=photos)
    await message.answer(f"✅ 已收到第 {len(photos)} 张照片。继续发送或回复「完成」。")


@router.message(SubmitLantern.photos, F.text == "完成")
async def submit_done(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("photo_file_ids"):
        await message.answer("请至少上传一张照片。")
        return

    lantern_id = await create_lantern(
        city=data["city"],
        resource_type=data["resource_type"],
        price_range=data["price_range"],
        description=data["description"],
        photo_file_ids=data["photo_file_ids"],
        submitted_by=message.from_user.id,
    )
    await state.clear()
    await message.answer(
        "🌙 <b>灯笼已成功投稿！</b>\n\n"
        "正在等待人工 + AI 双重审核，通过后将点亮秘境。\n"
        "感谢你为兰花会做出贡献！\n\n"
        f"🆔 灯笼编号：<code>{lantern_id[:8]}…</code>",
        reply_markup=main_menu_keyboard(),
    )

    task = asyncio.create_task(_async_analyze(lantern_id, data["photo_file_ids"]))
    task.add_done_callback(
        lambda t: logger.error("鉴真任务异常: %s", t.exception()) if t.exception() else None
    )
    await update_credit(message.from_user.id, +10, "投稿灯笼资源")


async def _async_analyze(lantern_id: str, photo_file_ids: list):
    """后台 AI 鉴真并更新灯笼真实度分数；高风险灯笼标记为需人工复核。"""
    try:
        result = await analyze_authenticity(photo_file_ids)
        score = result["score"]
        labels = result.get("labels", [])
        needs_review = result.get("needs_review", False)

        fields = {
            "authenticity_score": score,
            "authenticity_labels": labels,
            "updated_at": datetime.utcnow(),
        }
        if needs_review:
            fields["needs_human_review"] = True

        await update_lantern_fields(lantern_id, fields)
        logger.info("灯笼 %s 鉴真评分：%.1f | 标签：%s | 需人工：%s",
                    lantern_id, score, labels, needs_review)

        # 若真实度极低，扣除投稿者信用
        if score < 40:
            lantern = await get_lantern_by_id(lantern_id)
            if lantern and lantern.get("submitted_by"):
                uid = lantern["submitted_by"]
                await update_credit(uid, -20, "照片鉴真不合格")
                await _apply_eclipse_if_needed(uid)
                # 推进修行任务中"无违规"类任务的清零
                await update_recovery_task_progress(uid, "violation")
    except Exception as e:
        logger.error("AI 鉴真失败 %s: %s", lantern_id, e)


# ---------------------------------------------------------------------------
# 兰花令牌（信用分查询）
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:credit")
async def cb_credit(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    # 日常恢复
    recovered = await try_daily_recovery(user_id)

    user = await get_or_create_user(user_id)
    report = format_credit_report(user)

    recovery_note = f"\n🌱 今日恢复 <b>+{recovered}</b> 兰花令" if recovered else ""
    await callback.message.answer(
        report + recovery_note,
        reply_markup=main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# 时光秘匣
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:collection")
async def cb_collection(callback: CallbackQuery):
    await callback.answer()
    user = await get_or_create_user(callback.from_user.id)
    collected = user.get("collected_lanterns", [])

    if not collected:
        await callback.message.answer(
            "🕰 <b>你的时光秘匣是空的。</b>\n\n"
            "在秘境中找到心仪的灯笼后，点击「收藏」即可存入秘匣。",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = f"🕰 <b>你的时光秘匣（{len(collected)} 盏灯笼）</b>\n\n"
    for lid in collected[:10]:
        text += f"• <code>{lid[:8]}…</code>\n"
    if len(collected) > 10:
        text += f"\n…及其他 {len(collected) - 10} 盏"

    await callback.message.answer(text, reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# 车姬守护
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:guard")
async def cb_guard(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🛡 <b>车姬守护模式</b>\n\n"
        "将月影车姬机器人加入你的群组并授予管理员权限，即可开启守护：\n\n"
        "• 自动过滤广告和中介话术\n"
        "• 识别骗子关键词并提醒新人\n"
        "• 新人进群提示「先查信用分」\n\n"
        "在群组内发送 /guard_on 开启，/guard_off 关闭。",
        reply_markup=main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# 匿名月影会话 — 申请流程
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("anon:req:"))
async def cb_request_anon_chat(callback: CallbackQuery):
    """用户申请与灯笼主开启匿名月影会话。"""
    await callback.answer()
    if await _check_eclipse(callback.from_user.id, "no_session", callback):
        return
    if await _check_rate_limit(callback.from_user.id, "session", callback):
        return

    lantern_id = callback.data.split(":", 2)[2]
    lantern = await get_lantern_by_id(lantern_id)
    if not lantern:
        await callback.message.answer("该灯笼已不存在。")
        return

    owner_id = lantern.get("submitted_by")
    if not owner_id or owner_id == callback.from_user.id:
        await callback.message.answer("无法向自己的灯笼发起月影会话。")
        return

    request_id = await create_chat_request(callback.from_user.id, lantern_id, owner_id)

    # 通知灯笼主
    consent_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ 同意开启月影会话", callback_data=f"anon:accept:{request_id}"),
            InlineKeyboardButton(text="❌ 婉拒", callback_data=f"anon:decline:{request_id}"),
        ]]
    )
    try:
        await bot.send_message(
            owner_id,
            f"🌙 <b>有人向你的灯笼发起月影会话申请！</b>\n\n"
            f"灯笼：{lantern.get('city', '?')} · {lantern.get('type', '?')}\n"
            "对方将以「寻灯人」身份与你匿名交流，24小时后会话自动销毁。\n\n"
            "是否同意开启？",
            reply_markup=consent_kb,
        )
    except Exception as e:
        logger.error("通知灯笼主失败 owner=%s: %s", owner_id, e)
        await callback.message.answer("发送申请失败，对方可能未开启私聊。")
        return

    await callback.message.answer(
        "💌 申请已发送，等待灯笼主回应。\n"
        "对方同意后，你将收到通知并进入月影会话 🌙"
    )
    await log_metric("anon_chat_requested", {"requester": callback.from_user.id, "lantern": lantern_id})


@router.callback_query(F.data.startswith("anon:accept:"))
async def cb_anon_accept(callback: CallbackQuery, state: FSMContext):
    """灯笼主同意申请，创建匿名会话并通知双方。"""
    await callback.answer()
    request_id = callback.data.split(":", 2)[2]
    req = await accept_chat_request(request_id)
    if not req:
        await callback.message.answer("该申请已过期或不存在。")
        return

    requester_id = req["requester_id"]
    owner_id = req["lantern_owner_id"]

    # 创建匿名会话
    chat_id = await create_anonymous_chat(requester_id, owner_id)

    # 灯笼主进入 AnonChat 状态
    await state.set_state(AnonChat.active)
    await state.update_data(chat_id=chat_id, other_user_id=requester_id, my_alias="灯笼主")

    await callback.message.answer(
        "🌙 <b>月影会话已开启！</b>\n\n"
        "你现在以「<b>灯笼主</b>」身份与对方匿名交流。\n"
        "直接发送消息即可。会话24小时后自动销毁。",
        reply_markup=anon_chat_action_keyboard(chat_id),
    )

    # 通知申请方，让其点击进入
    enter_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="💬 进入月影会话", callback_data=f"anon:enter:{chat_id}")
        ]]
    )
    try:
        await bot.send_message(
            requester_id,
            "🌙 <b>灯笼主已同意，月影会话准备就绪！</b>\n\n"
            "点击下方按钮以「<b>寻灯人</b>」身份进入匿名会话。",
            reply_markup=enter_kb,
        )
    except Exception as e:
        logger.error("通知申请方失败 requester=%s: %s", requester_id, e)

    await log_metric("anon_chat_started", {"chat_id": chat_id})


@router.callback_query(F.data.startswith("anon:decline:"))
async def cb_anon_decline(callback: CallbackQuery):
    """灯笼主婉拒申请。"""
    await callback.answer()
    request_id = callback.data.split(":", 2)[2]
    req = await get_chat_request(request_id)
    if req:
        await decline_chat_request(request_id)
        try:
            await bot.send_message(
                req["requester_id"],
                "🌒 灯笼主暂时不方便开启月影会话，请换一盏灯笼试试 🌙",
            )
        except Exception:
            pass
    await callback.message.answer("已婉拒对方的月影会话申请。")


@router.callback_query(F.data.startswith("anon:enter:"))
async def cb_anon_enter(callback: CallbackQuery, state: FSMContext):
    """申请方点击进入会话。"""
    await callback.answer()
    chat_id = callback.data.split(":", 2)[2]
    chat = await get_chat_by_id(chat_id)
    if not chat:
        await callback.message.answer("该月影会话已失效。")
        return

    uid = callback.from_user.id
    other = chat["user2"] if chat["user1"] == uid else chat["user1"]

    await state.set_state(AnonChat.active)
    await state.update_data(chat_id=chat_id, other_user_id=other, my_alias="寻灯人")

    await callback.message.answer(
        "🌙 <b>月影会话已就绪！</b>\n\n"
        "你现在以「<b>寻灯人</b>」身份与灯笼主匿名交流。\n"
        "直接发送消息即可。会话24小时后自动销毁。",
        reply_markup=anon_chat_action_keyboard(chat_id),
    )


# ---------------------------------------------------------------------------
# 匿名会话 — 消息中继
# ---------------------------------------------------------------------------

@router.message(AnonChat.active)
async def handle_anon_message(message: Message, state: FSMContext):
    """在匿名会话中中继消息。"""
    data = await state.get_data()
    chat_id = data.get("chat_id")
    other_user_id = data.get("other_user_id")
    my_alias = data.get("my_alias", "月影")

    if not chat_id or not other_user_id:
        await message.answer("会话状态异常，已退出。")
        await state.clear()
        return

    # 记录消息
    text = message.text or ""
    if text:
        await append_message(chat_id, message.from_user.id, text)

    # 记录照片
    if message.photo:
        await mark_photo_shared(chat_id, message.from_user.id)

    # 转发给对方
    try:
        if message.text:
            await bot.send_message(
                other_user_id,
                f"🌙 <b>{my_alias}</b>：{message.text}",
            )
        elif message.photo:
            await bot.send_photo(
                other_user_id,
                message.photo[-1].file_id,
                caption=f"🌙 <b>{my_alias}</b> 分享了一张照片",
            )
        elif message.sticker:
            await bot.send_sticker(other_user_id, message.sticker.file_id)
        else:
            await bot.send_message(other_user_id, f"🌙 <b>{my_alias}</b> 发送了一条消息（不支持的格式）")
    except Exception as e:
        logger.error("消息中继失败 chat=%s: %s", chat_id, e)
        await message.answer("消息发送失败，对方可能已离线。")


@router.callback_query(F.data.startswith("anon:end:"))
async def cb_anon_end(callback: CallbackQuery, state: FSMContext):
    """用户主动结束会话，触发评分流程。"""
    await callback.answer()
    data = await state.get_data()
    chat_id = data.get("chat_id") or callback.data.split(":", 2)[2]
    other_user_id = data.get("other_user_id")

    await end_chat_naturally(chat_id)
    await state.set_state(AnonChat.rating)
    await state.update_data(chat_id=chat_id, other_user_id=other_user_id)

    await callback.message.answer(
        "🌙 <b>月影会话即将结束。</b>\n\n"
        "请为这次邂逅打分，分数将影响双方的兰花令信用：",
        reply_markup=stars_keyboard(chat_id),
    )

    # 通知对方
    if other_user_id:
        try:
            await bot.send_message(
                other_user_id,
                "🌒 <b>对方已结束月影会话。</b>\n\n"
                "请为这次邂逅打分，兰花令即将结算：",
                reply_markup=stars_keyboard(chat_id),
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("rate:"))
async def cb_rate_session(callback: CallbackQuery, state: FSMContext):
    """接收评分并尝试结算双方积分。"""
    await callback.answer()
    _, chat_id, stars_str = callback.data.split(":", 2)
    stars = int(stars_str)
    user_id = callback.from_user.id

    if await _check_rate_limit(user_id, "rate", callback):
        return

    session = await rate_session(chat_id, user_id, stars)
    await state.clear()

    await callback.message.answer(f"⭐ 已提交 {'⭐' * stars} 评分，感谢你的反馈！")

    if not session:
        return

    ratings = session.get("ratings", {})
    if len(ratings) < 2:
        return  # 等待对方评分

    # 双方均已评分 → 结算
    user1, user2 = session["user1"], session["user2"]
    r1 = ratings.get(str(user1), {}).get("stars", 3)
    r2 = ratings.get(str(user2), {}).get("stars", 3)
    messages = session.get("messages", [])
    created_at = session.get("created_at", datetime.utcnow())
    ended_at = session.get("ended_at", datetime.utcnow())
    duration_minutes = max(0, (ended_at - created_at).total_seconds() / 60)

    photos_shared = session.get("photos_shared", {})
    u1_photo = photos_shared.get(str(user1), False)
    u2_photo = photos_shared.get(str(user2), False)
    both_photos = u1_photo and u2_photo
    one_photo = u1_photo or u2_photo
    completed_naturally = session.get("completed_naturally", False)

    # AI 质量评分
    ai_quality = await score_session_quality(messages)

    # 刷分检测
    gaming = detect_session_gaming(
        duration_minutes, len(messages), r1, r2
    )

    # 分别结算
    for uid, my_rating, their_rating in [(user1, r2, r1), (user2, r1, r2)]:
        delta, breakdown = calculate_session_credit(
            rating_received=their_rating,
            duration_minutes=duration_minutes,
            photos_both_shared=both_photos,
            photo_one_shared=one_photo,
            completed_naturally=completed_naturally,
            ai_quality_score=ai_quality,
            fraud_complaint=False,
            gaming_detected=gaming,
        )
        reason = "月影会话评分结算"
        if gaming:
            reason = "月影会话结算（刷分检测）"
        await update_credit(uid, delta, reason)
        await _apply_eclipse_if_needed(uid)

        # 推进修行任务
        task_action = "session_good_4plus" if their_rating >= 4 else ""
        if task_action:
            newly_done = await update_recovery_task_progress(uid, task_action)
        else:
            newly_done = []
        for done_task in newly_done:
            await update_credit(uid, done_task["reward"], f"修行任务完成：{done_task['description']}")

        summary = format_session_credit_summary(breakdown, delta)
        task_note = _notify_recovery_completions(newly_done)
        try:
            await bot.send_message(uid, summary + task_note)
        except Exception as e:
            logger.error("发送结算消息失败 uid=%s: %s", uid, e)

    if gaming:
        await log_metric("session_gaming_detected", {"chat_id": chat_id})


# ---------------------------------------------------------------------------
# 举报灯笼
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("anon:reveal:"))
async def cb_anon_reveal(callback: CallbackQuery, state: FSMContext):
    """申请互揭真身——通知对方确认。"""
    await callback.answer()
    data = await state.get_data()
    chat_id = data.get("chat_id") or callback.data.split(":", 2)[2]
    other_user_id = data.get("other_user_id")

    if not other_user_id:
        await callback.message.answer("会话状态异常。")
        return

    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ 同意揭开", callback_data=f"anon:revealok:{chat_id}:{callback.from_user.id}"),
            InlineKeyboardButton(text="❌ 暂不揭开", callback_data="anon:revealno"),
        ]]
    )
    try:
        await bot.send_message(
            other_user_id,
            "🎭 <b>对方申请互揭真身。</b>\n\n"
            "同意后，双方将互相看到对方的 Telegram 用户名。\n"
            "你是否同意？",
            reply_markup=confirm_kb,
        )
        await callback.message.answer("✨ 已向对方发送揭身申请，等待确认…")
    except Exception as e:
        logger.error("揭身通知失败: %s", e)


@router.callback_query(F.data.startswith("anon:revealok:"))
async def cb_reveal_ok(callback: CallbackQuery, state: FSMContext):
    """对方同意揭身，双方收到用户名。"""
    await callback.answer()
    parts = callback.data.split(":")
    chat_id = parts[2]
    requester_id = int(parts[3])

    chat = await get_chat_by_id(chat_id)
    if not chat:
        await callback.message.answer("会话已失效。")
        return

    # 获取双方 Telegram 信息
    try:
        req_user = await bot.get_chat(requester_id)
        acc_user = await bot.get_chat(callback.from_user.id)
        req_name = f"@{req_user.username}" if req_user.username else req_user.full_name
        acc_name = f"@{acc_user.username}" if acc_user.username else acc_user.full_name
    except Exception:
        req_name = "对方"
        acc_name = "你"

    reveal_msg_to_req = f"🎭 对方已同意揭身！\n\n灯笼主：<b>{acc_name}</b>"
    reveal_msg_to_acc = f"🎭 揭身成功！\n\n寻灯人：<b>{req_name}</b>"

    try:
        await bot.send_message(requester_id, reveal_msg_to_req)
    except Exception:
        pass
    await callback.message.answer(reveal_msg_to_acc)


@router.callback_query(F.data == "anon:revealno")
async def cb_reveal_no(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("🌒 已婉拒揭身申请，月影继续守护双方隐私。")


# ---------------------------------------------------------------------------
# 管理员：审核待审灯笼
# ---------------------------------------------------------------------------

@router.message(Command("admin_pending"))
async def cmd_admin_pending(message: Message):
    if not is_admin(message.from_user.id):
        return

    pending = await get_pending_lanterns(limit=5)
    if not pending:
        await message.answer("✅ 暂无待审核灯笼。")
        return

    for lantern in pending:
        lid = lantern["lantern_id"]
        auth_val = lantern.get("authenticity_score")
        auth_str = f"{auth_val}%" if auth_val is not None else "分析中"
        label_map = {"ai_generated": "疑似AI", "heavy_edit": "重修图", "stolen": "盗图"}
        labels = lantern.get("authenticity_labels", [])
        label_str = "、".join(label_map.get(lb, lb) for lb in labels)
        needs_review = lantern.get("needs_human_review", False)

        text = (
            f"🏮 <b>待审核灯笼</b>{'🚨 需重点核查' if needs_review else ''}\n"
            f"ID：<code>{lid[:8]}…</code>\n"
            f"城市：{lantern.get('city')} | 类型：{lantern.get('type')}\n"
            f"价位：{lantern.get('price_range')}\n"
            f"真实度：{auth_str}{(' (' + label_str + ')') if label_str else ''}\n"
            f"描述：{lantern.get('description', '')[:100]}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="✅ 通过", callback_data=f"admin:approve:{lid}"),
                InlineKeyboardButton(text="❌ 拒绝", callback_data=f"admin:reject:{lid}"),
            ]]
        )
        photos = lantern.get("photo_file_ids", [])
        if photos:
            await message.answer_photo(photos[0], caption=text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("admin:approve:"))
async def cb_admin_approve(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    lantern_id = callback.data.split(":", 2)[2]
    await approve_lantern(lantern_id)

    # 奖励投稿者
    lantern = await get_lantern_by_id(lantern_id)
    if lantern and lantern.get("submitted_by"):
        uid = lantern["submitted_by"]
        await update_credit(uid, +15, "灯笼审核通过")
        # 推进修行任务
        newly_done = await update_recovery_task_progress(uid, "lantern_approved")
        for t in newly_done:
            await update_credit(uid, t["reward"], f"修行任务完成：{t['description']}")

    await callback.answer("✅ 已通过", show_alert=True)
    caption = (callback.message.caption or "") + "\n\n<b>✅ 已审核通过</b>"
    if callback.message.photo:
        await callback.message.edit_caption(caption)
    else:
        await callback.message.edit_text(callback.message.text + "\n\n<b>✅ 已审核通过</b>")


@router.callback_query(F.data.startswith("admin:reject:"))
async def cb_admin_reject(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    lantern_id = callback.data.split(":", 2)[2]
    await reject_lantern(lantern_id)

    # 扣除投稿者信用
    lantern = await get_lantern_by_id(lantern_id)
    if lantern and lantern.get("submitted_by"):
        uid = lantern["submitted_by"]
        await update_credit(uid, -20, "灯笼审核不通过")
        await _apply_eclipse_if_needed(uid)

    await callback.answer("❌ 已拒绝", show_alert=True)
    if callback.message.photo:
        await callback.message.edit_caption(
            (callback.message.caption or "") + "\n\n<b>❌ 已拒绝</b>"
        )
    else:
        await callback.message.edit_text(callback.message.text + "\n\n<b>❌ 已拒绝</b>")


# ---------------------------------------------------------------------------
# 群管守护：新成员提醒 + 反诈检测
# ---------------------------------------------------------------------------

@router.message(F.new_chat_members)
async def on_new_member(message: Message):
    names = ", ".join(m.full_name for m in message.new_chat_members)
    await message.answer(
        f"🌙 欢迎 {names} 加入月影秘境！\n\n"
        "⚠️ 温馨提示：交流前请先在机器人私聊中查询对方<b>兰花令信用分</b>，保护自己安全！\n"
        "私聊机器人发送 /start 即可开始。"
    )


@router.message(F.text & F.chat.type.in_({"group", "supergroup"}))
async def group_anti_fraud_monitor(message: Message):
    """群组消息反诈监控：检测高危关键词并提醒。"""
    if not message.text:
        return
    triggered = check_anti_fraud(message.text)
    if triggered:
        kws = "、".join(triggered)
        await message.reply(
            f"🚨 <b>月影守护提醒</b>\n\n"
            f"检测到高危词汇「{kws}」\n"
            "⚠️ 请勿提前转账、充值或付定金！"
        )
        await log_metric("group_anti_fraud_triggered", {
            "chat_id": message.chat.id,
            "user_id": message.from_user.id,
            "keywords": triggered,
        })


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

async def main():
    logger.info("正在初始化数据库索引…")
    create_indexes()
    logger.info("🌙 月影车姬机器人（v2）启动中…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
