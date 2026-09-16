from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import PUBLIC_BASE_URL
from dashboard.server import get_or_create_dashboard_token
from monitoring.managers import is_owner


async def on_shift_dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/shift_dashboard — только владелец. Не путать с /dashboard_market
    (bot/dashboard_cmd.py — дашборд мониторинга конкурентов): это отдельная
    фича про вечерние отчёты смен. Отдаёт ссылку на живую веб-страницу
    (см. dashboard/server.py) с показателями по вечерним отчётам смен —
    цифры, погода, значимые события и корреляции между ними. Дальше
    переключение точки/периода — уже внутри самой страницы."""
    if not is_owner(update.effective_user.id):
        return
    if not PUBLIC_BASE_URL:
        await update.effective_message.reply_text(
            "Дашборд ещё не настроен: в Railway нужно включить публичный домен "
            "(Settings → Networking) и задать переменную окружения PUBLIC_BASE_URL "
            "с этим доменом (https://...)."
        )
        return

    token = get_or_create_dashboard_token()
    url = f"{PUBLIC_BASE_URL.rstrip('/')}/dashboard?token={token}"
    await update.effective_message.reply_text(
        "Живой дашборд по отчётам смен — цифры, погода и как одно влияет на другое:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 Открыть дашборд", url=url)]]),
    )
