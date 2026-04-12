from db import db_exec


def init_db():
    # Core group/settings tables (preserve existing)
    db_exec("""
        CREATE TABLE IF NOT EXISTS groups (
            gid TEXT PRIMARY KEY,
            gname TEXT,
            config JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS settings (
            gid TEXT,
            key TEXT,
            value TEXT,
            PRIMARY KEY (gid, key)
        )
    """)

    # Subscription rules: channels users must join before using features
    db_exec("""
        CREATE TABLE IF NOT EXISTS subscription_rules (
            id SERIAL PRIMARY KEY,
            gid TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT,
            channel_url TEXT,
            feature TEXT DEFAULT 'all',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Telegram users registry
    db_exec("""
        CREATE TABLE IF NOT EXISTS users (
            uid BIGINT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            points INTEGER DEFAULT 0,
            membership_level TEXT DEFAULT 'free',
            risk_status TEXT DEFAULT 'normal',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            last_seen TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Certified users (core business entity)
    db_exec("""
        CREATE TABLE IF NOT EXISTS certified_users (
            id SERIAL PRIMARY KEY,
            uid BIGINT REFERENCES users(uid) ON DELETE SET NULL,
            display_name TEXT NOT NULL,
            username TEXT,
            category TEXT DEFAULT 'general',
            tags TEXT[] DEFAULT '{}',
            level INTEGER DEFAULT 1,
            region TEXT,
            city TEXT,
            bio TEXT,
            contact TEXT,
            trust_score NUMERIC(4,2) DEFAULT 5.00,
            risk_score INTEGER DEFAULT 0,
            activity_score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            valid_from DATE,
            valid_until DATE,
            added_by BIGINT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Daily check-in records
    db_exec("""
        CREATE TABLE IF NOT EXISTS checkins (
            id SERIAL PRIMARY KEY,
            uid BIGINT NOT NULL,
            checkin_date DATE NOT NULL,
            points_earned INTEGER DEFAULT 10,
            streak INTEGER DEFAULT 1,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (uid, checkin_date)
        )
    """)

    # Online status tracking
    db_exec("""
        CREATE TABLE IF NOT EXISTS online_status (
            uid BIGINT PRIMARY KEY,
            certified_user_id INTEGER REFERENCES certified_users(id) ON DELETE CASCADE,
            last_seen TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '8 hours'),
            city TEXT,
            region TEXT
        )
    """)

    # Ratings and reviews
    db_exec("""
        CREATE TABLE IF NOT EXISTS ratings (
            id SERIAL PRIMARY KEY,
            certified_user_id INTEGER REFERENCES certified_users(id) ON DELETE CASCADE,
            rater_uid BIGINT REFERENCES users(uid) ON DELETE CASCADE,
            stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
            comment TEXT,
            tags TEXT[] DEFAULT '{}',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Task definitions
    db_exec("""
        CREATE TABLE IF NOT EXISTS task_definitions (
            id SERIAL PRIMARY KEY,
            task_key TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            points INTEGER DEFAULT 10,
            frequency TEXT DEFAULT 'daily',
            is_active BOOLEAN DEFAULT TRUE
        )
    """)

    # User completed tasks log
    db_exec("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            id SERIAL PRIMARY KEY,
            uid BIGINT REFERENCES users(uid) ON DELETE CASCADE,
            task_key TEXT NOT NULL,
            completed_at TIMESTAMPTZ DEFAULT NOW(),
            points_earned INTEGER DEFAULT 0
        )
    """)

    # Coupon publishing records
    db_exec("""
        CREATE TABLE IF NOT EXISTS coupons (
            id SERIAL PRIMARY KEY,
            certified_user_id INTEGER REFERENCES certified_users(id) ON DELETE CASCADE,
            uid BIGINT REFERENCES users(uid) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            discount TEXT,
            valid_until DATE,
            status TEXT DEFAULT 'pending',
            published_at TIMESTAMPTZ,
            channel_id TEXT,
            message_id INTEGER,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Channel publish log
    db_exec("""
        CREATE TABLE IF NOT EXISTS channel_pushes (
            id SERIAL PRIMARY KEY,
            certified_user_id INTEGER REFERENCES certified_users(id) ON DELETE CASCADE,
            channel_id TEXT NOT NULL,
            message_id INTEGER,
            push_type TEXT DEFAULT 'profile',
            pushed_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Risk and moderation action log
    db_exec("""
        CREATE TABLE IF NOT EXISTS risk_logs (
            id SERIAL PRIMARY KEY,
            uid BIGINT,
            certified_user_id INTEGER,
            action TEXT NOT NULL,
            reason TEXT,
            performed_by BIGINT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Group moderation violations
    db_exec("""
        CREATE TABLE IF NOT EXISTS violations (
            id SERIAL PRIMARY KEY,
            uid BIGINT NOT NULL,
            gid TEXT NOT NULL,
            violation_type TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # New member verification challenges
    db_exec("""
        CREATE TABLE IF NOT EXISTS verifications (
            id SERIAL PRIMARY KEY,
            uid BIGINT NOT NULL,
            gid TEXT NOT NULL,
            challenge TEXT NOT NULL,
            answer TEXT NOT NULL,
            message_id INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '5 minutes')
        )
    """)

    # Points transaction history
    db_exec("""
        CREATE TABLE IF NOT EXISTS points_transactions (
            id SERIAL PRIMARY KEY,
            uid BIGINT REFERENCES users(uid) ON DELETE CASCADE,
            amount INTEGER NOT NULL,
            reason TEXT NOT NULL,
            balance_after INTEGER NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Seed default tasks
    db_exec("""
        INSERT INTO task_definitions (task_key, title, description, points, frequency)
        VALUES
            ('daily_checkin', '每日签到', '每天签到一次获得积分', 10, 'daily'),
            ('rate_user', '评价用户', '对认证用户进行评分', 20, 'daily'),
            ('share_bot', '分享机器人', '邀请新用户加入', 50, 'weekly'),
            ('invite_user', '邀请好友', '成功邀请好友注册', 30, 'once')
        ON CONFLICT (task_key) DO NOTHING
    """)
