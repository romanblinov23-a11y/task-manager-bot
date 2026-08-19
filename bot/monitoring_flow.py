from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.factor_wizard import field_keyboard, field_prompt_text
from config.timeutil import parse_date, today
from monitoring.calculations import compute_market_capacity, compute_share, is_sudden_change
from monitoring.competitors import get_competitor, list_competitors
from monitoring.constants import MONITORING_CYCLE_DAYS, OBSERVATION_CATEGORIES
from monitoring.factor_schema import (
    FACTOR_BLOCKS,
    advance_factor_cursor,
    current_field,
    factor_progress_done,
    factor_state_init,
    is_first_field_of_block,
    parse_field_value,
    record_answer,
    serialized_blocks,
)
from monitoring.factors import get_latest_factors, save_factors
from monitoring.managers import get_managers_for_market, get_markets_for_manager, is_active_manager, is_owner
from monitoring.markets import get_market, list_markets
from monitoring.observations import create_observation
from monitoring.readings import get_competitors_pending_this_cycle, get_last_market_cycle_date, get_latest_reading, record_reading
from monitoring.schedule import list_markets_scheduled_for_weekday

# user_id (str) -> состояние диалога /monitoring
_pending: dict[str, dict] = {}


def _available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"monf_market:{m['id']}")] for m in markets])


def _skip_keyboard(competitor_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить", callback_data=f"monf_skip:{competitor_id}")]])


def _date_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Сегодня", callback_data="monf_date:today"), InlineKeyboardButton("Другая дата", callback_data="monf_date:custom")]]
    )


def _yesno_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Да", callback_data=f"{prefix}:yes"), InlineKeyboardButton("Нет", callback_data=f"{prefix}:no")]])


def _category_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for i in range(0, len(OBSERVATION_CATEGORIES), 2):
        row = [InlineKeyboardButton(OBSERVATION_CATEGORIES[i], callback_data=f"monf_cat:{i}")]
        if i + 1 < len(OBSERVATION_CATEGORIES):
            row.append(InlineKeyboardButton(OBSERVATION_CATEGORIES[i + 1], callback_data=f"monf_cat:{i + 1}"))
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def _factor_block_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for i in range(0, len(FACTOR_BLOCKS), 2):
        row = [InlineKeyboardButton(FACTOR_BLOCKS[i][1], callback_data=f"monf_fblock:{i}")]
        if i + 1 < len(FACTOR_BLOCKS):
            row.append(InlineKeyboardButton(FACTOR_BLOCKS[i + 1][1], callback_data=f"monf_fblock:{i + 1}"))
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def _competitor_label(c: dict) -> str:
    return f"{c['code']} — {c['name']}{' (наша точка)' if c['is_own'] else ''}"


async def _ask_reading(message, state: dict) -> None:
    competitor = state["current"]
    await message.reply_text(
        f"{_competitor_label(competitor)}\n\n"
        "Среднее число чеков в день? (напоминание: (последний номер чека − предыдущий) ÷ "
        "число дней между визитами)",
        reply_markup=_skip_keyboard(competitor["id"]),
    )


async def _advance_to_next(message, user_id: str) -> None:
    state = _pending[user_id]
    if not state["queue"]:
        await _finish_cycle(message, user_id)
        return
    state["current"] = state["queue"].pop(0)
    state["step"] = "reading"
    await _ask_reading(message, state)


async def _finish_cycle(message, user_id: str) -> None:
    state = _pending.pop(user_id)
    market_id = state["market_id"]
    competitors = list_competitors(market_id)

    readings: dict[int, float] = {}
    stale_ids: set[int] = set()
    for c in competitors:
        latest = get_latest_reading(c["id"])
        if not latest:
            continue
        readings[c["id"]] = latest["avg_checks_per_day"]
        cutoff = today().fromordinal(today().toordinal() - MONITORING_CYCLE_DAYS).isoformat()
        if latest["reading_at"] < cutoff:
            stale_ids.add(c["id"])

    capacity = compute_market_capacity(readings)
    lines = [f"📊 Итоги мониторинга по рынку «{state['market_name']}»:\n"]
    for c in sorted(competitors, key=lambda x: (not x["is_own"], x["code"])):
        if c["id"] not in readings:
            lines.append(f"{_competitor_label(c)} — нет данных")
            continue
        value = readings[c["id"]]
        share = compute_share(value, capacity)
        flag = " ⚠️ данные устарели, обновите на следующем мониторинге" if c["id"] in stale_ids else ""
        lines.append(f"{_competitor_label(c)}: {value:g} чек/день, доля {share:.1f}%{flag}")
    lines.append(f"\nЁмкость рынка: {capacity:g} чек/день")

    if state["skipped"]:
        skipped_labels = ", ".join(state["skipped"])
        lines.append(f"\nПропущено в этот раз (спросим на следующем /monitoring): {skipped_labels}")

    await message.reply_text("\n".join(lines))


async def _start_flow_for_market(message, user_id: str, market: dict) -> None:
    pending_competitors = get_competitors_pending_this_cycle(market["id"])
    all_competitors = list_competitors(market["id"])

    if not all_competitors:
        await message.reply_text(
            f"На рынке «{market['name']}» ещё нет ни одного конкурента. Сначала добавьте их через /add_competitor."
        )
        return

    if not pending_competitors:
        last_date = get_last_market_cycle_date(market["id"])
        next_date = "—"
        if last_date:
            from datetime import date as _date

            next_date = (_date.fromisoformat(last_date).fromordinal(_date.fromisoformat(last_date).toordinal() + MONITORING_CYCLE_DAYS)).isoformat()
        await message.reply_text(
            f"Мониторинг по рынку «{market['name']}» уже проведён на этой неделе. "
            f"Следующая проверка возможна с {next_date}."
        )
        return

    _pending[user_id] = {
        "market_id": market["id"],
        "market_name": market["name"],
        "queue": [c["id"] for c in pending_competitors],
        "skipped": [],
    }
    await message.reply_text(f"Начинаем мониторинг рынка «{market['name']}» — точек к проверке: {len(pending_competitors)}.")
    await _advance_to_next(message, user_id)


async def on_monitoring_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not (is_owner(user.id) or is_active_manager(user.id)):
        await update.effective_message.reply_text("Эта команда доступна только подтверждённым владельцем менеджерам.")
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _start_flow_for_market(update.effective_message, str(user.id), markets[0])
        return

    await update.effective_message.reply_text("По какому рынку проводим мониторинг?", reply_markup=_market_pick_keyboard(markets))


async def on_monitoring_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _start_flow_for_market(query.message, user_id, market)


async def on_monitoring_start_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «Начать мониторинг» из напоминания (§6)."""
    query = update.callback_query
    user = query.from_user
    market_id = int(query.data.split(":", 1)[1])
    if not (is_owner(user.id) or is_active_manager(user.id)):
        await query.answer("Доступ ещё не подтверждён владельцем", show_alert=True)
        return
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await _start_flow_for_market(query.message, str(user.id), market)


async def on_monitoring_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or not state.get("current") or state["current"]["id"] != int(query.data.split(":", 1)[1]):
        await query.answer()
        return
    await query.answer("Пропущено")
    state["skipped"].append(_competitor_label(state["current"]))
    await query.edit_message_reply_markup(reply_markup=None)
    await _advance_to_next(query.message, user_id)


async def on_monitoring_date_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "date_choice":
        await query.answer()
        return
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "today":
        await _save_reading_and_continue(query.message, user_id, today().isoformat())
    else:
        state["step"] = "date_value"
        await query.edit_message_text("Введите дату снятия (например, 15.08.2026):")


async def _save_reading_and_continue(message, user_id: str, reading_at: str) -> None:
    state = _pending[user_id]
    competitor = state["current"]
    record_reading(competitor["id"], state["reading_value"], created_by=int(user_id), reading_at=reading_at)
    state["step"] = "obs_choice"
    await message.reply_text("Заметили видимые изменения по этой точке?", reply_markup=_yesno_keyboard("monf_obs"))


async def on_monitoring_obs_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "obs_choice":
        await query.answer()
        return
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "no":
        await query.edit_message_text("Хорошо.")
        await _advance_to_next(query.message, user_id)
        return
    state["step"] = "obs_category"
    await query.edit_message_text("Что изменилось?", reply_markup=_category_keyboard())


async def on_monitoring_category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "obs_category":
        await query.answer()
        return
    index = int(query.data.split(":", 1)[1])
    if index < 0 or index >= len(OBSERVATION_CATEGORIES):
        await query.answer()
        return
    state["obs_category"] = OBSERVATION_CATEGORIES[index]
    state["step"] = "obs_comment"
    await query.answer()
    await query.edit_message_text(f"Категория: {state['obs_category']}")
    await query.message.reply_text("Опишите изменение (свободный текст):")


async def on_monitoring_factors_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "factors_prompt":
        await query.answer()
        return
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "no":
        await query.edit_message_text("Хорошо.")
        await _advance_to_next(query.message, user_id)
        return
    state["step"] = "factors_block_choice"
    await query.edit_message_text("Какой блок изменился?", reply_markup=_factor_block_keyboard())


async def on_monitoring_factor_block_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "factors_block_choice":
        await query.answer()
        return
    index = int(query.data.split(":", 1)[1])
    if index < 0 or index >= len(FACTOR_BLOCKS):
        await query.answer()
        return
    block_key, title, _ = FACTOR_BLOCKS[index]
    state["step"] = "factors_wizard"
    state["fstate"] = factor_state_init([block_key])
    await query.answer()
    await query.edit_message_text(f"Блок: {title}")
    await _prompt_current_factor_field(query.message, state)


async def _prompt_current_factor_field(message, state: dict) -> None:
    fstate = state["fstate"]
    _, block_title, field = current_field(fstate)
    await message.reply_text(
        field_prompt_text(block_title, field, is_first_field_of_block(fstate)),
        reply_markup=field_keyboard(field, "monf_ffield"),
    )


async def _advance_factor_wizard(message, user_id: str) -> None:
    state = _pending[user_id]
    fstate = state["fstate"]
    advance_factor_cursor(fstate)
    if factor_progress_done(fstate):
        competitor = state["current"]
        latest = get_latest_factors(competitor["id"]) or {}
        merged = {key: latest.get(key, "") for key, _, _ in FACTOR_BLOCKS}
        merged.update(serialized_blocks(fstate))
        save_factors(competitor["id"], **merged)
        del state["fstate"]
        await message.reply_text("Записал.")
        await _advance_to_next(message, user_id)
        return
    await _prompt_current_factor_field(message, state)


async def on_monitoring_factor_field_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "factors_wizard":
        await query.answer()
        return
    block_key, _, field = current_field(state["fstate"])
    field_key, label, kind, options, _ = field
    if kind != "buttons":
        await query.answer()
        return
    index = int(query.data.split(":", 1)[1])
    if index < 0 or index >= len(options):
        await query.answer()
        return
    value = options[index]
    await query.answer()
    await query.edit_message_text(f"{label}: {value}")
    record_answer(state["fstate"], block_key, field_key, value)
    await _advance_factor_wizard(query.message, user_id)


async def on_monitoring_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текстовые ответы диалога /monitoring (показатель, дата,
    комментарий к наблюдению, текст обновлённого блока факторов)."""
    user_id = str(update.effective_user.id)
    state = _pending.get(user_id)
    if not state:
        return False

    step = state.get("step")
    text = update.effective_message.text.strip() if update.effective_message.text else ""

    if step == "reading":
        try:
            value = float(text.replace(",", "."))
            if value <= 0:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Нужно положительное число. Попробуйте ещё раз:")
            return True

        competitor = state["current"]
        previous = get_latest_reading(competitor["id"])
        warning = ""
        if previous and is_sudden_change(value, previous["avg_checks_per_day"]):
            warning = f"⚠️ Похоже на резкое изменение (было {previous['avg_checks_per_day']:g}, стало {value:g}). Точно? Записал.\n\n"

        state["reading_value"] = value
        state["step"] = "date_choice"
        await update.effective_message.reply_text(f"{warning}Дата снятия?", reply_markup=_date_keyboard())
        return True

    if step == "date_value":
        parsed = parse_date(text)
        if not parsed:
            await update.effective_message.reply_text("Не понял дату. Попробуйте, например, 15.08.2026:")
            return True
        await _save_reading_and_continue(update.effective_message, user_id, parsed)
        return True

    if step == "obs_comment":
        create_observation(
            competitor_id=state["current"]["id"],
            market_id=state["market_id"],
            category=state["obs_category"],
            text=text,
            created_by=int(user_id),
        )
        state["step"] = "factors_prompt"
        await update.effective_message.reply_text(
            "Обновить блок факторов у этой точки?", reply_markup=_yesno_keyboard("monf_factors")
        )
        return True

    if step == "factors_wizard":
        block_key, _, field = current_field(state["fstate"])
        field_key, label, kind, options, _ = field
        if kind == "buttons":
            await update.effective_message.reply_text("Пожалуйста, выберите вариант кнопкой выше.")
            return True
        try:
            value = parse_field_value(field, text)
        except ValueError:
            await update.effective_message.reply_text("Не понял значение, попробуйте ещё раз:")
            return True
        record_answer(state["fstate"], block_key, field_key, value)
        await _advance_factor_wizard(update.effective_message, user_id)
        return True

    return False


async def send_monitoring_reminders(bot: Bot) -> None:
    """Ежедневный job (§6): по рынкам, у которых сегодня день мониторинга,
    шлёт менеджерам задание со списком конкурентов и кнопкой запуска."""
    weekday = today().isoweekday()
    for market_id in list_markets_scheduled_for_weekday(weekday):
        market = get_market(market_id)
        if not market:
            continue
        competitors = list_competitors(market_id)
        if not competitors:
            continue
        listing = "\n".join(f"— {_competitor_label(c)}" for c in competitors)
        text = (
            "📋 Задание: выполнить мониторинг конкурентов. По каждой точке посчитайте среднее число "
            f"чеков в день и пришлите.\n\nРынок «{market['name']}»:\n{listing}"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Начать мониторинг", callback_data=f"monf_go:{market_id}")]])
        for manager in get_managers_for_market(market_id):
            if manager["status"] != "active":
                continue
            try:
                await bot.send_message(chat_id=manager["telegram_user_id"], text=text, reply_markup=keyboard)
            except Exception:
                pass
