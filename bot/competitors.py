from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.competitors import (
    close_competitor,
    code_taken,
    create_competitor,
    get_competitor,
    get_own_competitor,
    list_competitors,
    next_code,
    reopen_competitor,
    set_own_competitor,
)
from bot.factor_wizard import field_keyboard, field_prompt_text
from monitoring.constants import COMPETITOR_FORMATS
from monitoring.factor_schema import (
    advance_factor_cursor,
    current_field,
    factor_progress_done,
    factor_state_init,
    is_first_field_of_block,
    parse_field_value,
    record_answer,
    serialized_blocks,
)
from monitoring.factors import save_factors
from monitoring.managers import get_markets_for_manager, is_market_editor, is_owner
from monitoring.markets import get_market, list_markets
from monitoring.readings import record_reading

# user_id (str) -> состояние диалога добавления конкурента
_pending: dict[str, dict] = {}


def _available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"addc_market:{m['id']}")] for m in markets])


def _format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(f, callback_data=f"addc_format:{i}")] for i, f in enumerate(COMPETITOR_FORMATS)])


def _yesno_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Да, сейчас", callback_data=f"{prefix}:now"), InlineKeyboardButton("Позже", callback_data=f"{prefix}:skip")]]
    )


def _own_first_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🏠 Сначала точку Surf", callback_data="addc_ownfirst:surf"),
                InlineKeyboardButton("🏪 Сначала конкурента", callback_data="addc_ownfirst:competitor"),
            ]
        ]
    )


def _own_name_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Да, это она", callback_data="addc_ownnameconfirm:yes"), InlineKeyboardButton("✏️ Ввести заново", callback_data="addc_ownnameconfirm:retry")]]
    )


async def _start_competitor_flow(message, user_id: str, market: dict, *, is_own: bool = False) -> None:
    if not is_own and not get_own_competitor(market["id"]):
        _pending[user_id] = {"step": "own_first_choice", "market_id": market["id"], "market_name": market["name"]}
        await message.reply_text(
            f"ℹ️ На рынке «{market['name']}» ещё не добавлена сама точка Surf «{market['our_point_name']}» — "
            "без неё нельзя посчитать нашу долю рынка. Что добавляем сначала?",
            reply_markup=_own_first_keyboard(),
        )
        return
    await _start_code_step(message, user_id, market, is_own=is_own)


async def _start_code_step(message, user_id: str, market: dict, *, is_own: bool) -> None:
    suggested = next_code(market["id"])
    _pending[user_id] = {
        "step": "code",
        "market_id": market["id"],
        "market_name": market["name"],
        "is_own": is_own,
        "suggested_code": suggested,
        "factors": {},
    }
    prefix = f"Добавляем саму точку Surf «{market['our_point_name']}» как объект мониторинга.\n\n" if is_own else ""
    await message.reply_text(
        f"{prefix}Код конкурента (например «{suggested}») — можно использовать предложенный или ввести свой:"
    )


async def on_add_competitor_own_first_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "own_first_choice":
        await query.answer()
        return
    choice = query.data.split(":", 1)[1]
    market = get_market(state["market_id"])
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    if choice == "surf":
        await query.edit_message_text("Добавляем точку Surf.")
        await _start_code_step(query.message, user_id, market, is_own=True)
    else:
        await query.edit_message_text("Добавляем конкурента (точку Surf можно будет завести следующим /add_competitor).")
        await _start_code_step(query.message, user_id, market, is_own=False)


async def on_add_competitor_own_name_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "own_name_confirm":
        await query.answer()
        return
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "retry":
        state["step"] = "name"
        await query.edit_message_text("Хорошо, введите название точки Surf ещё раз:")
        return
    state["step"] = "address"
    await query.edit_message_text(f"Точка Surf: «{state['name']}».")
    await query.message.reply_text("Адрес:")


async def on_add_competitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_market_editor(user.id):
        await update.effective_message.reply_text(
            "Список конкурентов может менять только владелец или Управляющий."
        )
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _start_competitor_flow(update.effective_message, str(user.id), markets[0])
        return

    await update.effective_message.reply_text(
        "По какому рынку добавляем конкурента?", reply_markup=_market_pick_keyboard(markets)
    )


async def on_add_competitor_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _start_competitor_flow(query.message, user_id, market)


async def _finish_competitor(message, user_id: str) -> None:
    state = _pending.pop(user_id)
    competitor = create_competitor(
        market_id=state["market_id"],
        code=state["code"],
        name=state["name"],
        address=state["address"],
        format_=state["format"],
        is_own=state["is_own"],
    )

    if state.get("reading_value") is not None:
        record_reading(competitor["id"], state["reading_value"], created_by=int(user_id))
    if state["factors"]:
        save_factors(competitor["id"], **state["factors"])

    kind = "Точка Surf" if state["is_own"] else "Конкурент"
    await message.reply_text(f"✅ {kind} «{state['name']}» ({state['code']}) добавлен(а) на рынке «{state['market_name']}».")


async def on_add_competitor_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "format":
        await query.answer()
        return
    index = int(query.data.split(":", 1)[1])
    if index < 0 or index >= len(COMPETITOR_FORMATS):
        await query.answer()
        return
    state["format"] = COMPETITOR_FORMATS[index]
    state["step"] = "reading_choice"
    await query.answer()
    await query.edit_message_text(f"Формат: {state['format']}")
    await query.message.reply_text(
        "Указать среднее число чеков в день сейчас? (напоминание: (последний номер чека − предыдущий) ÷ "
        "число дней между визитами)",
        reply_markup=_yesno_keyboard("addc_reading"),
    )


async def on_add_competitor_reading_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "reading_choice":
        await query.answer()
        return
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "now":
        state["step"] = "reading_value"
        await query.edit_message_text("Укажу показатель.")
        await query.message.reply_text("Среднее число чеков в день (число):")
    else:
        state["reading_value"] = None
        state["step"] = "factors_choice"
        await query.edit_message_text("Показатель — позже, на ближайшем мониторинге.")
        await query.message.reply_text(
            "Заполнить факторы формирования сейчас или позже?", reply_markup=_yesno_keyboard("addc_factors")
        )


async def on_add_competitor_factors_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "factors_choice":
        await query.answer()
        return
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "now":
        state["step"] = "factors_wizard"
        state["fstate"] = factor_state_init()
        await query.edit_message_text("Заполняем факторы формирования.")
        await _prompt_current_factor_field(query.message, state)
    else:
        await query.edit_message_text("Факторы — позже.")
        await _finish_competitor(query.message, user_id)


async def _prompt_current_factor_field(message, state: dict) -> None:
    fstate = state["fstate"]
    _, block_title, field = current_field(fstate)
    await message.reply_text(
        field_prompt_text(block_title, field, is_first_field_of_block(fstate)),
        reply_markup=field_keyboard(field, "addc_factor"),
    )


async def _advance_factor_wizard(message, user_id: str) -> None:
    state = _pending[user_id]
    fstate = state["fstate"]
    advance_factor_cursor(fstate)
    if factor_progress_done(fstate):
        state["factors"] = serialized_blocks(fstate)
        del state["fstate"]
        await message.reply_text("Факторы записаны.")
        await _finish_competitor(message, user_id)
        return
    await _prompt_current_factor_field(message, state)


async def on_add_competitor_factor_field_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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


async def on_add_competitor_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текстовые ответы диалога /add_competitor (код, название,
    адрес, показатель, блоки факторов). Возвращает True, если сообщение
    обработано — по конвенции остальных claim-хендлеров в on_private_text."""
    user_id = str(update.effective_user.id)
    state = _pending.get(user_id)
    if not state:
        return False

    step = state.get("step")
    text = update.effective_message.text.strip() if update.effective_message.text else ""

    if step == "code":
        code = text or state["suggested_code"]
        if code_taken(state["market_id"], code):
            await update.effective_message.reply_text(f"Код «{code}» уже занят на этом рынке. Введите другой:")
            return True
        state["code"] = code
        state["step"] = "name"
        await update.effective_message.reply_text("Название:")
        return True

    if step == "name":
        if not text:
            await update.effective_message.reply_text("Название не может быть пустым. Введите ещё раз:")
            return True
        state["name"] = text
        if state["is_own"]:
            market = get_market(state["market_id"])
            expected = market["our_point_name"] if market else ""
            state["step"] = "own_name_confirm"
            hint = ""
            if expected and expected.strip().lower() != text.strip().lower():
                hint = f"\n⚠️ На рынке ожидалась точка «{expected}», а введено «{text}» — точно она же?"
            await update.effective_message.reply_text(
                f"Подтвердите: это НАША точка Surf — «{text}».{hint}",
                reply_markup=_own_name_confirm_keyboard(),
            )
            return True
        state["step"] = "address"
        await update.effective_message.reply_text("Адрес:")
        return True

    if step == "address":
        state["address"] = text
        state["step"] = "format"
        await update.effective_message.reply_text("Формат:", reply_markup=_format_keyboard())
        return True

    if step == "reading_value":
        try:
            value = float(text.replace(",", "."))
            if value <= 0:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Нужно положительное число. Попробуйте ещё раз:")
            return True
        state["reading_value"] = value
        state["step"] = "factors_choice"
        await update.effective_message.reply_text(
            "Заполнить факторы формирования сейчас или позже?", reply_markup=_yesno_keyboard("addc_factors")
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


def _close_market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"cc_market:{m['id']}")] for m in markets])


def _close_competitor_pick_keyboard(competitors: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for c in competitors:
        icon = "🔴" if c["status"] == "closed" else "🟢"
        label = f"{icon} {c['code']} — {c['name']}" + (" (закрыт)" if c["status"] == "closed" else "")
        buttons.append([InlineKeyboardButton(label, callback_data=f"cc_pick:{c['id']}")])
    return InlineKeyboardMarkup(buttons)


async def _show_competitor_list(message, market: dict) -> None:
    competitors = list_competitors(market["id"], include_closed=True)
    if not competitors:
        await message.reply_text(f"На рынке «{market['name']}» ещё нет конкурентов.")
        return
    await message.reply_text(
        f"Рынок «{market['name']}» — кого закрыть или открыть заново?",
        reply_markup=_close_competitor_pick_keyboard(competitors),
    )


async def on_close_competitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_market_editor(user.id):
        await update.effective_message.reply_text("Список конкурентов может менять только владелец или Управляющий.")
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _show_competitor_list(update.effective_message, markets[0])
        return

    await update.effective_message.reply_text(
        "По какому рынку?", reply_markup=_close_market_pick_keyboard(markets)
    )


async def on_close_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _show_competitor_list(query.message, market)


async def on_close_competitor_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    competitor_id = int(query.data.split(":", 1)[1])
    competitor = get_competitor(competitor_id)
    if not competitor:
        await query.answer("Не найден", show_alert=True)
        return
    await query.answer()
    if competitor["status"] == "closed":
        await query.edit_message_text(
            f"Открыть заново «{competitor['name']}» ({competitor['code']})?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("♻️ Да, открыть", callback_data=f"cc_confirm:reopen:{competitor_id}"),
                        InlineKeyboardButton("Отмена", callback_data=f"cc_confirm:cancel:{competitor_id}"),
                    ]
                ]
            ),
        )
    else:
        await query.edit_message_text(
            f"Закрыть «{competitor['name']}» ({competitor['code']})? История снятий и наблюдений сохранится, "
            "но точка перестанет участвовать в еженедельном мониторинге и расчёте ёмкости рынка.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔴 Да, закрыть", callback_data=f"cc_confirm:close:{competitor_id}"),
                        InlineKeyboardButton("Отмена", callback_data=f"cc_confirm:cancel:{competitor_id}"),
                    ]
                ]
            ),
        )


async def on_close_competitor_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, action, competitor_id_str = query.data.split(":", 2)
    competitor_id = int(competitor_id_str)
    competitor = get_competitor(competitor_id)
    if not competitor:
        await query.answer("Не найден", show_alert=True)
        return
    await query.answer()
    if action == "close":
        close_competitor(competitor_id)
        await query.edit_message_text(f"🔴 «{competitor['name']}» ({competitor['code']}) закрыт(а).")
    elif action == "reopen":
        reopen_competitor(competitor_id)
        await query.edit_message_text(f"🟢 «{competitor['name']}» ({competitor['code']}) снова активен(на).")
    else:
        await query.edit_message_text("Отменено.")


def _own_point_market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"ownpt_market:{m['id']}")] for m in markets])


def _own_point_pick_keyboard(competitors: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for c in competitors:
        icon = "⭐" if c["is_own"] else "▫️"
        label = f"{icon} {c['code']} — {c['name']}" + (" (закрыт)" if c["status"] == "closed" else "")
        buttons.append([InlineKeyboardButton(label, callback_data=f"ownpt_pick:{c['id']}")])
    return InlineKeyboardMarkup(buttons)


async def _show_own_point_picker(message, market: dict) -> None:
    competitors = list_competitors(market["id"], include_closed=True)
    if not competitors:
        await message.reply_text(f"На рынке «{market['name']}» ещё нет ни одной точки.")
        return
    await message.reply_text(
        f"Рынок «{market['name']}». Какая точка на самом деле наша (Surf)? ⭐ — отмечена как наша сейчас.",
        reply_markup=_own_point_pick_keyboard(competitors),
    )


async def on_set_own_point_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_own_point — исправить, если флаг «наша точка» случайно достался
    не той точке во время /add_competitor (не было способа поправить это,
    кроме прямого доступа к базе)."""
    user = update.effective_user
    if not is_market_editor(user.id):
        await update.effective_message.reply_text("Список конкурентов может менять только владелец или Управляющий.")
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _show_own_point_picker(update.effective_message, markets[0])
        return

    await update.effective_message.reply_text(
        "По какому рынку?", reply_markup=_own_point_market_pick_keyboard(markets)
    )


async def on_set_own_point_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _show_own_point_picker(query.message, market)


async def on_set_own_point_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    competitor_id = int(query.data.split(":", 1)[1])
    competitor = get_competitor(competitor_id)
    if not competitor:
        await query.answer("Не найден", show_alert=True)
        return
    if competitor["is_own"]:
        await query.answer("Уже отмечена как наша точка", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        f"Сделать «{competitor['name']}» ({competitor['code']}) нашей точкой Surf? Текущая точка (если была) "
        "потеряет этот статус — история снятий и наблюдений всех точек не тронется.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Да, это наша точка", callback_data=f"ownpt_confirm:{competitor_id}"),
                    InlineKeyboardButton("Отмена", callback_data="ownpt_cancel"),
                ]
            ]
        ),
    )


async def on_set_own_point_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    competitor_id = int(query.data.split(":", 1)[1])
    competitor = get_competitor(competitor_id)
    if not competitor:
        await query.answer("Не найден", show_alert=True)
        return
    await query.answer("Готово")
    set_own_competitor(competitor["market_id"], competitor_id)
    await query.edit_message_text(f"⭐ «{competitor['name']}» ({competitor['code']}) теперь отмечена как наша точка Surf.")


async def on_set_own_point_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Отменено.")
