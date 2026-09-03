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
    delete_report,
    get_previous_week_report,
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
    {"key": "revenue_total", "kind": "money", "prompt": "💰 Общая выручка за день\nНапример: 324675,87"},
    {"key": "revenue_cash", "kind": "money", "prompt": "💵 Наличная выручка\nНапример: 324 675,87"},
    {"key": "revenue_noncash", "kind": "money", "prompt": "💳 Безналичная выручка\nНапример: 324675,87"},
    {"key": "avg_check", "kind": "money", "prompt": "🧾 Средний чек за смену\nНапример: 789,87"},
    {"key": "guests", "kind": "guests", "prompt": "👥 Количество гостей\nНапример: 1 234"},
    {
        "key": "writeoff_expiry",
        "kind": "money",
        "prompt": "🗑 Списания «Истёк срок годности»\nЗа сегодня, себестоимость. Например: 7 189,87",
    },
    {
        "key": "writeoff_compliment",
        "kind": "money",
        "prompt": "🎁 Списания «Комплимент»\nЗа сегодня, себестоимость. Например: 7 189,87",
    },
    {
        "key": "writeoff_staff_meals",
        "kind": "money",
        "prompt": "🍽 Списания «Питание сотрудников»\nЗа сегодня, себестоимость. Например: 7 189,87",
    },
    {"key": "avg_service_time", "kind": "time", "prompt": "⏱ Среднее время выдачи и приготовления заказов\nНапример: 5:35"},
    {
        "key": "comment_general",
        "kind": "text",
        "prompt": (
            "📝 Общая работа точки\n"
            "Как прошла смена, с какими трудностями столкнулись, а с чем справились хорошо? На что обратить "
            "внимание менеджеру следующего дня?"
        ),
    },
    {
        "key": "comment_service",
        "kind": "text",
        "prompt": (
            "🙂 Обслуживание гостей\n"
            "Как проходило сегодня? Были позитивные отзывы? Как гости реагировали на новинки в меню, если такие есть?"
        ),
    },
    {"key": "comment_conflicts", "kind": "text", "prompt": "⚠️ Конфликтные ситуации\nБыли сегодня? Опиши, если да — или напиши «не было»."},
    {
        "key": "comment_equipment",
        "kind": "text",
        "prompt": (
            "🔧 Оборудование\n"
            "Что с техникой, интерьером, мебелью? Если что-то неисправно — сообщи, даже если уже говорил(а) об этом раньше."
        ),
    },
    {
        "key": "comment_weather_flow",
        "kind": "text",
        "prompt": (
            "🌤 Погода и поток гостей\n"
            "Какая была погода и как она влияла на поток в течение дня? Что ещё могло повлиять, если сравнить с планом?"
        ),
    },
    {
        "key": "comment_events",
        "kind": "text",
        "prompt": "📅 Значимые события\nБыли события или мероприятия в Парке или рядом, которые повлияли на поток — в любую сторону?",
    },
    {
        "key": "shift_composition",
        "kind": "text",
        "prompt": (
            "👩‍🍳 Состав смены\n"
            "Кто и в каком количестве работал — отдельно утро и вечер (менеджеры, бариста, клинеры, «уютные»)."
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
    "comment_equipment": "Оборудование",
    "comment_weather_flow": "Погода и поток гостей",
    "comment_events": "Значимые события",
    "shift_composition": "Состав смены",
    "revenue_total_prev": "Выручка (сравнение)",
    "avg_check_prev": "Средний чек (сравнение)",
    "guests_prev": "Гости (сравнение)",
}

# Поля для сравнения с той же датой на прошлой неделе — запрашиваются
# дополнительно только если за прошлую неделю нет согласованного отчёта.
_BASELINE_FIELD_DEFS = {
    "revenue_total_prev": {"key": "revenue_total_prev", "kind": "money", "prompt": "📊 Выручка на сравниваемую дату\nНапример: 324675,87"},
    "avg_check_prev": {"key": "avg_check_prev", "kind": "money", "prompt": "📊 Средний чек на сравниваемую дату\nНапример: 789,87"},
    "guests_prev": {"key": "guests_prev", "kind": "guests", "prompt": "📊 Количество гостей на сравниваемую дату\nНапример: 1 234"},
}

_QUESTION_BY_KEY = {q["key"]: q for q in _QUESTIONS}
_QUESTION_BY_KEY.update(_BASELINE_FIELD_DEFS)

# telegram_user_id (str) заполняющего -> {"market_id", "market_name", "report_date",
# "report_id", "step_index", "answers", "is_supervisor_filling"}
_pending: dict[str, dict] = {}

# telegram_user_id (str) -> {"report_id": int, "field_key": str} — точечная правка поля
_editing: dict[str, dict] = {}

# telegram_user_id (str) владельца -> {"report_id": int} — ждём текст замечания для управляющего
_pending_more_info: dict[str, dict] = {}


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


def _format_money(value: float) -> str:
    """Строгий формат для итогового отчёта: 1 111 111,11 — пробел как
    разделитель тысяч, запятая как разделитель копеек, всегда два знака
    после запятой, даже если копейки нулевые."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _format_count(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _money_field(data: dict, key: str) -> str:
    value = _parse_amount(data.get(key, "") or "")
    return _format_money(value) if value is not None else "—"


def _count_field(data: dict, key: str) -> str:
    value = _parse_int(data.get(key, "") or "")
    return _format_count(value) if value is not None else "—"


def _validate_reconciliation(answers: dict) -> tuple[str, list[str]] | None:
    """Сверка сумм: наличные+безнал должны совпадать с выручкой до копейки,
    средний чек должен совпадать с выручкой/гости с точностью до рубля.
    Вызывается после каждого ответа — срабатывает, только когда все нужные
    для конкретной проверки поля уже заполнены. Возвращает текст ошибки и
    список полей, любое из которых может быть неверным — сотрудник сам
    выбирает, какое поправить, вместо того чтобы гадать за него."""
    total = _parse_amount(answers.get("revenue_total", "") or "")
    cash = _parse_amount(answers.get("revenue_cash", "") or "")
    noncash = _parse_amount(answers.get("revenue_noncash", "") or "")
    if total is not None and cash is not None and noncash is not None:
        if abs(round(cash + noncash, 2) - round(total, 2)) > 0.005:
            return (
                f"⚠️ Наличные + Безнал = {_format_money(cash)} + {_format_money(noncash)} = "
                f"{_format_money(cash + noncash)}, а выручка указана как {_format_money(total)} — не сходится.",
                ["revenue_total", "revenue_cash", "revenue_noncash"],
            )

    avg_check = _parse_amount(answers.get("avg_check", "") or "")
    guests = _parse_int(answers.get("guests", "") or "")
    if total is not None and avg_check is not None and guests:
        expected = total / guests
        if round(expected) != round(avg_check):
            return (
                f"⚠️ При выручке {_format_money(total)} и {guests} гостях средний чек должен быть "
                f"≈ {_format_money(expected)}, а указано {_format_money(avg_check)} — не сходится.",
                ["revenue_total", "avg_check", "guests"],
            )
    return None


def _validate(kind: str, text: str) -> tuple[str | None, str | None]:
    text = text.strip()
    if kind == "money":
        if _parse_amount(text) is None:
            return None, "🤔 Не понял число. Попробуй ещё раз, например: 324675,87"
        return text, None
    if kind == "guests":
        if _parse_int(text) is None:
            return None, "🤔 Нужно целое число. Попробуй ещё раз, например: 737"
        return text, None
    if kind == "time":
        if not re.match(r"^\d{1,2}:\d{2}$", text):
            return None, "🤔 Нужен формат М:СС. Попробуй ещё раз, например: 5:35"
        return text, None
    if not text:
        return None, "🤔 Не может быть пустым — напиши хотя бы коротко:"
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


def _edit_field_keyboard(report: dict) -> InlineKeyboardMarkup:
    report_id = report["id"]
    buttons = [
        [InlineKeyboardButton(_FIELD_LABELS[q["key"]], callback_data=f"shrep_editfield:{report_id}:{q['key']}")]
        for q in _QUESTIONS
    ]
    for key in _BASELINE_FIELD_DEFS:
        if key in report["data"]:
            buttons.append([InlineKeyboardButton(_FIELD_LABELS[key], callback_data=f"shrep_editfield:{report_id}:{key}")])
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"shrep_editcancel:{report_id}")])
    return InlineKeyboardMarkup(buttons)


def _fix_field_keyboard_collection(keys: list[str]) -> InlineKeyboardMarkup:
    """Кнопки выбора поля при сведении сумм ещё во время сбора отчёта —
    сотрудник мог ошибиться в любом из участвующих в проверке полей, не
    обязательно в том, что спрашивали последним."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(_FIELD_LABELS[k], callback_data=f"shrep_fixfield:{k}")] for k in keys])


def _fix_field_keyboard_edit(report_id: int, keys: list[str]) -> InlineKeyboardMarkup:
    """То же самое, но при правке уже собранного отчёта — переиспользует
    те же callback'и, что и «✏️ Редактировать»."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(_FIELD_LABELS[k], callback_data=f"shrep_editfield:{report_id}:{k}")] for k in keys])


def _format_delta(current: float, previous: float) -> str:
    """Δ% к той же дате прошлой недели, со стрелкой направления. Пустая
    строка, если сравнивать не с чем (previous = 0)."""
    if not previous:
        return ""
    pct = (current - previous) / previous * 100
    arrow = "↗️" if pct > 0 else ("↘️" if pct < 0 else "➡️")
    pct_str = f"{abs(pct):.2f}".replace(".", ",")
    return f"    {pct_str}% {arrow}"


def _money_compare_line(label: str, data: dict, key: str) -> str:
    current = _parse_amount(data.get(key, "") or "")
    current_str = _format_money(current) if current is not None else "—"
    prev_raw = data.get(f"{key}_prev")
    prev = _parse_amount(prev_raw or "") if prev_raw is not None else None
    if current is not None and prev is not None:
        return f"{label}: {current_str} ({_format_money(prev)}){_format_delta(current, prev)}"
    return f"{label}: {current_str}"


def _count_compare_line(label: str, data: dict, key: str) -> str:
    current = _parse_int(data.get(key, "") or "")
    current_str = _format_count(current) if current is not None else "—"
    prev_raw = data.get(f"{key}_prev")
    prev = _parse_int(prev_raw or "") if prev_raw is not None else None
    if current is not None and prev is not None:
        return f"{label}: {current_str} ({_format_count(prev)}){_format_delta(current, prev)}"
    return f"{label}: {current_str}"


def render_finance_report(market: dict, report_date: str, data: dict) -> str:
    """Форматирует отчёт строго по образцу Романа: заголовок, денежные
    поля (у выручки/среднего чека/гостей — сравнение с той же датой на
    прошлой неделе и % изменения), блок комментариев без разметки, состав
    смены свободным текстом в конце."""
    weekday = _WEEKDAY_RU[_date.fromisoformat(report_date).weekday()].capitalize()
    lines = [
        f"Отчет {fmt_date(report_date)} {weekday}",
        "",
        _money_compare_line("выручка", data, "revenue_total"),
        f"наличные: {_money_field(data, 'revenue_cash')}",
        f"безнал: {_money_field(data, 'revenue_noncash')}",
        "",
        _money_compare_line("средний чек", data, "avg_check"),
        "",
        _count_compare_line("гости", data, "guests"),
        "",
        f"срок годности: {_money_field(data, 'writeoff_expiry')}",
        f"комплимент: {_money_field(data, 'writeoff_compliment')}",
        f"питание: {_money_field(data, 'writeoff_staff_meals')}",
        "",
        f"Среднее время отдачи за день: {data.get('avg_service_time', '—')}",
        "",
        "Комментарий:",
        "",
        f"Общая работа точки: {data.get('comment_general', '—')}",
        "",
        f"Обслуживание гостей: {data.get('comment_service', '—')}",
        "",
        f"Конфликтные ситуации: {data.get('comment_conflicts', '—')}",
        "",
        f"Оборудование: {data.get('comment_equipment', '—')}",
        "",
        f"Погода и поток гостей: {data.get('comment_weather_flow', '—')}",
        "",
        f"Значимые события: {data.get('comment_events', '—')}",
        "Состав смены:",
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
            text="👋 Привет! Пора сдавать вечерний отчёт по смене.",
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


def _build_questions(report_date: str, answers: dict) -> list[dict]:
    """Основные 16 вопросов, плюс — только если для сравнения ещё нет
    данных (нет согласованного отчёта за ту же дату прошлой недели и они
    не введены вручную ранее) — 3 дополнительных вопроса на выручку/чек/
    гостей за прошлую неделю, чтобы было с чем сравнивать в самом отчёте."""
    if all(k in answers for k in _BASELINE_FIELD_DEFS):
        return list(_QUESTIONS)
    prev_date = (_date.fromisoformat(report_date) - timedelta(days=7)).isoformat()
    weekday_name = _WEEKDAY_RU[_date.fromisoformat(prev_date).weekday()]
    baseline = [
        {
            "key": "revenue_total_prev",
            "kind": "money",
            "prompt": (
                f"📊 Не нашёл отчёт за прошлую {weekday_name} ({fmt_date(prev_date)}) для сравнения — "
                "подскажи вручную, какая тогда была выручка?\nНапример: 324675,87"
            ),
        },
        {"key": "avg_check_prev", "kind": "money", "prompt": f"📊 Какой был средний чек в прошлую {weekday_name}?\nНапример: 789,87"},
        {"key": "guests_prev", "kind": "guests", "prompt": f"📊 Сколько было гостей в прошлую {weekday_name}?\nНапример: 1 234"},
    ]
    return baseline + list(_QUESTIONS)


async def _ask_current_question(bot: Bot, message, user_id: str) -> None:
    state = _pending[user_id]
    idx = state["step_index"]
    questions = state["questions"]
    if idx >= len(questions):
        await _finish_collection(bot, message, user_id)
        return
    progress = f"Шаг {idx + 1} из {len(questions)}"
    await message.reply_text(f"{progress}\n\n{questions[idx]['prompt']}")


async def _finish_collection(bot: Bot, message, user_id: str) -> None:
    state = _pending.pop(user_id)
    report_id = state["report_id"]
    if state["is_supervisor_filling"]:
        await message.reply_text("🎉 Спасибо, отчёт готов! Отправляю Роману на согласование.")
        await _send_for_owner_approval(bot, report_id)
    else:
        await message.reply_text("🎉 Спасибо, отчёт готов! Отправляю управляющему на согласование.")
        await _send_for_supervisor_approval(bot, report_id)


async def _begin_collection(bot: Bot, message, user_id: int, market: dict, report_date: str) -> None:
    report = create_or_get_draft(market["id"], report_date, user_id)
    answers = dict(report.get("data") or {})
    if not all(k in answers for k in _BASELINE_FIELD_DEFS):
        prev_report = get_previous_week_report(market["id"], report_date)
        if prev_report and all(k in prev_report["data"] for k in ("revenue_total", "avg_check", "guests")):
            answers.setdefault("revenue_total_prev", prev_report["data"]["revenue_total"])
            answers.setdefault("avg_check_prev", prev_report["data"]["avg_check"])
            answers.setdefault("guests_prev", prev_report["data"]["guests"])

    _pending[str(user_id)] = {
        "market_id": market["id"],
        "market_name": market["name"],
        "report_date": report_date,
        "report_id": report["id"],
        "step_index": 0,
        "answers": answers,
        "questions": _build_questions(report_date, answers),
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


def _send_now_market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"shrep_sendmarket:{m['id']}")] for m in markets])


async def _send_report_now(bot: Bot, message, market: dict) -> None:
    date_iso = tz_today().isoformat()
    report = get_report_by_date(market["id"], date_iso)
    if not report:
        await message.reply_text(f"На «{market['name']}» ещё нет отчёта за сегодня.")
        return

    finance_chat = get_report_chat(market["id"], "finance")
    team_chat = get_report_chat(market["id"], "team")
    if not finance_chat and not team_chat:
        await message.reply_text(f"Для «{market['name']}» не привязан ни один чат — сначала /register_report_chat.")
        return

    sent = []
    if finance_chat:
        text = render_finance_report(market, date_iso, report["data"])
        if finance_chat.get("mention"):
            text = f"{finance_chat['mention']}\n\n{text}"
        try:
            await bot.send_message(chat_id=finance_chat["chat_id"], text=text)
            sent.append("финпартнёры")
        except Exception as e:
            await message.reply_text(f"⚠️ Не смог отправить в чат финпартнёров: {e}")

    if team_chat:
        text = render_team_report(market, date_iso, report["data"])
        if team_chat.get("mention"):
            text = f"{team_chat['mention']}\n\n{text}"
        try:
            await bot.send_message(chat_id=team_chat["chat_id"], text=text)
            sent.append("команда точки")
        except Exception as e:
            await message.reply_text(f"⚠️ Не смог отправить в чат команды точки: {e}")

    if sent:
        await message.reply_text(
            f"✅ Отправил сегодняшний отчёт по «{market['name']}» в: {', '.join(sent)}.\n"
            f"Статус отчёта (сейчас: {report['status']}) не менялся — автоматическая рассылка завтра в 10:00 всё равно сработает как обычно."
        )


async def on_send_shift_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/send_shift_report — владелец сразу отправляет сегодняшний отчёт в
    привязанные чаты (финпартнёры/команда точки), не дожидаясь
    автоматической рассылки в 10:00 следующего дня — удобно, чтобы
    проверить формат. Статус отчёта при этом не меняется."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка.")
        return
    if len(markets) == 1:
        await _send_report_now(context.bot, update.effective_message, markets[0])
        return
    await update.effective_message.reply_text(
        "По какому рынку отправить сегодняшний отчёт?", reply_markup=_send_now_market_pick_keyboard(markets)
    )


async def on_send_shift_report_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _send_report_now(context.bot, query.message, market)


def _reset_market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"shrep_resetmarket:{m['id']}")] for m in markets])


def _reset_confirm_keyboard(market_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Да, сбросить", callback_data=f"shrep_resetconfirm:{market_id}"),
                InlineKeyboardButton("Отмена", callback_data="shrep_resetcancel"),
            ]
        ]
    )


async def on_reset_shift_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reset_shift_report — владелец удаляет сегодняшний отчёт целиком
    (собранные ответы или согласование), чтобы прогнать /shift_report
    заново — например, для тестирования."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка.")
        return
    await update.effective_message.reply_text(
        "По какому рынку сбросить сегодняшний отчёт?", reply_markup=_reset_market_pick_keyboard(markets)
    )


async def on_reset_shift_report_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    date_iso = tz_today().isoformat()
    if not get_report_by_date(market_id, date_iso):
        await query.answer()
        await query.edit_message_text(f"На «{market['name']}» отчёта за сегодня и не было — сбрасывать нечего.")
        return
    await query.answer()
    await query.edit_message_text(
        f"⚠️ Удалить сегодняшний отчёт по «{market['name']}» целиком (собранные ответы, согласование)? Действие необратимо.",
        reply_markup=_reset_confirm_keyboard(market_id),
    )


async def on_reset_shift_report_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer("Сбрасываю…")
    delete_report(market_id, tz_today().isoformat())
    await query.edit_message_text(f"✅ Отчёт по «{market['name']}» за сегодня сброшен. Запустите заново через /shift_report.")


async def on_reset_shift_report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    await query.answer()
    await query.edit_message_text("Отменено.")


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


async def _handle_reconciliation_failure(message, state: dict, error: str, keys: list[str]) -> None:
    save_report_data(state["report_id"], state["answers"])
    await message.reply_text(f"{error}\n\nКакое из полей поправить?", reply_markup=_fix_field_keyboard_collection(keys))


async def on_shift_report_fix_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сотрудник выбрал, какое из полей поправить, после того как суммы не
    сошлись — открывает именно этот вопрос повторно, не трогая остальные
    уже собранные ответы."""
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state:
        await query.answer()
        return

    key = query.data.split(":", 1)[1]
    question = _QUESTION_BY_KEY.get(key)
    if not question:
        await query.answer()
        return

    state["awaiting_fix_key"] = key
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(question["prompt"])


async def on_shift_report_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает ответы на вопросы вечернего отчёта. Возвращает True, если
    сообщение обработано — по конвенции остальных claim-хендлеров в
    on_private_text."""
    user_id = str(update.effective_user.id)
    state = _pending.get(user_id)
    if not state:
        return False

    fix_key = state.get("awaiting_fix_key")
    if fix_key:
        question = _QUESTION_BY_KEY[fix_key]
        text = update.effective_message.text or ""
        value, error = _validate(question["kind"], text)
        if error:
            await update.effective_message.reply_text(error)
            return True

        state["answers"][fix_key] = value
        del state["awaiting_fix_key"]
        recon = _validate_reconciliation(state["answers"])
        if recon:
            await _handle_reconciliation_failure(update.effective_message, state, recon[0], recon[1])
            return True

        state["step_index"] += 1
        save_report_data(state["report_id"], state["answers"])
        await _ask_current_question(context.bot, update.effective_message, user_id)
        return True

    idx = state["step_index"]
    questions = state["questions"]
    if idx >= len(questions):
        return False
    question = questions[idx]
    text = update.effective_message.text or ""
    value, error = _validate(question["kind"], text)
    if error:
        await update.effective_message.reply_text(error)
        return True

    state["answers"][question["key"]] = value
    recon = _validate_reconciliation(state["answers"])
    if recon:
        await _handle_reconciliation_failure(update.effective_message, state, recon[0], recon[1])
        return True

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
    await query.edit_message_reply_markup(reply_markup=_edit_field_keyboard(report))


async def on_shift_report_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, report_id_str, key = query.data.split(":", 2)
    report_id = int(report_id_str)
    report = get_report(report_id)
    question = _QUESTION_BY_KEY.get(key)
    if not report or not question:
        await query.answer("Не найдено", show_alert=True)
        return
    if not (is_owner(query.from_user.id) or _is_market_supervisor(query.from_user.id, report["market_id"])):
        await query.answer()
        return
    _editing[str(query.from_user.id)] = {"report_id": report_id, "field_key": key}
    await query.answer()
    await query.message.reply_text(f"Новое значение — {_FIELD_LABELS[key]}:\n{question['prompt']}")


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

    question = _QUESTION_BY_KEY[state["field_key"]]
    text = update.effective_message.text or ""
    value, error = _validate(question["kind"], text)
    if error:
        await update.effective_message.reply_text(error)
        return True

    data = report["data"]
    candidate = dict(data)
    candidate[question["key"]] = value
    recon = _validate_reconciliation(candidate)
    if recon:
        error_text, keys = recon
        del _editing[user_id]
        await update.effective_message.reply_text(
            f"{error_text}\n\nКакое из полей поправить?", reply_markup=_fix_field_keyboard_edit(report["id"], keys)
        )
        return True

    del _editing[user_id]
    data = candidate
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
    """Замечание владельца пересылается управляющему вместе с пикером полей
    (тем же, что у «✏️ Редактировать») — управляющий правит конкретное поле
    целиком, а не дописывает текст поверх него: так отчёт не отклоняется от
    шаблона посторонними фразами вроде "Дополнение от управляющего"."""
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
            f"💬 Рома просит поправить отчёт по «{market['name']}» за {fmt_date(report['report_date'])}:\n"
            f"«{note}»\n\nВыбери, что поправить:"
        ),
        reply_markup=_edit_field_keyboard(report),
    )
    await update.effective_message.reply_text("Передал управляющему.")
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

        finance_chat = get_report_chat(report["market_id"], "finance")
        if finance_chat:
            text = render_finance_report(market, report["report_date"], report["data"])
            if finance_chat.get("mention"):
                text = f"{finance_chat['mention']}\n\n{text}"
            try:
                await bot.send_message(chat_id=finance_chat["chat_id"], text=text)
            except Exception:
                pass

        team_chat = get_report_chat(report["market_id"], "team")
        if team_chat:
            text = render_team_report(market, report["report_date"], report["data"])
            if team_chat.get("mention"):
                text = f"{team_chat['mention']}\n\n{text}"
            try:
                await bot.send_message(chat_id=team_chat["chat_id"], text=text)
            except Exception:
                pass

        set_report_status(report["id"], "dispatched")
