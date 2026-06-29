import logging
from datetime import datetime, time as dt_time

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot.chat_registration import on_register_project
from bot.confirmation import on_confirmation_callback, on_employee_disambiguation
from bot.daily_report import send_daily_report
from bot.handlers import on_group_message
from bot.onboarding import on_force_onboard, on_start
from bot.private import on_private_document, on_private_text, on_project_selected
from bot.status_cycle import run_status_check
from bot.weekly_report import on_weekly_command, send_weekly_report
from config.settings import (
    DAILY_REPORT_TIME,
    STATUS_CHECK_TIME,
    TELEGRAM_BOT_TOKEN,
    WEEKLY_REPORT_DAY,
    WEEKLY_REPORT_TIME,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


async def _status_check_job(context) -> None:
    await run_status_check(context.bot)


async def _daily_report_job(context) -> None:
    await send_daily_report(context.bot)


async def _weekly_report_job(context) -> None:
    await send_weekly_report(context.bot)


def _parse_time(value: str, tzinfo) -> dt_time:
    hour, minute = (int(part) for part in value.split(":"))
    return dt_time(hour=hour, minute=minute, tzinfo=tzinfo)


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", on_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("weekly", on_weekly_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("register_project", on_register_project, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("onboard", on_force_onboard, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT, on_group_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, on_private_text))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, on_private_document))
    app.add_handler(CallbackQueryHandler(on_confirmation_callback, pattern=r"^confirm:"))
    app.add_handler(CallbackQueryHandler(on_project_selected, pattern=r"^project:"))
    app.add_handler(CallbackQueryHandler(on_employee_disambiguation, pattern=r"^employee:"))

    local_tzinfo = datetime.now().astimezone().tzinfo
    app.job_queue.run_daily(_status_check_job, time=_parse_time(STATUS_CHECK_TIME, local_tzinfo))
    app.job_queue.run_daily(_daily_report_job, time=_parse_time(DAILY_REPORT_TIME, local_tzinfo))
    app.job_queue.run_daily(
        _weekly_report_job,
        time=_parse_time(WEEKLY_REPORT_TIME, local_tzinfo),
        days=(_WEEKDAYS[WEEKLY_REPORT_DAY.lower()],),
    )

    app.run_polling()


if __name__ == "__main__":
    main()
