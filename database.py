import sqlite3
import os
from config import DB_FILE

os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)

def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY,
            welcome TEXT,
            captcha_enabled INTEGER DEFAULT 1
        );
        
        CREATE TABLE IF NOT EXISTS certified_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            user_id INTEGER UNIQUE,
            title TEXT DEFAULT "认证用户",
            checkin_streak INTEGER DEFAULT 0,
            last_checkin TEXT,
            total_checkins INTEGER DEFAULT 0
        );
        
        CREATE TABLE IF NOT EXISTS auto_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            condition_type TEXT DEFAULT 'contains',
            condition_text TEXT,
            reply_content TEXT,
            enabled INTEGER DEFAULT 1
        );
        
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            content TEXT,
            time TEXT,              -- 简单格式 "09:00"
            enabled INTEGER DEFAULT 1
        );
    ''')
    conn.commit()
    conn.close()

init_db()
