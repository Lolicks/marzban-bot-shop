from __future__ import annotations

import asyncio
import logging

import uvicorn

from bot.loader import configure_bot_commands, create_bot_and_dispatcher
from config import get_settings
from database import crud
from database.base import SessionLocal, init_db
from services.scheduler import (
    daily_sales_report_task,
    expiry_reminders_task,
    suspend_expired_users_task,
    telegram_db_backup_task,
    referral_rewards_task,
)
from web_panel.app import create_web_app


async def bootstrap() -> None:
    settings = get_settings()
    await init_db()
    async with SessionLocal() as session:
        await crud.ensure_superadmin(session, settings.superadmin_telegram_id)
        await crud.create_default_plans(session)
        # One-time data repair: legacy account labels like "Plan#1 payment#2" -> plan name.
        await crud.repair_legacy_vpn_account_labels(session)


async def run_bot() -> None:
    bot, dp = create_bot_and_dispatcher()
    await configure_bot_commands(bot)
    asyncio.create_task(suspend_expired_users_task(bot))
    asyncio.create_task(expiry_reminders_task(bot))
    asyncio.create_task(daily_sales_report_task(bot))
    asyncio.create_task(telegram_db_backup_task(bot))
    asyncio.create_task(referral_rewards_task())
    await dp.start_polling(bot)


async def run_web() -> None:
    settings = get_settings()
    app = create_web_app()
    config = uvicorn.Config(app=app, host=settings.web_host, port=settings.web_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    await bootstrap()
    await asyncio.gather(run_bot(), run_web())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(main())
