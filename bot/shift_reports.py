import re
from datetime import date as _date
from datetime import timedelta

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.onboarding import get_display_name
from config.settings import ROMAN_TELEGRAM_ID
from config.timeutil import fmt_date
from config.timeutil import today as tz_today
from monitoring.managers import get_manager, get_market_supervisor, get_markets_for_manager, is_owner
from monitoring.markets import get_market, list_markets
from monitoring.shift_reports import (
    create_or_get_draft,
    get_report,
    get_report_by_date,
    get_report_chat,
    list_reports_by_status_and_date,
    save_report_data,
    set_report_status,
)
from monitoring.shift_schedule import list_markets_with_shift

_WEEKDAY_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

# Вопросы в порядке образца отчёта (не прозы ТЗ — там расходился порядок
# "Комплимент"/"Питание" с образцом; ориентируемся на образец).
_QUESTIONS = [
    {"key": "revenue_total", "kind": "money", "prompt": "Привет! Пришло время вечернего отчёта! Начнём: напиши сумму общей выручки за день (формат: 324675,87):"},
    {"key": "revenue_cash", "kind": "money", "prompt": "Напиши сумму наличной выручки (формат: 324 675,87):"},
    {"key": "revenue_noncash", "kind": "money", "prompt": "Напиши сумму безналичной выручки (формат: 324675,87):"},
    {"key": "avg_check", "kind": "money", "prompt": "Напиши средний чек за смену (формат: 789,87):"},
    {"key": "guests", "kind": "guests", "prompt": "Напиши количество гостей (формат: 1 234):"},
    {"key": "writeoff_expiry", "kind": "money", "prompt": "Укажи сумму списаний на статью «Истёк срок годности» за сегодня, с/с (формат: 7 189,87):"},
    {"key": "writeoff_compliment", "kind": "money", "prompt": "Укажи сумму списаний на статью «Комплимент» за сегодня, с/с (формат: 7 189,87):"},
    {"key": "writeoff_staff_meals", "kind": "money", "prompt": "Укажи сумму списаний на статью «Питание сотрудников» за сегодня, с/с (формат: 7 189,87):"},
    {"key": "avg_service_time", "kind": "time", "prompt": "Укажи среднее время выдачи и приготовления заказов за сегодняшний день (формат: 5:35):"},
    {
        "key": "comment_general",
        "kind": "text",
        "prompt": (
            "Отлично! Перейдём к комментариям. Расскажи общие комментарии по работе кофейни за сегодня: как "
            "прошла смена, с какими трудностями столкнулись, а с чем справились хорошо, на что стоит обратить "
            "внимание менеджеру следующего дня?"
        ),
    },
    {
        "key": "comment_service",
        "kind": "text",
        "prompt": (
            "Супер! Расскажи, как проходило обслуживание гостей сегодня. Были ли позитивные отзывы? Как гости "
            "реагировали на новинки в меню, если такие есть?"
        ),
    },
    {"key": "comment_conflicts", "kind": "text", "prompt": "Случались ли сегодня конфликтные ситуации? Опиши, если да — или напиши «не было»."},
    {
        "key": "comment_equipment",
        "kind": "text",
        "prompt": (
            "Отлично! Теперь дай комментарий по работе оборудования, состоянию предметов интерьера или мебели. "
            "Даже если уже говорил(а) об этом раньше — сообщи ещё раз, если что-то неисправно: так мы увидим, "
            "что проблема острая."
        ),
    },
    {
        "key": "comment_weather_flow",
        "kind": "text",
        "prompt": (
            "Супер, спасибо! Обсудим гостевой поток и погоду. Опиши: какая была погода и влияла ли она на поток "
            "в течение дня. Соотнеси с планом — что ещё могло повлиять на поток?"
        ),
    },
    {
        "key": "comment_events",
        "kind": "text",
        "prompt": "Спасибо! Теперь опиши события и мероприятия в Парке или рядом, которые могли повлиять на гостевой поток — в любую сторону.",
    },
    {
        "key": "shift_composition",
        "kind": "text",
        "prompt": (
            "Класс, ты почти закончил! Расскажи, кто и в каком составе работал сегодня — в утреннюю и в "
            "вечернюю смену (сколько менеджеров, бариста, клинеров и «уютных»)."
        ),
    },
]

_FIELD_LABELS = {
    "revenue_total": "Выручка",
    "revenue_cash": "Наличные",
    "revenue_noncash": "Безнал",
    "avg_check": "Средний чек",
    "guests": "Гости",
    "writeoff_expiry": "Срок годности",
    "writeoff_compliment": "Комплимент",
    "writeoff_staff_meals": "Питание",
    "avg_service_time": "Среднее время отдачи",
    "comment_general": "Общая работа точки",
    "comment_service": "Обслуживание гостей",
    "comment_conflicts": "Конфликтные ситуации",
    "comment_equipment": "Оборудование и техпроблемы",
    "comment_weather_flow": "Погода и поток гостей",
    "comment_events": "Значимые события",
    "shift_composition": "Состав смены",
}

# telegram_user_id (str) заполняющего -> {"market_id", "market_name", "report_date",
# "report_id", "step_index", "answers", "is_supervisor_filling"}
_pending: dict[str, dict] = {}

# telegram_user_id (str) -> {"report_id": int, "field_index": int} — точечная правка поля
_editing: dict[str, dict] = {}

# telegram_user_id (str) владельца -> {"report_id": int} — ждём текст замечания для управляющего
_pending_more_info: dict[str, dict] = {}

# telegram_user_id (str) управляющего -> {"report_id": int} — ждём текст дополнения к отчёту
_awaiting_addendum: dict[str, dict] = {}


def _parse_amount(text: str) -> float | None:
    cleaned = text.strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_int(text: str) -> int | None:
    cleaned = text.strip().replace(" ", "").replace(" ", "")
    return int(cleaned) if cleaned.isdigit() else None


def _validate(kind: str, text: str) -> tuple[str | None, str | None]:
    text = text.strip()
    if kind == "money":
        if _parse_amount(text) is None:
            return None, "Нужно число, например 324675,87. Попробуй ещё раз:"
        return text, None
    if kind == "guests":
        if _parse_int(text) is None:
            return None, "Нужно целое число, например 737. Попробуй ещё раз:"
        return text, None
    if kind == "time":
        if not re.match(r"^\d{1,2}:\d{2}$", text):
            return None, "Нужен формат М:СС, например 5:35. Попробуй ещё раз:"
        return text, None
    if not text:
        return None, "Не может быть пустым, напиши хотя бы коротко:"
    return text, None


def _is_market_supervisor(user_id: int, market_id: int) -> bool:
    supervisor = get_market_supervisor(market_id)
    return bool(supervisor and supervisor["telegram_user_id"] == user_id)


def _kickoff_keyboard(market_id: int, report_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📝 Заполню сам", callback_data=f"shrep_fill:{market_id}:{report_date}"),
                InlineKeyboardButton("🚫 Не работаю сегодня", callback_data=f"shrep_absent:{market_id}:{report_date}"),
            ]
        ]
    )


def _approval_keyboard_supervisor(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Согласовать", callback_data=f"shrep_svapprove:{report_id}"),
                InlineKeyboardButton("✏️ Редактировать", callback_data=f"shrep_edit:{report_id}"),
            ]
        ]
    )


def _approval_keyboard_owner(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Согласовать", callback_data=f"shrep_ownapprove:{report_id}"),
                InlineKeyboardButton("💬 Запросить дополнения", callback_data=f"shrep_moreinfo:{report_id}"),
            ]
        ]
    )


def _edit_field_keyboard(report_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(_FIELD_LABELS[q["key"]], callback_data=f"shrep_editfield:{report_id}:{i}")]
        for i, q in enumerate(_QUESTIONS)
    ]
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"shrep_editcancel:{report_id}")])
    return InlineKeyboardMarkup(buttons)


def render_finance_report(market: dict, report_date: str, data: dict) -> str:
    """Форматирует отчёт строго по образцу Романа — заголовок, денежные
    поля, блок ***Комментарий:*** с жирными подзаголовками, состав смены
    свободным текстом в конце."""
    weekday = _WEEKDAY_RU[_date.fromisoformat(report_date).weekday()].capitalize()
    lines = [
        f"Отчет {fmt_date(report_date)} {weekday}",
        "",
        f"Выручка: {data.get('revenue_total', '—')}",
        "",
        f"Наличные: {data.get('revenue_cash', '—')}",
        "",
        f"Безнал: {data.get('revenue_noncash', '—')}",
        "",
        f"Средний чек: {data.get('avg_check', '—')}",
        "",
        f"Гости: {data.get('guests', '—')}",
        "",
        f"Срок годности: {data.get('writeoff_expiry', '—')}",
        "",
        f"Комплимент: {data.get('writeoff_compliment', '—')}",
        "",
        f"Питание: {data.get('writeoff_staff_meals', '—')}",
        "",
        f"Среднее время отдачи за день: {data.get('avg_service_time', '—')}",
        "",
        "***Комментарий:***",
        "",
        f"**Общая работа точки:** {data.get('comment_general', '—')}",
        "",
        f"**Обслуживание гостей:** {data.get('comment_service', '—')}",
        "",
        f"**Конфликтные ситуации:** {data.get('comment_conflicts', '—')}",
        "",
        f"**Оборудование и технические проблемы:** {data.get('comment_equipment', '—')}",
        "",
        f"**Погода и поток гостей:** {data.get('comment_weather_flow', '—')}",
        "",
        f"**Значимые события:** {data.get('comment_events', '—')}",
        "",
        "**Состав смены:**",
        "",
        data.get("shift_composition", "—"),
    ]
    return "\n".join(lines)


def render_team_report(market: dict, report_date: str, data: dict) -> str:
    """Заглушка — Роман пришлёт формат отчёта для чата команды точки
    отдельно. Пока переиспользуем отчёт для финпартнёров с пометкой, что
    формат черновой — заменить одной этой функцией, когда формат придёт."""
    return "⚠️ Черновой формат — для чата команды формат ещё не задан:\n\n" + render_finance_report(market, report_date, data)


async def _offer_report_or_absence(bot: Bot, telegram_user_id: int, market: dict, report_date: str) -> None:
    try:
        await bot.send_message(
            chat_id=telegram_user_id,
            text="Привет! Пришло время вечернего отчёта!",
            reply_markup=_kickoff_keyboard(market["id"], report_date),
        )
    except Exception:
        pass


async def _send_for_supervisor_approval(bot: Bot, report_id: int) -> None:
    report = get_report(report_id)
    market = get_market(report["market_id"])
    supervisor = get_market_supervisor(report["market_id"])
    if not supervisor:
        await _send_for_owner_approval(bot, report_id)
        return
    set_report_status(report_id, "awaiting_supervisor")
    text = render_finance_report(market, report["report_date"], report["data"])
    await bot.send_message(
        chat_id=supervisor["telegram_user_id"],
        text=f"📋 Отчёт за {fmt_date(report['report_date'])} на согласование:\n\n{text}",
        reply_markup=_approval_keyboard_supervisor(report_id),
    )


async def _send_for_owner_approval(bot: Bot, report_id: int) -> None:
    report = get_report(report_id)
    market = get_market(report["market_id"])
    set_report_status(report_id, "awaiting_owner")
    text = render_finance_report(market, report["report_date"], report["data"])
    await bot.send_message(
        chat_id=ROMAN_TELEGRAM_ID,
        text=f"📋 Отчёт за {fmt_date(report['report_date'])} на согласование:\n\n{text}",
        reply_markup=_approval_keyboard_owner(report_id),
    )


async def _ask_current_question(bot: Bot, message, user_id: str) -> None:
    state = _pending[user_id]
    idx = state["step_index"]
    if idx >= len(_QUESTIONS):
        await _finish_collection(bot, message, user_id)
        return
    await message.reply_text(_QUESTIONS[idx]["prompt"])


async def _finish_collection(bot: Bot, message, user_id: str) -> None:
    state = _pending.pop(user_id)
    report_id = state["report_id"]
    if state["is_supervisor_filling"]:
        await message.reply_text("Спасибо, отчёт готов! Отправляю Роману на согласование.")
        await _send_for_owner_approval(bot, report_id)
    else:
        await message.reply_text("Спасибо, отчёт готов! Отправляю управляющему на согласование.")
        await _send_for_supervisor_approval(bot, report_id)


async def _begin_collection(bot: Bot, message, user_id: int, market: dict, report_date: str) -> None:
    report = create_or_get_draft(market["id"], report_date, user_id)
    _pending[str(user_id)] = {
        "market_id": market["id"],
        "market_name": market["name"],
        "report_date": report_date,
        "report_id": report["id"],
        "step_index": 0,
        "answers": dict(report.get("data") or {}),
        "is_supervisor_filling": _is_market_supervisor(user_id, market["id"]),
    }
    await _ask_current_question(bot, message, str(user_id))


async def on_shift_report_fill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, market_id_str, report_date = query.data.split(":", 2)
    market_id = int(market_id_str)
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    user_id = query.from_user.id
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await _begin_collection(context.bot, query.message, user_id, market, report_date)


_STATUS_LABELS = {
    "awaiting_supervisor": "уже отправлен управляющему на согласование",
    "awaiting_owner": "уже отправлен Роману на согласование",
    "approved": "уже согласован, ждёт рассылки",
    "dispatched": "уже разослан",
}


def _manual_market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"shrep_manualmarket:{m['id']}")] for m in markets])


async def _start_manual_report(bot: Bot, message, user_id: int, market: dict) -> None:
    date_iso = tz_today().isoformat()
    existing = get_report_by_date(market["id"], date_iso)
    if existing and existing["status"] != "collecting":
        label = _STATUS_LABELS.get(existing["status"], "уже обработан")
        await message.reply_text(f"Отчёт по «{market['name']}» за сегодня {label}.")
        return
    await _begin_collection(bot, message, user_id, market, date_iso)


async def on_shift_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/shift_report — принудительно начать (или продолжить) сегодняшний
    вечерний отчёт, не дожидаясь автоматического опроса в 22:00. Доступно
    любому активному менеджеру рынка (не только назначенному по графику) и
    владельцу — например, если график ещё не загружен, отчёт нужно сдать
    раньше срока, или сдаёт не тот, кто был по графику."""
    user = update.effective_user
    manager = get_manager(user.id)
    if not is_owner(user.id) and (not manager or manager["status"] != "active"):
        await update.effective_message.reply_text("Эта команда доступна только подтверждённым владельцем менеджерам.")
        return

    markets = list_markets() if is_owner(user.id) else get_markets_for_manager(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _start_manual_report(context.bot, update.effective_message, user.id, markets[0])
        return

    await update.effective_message.reply_text("По какому рынку вносим отчёт?", reply_markup=_manual_market_pick_keyboard(markets))


async def on_shift_report_manual_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _start_manual_report(context.bot, query.message, query.from_user.id, market)


async def on_shift_report_absent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, market_id_str, report_date = query.data.split(":", 2)
    market_id = int(market_id_str)
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    user_id = query.from_user.id
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    supervisor = get_market_supervisor(market_id, exclude_telegram_user_id=user_id)
    if supervisor:
        await query.message.reply_text("Хорошо, передал управляющему.")
        await _offer_report_or_absence(context.bot, supervisor["telegram_user_id"], market, report_date)
    else:
        await query.message.reply_text("Хорошо, сообщил владельцу.")
        await context.bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text=f"⚠️ На «{market['name']}» некому сдать отчёт за {report_date} — менеджер по графику недоступен, управляющего нет.",
        )


async def on_shift_report_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает ответы на вопросы вечернего отчёта. Возвращает True, если
    сообщение обработано — по конвенции остальных claim-хендлеров в
    on_private_text."""
    user_id = str(update.effective_user.id)
    state = _pending.get(user_id)
    if not state:
        return False

    idx = state["step_index"]
    if idx >= len(_QUESTIONS):
        return False
    question = _QUESTIONS[idx]
    text = update.effective_message.text or ""
    value, error = _validate(question["kind"], text)
    if error:
        await update.effective_message.reply_text(error)
        return True

    state["answers"][question["key"]] = value
    state["step_index"] += 1
    save_report_data(state["report_id"], state["answers"])
    await _ask_current_question(context.bot, update.effective_message, user_id)
    return True


async def on_shift_report_supervisor_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    report_id = int(query.data.split(":", 1)[1])
    report = get_report(report_id)
    if not report:
        await query.answer("Отчёт не найден", show_alert=True)
        return
    if not (is_owner(query.from_user.id) or _is_market_supervisor(query.from_user.id, report["market_id"])):
        await query.answer()
        return
    await query.answer("Согласовано")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("✅ Согласовано, отправляю Роману.")
    await _send_for_owner_approval(context.bot, report_id)


async def on_shift_report_owner_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    report_id = int(query.data.split(":", 1)[1])
    report = get_report(report_id)
    if not report:
        await query.answer("Отчёт не найден", show_alert=True)
        return
    await query.answer("Согласовано")
    set_report_status(report_id, "approved")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("✅ Согласовано. Отчёт уйдёт в чат финпартнёров завтра в 10:00.")


async def on_shift_report_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    report_id = int(query.data.split(":", 1)[1])
    report = get_report(report_id)
    if not report:
        await query.answer("Отчёт не найден", show_alert=True)
        return
    if not (is_owner(query.from_user.id) or _is_market_supervisor(query.from_user.id, report["market_id"])):
        await query.answer()
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=_edit_field_keyboard(report_id))


async def on_shift_report_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, report_id_str, idx_str = query.data.split(":")
    report_id, idx = int(report_id_str), int(idx_str)
    report = get_report(report_id)
    if not report or idx >= len(_QUESTIONS):
        await query.answer("Не найдено", show_alert=True)
        return
    if not (is_owner(query.from_user.id) or _is_market_supervisor(query.from_user.id, report["market_id"])):
        await query.answer()
        return
    _editing[str(query.from_user.id)] = {"report_id": report_id, "field_index": idx}
    await query.answer()
    question = _QUESTIONS[idx]
    await query.message.reply_text(f"Новое значение — {_FIELD_LABELS[question['key']]}:\n{question['prompt']}")


async def on_shift_report_edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    report_id = int(query.data.split(":", 1)[1])
    report = get_report(report_id)
    if not report:
        await query.answer()
        return
    await query.answer()
    keyboard = _approval_keyboard_supervisor(report_id) if report["status"] == "awaiting_supervisor" else _approval_keyboard_owner(report_id)
    await query.edit_message_reply_markup(reply_markup=keyboard)


async def on_shift_report_edit_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = str(update.effective_user.id)
    state = _editing.get(user_id)
    if not state:
        return False

    report = get_report(state["report_id"])
    if not report:
        del _editing[user_id]
        await update.effective_message.reply_text("Отчёт больше недоступен.")
        return True

    question = _QUESTIONS[state["field_index"]]
    text = update.effective_message.text or ""
    value, error = _validate(question["kind"], text)
    if error:
        await update.effective_message.reply_text(error)
        return True

    del _editing[user_id]
    data = report["data"]
    data[question["key"]] = value
    save_report_data(report["id"], data)
    market = get_market(report["market_id"])
    text_out = render_finance_report(market, report["report_date"], data)
    keyboard = _approval_keyboard_supervisor(report["id"]) if report["status"] == "awaiting_supervisor" else _approval_keyboard_owner(report["id"])
    await update.effective_message.reply_text(f"✅ Обновлено.\n\n{text_out}", reply_markup=keyboard)
    return True


async def on_shift_report_more_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    report_id = int(query.data.split(":", 1)[1])
    report = get_report(report_id)
    if not report:
        await query.answer("Отчёт не найден", show_alert=True)
        return
    _pending_more_info[str(query.from_user.id)] = {"report_id": report_id}
    await query.answer()
    await query.message.reply_text("Что нужно дополнить или уточнить? Напиши одним сообщением — перешлю управляющему.")


async def on_shift_report_more_info_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    owner_id = str(update.effective_user.id)
    state = _pending_more_info.pop(owner_id, None)
    if not state:
        return False

    report = get_report(state["report_id"])
    if not report:
        await update.effective_message.reply_text("Отчёт больше недоступен.")
        return True

    market = get_market(report["market_id"])
    supervisor = get_market_supervisor(report["market_id"])
    note = update.effective_message.text or ""
    if not supervisor:
        await update.effective_message.reply_text("У рынка нет управляющего — некому передать замечание.")
        return True

    set_report_status(report["id"], "awaiting_supervisor")
    await context.bot.send_message(
        chat_id=supervisor["telegram_user_id"],
        text=(
            f"💬 Рома просит дополнить отчёт по «{market['name']}» за {fmt_date(report['report_date'])}:\n"
            f"«{note}»\n\nНапиши дополнение одним сообщением:"
        ),
    )
    _awaiting_addendum[str(supervisor["telegram_user_id"])] = {"report_id": report["id"]}
    await update.effective_message.reply_text("Передал управляющему, жду дополнение.")
    return True


async def on_shift_report_addendum_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = str(update.effective_user.id)
    state = _awaiting_addendum.pop(user_id, None)
    if not state:
        return False

    report = get_report(state["report_id"])
    if not report:
        await update.effective_message.reply_text("Отчёт больше недоступен.")
        return True

    addendum = (update.effective_message.text or "").strip()
    data = report["data"]
    existing = data.get("comment_general", "")
    data["comment_general"] = f"{existing}\n\nДополнение от управляющего: {addendum}" if existing else f"Дополнение от управляющего: {addendum}"
    save_report_data(report["id"], data)
    await update.effective_message.reply_text("Спасибо, отправляю Роману.")
    await _send_for_owner_approval(context.bot, report["id"])
    return True


async def send_shift_report_kickoffs(bot: Bot) -> None:
    """22:00 — для каждого рынка с записью в графике на сегодня предлагает
    назначенному менеджеру заполнить отчёт (см. main.py)."""
    date_iso = tz_today().isoformat()
    for market in list_markets_with_shift(date_iso):
        await _offer_report_or_absence(bot, market["scheduled_manager_id"], market, date_iso)


async def send_shift_report_escalations(bot: Bot) -> None:
    """23:30 — если по рынку с сегодняшней записью в графике отчёт так и не
    начали сдавать управляющему, сообщает управляющему (или владельцу, если
    управляющего нет) и предлагает те же две кнопки (см. main.py)."""
    date_iso = tz_today().isoformat()
    for market in list_markets_with_shift(date_iso):
        report = get_report_by_date(market["id"], date_iso)
        if report and report["status"] != "collecting":
            continue

        manager_name = get_display_name(market["scheduled_manager_id"])
        supervisor = get_market_supervisor(market["id"])
        if supervisor:
            await bot.send_message(
                chat_id=supervisor["telegram_user_id"],
                text=f"⚠️ {manager_name} не сдал(а) отчёт по «{market['name']}» за сегодня.",
            )
            await _offer_report_or_absence(bot, supervisor["telegram_user_id"], market, date_iso)
        else:
            await bot.send_message(
                chat_id=ROMAN_TELEGRAM_ID,
                text=f"⚠️ {manager_name} не сдал(а) отчёт по «{market['name']}» за сегодня, управляющего нет.",
            )


async def send_pending_reports(bot: Bot) -> None:
    """10:00 — рассылает вчерашние согласованные отчёты в зарегистрированные
    чаты финпартнёров и команды точки (см. main.py, bot/report_chat_registration.py)."""
    yesterday = (tz_today() - timedelta(days=1)).isoformat()
    for report in list_reports_by_status_and_date(yesterday, "approved"):
        market = get_market(report["market_id"])
        if not market:
            continue

        finance_chat_id = get_report_chat(report["market_id"], "finance")
        if finance_chat_id:
            try:
                await bot.send_message(chat_id=finance_chat_id, text=render_finance_report(market, report["report_date"], report["data"]))
            except Exception:
                pass

        team_chat_id = get_report_chat(report["market_id"], "team")
        if team_chat_id:
            try:
                await bot.send_message(chat_id=team_chat_id, text=render_team_report(market, report["report_date"], report["data"]))
            except Exception:
                pass

        set_report_status(report["id"], "dispatched")
