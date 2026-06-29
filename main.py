import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot.confirmation import on_confirmation_callback
from bot.handlers import on_group_message
from bot.onboarding import on_start
from bot.private import on_private_document, on_private_text, on_project_selected
from config.settings import TELEGRAM_BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", on_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT, on_group_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, on_private_text))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, on_private_document))
    app.add_handler(CallbackQueryHandler(on_confirmation_callback, pattern=r"^confirm:"))
    app.add_handler(CallbackQueryHandler(on_project_selected, pattern=r"^project:"))

    app.run_polling()


if __name__ == "__main__":
    main()
