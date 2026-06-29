import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from bot.confirmation import on_confirmation_callback, on_edit_reply
from bot.handlers import on_group_message
from config.settings import TELEGRAM_BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def on_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.debug("Личное сообщение от %s: %r", update.effective_user.id, update.effective_message.text)
    if await on_edit_reply(update, context):
        return
    # Остальная логика личных сообщений (раздел 2.2/2.3 PROJECT_SPEC.md) — следующий этап


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT, on_group_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, on_private_message))
    app.add_handler(CallbackQueryHandler(on_confirmation_callback, pattern=r"^confirm:"))

    app.run_polling()


if __name__ == "__main__":
    main()
