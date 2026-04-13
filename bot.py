"""
月影车姬机器人 - 主程序
YueYingCheJiBot - Main Bot Logic

框架：aiogram 3.x
功能：
  - /start：欢迎词 + 功能菜单
  - 月影媒婆 AI 匹配（自然语言查询）
  - 灯笼投稿 & 审核流程
  - 兰花信用分查询
  - 时光秘匣（收藏 & 提醒）
  - 车姬守护（群管模式）
  - 管理员审核面板
"""

import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from models import (
    create_indexes,
    get_or_create_user,
    update_credit,
    collect_lantern,
    create_lantern,
    approve_lantern,
    reject_lantern,
    report_lantern,
    get_lanterns_by_city,
    get_pending_lanterns,
)
from ai import match_lanterns, analyze_authenticity

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
ADMIN_IDS: list[int] = [
    int(uid) for uid in os.environ.get("ADMIN_IDS", "").split(",") if uid.strip()
]

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)


# ---------------------------------------------------------------------------
# FSM 状态组
# ---------------------------------------------------------------------------
class SubmitLantern(StatesGroup):
    """灯笼投稿流程状态。"""
    city = State()
    resource_type = State()
    price_range = State()
    description = State()
    photos = State()


class MatchQuery(StatesGroup):
    """月影媒婆匹配流程状态。"""
    waiting_query = State()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """构建主菜单内联键盘。"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗺 进入秘境",
                    web_app=WebAppInfo(url=MINI_APP_URL),
                )
            ],
            [InlineKeyboardButton(text="🔮 媒婆匹配", callback_data="cmd:match")],
            [InlineKeyboardButton(text="🏮 投稿灯笼", callback_data="cmd:submit")],
            [InlineKeyboardButton(text="🌸 兰花令牌", callback_data="cmd:credit")],
            [InlineKeyboardButton(text="🕰 时光秘匣", callback_data="cmd:collection")],
            [InlineKeyboardButton(text="🛡 车姬守护", callback_data="cmd:guard")],
        ]
    )


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ---------------------------------------------------------------------------
# /start 命令
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name or "",
    )
    credit = user.get("credit_score", 100)
    await message.answer(
        "🌙 <b>月影车姬欢迎你，老司机！</b> 🌙\n\n"
        "在月下秘境中，每一盏灯笼都藏着真实的邂逅。\n"
        "让月影媒婆为你牵线，兰花会守护你的信任。\n\n"
        f"✨ 你当前的兰花令：<b>{credit}</b> 枚\n\n"
        "<i>月下寻花，影中见真</i> — 选择你的探索之路：",
        reply_markup=main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# 月影媒婆 AI 匹配
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:match")
async def cb_match(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "🔮 <b>月影媒婆为你服务！</b>\n\n"
        "请用自然语言描述你的需求，例如：\n"
        "<i>「台北 大学生 KH 6000左右，需要真实照」</i>\n\n"
        "发送描述后，媒婆会为你匹配最佳灯笼 ✨"
    )
    await state.set_state(MatchQuery.waiting_query)


@router.message(MatchQuery.waiting_query)
async def handle_match_query(message: Message, state: FSMContext):
    await state.clear()
    query = message.text.strip()
    if not query:
        await message.answer("请输入有效的描述文字。")
        return

    await message.answer("🌙 月影媒婆正在为你寻灯……请稍候")

    try:
        results = await match_lanterns(query)
    except Exception as e:
        logger.error("AI 匹配失败: %s", e)
        await message.answer("抱歉，媒婆暂时不在线，请稍后再试。")
        return

    if not results:
        await message.answer("🕯 当前秘境中暂无匹配的灯笼，请换个描述试试。")
        return

    lines = ["🏮 <b>月影媒婆为你找到以下灯笼：</b>\n"]
    for i, r in enumerate(results[:5], 1):
        auth = f"{r.get('authenticity_score', '?')}%" if r.get("authenticity_score") is not None else "未鉴定"
        lines.append(
            f"{i}. 🌙 <b>{r.get('city', '?')} · {r.get('type', '?')}</b>\n"
            f"   💰 价位：{r.get('price_range', '?')} | 真实度：{auth}\n"
            f"   📝 {r.get('description', '')[:60]}…\n"
            f"   匹配度：<b>{r.get('match_score', '?')}%</b>"
        )

    await message.answer("\n".join(lines), reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# 灯笼投稿流程
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:submit")
async def cb_submit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
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
        "⚠️ 照片将经过 AI 鉴真分析，确保真实度。"
    )
    await state.update_data(photo_file_ids=[])
    await state.set_state(SubmitLantern.photos)


@router.message(SubmitLantern.photos, F.photo)
async def submit_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photo_file_ids", [])
    # 取最高分辨率
    photos.append(message.photo[-1].file_id)
    await state.update_data(photo_file_ids=photos)
    await message.answer(f"✅ 已收到第 {len(photos)} 张照片。继续发送或回复「完成」。")


@router.message(SubmitLantern.photos, F.text == "完成")
async def submit_done(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("photo_file_ids"):
        await message.answer("请至少上传一张照片。")
        return

    lantern_id = create_lantern(
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

    # 通知 AI 异步鉴真（不阻塞用户）
    asyncio.create_task(_async_analyze(lantern_id, data["photo_file_ids"]))

    # 奖励投稿者兰花令
    update_credit(message.from_user.id, +10, "投稿灯笼资源")


async def _async_analyze(lantern_id: str, photo_file_ids: list):
    """后台异步对照片进行 AI 鉴真，并更新灯笼真实度分数。"""
    try:
        score = await analyze_authenticity(photo_file_ids)
        from models import lanterns_col
        from datetime import datetime
        lanterns_col.update_one(
            {"lantern_id": lantern_id},
            {"$set": {"authenticity_score": score, "updated_at": datetime.utcnow()}},
        )
        logger.info("灯笼 %s 真实度评分：%.1f", lantern_id, score)
    except Exception as e:
        logger.error("AI 鉴真失败 %s: %s", lantern_id, e)


# ---------------------------------------------------------------------------
# 兰花令牌（信用分查询）
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:credit")
async def cb_credit(callback: CallbackQuery):
    await callback.answer()
    user = get_or_create_user(callback.from_user.id)
    credit = user.get("credit_score", 0)
    history = user.get("credit_history", [])[-5:]  # 最近5条记录

    lines = [f"🌸 <b>你的兰花令：{credit} 枚</b>\n"]
    if history:
        lines.append("最近变动：")
        for h in reversed(history):
            sign = "+" if h["delta"] > 0 else ""
            lines.append(f"  {sign}{h['delta']} — {h.get('reason', '')}")
    else:
        lines.append("暂无变动记录。投稿优质资源或举报骗子可获得兰花令！")

    await callback.message.answer("\n".join(lines), reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# 时光秘匣（收藏列表）
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:collection")
async def cb_collection(callback: CallbackQuery):
    await callback.answer()
    user = get_or_create_user(callback.from_user.id)
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
# 车姬守护（群管模式说明）
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cmd:guard")
async def cb_guard(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🛡 <b>车姬守护模式</b>\n\n"
        "将月影车姬机器人加入你的群组，并授予管理员权限，即可开启守护模式：\n\n"
        "• 自动过滤广告和中介话术\n"
        "• 识别骗子关键词并提醒新人\n"
        "• 新人进群提示「先查信用分」\n\n"
        "在群组内发送 /guard_on 开启，/guard_off 关闭。",
        reply_markup=main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# 管理员：审核待审灯笼
# ---------------------------------------------------------------------------

@router.message(Command("admin_pending"))
async def cmd_admin_pending(message: Message):
    if not is_admin(message.from_user.id):
        return

    pending = get_pending_lanterns(limit=5)
    if not pending:
        await message.answer("✅ 暂无待审核灯笼。")
        return

    for lantern in pending:
        lid = lantern["lantern_id"]
        text = (
            f"🏮 <b>待审核灯笼</b>\n"
            f"ID：<code>{lid[:8]}…</code>\n"
            f"城市：{lantern.get('city')} | 类型：{lantern.get('type')}\n"
            f"价位：{lantern.get('price_range')}\n"
            f"真实度：{lantern.get('authenticity_score', '分析中')}%\n"
            f"描述：{lantern.get('description', '')[:100]}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ 通过", callback_data=f"admin:approve:{lid}"),
                    InlineKeyboardButton(text="❌ 拒绝", callback_data=f"admin:reject:{lid}"),
                ]
            ]
        )
        # 发送第一张照片（如有）
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
    approve_lantern(lantern_id)
    await callback.answer("✅ 已通过", show_alert=True)
    await callback.message.edit_caption(
        callback.message.caption + "\n\n<b>✅ 已审核通过</b>"
    )


@router.callback_query(F.data.startswith("admin:reject:"))
async def cb_admin_reject(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    lantern_id = callback.data.split(":", 2)[2]
    reject_lantern(lantern_id)
    await callback.answer("❌ 已拒绝", show_alert=True)
    await callback.message.edit_caption(
        callback.message.caption + "\n\n<b>❌ 已拒绝</b>"
    )


# ---------------------------------------------------------------------------
# 群管守护：新成员提醒
# ---------------------------------------------------------------------------

@router.message(F.new_chat_members)
async def on_new_member(message: Message):
    """新成员进群时提醒查看信用分。"""
    names = ", ".join(m.full_name for m in message.new_chat_members)
    await message.answer(
        f"🌙 欢迎 {names} 加入月影秘境！\n\n"
        "⚠️ 温馨提示：交流前请先在机器人私聊中查询对方<b>兰花令信用分</b>，保护自己安全！\n"
        "私聊机器人发送 /start 即可开始。"
    )


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

async def main():
    logger.info("正在初始化数据库索引…")
    create_indexes()
    logger.info("月影车姬机器人启动中… 🌙")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
