# database.py
import sqlite3
import os
from config import DB_FILE

# 确保 data 文件夹存在
os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)

def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript('''
        -- 群组配置表
        CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY,
            welcome TEXT,
            farewell TEXT,
            captcha_enabled INTEGER DEFAULT 1,
            force_sub TEXT,
            night_mode TEXT,
            sensitive_words TEXT,
            whitelist TEXT,
            blacklist TEXT,
            auto_delete_join INTEGER DEFAULT 0,
            auto_delete_leave INTEGER DEFAULT 1,
            auto_delete_pin_notify INTEGER DEFAULT 0,
            auto_unpin_channel INTEGER DEFAULT 0,
            auto_delete_cross_promo INTEGER DEFAULT 0,
            checkin_enabled INTEGER DEFAULT 1,
            auto_like_certified INTEGER DEFAULT 1,
            exempt_filter_certified INTEGER DEFAULT 1
        );

        -- 认证用户表
        CREATE TABLE IF NOT EXISTS certified_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            user_id INTEGER UNIQUE,
            title TEXT DEFAULT "认证用户",
            custom_fields TEXT,
            certified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checkin_streak INTEGER DEFAULT 0,
            last_checkin DATE,
            total_checkins INTEGER DEFAULT 0,
            avg_rating REAL DEFAULT 0,
            rating_count INTEGER DEFAULT 0
        );

        -- 自动回复规则表
        CREATE TABLE IF NOT EXISTS auto_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            condition_type TEXT DEFAULT 'equals',
            condition_text TEXT NOT NULL,
            reply_content TEXT NOT NULL,
            buttons TEXT,
            quote_original INTEGER DEFAULT 1,
            delete_user_msg TEXT DEFAULT 'no',
            delete_bot_msg INTEGER DEFAULT 0,
            require_points INTEGER DEFAULT 0,
            admin_exempt INTEGER DEFAULT 1,
            certified_only INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1
        );

        -- 定时任务表
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            task_name TEXT,
            task_type TEXT DEFAULT 'message',
            content TEXT,
            media_type TEXT,
            media_value TEXT,
            buttons TEXT,
            repeat_type TEXT DEFAULT 'custom',
            start_time TEXT,
            end_time TEXT,
            delete_after INTEGER DEFAULT 0,
            pin_message INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1
        );

        -- 轮播广告表
        CREATE TABLE IF NOT EXISTS carousel_ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            content TEXT,
            media_type TEXT,
            media_value TEXT,
            buttons TEXT,
            interval_minutes INTEGER DEFAULT 30,
            enabled INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        );

        -- 消息日志（用于统计和查U）
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            user_id INTEGER,
            username TEXT,
            text TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- 用户统计
        CREATE TABLE IF NOT EXISTS user_stats (
            group_id INTEGER,
            user_id INTEGER,
            msg_count INTEGER DEFAULT 0,
            PRIMARY KEY(group_id, user_id)
        );

        -- 积分表（可扩展）
        CREATE TABLE IF NOT EXISTS points (
            group_id INTEGER,
            user_id INTEGER,
            points INTEGER DEFAULT 0,
            last_sign DATE,
            PRIMARY KEY(group_id, user_id)
        );
    ''')
    conn.commit()
    conn.close()

init_db()
print("数据库初始化完成")
