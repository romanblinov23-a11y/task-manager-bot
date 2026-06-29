import logging
from datetime import datetime, time as dt_time

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot.confirmation import on_confirmation_callback
from bot.handlers import on_group_message
from bot.onboarding import on_start
from bot.private import on_private_document, on_private_text, on_project_selected
from bot.status_cycle import run_status_check
from config.settings import STATUS_CHECK_TIME, TELEGRAM_BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def _status_check_job(context) -> None:
    await run_status_check(context.bot)


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", on_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT, on_group_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, on_private_text))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, on_private_document))
    app.add_handler(CallbackQueryHandler(on_confirmation_callback, pattern=r"^confirm:"))
    app.add_handler(CallbackQueryHandler(on_project_selected, pattern=r"^project:"))

    hour, minute = (int(part) for part in STATUS_CHECK_TIME.split(":"))
    local_tzinfo = datetime.now().astimezone().tzinfo
    app.job_queue.run_daily(_status_check_job, time=dt_time(hour=hour, minute=minute, tzinfo=local_tzinfo))

    app.run_polling()


if __name__ == "__main__":
    main()
