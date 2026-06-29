from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_lightweight_migrations(conn)


async def _apply_lightweight_migrations(conn) -> None:
    # Keep existing installations working without Alembic.
    dialect = conn.engine.dialect.name
    if dialect == "sqlite":
        rows = await conn.execute(text("PRAGMA table_info(telegram_users)"))
        columns = {r[1] for r in rows.fetchall()}
        if "balance_usd" not in columns:
            await conn.execute(text("ALTER TABLE telegram_users ADD COLUMN balance_usd NUMERIC(10,2) DEFAULT 0"))
        if "admin_note" not in columns:
            await conn.execute(text("ALTER TABLE telegram_users ADD COLUMN admin_note TEXT"))
        if "reminder_3d_sent" not in columns:
            await conn.execute(text("ALTER TABLE telegram_users ADD COLUMN reminder_3d_sent BOOLEAN DEFAULT 0"))
        if "reminder_1d_sent" not in columns:
            await conn.execute(text("ALTER TABLE telegram_users ADD COLUMN reminder_1d_sent BOOLEAN DEFAULT 0"))
        promo_rows = await conn.execute(text("PRAGMA table_info(promo_codes)"))
        promo_cols = {r[1] for r in promo_rows.fetchall()}
        if "bonus_balance_rub" not in promo_cols:
            await conn.execute(text("ALTER TABLE promo_codes ADD COLUMN bonus_balance_rub NUMERIC(10,2) DEFAULT 0"))
        vpn_tables = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='vpn_accounts'"))
        if not vpn_tables.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE vpn_accounts ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "user_id INTEGER NOT NULL,"
                    "label VARCHAR(64),"
                    "marzban_username VARCHAR(64) NOT NULL UNIQUE,"
                    "subscription_url TEXT,"
                    "status VARCHAR(16) DEFAULT 'active',"
                    "expire_at DATETIME,"
                    "traffic_limit_gb INTEGER DEFAULT 0,"
                    "traffic_used_gb INTEGER DEFAULT 0,"
                    "is_trial BOOLEAN DEFAULT 0,"
                    "plan_id INTEGER,"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "FOREIGN KEY(user_id) REFERENCES telegram_users(id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vpn_accounts_user_id ON vpn_accounts(user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vpn_accounts_expire_at ON vpn_accounts(expire_at)"))
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_vpn_accounts_username ON vpn_accounts(marzban_username)"))
        else:
            vpn_cols = await conn.execute(text("PRAGMA table_info(vpn_accounts)"))
            vpn_cols = {r[1] for r in vpn_cols.fetchall()}
            if "plan_id" not in vpn_cols:
                await conn.execute(text("ALTER TABLE vpn_accounts ADD COLUMN plan_id INTEGER"))

        news_tables = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='news_posts'"))
        if not news_tables.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE news_posts ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "title VARCHAR(200) NOT NULL,"
                    "slug VARCHAR(200) NOT NULL UNIQUE,"
                    "content_md TEXT DEFAULT '',"
                    "image_url TEXT,"
                    "is_published BOOLEAN DEFAULT 0,"
                    "mini_enabled BOOLEAN DEFAULT 0,"
                    "mini_only BOOLEAN DEFAULT 0,"
                    "mini_days INTEGER DEFAULT 0,"
                    "mini_start_at DATETIME,"
                    "published_at DATETIME,"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_slug ON news_posts(slug)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_is_published ON news_posts(is_published)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_published_at ON news_posts(published_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_created_at ON news_posts(created_at)"))
        else:
            news_rows = await conn.execute(text("PRAGMA table_info(news_posts)"))
            news_cols = {r[1] for r in news_rows.fetchall()}
            if "image_url" not in news_cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN image_url TEXT"))
            if "mini_enabled" not in news_cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN mini_enabled BOOLEAN DEFAULT 0"))
            if "mini_only" not in news_cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN mini_only BOOLEAN DEFAULT 0"))
            if "mini_days" not in news_cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN mini_days INTEGER DEFAULT 0"))
            if "mini_start_at" not in news_cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN mini_start_at DATETIME"))

        plan_rows = await conn.execute(text("PRAGMA table_info(plans)"))
        plan_cols = {r[1] for r in plan_rows.fetchall()}
        if "protocols_json" not in plan_cols:
            await conn.execute(text("ALTER TABLE plans ADD COLUMN protocols_json TEXT DEFAULT ''"))
        if "price_stars" not in plan_cols:
            await conn.execute(text("ALTER TABLE plans ADD COLUMN price_stars INTEGER DEFAULT 0"))

        payment_rows = await conn.execute(text("PRAGMA table_info(payment_logs)"))
        payment_cols = {r[1] for r in payment_rows.fetchall()}
        if "amount_stars" not in payment_cols:
            await conn.execute(text("ALTER TABLE payment_logs ADD COLUMN amount_stars INTEGER DEFAULT 0"))
        if "balance_used_rub" not in payment_cols:
            await conn.execute(text("ALTER TABLE payment_logs ADD COLUMN balance_used_rub NUMERIC(10,2) DEFAULT 0"))

        referral_table = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='referral_clicks'")
        )
        if not referral_table.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE referral_clicks ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "referrer_id INTEGER,"
                    "referral_code VARCHAR(32) NOT NULL,"
                    "ip VARCHAR(64),"
                    "user_agent TEXT,"
                    "path VARCHAR(255),"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "FOREIGN KEY(referrer_id) REFERENCES telegram_users(id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_clicks_referrer ON referral_clicks(referrer_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_clicks_code ON referral_clicks(referral_code)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_clicks_created ON referral_clicks(created_at)"))

        referral_events_table = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='referral_events'")
        )
        if not referral_events_table.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE referral_events ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "referrer_id INTEGER NOT NULL,"
                    "referred_id INTEGER,"
                    "referral_code VARCHAR(32),"
                    "event_type VARCHAR(32) NOT NULL,"
                    "source VARCHAR(32) DEFAULT 'unknown',"
                    "meta TEXT,"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "FOREIGN KEY(referrer_id) REFERENCES telegram_users(id),"
                    "FOREIGN KEY(referred_id) REFERENCES telegram_users(id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_referrer ON referral_events(referrer_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_referred ON referral_events(referred_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_code ON referral_events(referral_code)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_type ON referral_events(event_type)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_created ON referral_events(created_at)"))

        referrals_table = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='referrals'")
        )
        if not referrals_table.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE referrals ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "referrer_id INTEGER NOT NULL,"
                    "referred_id INTEGER NOT NULL,"
                    "status VARCHAR(16) DEFAULT 'pending',"
                    "reward_due_at DATETIME NOT NULL,"
                    "reward_given BOOLEAN DEFAULT 0,"
                    "ip_hash VARCHAR(64),"
                    "user_agent TEXT,"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "FOREIGN KEY(referrer_id) REFERENCES telegram_users(id),"
                    "FOREIGN KEY(referred_id) REFERENCES telegram_users(id),"
                    "UNIQUE(referrer_id, referred_id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_referrer ON referrals(referrer_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_referred ON referrals(referred_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_due ON referrals(reward_due_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_created ON referrals(created_at)"))

        desktop_tables = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='desktop_login_codes'")
        )
        if not desktop_tables.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE desktop_login_codes ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "telegram_id INTEGER NOT NULL,"
                    "code VARCHAR(32) NOT NULL UNIQUE,"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "expires_at DATETIME NOT NULL,"
                    "used_at DATETIME"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_login_codes_tg ON desktop_login_codes(telegram_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_login_codes_exp ON desktop_login_codes(expires_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_login_codes_used ON desktop_login_codes(used_at)"))

        desktop_trial_table = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='desktop_trial_devices'")
        )
        if not desktop_trial_table.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE desktop_trial_devices ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "device_id VARCHAR(64) NOT NULL UNIQUE,"
                    "user_id INTEGER,"
                    "marzban_username VARCHAR(64),"
                    "last_granted_at DATETIME,"
                    "last_ip VARCHAR(64),"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "FOREIGN KEY(user_id) REFERENCES telegram_users(id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_trial_device_id ON desktop_trial_devices(device_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_trial_user_id ON desktop_trial_devices(user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_trial_last_granted ON desktop_trial_devices(last_granted_at)"))

        feedback_table = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_entries'")
        )
        if not feedback_table.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE feedback_entries ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "telegram_id INTEGER NOT NULL,"
                    "username VARCHAR(64),"
                    "rating INTEGER DEFAULT 5,"
                    "message TEXT,"
                    "source VARCHAR(32) DEFAULT 'account',"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_feedback_entries_tg ON feedback_entries(telegram_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_feedback_entries_created ON feedback_entries(created_at)"))
        else:
            cols = await conn.execute(text("PRAGMA table_info(feedback_entries)"))
            col_names = {row[1] for row in cols.fetchall()}
            if "show_on_site" not in col_names:
                await conn.execute(text("ALTER TABLE feedback_entries ADD COLUMN show_on_site INTEGER DEFAULT 0"))
                await conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_feedback_entries_show ON feedback_entries(show_on_site)")
                )

        admin_accounts_table = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='admin_accounts'")
        )
        if not admin_accounts_table.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE admin_accounts ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "user_id INTEGER NOT NULL,"
                    "username VARCHAR(64) NOT NULL UNIQUE,"
                    "password_hash VARCHAR(255) NOT NULL,"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "FOREIGN KEY(user_id) REFERENCES telegram_users(id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_accounts_user_id ON admin_accounts(user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_accounts_username ON admin_accounts(username)"))

        admin_invites_table = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='admin_invites'")
        )
        if not admin_invites_table.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE admin_invites ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "token VARCHAR(64) NOT NULL UNIQUE,"
                    "user_id INTEGER NOT NULL,"
                    "role VARCHAR(16) DEFAULT 'admin',"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "expires_at DATETIME,"
                    "used_at DATETIME,"
                    "FOREIGN KEY(user_id) REFERENCES telegram_users(id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_invites_user_id ON admin_invites(user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_invites_token ON admin_invites(token)"))

        admin_rebind_table = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='admin_rebind_codes'")
        )
        if not admin_rebind_table.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE admin_rebind_codes ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "admin_user_id INTEGER NOT NULL,"
                    "new_telegram_id INTEGER NOT NULL,"
                    "code VARCHAR(12) NOT NULL,"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "expires_at DATETIME,"
                    "used_at DATETIME,"
                    "FOREIGN KEY(admin_user_id) REFERENCES telegram_users(id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_rebind_user_id ON admin_rebind_codes(admin_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_rebind_code ON admin_rebind_codes(code)"))

        admin_audit_table = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='admin_audit_logs'")
        )
        if not admin_audit_table.fetchone():
            await conn.execute(
                text(
                    "CREATE TABLE admin_audit_logs ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "admin_user_id INTEGER,"
                    "admin_username VARCHAR(64),"
                    "action VARCHAR(16) DEFAULT 'view',"
                    "method VARCHAR(8) DEFAULT 'GET',"
                    "path VARCHAR(255) DEFAULT '',"
                    "status_code INTEGER,"
                    "ip VARCHAR(64),"
                    "user_agent TEXT,"
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                    "FOREIGN KEY(admin_user_id) REFERENCES telegram_users(id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_audit_user_id ON admin_audit_logs(admin_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_audit_created ON admin_audit_logs(created_at)"))
    elif dialect == "postgresql":
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='telegram_users' AND table_schema='public'"
            )
        )
        columns = {r[0] for r in rows.fetchall()}
        if "balance_usd" not in columns:
            await conn.execute(text("ALTER TABLE telegram_users ADD COLUMN balance_usd NUMERIC(10,2) DEFAULT 0"))
        if "admin_note" not in columns:
            await conn.execute(text("ALTER TABLE telegram_users ADD COLUMN admin_note TEXT"))
        if "reminder_3d_sent" not in columns:
            await conn.execute(text("ALTER TABLE telegram_users ADD COLUMN reminder_3d_sent BOOLEAN DEFAULT FALSE"))
        if "reminder_1d_sent" not in columns:
            await conn.execute(text("ALTER TABLE telegram_users ADD COLUMN reminder_1d_sent BOOLEAN DEFAULT FALSE"))
        promo_rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='promo_codes' AND table_schema='public'"
            )
        )
        promo_cols = {r[0] for r in promo_rows.fetchall()}
        if "bonus_balance_rub" not in promo_cols:
            await conn.execute(text("ALTER TABLE promo_codes ADD COLUMN bonus_balance_rub NUMERIC(10,2) DEFAULT 0"))
        vpn_table = await conn.execute(
            text(
                "SELECT to_regclass('public.vpn_accounts')"
            )
        )
        if not vpn_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE vpn_accounts ("
                    "id SERIAL PRIMARY KEY,"
                    "user_id INTEGER NOT NULL REFERENCES telegram_users(id),"
                    "label VARCHAR(64),"
                    "marzban_username VARCHAR(64) NOT NULL UNIQUE,"
                    "subscription_url TEXT,"
                    "status VARCHAR(16) DEFAULT 'active',"
                    "expire_at TIMESTAMPTZ,"
                    "traffic_limit_gb INTEGER DEFAULT 0,"
                    "traffic_used_gb INTEGER DEFAULT 0,"
                    "is_trial BOOLEAN DEFAULT FALSE,"
                    "plan_id INTEGER REFERENCES plans(id),"
                    "created_at TIMESTAMPTZ DEFAULT now()"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vpn_accounts_user_id ON vpn_accounts(user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vpn_accounts_expire_at ON vpn_accounts(expire_at)"))
        else:
            vpn_cols = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='vpn_accounts' AND table_schema='public'"
                )
            )
            vpn_cols = {r[0] for r in vpn_cols.fetchall()}
            if "plan_id" not in vpn_cols:
                await conn.execute(text("ALTER TABLE vpn_accounts ADD COLUMN plan_id INTEGER REFERENCES plans(id)"))

        news_table = await conn.execute(text("SELECT to_regclass('public.news_posts')"))
        if not news_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE news_posts ("
                    "id SERIAL PRIMARY KEY,"
                    "title VARCHAR(200) NOT NULL,"
                    "slug VARCHAR(200) NOT NULL UNIQUE,"
                    "content_md TEXT DEFAULT '',"
                    "image_url TEXT,"
                    "is_published BOOLEAN DEFAULT FALSE,"
                    "mini_enabled BOOLEAN DEFAULT FALSE,"
                    "mini_only BOOLEAN DEFAULT FALSE,"
                    "mini_days INTEGER DEFAULT 0,"
                    "mini_start_at TIMESTAMPTZ,"
                    "published_at TIMESTAMPTZ,"
                    "created_at TIMESTAMPTZ DEFAULT now(),"
                    "updated_at TIMESTAMPTZ DEFAULT now()"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_slug ON news_posts(slug)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_is_published ON news_posts(is_published)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_published_at ON news_posts(published_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_created_at ON news_posts(created_at)"))
        else:
            rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='news_posts' AND table_schema='public'"
                )
            )
            cols = {r[0] for r in rows.fetchall()}
            if "image_url" not in cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN image_url TEXT"))
            if "mini_enabled" not in cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN mini_enabled BOOLEAN DEFAULT FALSE"))
            if "mini_only" not in cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN mini_only BOOLEAN DEFAULT FALSE"))
            if "mini_days" not in cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN mini_days INTEGER DEFAULT 0"))
            if "mini_start_at" not in cols:
                await conn.execute(text("ALTER TABLE news_posts ADD COLUMN mini_start_at TIMESTAMPTZ"))

        plan_rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='plans' AND table_schema='public'"
            )
        )
        plan_cols = {r[0] for r in plan_rows.fetchall()}
        if "protocols_json" not in plan_cols:
            await conn.execute(text("ALTER TABLE plans ADD COLUMN protocols_json TEXT DEFAULT ''"))
        if "price_stars" not in plan_cols:
            await conn.execute(text("ALTER TABLE plans ADD COLUMN price_stars INTEGER DEFAULT 0"))

        payment_rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='payment_logs' AND table_schema='public'"
            )
        )
        payment_cols = {r[0] for r in payment_rows.fetchall()}
        if "amount_stars" not in payment_cols:
            await conn.execute(text("ALTER TABLE payment_logs ADD COLUMN amount_stars INTEGER DEFAULT 0"))
        if "balance_used_rub" not in payment_cols:
            await conn.execute(text("ALTER TABLE payment_logs ADD COLUMN balance_used_rub NUMERIC(10,2) DEFAULT 0"))

        desktop_table = await conn.execute(text("SELECT to_regclass('public.desktop_login_codes')"))
        if not desktop_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE desktop_login_codes ("
                    "id SERIAL PRIMARY KEY,"
                    "telegram_id BIGINT NOT NULL,"
                    "code VARCHAR(32) NOT NULL UNIQUE,"
                    "created_at TIMESTAMPTZ DEFAULT now(),"
                    "expires_at TIMESTAMPTZ NOT NULL,"
                    "used_at TIMESTAMPTZ"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_login_codes_tg ON desktop_login_codes(telegram_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_login_codes_exp ON desktop_login_codes(expires_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_login_codes_used ON desktop_login_codes(used_at)"))

        desktop_trial_table = await conn.execute(text("SELECT to_regclass('public.desktop_trial_devices')"))
        if not desktop_trial_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE desktop_trial_devices ("
                    "id SERIAL PRIMARY KEY,"
                    "device_id VARCHAR(64) NOT NULL UNIQUE,"
                    "user_id INTEGER REFERENCES telegram_users(id),"
                    "marzban_username VARCHAR(64),"
                    "last_granted_at TIMESTAMPTZ,"
                    "last_ip VARCHAR(64),"
                    "created_at TIMESTAMPTZ DEFAULT now()"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_trial_device_id ON desktop_trial_devices(device_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_trial_user_id ON desktop_trial_devices(user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_desktop_trial_last_granted ON desktop_trial_devices(last_granted_at)"))

        referral_table = await conn.execute(text("SELECT to_regclass('public.referral_clicks')"))
        if not referral_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE referral_clicks ("
                    "id SERIAL PRIMARY KEY,"
                    "referrer_id INTEGER REFERENCES telegram_users(id),"
                    "referral_code VARCHAR(32) NOT NULL,"
                    "ip VARCHAR(64),"
                    "user_agent TEXT,"
                    "path VARCHAR(255),"
                    "created_at TIMESTAMPTZ DEFAULT now()"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_clicks_referrer ON referral_clicks(referrer_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_clicks_code ON referral_clicks(referral_code)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_clicks_created ON referral_clicks(created_at)"))

        referral_events_table = await conn.execute(text("SELECT to_regclass('public.referral_events')"))
        if not referral_events_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE referral_events ("
                    "id SERIAL PRIMARY KEY,"
                    "referrer_id INTEGER NOT NULL REFERENCES telegram_users(id),"
                    "referred_id INTEGER REFERENCES telegram_users(id),"
                    "referral_code VARCHAR(32),"
                    "event_type VARCHAR(32) NOT NULL,"
                    "source VARCHAR(32) DEFAULT 'unknown',"
                    "meta TEXT,"
                    "created_at TIMESTAMPTZ DEFAULT now()"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_referrer ON referral_events(referrer_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_referred ON referral_events(referred_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_code ON referral_events(referral_code)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_type ON referral_events(event_type)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_events_created ON referral_events(created_at)"))

        referrals_table = await conn.execute(text("SELECT to_regclass('public.referrals')"))
        if not referrals_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE referrals ("
                    "id SERIAL PRIMARY KEY,"
                    "referrer_id INTEGER NOT NULL REFERENCES telegram_users(id),"
                    "referred_id INTEGER NOT NULL REFERENCES telegram_users(id),"
                    "status VARCHAR(16) DEFAULT 'pending',"
                    "reward_due_at TIMESTAMPTZ NOT NULL,"
                    "reward_given BOOLEAN DEFAULT FALSE,"
                    "ip_hash VARCHAR(64),"
                    "user_agent TEXT,"
                    "created_at TIMESTAMPTZ DEFAULT now(),"
                    "UNIQUE(referrer_id, referred_id)"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_referrer ON referrals(referrer_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_referred ON referrals(referred_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_due ON referrals(reward_due_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_created ON referrals(created_at)"))

        feedback_table = await conn.execute(text("SELECT to_regclass('public.feedback_entries')"))
        if not feedback_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE feedback_entries ("
                    "id SERIAL PRIMARY KEY,"
                    "telegram_id BIGINT NOT NULL,"
                    "username VARCHAR(64),"
                    "rating INTEGER DEFAULT 5,"
                    "message TEXT,"
                    "source VARCHAR(32) DEFAULT 'account',"
                    "created_at TIMESTAMPTZ DEFAULT now()"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_feedback_entries_tg ON feedback_entries(telegram_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_feedback_entries_created ON feedback_entries(created_at)"))
        else:
            await conn.execute(text("ALTER TABLE feedback_entries ADD COLUMN IF NOT EXISTS show_on_site BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_feedback_entries_show ON feedback_entries(show_on_site)"))

        admin_accounts_table = await conn.execute(text("SELECT to_regclass('public.admin_accounts')"))
        if not admin_accounts_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE admin_accounts ("
                    "id SERIAL PRIMARY KEY,"
                    "user_id INTEGER NOT NULL REFERENCES telegram_users(id),"
                    "username VARCHAR(64) NOT NULL UNIQUE,"
                    "password_hash VARCHAR(255) NOT NULL,"
                    "created_at TIMESTAMPTZ DEFAULT now(),"
                    "updated_at TIMESTAMPTZ DEFAULT now()"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_accounts_user_id ON admin_accounts(user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_accounts_username ON admin_accounts(username)"))

        admin_invites_table = await conn.execute(text("SELECT to_regclass('public.admin_invites')"))
        if not admin_invites_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE admin_invites ("
                    "id SERIAL PRIMARY KEY,"
                    "token VARCHAR(64) NOT NULL UNIQUE,"
                    "user_id INTEGER NOT NULL REFERENCES telegram_users(id),"
                    "role VARCHAR(16) DEFAULT 'admin',"
                    "created_at TIMESTAMPTZ DEFAULT now(),"
                    "expires_at TIMESTAMPTZ,"
                    "used_at TIMESTAMPTZ"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_invites_user_id ON admin_invites(user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_invites_token ON admin_invites(token)"))

        admin_rebind_table = await conn.execute(text("SELECT to_regclass('public.admin_rebind_codes')"))
        if not admin_rebind_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE admin_rebind_codes ("
                    "id SERIAL PRIMARY KEY,"
                    "admin_user_id INTEGER NOT NULL REFERENCES telegram_users(id),"
                    "new_telegram_id BIGINT NOT NULL,"
                    "code VARCHAR(12) NOT NULL,"
                    "created_at TIMESTAMPTZ DEFAULT now(),"
                    "expires_at TIMESTAMPTZ,"
                    "used_at TIMESTAMPTZ"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_rebind_user_id ON admin_rebind_codes(admin_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_rebind_code ON admin_rebind_codes(code)"))

        admin_audit_table = await conn.execute(text("SELECT to_regclass('public.admin_audit_logs')"))
        if not admin_audit_table.scalar_one_or_none():
            await conn.execute(
                text(
                    "CREATE TABLE admin_audit_logs ("
                    "id SERIAL PRIMARY KEY,"
                    "admin_user_id INTEGER REFERENCES telegram_users(id),"
                    "admin_username VARCHAR(64),"
                    "action VARCHAR(16) DEFAULT 'view',"
                    "method VARCHAR(8) DEFAULT 'GET',"
                    "path VARCHAR(255) DEFAULT '',"
                    "status_code INTEGER,"
                    "ip VARCHAR(64),"
                    "user_agent TEXT,"
                    "created_at TIMESTAMPTZ DEFAULT now()"
                    ")"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_audit_user_id ON admin_audit_logs(admin_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_audit_created ON admin_audit_logs(created_at)"))
