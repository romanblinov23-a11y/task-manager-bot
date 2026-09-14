import html
import re
from datetime import date as _date

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import ROMAN_TELEGRAM_ID
from config.timeutil import fmt_date
from config.timeutil import now as tz_now
from config.timeutil import today as tz_today
from monitoring.handover_reports import (
    create_or_get_draft,
    delete_report,
    get_report,
    get_report_by_date,
    save_report_data,
    set_report_status,
)
from monitoring.handover_schedule import list_markets_with_handover
from monitoring.managers import (
    get_market_supervisor,
    get_markets_for_manager,
    has_handover_access,
    is_handover_editor,
    is_owner,
    market_handover_enabled,
)
from monitoring.markets import get_effective_handover_time, get_market, list_markets
from monitoring.shift_reports import get_report_chat

_WEEKDAY_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

_QUESTIONS = [
    {"key": "staff", "kind": "text", "prompt": "🏄🏽 Бариста\nКто работал на смене?"},
    {"key": "revenue_net", "kind": "money", "prompt": "💸 Общая выручка чистыми\nНапример: 45000"},
    {"key": "points_written_off", "kind": "count", "prompt": "💯 Списанные баллы\nСколько баллов списано за смену? Например: 320"},
    {"key": "licenses_issued", "kind": "count", "prompt": "🧮 Количество оформленных лицензий\nНапример: 3"},
    {"key": "checks_count", "kind": "count", "prompt": "🧾 Количество чеков\nНапример: 62"},
    {
        "key": "hourly_breakdown",
        "kind": "text",
        "prompt": "📈 По часам\nКак распределился поток по часам? Например: 10–12 густо, 12–15 спокойно, 15–18 снова поток",
    },
    {"key": "avg_check", "kind": "money", "prompt": "💵 Средний чек\nНапример: 725"},
    {"key": "guest_feedback", "kind": "text", "prompt": "💬 Обратная связь от гостей\nБыли отзывы, жалобы, похвала?"},
    {"key": "stop_list", "kind": "text", "prompt": "🚫 Стоп\nЧто сейчас в стопе?"},
    {"key": "start_list", "kind": "text", "prompt": "‼️ Старт\nЧто добавилось/появилось?"},
    {"key": "comment_top_sellers", "kind": "text", "prompt": "✍🏻 Что активно брали?"},
    {"key": "comment_low_sellers", "kind": "text", "prompt": "✍🏻 Что не брали?"},
    {"key": "comment_flow", "kind": "text", "prompt": "✍🏻 Какие были потоки?"},
    {"key": "comment_pros_cons", "kind": "text", "prompt": "✍🏻 Положительные и негативные моменты за смену?"},
    {"key": "mood_vibe", "kind": "text", "prompt": "✍🏻 Вайб на смене?"},
    {"key": "mood_memorable", "kind": "text", "prompt": "✍🏻 Что запомнилось со смены больше всего?"},
]

_FIELD_LABELS = {
    "staff": "Бариста",
    "revenue_net": "Выручка чистыми",
    "points_written_off": "Списанные баллы",
    "licenses_issued": "Оформлено лицензий",
    "checks_count": "Количество чеков",
    "hourly_breakdown": "По часам",
    "avg_check": "Средний чек",
    "guest_feedback": "Обратная связь от гостей",
    "stop_list": "Стоп",
    "start_list": "Старт",
    "comment_top_sellers": "Что активно брали",
    "comment_low_sellers": "Что не брали",
    "comment_flow": "Какие были потоки",
    "comment_pros_cons": "Плюсы/минусы смены",
    "mood_vibe": "Вайб на смене",
    "mood_memorable": "Что запомнилось",
}

_QUESTION_BY_KEY = {q["key"]: q for q in _QUESTIONS}

# telegram_user_id (str) заполняющего -> {"market_id", "market_name", "report_date",
# "report_id", "step_index", "answers", "awaiting_fix_key"}
_pending: dict[str, dict] = {}


def _parse_amount(text: str) -> float | None:
    cleaned = re.sub(r"\s", "", text).replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_int(text: str) -> int | None:
    cleaned = re.sub(r"\s", "", text)
    return int(cleaned) if cleaned.isdigit() else None


def _format_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _format_count(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _money_field(data: dict, key: str) -> str:
    value = _parse_amount(data.get(key, "") or "")
    return _format_money(value) if value is not None else "—"


def _count_field(data: dict, key: str) -> str:
    value = _parse_int(data.get(key, "") or "")
    return _format_count(value) if value is not None else "—"


def _esc(text: str | None) -> str:
    return html.escape(text, quote=False) if text else "—"


def _validate(kind: str, text: str) -> tuple[str | None, str | None]:
    text = text.strip()
    if kind == "money":
        if _parse_amount(text) is None:
            return None, "🤔 Не понял число. Попробуй ещё раз, например: 45000"
        return text, None
    if kind == "count":
        if _parse_int(text) is None:
            return None, "🤔 Нужно целое число. Попробуй ещё раз, например: 62"
        return text, None
    if not text:
        return None, "🤔 Не может быть пустым — напиши хотя бы коротко:"
    return text, None


def _validate_reconciliation(answers: dict) -> tuple[str, list[str]] | None:
    """Единственная сверка в пересменке: средний чек должен примерно
    совпадать с выручкой, делённой на количество чеков."""
    revenue = _parse_amount(answers.get("revenue_net", "") or "")
    checks = _parse_int(answers.get("checks_count", "") or "")
    avg_check = _parse_amount(answers.get("avg_check", "") or "")
    if revenue is not None and checks and avg_check is not None:
        expected = revenue / checks
        if round(expected) != round(avg_check):
            return (
                f"⚠️ При выручке {_format_money(revenue)} и {checks} чеках средний чек должен быть "
                f"≈ {_format_money(expected)}, а указано {_format_money(avg_check)} — не сходится.",
                ["revenue_net", "checks_count", "avg_check"],
            )
    return None


def _kickoff_keyboard(market_id: int, report_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📝 Заполню сам", callback_data=f"horep_fill:{market_id}:{report_date}"),
                InlineKeyboardButton("🚫 Не работаю сегодня", callback_data=f"horep_absent:{market_id}:{report_date}"),
            ]
        ]
    )


def _fix_field_keyboard(keys: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(_FIELD_LABELS[k], callback_data=f"horep_fixfield:{k}")] for k in keys])


async def _offer_handover_or_absence(bot: Bot, telegram_user_id: int, market: dict, report_date: str) -> None:
    try:
        await bot.send_message(
            chat_id=telegram_user_id,
            text="👋 Привет! Пора передавать пересменку.",
            reply_markup=_kickoff_keyboard(market["id"], report_date),
        )
    except Exception:
        pass


def render_handover_report(market: dict, report_date: str, data: dict) -> str:
    """Пересменка уходит прямо в чат команды точки, как только заполнена —
    без цепочки согласований, в отличие от вечернего отчёта по смене (см.
    bot.shift_reports.render_finance_report). Формат по образцу, который
    прислал Рома — плоский, минимум эмодзи, только там, где они были в
    исходном шаблоне."""
    weekday = _WEEKDAY_RU[_date.fromisoformat(report_date).weekday()]
    lines = [
        f"Смена передаёт эстафету — вот как прошло ({weekday}, {fmt_date(report_date)})",
        "",
        "<b>Показатели</b>",
        f"🏄🏽 Бариста: {_esc(data.get('staff'))}",
        f"💸 Выручка чистыми: {_money_field(data, 'revenue_net')} ₽",
        f"🧾 Чеков: {_count_field(data, 'checks_count')}",
        f"💵 Средний чек: {_money_field(data, 'avg_check')} ₽",
        f"💯 Списанные баллы: {_count_field(data, 'points_written_off')}",
        f"🧮 Оформлено лицензий: {_count_field(data, 'licenses_issued')}",
        f"📈 По часам: {_esc(data.get('hourly_breakdown'))}",
        "",
        "<b>Гости</b>",
        f"💬 Обратная связь: {_esc(data.get('guest_feedback'))}",
        f"🚫 Стоп: {_esc(data.get('stop_list'))}",
        f"‼️ Старт: {_esc(data.get('start_list'))}",
        "",
        "<b>Комментарий</b>",
        f"• Активно брали: {_esc(data.get('comment_top_sellers'))}",
        f"• Не брали: {_esc(data.get('comment_low_sellers'))}",
        f"• Потоки: {_esc(data.get('comment_flow'))}",
        f"• Плюсы/минусы смены: {_esc(data.get('comment_pros_cons'))}",
        "",
        "<b>Общее настроение</b>",
        f"• Вайб на смене: {_esc(data.get('mood_vibe'))}",
        f"• Запомнилось: {_esc(data.get('mood_memorable'))}",
        "",
        "Хорошей смены следующей команде!",
    ]
    return "\n".join(lines)


async def _dispatch_handover_now(bot: Bot, market: dict, report_date: str, data: dict) -> bool:
    team_chat = get_report_chat(market["id"], "team")
    if not team_chat:
        return False
    text = render_handover_report(market, report_date, data)
    if team_chat.get("mention"):
        text = f"{team_chat['mention']}\n\n{text}"
    try:
        await bot.send_message(
            chat_id=team_chat["chat_id"],
            text=text,
            message_thread_id=team_chat.get("message_thread_id"),
            parse_mode="HTML",
        )
        return True
    except Exception:
        return False


async def _ask_current_question(message, user_id: str) -> None:
    state = _pending[user_id]
    idx = state["step_index"]
    if idx >= len(_QUESTIONS):
        return
    progress = f"Шаг {idx + 1} из {len(_QUESTIONS)}"
    await message.reply_text(f"{progress}\n\n{_QUESTIONS[idx]['prompt']}")


async def _begin_collection(message, user_id: int, market: dict, report_date: str) -> None:
    report = create_or_get_draft(market["id"], report_date, user_id)
    _pending[str(user_id)] = {
        "market_id": market["id"],
        "market_name": market["name"],
        "report_date": report_date,
        "report_id": report["id"],
        "step_index": 0,
        "answers": dict(report.get("data") or {}),
    }
    await _ask_current_question(message, str(user_id))


async def on_handover_fill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await _begin_collection(query.message, user_id, market, report_date)


async def on_handover_absent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await _offer_handover_or_absence(context.bot, supervisor["telegram_user_id"], market, report_date)
    else:
        await query.message.reply_text("Хорошо, сообщил владельцу.")
        await context.bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text=f"⚠️ На «{market['name']}» некому передать пересменку за {report_date} — сотрудник по графику недоступен, управляющего нет.",
        )


async def _handle_reconciliation_failure(message, state: dict, error: str, keys: list[str]) -> None:
    save_report_data(state["report_id"], state["answers"])
    await message.reply_text(f"{error}\n\nКакое из полей поправить?", reply_markup=_fix_field_keyboard(keys))


async def on_handover_fix_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":", 1)[1]
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state:
        await query.answer("Сессия неактуальна", show_alert=True)
        return
    state["awaiting_fix_key"] = key
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"Новое значение — {_FIELD_LABELS[key]}:\n{_QUESTION_BY_KEY[key]['prompt']}")


async def _finish_collection(bot: Bot, message, user_id: str) -> None:
    state = _pending.pop(user_id)
    report_id = state["report_id"]
    report = get_report(report_id)
    market = get_market(report["market_id"])
    set_report_status(report_id, "dispatched")
    sent = await _dispatch_handover_now(bot, market, report["report_date"], report["data"])
    if sent:
        await message.reply_text("🎉 Спасибо, пересменка готова! Уже ушла в чат команды.")
    else:
        await message.reply_text(
            "🎉 Спасибо, пересменка готова! Правда, чат команды точки ещё не привязан — попроси управляющего "
            "настроить его через /register_report_chat."
        )


async def on_handover_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает ответы на вопросы пересменки. Возвращает True, если
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
        await update.effective_message.reply_text("✅ Обновлено.")
        if state["step_index"] >= len(_QUESTIONS):
            await _finish_collection(context.bot, update.effective_message, user_id)
        else:
            await _ask_current_question(update.effective_message, user_id)
        return True

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
    recon = _validate_reconciliation(state["answers"])
    if recon:
        await _handle_reconciliation_failure(update.effective_message, state, recon[0], recon[1])
        return True

    state["step_index"] += 1
    save_report_data(state["report_id"], state["answers"])
    if state["step_index"] >= len(_QUESTIONS):
        await _finish_collection(context.bot, update.effective_message, user_id)
    else:
        await _ask_current_question(update.effective_message, user_id)
    return True


_STATUS_LABELS = {"dispatched": "уже отправлена в чат команды"}


def _manual_market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"horep_manualmarket:{m['id']}")] for m in markets])


async def _start_manual_report(message, user_id: int, market: dict) -> None:
    date_iso = tz_today().isoformat()
    existing = get_report_by_date(market["id"], date_iso)
    if existing and existing["status"] != "collecting":
        label = _STATUS_LABELS.get(existing["status"], "уже обработана")
        await message.reply_text(f"Пересменка по «{market['name']}» за сегодня {label}.")
        return
    await _begin_collection(message, user_id, market, date_iso)


async def on_handover_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/handover_report — принудительно начать (или продолжить) сегодняшнюю
    пересменку, не дожидаясь автоматического опроса в настроенное время.
    Доступно любому активному сотруднику точки с блоком «Пересменка» и
    владельцу."""
    user = update.effective_user
    if not has_handover_access(user.id):
        await update.effective_message.reply_text("Эта команда доступна только сотрудникам с выданным блоком «Пересменка».")
        return

    markets = list_markets() if is_owner(user.id) else get_markets_for_manager(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _start_manual_report(update.effective_message, user.id, markets[0])
        return

    await update.effective_message.reply_text("По какому рынку вносим пересменку?", reply_markup=_manual_market_pick_keyboard(markets))


async def on_handover_report_manual_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _start_manual_report(query.message, query.from_user.id, market)


def _reset_available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _reset_market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"horep_resetmarket:{m['id']}")] for m in markets])


def _reset_confirm_keyboard(market_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Да, сбросить", callback_data=f"horep_resetconfirm:{market_id}"),
                InlineKeyboardButton("Отмена", callback_data="horep_resetcancel"),
            ]
        ]
    )


async def on_reset_handover_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reset_handover_report — владелец или Управляющий удаляет
    сегодняшнюю пересменку целиком, чтобы прогнать /handover_report заново."""
    user = update.effective_user
    if not is_handover_editor(user.id):
        await update.effective_message.reply_text(
            "Сбрасывать пересменку может только владелец или Управляющий с выданным блоком «Пересменка»."
        )
        return
    markets = _reset_available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return
    await update.effective_message.reply_text(
        "По какому рынку сбросить сегодняшнюю пересменку?", reply_markup=_reset_market_pick_keyboard(markets)
    )


async def on_reset_handover_report_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_handover_editor(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market or market_id not in {m["id"] for m in _reset_available_markets(query.from_user.id)}:
        await query.answer("Рынок не найден", show_alert=True)
        return
    date_iso = tz_today().isoformat()
    if not get_report_by_date(market_id, date_iso):
        await query.answer()
        await query.edit_message_text(f"На «{market['name']}» пересменки за сегодня и не было — сбрасывать нечего.")
        return
    await query.answer()
    await query.edit_message_text(
        f"⚠️ Удалить сегодняшнюю пересменку по «{market['name']}» целиком? Действие необратимо.",
        reply_markup=_reset_confirm_keyboard(market_id),
    )


async def on_reset_handover_report_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_handover_editor(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market or market_id not in {m["id"] for m in _reset_available_markets(query.from_user.id)}:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer("Сбрасываю…")
    delete_report(market_id, tz_today().isoformat())
    await query.edit_message_text(f"✅ Пересменка по «{market['name']}» за сегодня сброшена. Запустите заново через /handover_report.")


async def on_reset_handover_report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_handover_editor(query.from_user.id):
        await query.answer()
        return
    await query.answer()
    await query.edit_message_text("Отменено.")


async def send_handover_kickoffs(bot: Bot) -> None:
    """Каждая точка сама задаёт своё время запроса пересменки (см.
    market.handover_time, /set_handover_report) — эта функция вызывается
    раз в минуту (см. main.py) и для каждого рынка с записью в графике
    пересменок на сегодня сверяет, не наступило ли именно СЕЙЧАС его
    собственное время; если да — предлагает назначенному сотруднику
    заполнить пересменку. Пропускает рынки, у которых Управляющему сейчас
    отключён блок «Пересменка»."""
    date_iso = tz_today().isoformat()
    now_hhmm = tz_now().strftime("%H:%M")
    for market in list_markets_with_handover(date_iso):
        if not market_handover_enabled(market["id"]):
            continue
        if get_effective_handover_time(market) != now_hhmm:
            continue
        await _offer_handover_or_absence(bot, market["scheduled_manager_id"], market, date_iso)
