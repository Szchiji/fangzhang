# bot.py - 极简完整版
from pyrogram import Client, filters, types
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import *
from database import get_conn
import asyncio
from datetime import date, datetime, timedelta

app = Client("fangzhang", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# 欢迎 + 验证码
@app.on_message(filters.new_chat_members & filters.group)
async def welcome(client, message):
    conn = get_conn()
    row = conn.execute("SELECT welcome, captcha_enabled FROM groups WHERE group_id=?", (message.chat.id,)).fetchone()
    conn.close()
    if not row:
        return
    
    for user in message.new_chat_members:
        mention = user.mention
        if row['welcome']:
            await message.reply(row['welcome'].replace("{user}", mention))
        
        if row['captcha_enabled']:
            code = "1234"  # 简单固定，可后台改
            btn = InlineKeyboardMarkup([[InlineKeyboardButton(code, callback_data=f"captcha_{user.id}_{code}")]])
            await message.reply(f"{mention} 请点击下方验证码完成验证", reply_markup=btn)
            await client.restrict_chat_member(message.chat.id, user.id, permissions=types.ChatPermissions(can_send_messages=False))

# 验证码验证
@app.on_callback_query(filters.regex(r"^captcha_"))
async def captcha_verify(client, cb):
    parts = cb.data.split("_")
    if len(parts) == 3 and cb.from_user.id == int(parts[1]) and cb.message.reply_markup.inline_keyboard[0][0].text == parts[2]:
        await client.restrict_chat_member(cb.message.chat.id, cb.from_user.id, permissions=types.ChatPermissions(can_send_messages=True))
        await cb.edit_message_text("✅ 验证成功！欢迎发言")
    else:
        await cb.answer("验证码错误", show_alert=True)

# 自动回复
@app.on_message(filters.text & filters.group)
async def auto_reply(client, message):
    conn = get_conn()
    rules = conn.execute("SELECT * FROM auto_replies WHERE group_id=? AND enabled=1", (message.chat.id,)).fetchall()
    conn.close()
    
    text = message.text.lower()
    for rule in rules:
        condition = rule['condition_text'].lower()
        if (rule['condition_type'] == 'contains' and condition in text) or \
           (rule['condition_type'] == 'equals' and text == condition):
            await message.reply(rule['reply_content'])
            break

# 认证用户打卡 + 查询（只显示已打卡用户）
@app.on_message(filters.text & filters.group)
async def checkin_and_query(client, message):
    text = message.text.strip()
    if text not in ["打卡", "签到", "在线用户"]:
        return
    
    group_id = message.chat.id
    user_id = message.from_user.id
    today = date.today().isoformat()
    
    conn = get_conn()
    
    if text in ["打卡", "签到"]:
        # 检查是否认证用户
        cert = conn.execute("SELECT * FROM certified_users WHERE group_id=? AND user_id=?", (group_id, user_id)).fetchone()
        if not cert:
            await message.reply("❌ 仅认证用户可以打卡")
            conn.close()
            return
        
        if cert['last_checkin'] == today:
            await message.reply(f"✅ 你今天已打卡，连签 {cert['checkin_streak']} 天")
        else:
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            streak = cert['checkin_streak'] + 1 if cert['last_checkin'] == yesterday else 1
            conn.execute("UPDATE certified_users SET checkin_streak=?, last_checkin=?, total_checkins=total_checkins+1 WHERE group_id=? AND user_id=?",
                         (streak, today, group_id, user_id))
            conn.commit()
            await message.reply(f"🎉 打卡成功！当前连签 {streak} 天")
    
    elif text == "在线用户":
        users = conn.execute("SELECT cu.*, u.first_name, u.username FROM certified_users cu LEFT JOIN users u ON cu.user_id = u.id WHERE cu.group_id=? AND cu.last_checkin=?", (group_id, today)).fetchall()
        if not users:
            await message.reply("📭 今日暂无认证用户打卡")
        else:
            list_text = "📊 今日已打卡认证用户：\n\n"
            for u in users:
                name = u['first_name']
                if u['username']:
                    name = f"@{u['username']}"
                list_text += f"🟢 {name} - 连签 {u['checkin_streak']} 天\n"
            await message.reply(list_text)
    
    conn.close()
    # 自动删除指令
    await asyncio.sleep(30)
    await message.delete()

# 定时发送（简单每天固定时间）
async def daily_tasks():
    while True:
        await asyncio.sleep(60)
        now = datetime.now().strftime("%H:%M")
        conn = get_conn()
        tasks = conn.execute("SELECT * FROM scheduled_tasks WHERE enabled=1 AND time=?", (now,)).fetchall()
        conn.close()
        for task in tasks:
            await app.send_message(task['group_id'], task['content'])

asyncio.create_task(daily_tasks())

print("极简方丈机器人启动完成！所有配置请在网页后台操作")
app.run()
