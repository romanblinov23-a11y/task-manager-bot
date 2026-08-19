from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.competitors import code_taken, create_competitor, get_own_competitor, next_code
from monitoring.constants import COMPETITOR_FORMATS
from monitoring.factors import save_factors
from monitoring.managers import get_markets_for_manager, is_active_manager, is_owner
from monitoring.markets import get_market, list_markets
from monitoring.readings import record_reading

# user_id (str) -> состояние диалога добавления конкурента
_pending: dict[str, dict] = {}

_FACTOR_STEPS = [
    (
        "factors_product",
        "product",
        "Продукт",
        "смесь/зерно, молоко, оборудование, эспрессо-смесь, Decaf, STM, кофейная линейка, "
        "авторские напитки, еда, средний чек",
    ),
    (
        "factors_atmosphere",
        "atmosphere",
        "Атмосфера/интерьер",
        "посадочные места, мебель, музыка, свет, декор, фасад, указатели, санузел, чистота",
    ),
    (
        "factors_service",
        "service",
        "Персонализация/сервис",
        "встреча и прощание с гостем, коммуникация при заказе, опрятность, форма, слаженность "
        "команды, работа с отзывами, рейтинг",
    ),
    (
        "factors_brand",
        "brand_strength",
        "Сила бренда",
        "запросы в Яндекс.Wordstat за месяц, узнаваемость, соцсети",
    ),
    (
        "factors_labor",
        "labor_market",
        "Рынок труда",
        "ставка в час, премии, обучение, смены, питание, штрафы, форма за свой счёт, условия труда, карьерный рост",
    ),
]


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


async def _start_competitor_flow(message, user_id: str, market: dict, *, is_own: bool = False) -> None:
    if not is_own and not get_own_competitor(market["id"]):
        await message.reply_text(
            f"ℹ️ На рынке «{market['name']}» ещё не добавлена сама точка Surf «{market['our_point_name']}» — "
            "без неё нельзя посчитать нашу долю рынка. Можно завести её сейчас или продолжить добавлять "
            "конкурентов — в конце я ещё раз предложу добавить точку Surf, если вы её пропустите."
        )

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


async def on_add_competitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not (is_owner(user.id) or is_active_manager(user.id)):
        await update.effective_message.reply_text(
            "Эта команда доступна только подтверждённым владельцем менеджерам."
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


async def _finish_and_maybe_offer_own(message, user_id: str) -> None:
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

    if not state["is_own"] and not get_own_competitor(state["market_id"]):
        market = get_market(state["market_id"])
        _pending[user_id] = {"step": "own_offer", "market_id": state["market_id"], "market_name": state["market_name"]}
        await message.reply_text(
            f"Кстати, для рынка «{state['market_name']}» ещё не заведена сама точка Surf «{market['our_point_name']}» — "
            "без неё нельзя посчитать нашу долю рынка. Добавить её сейчас?",
            reply_markup=_yesno_keyboard("addc_own"),
        )


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
        state["step"] = _FACTOR_STEPS[0][0]
        await query.edit_message_text("Заполняем факторы формирования.")
        _, _, title, hint = _FACTOR_STEPS[0]
        await query.message.reply_text(f"{title}: опишите — {hint}.")
    else:
        await query.edit_message_text("Факторы — позже.")
        await _finish_and_maybe_offer_own(query.message, user_id)


async def on_add_competitor_own_offer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    state = _pending.get(user_id)
    if not state or state.get("step") != "own_offer":
        await query.answer()
        return
    choice = query.data.split(":", 1)[1]
    await query.answer()
    if choice == "no":
        del _pending[user_id]
        await query.edit_message_text("Хорошо, добавите точку Surf позже через /add_competitor.")
        return

    market = get_market(state["market_id"])
    await query.edit_message_text("Добавляем точку Surf.")
    await _start_competitor_flow(query.message, user_id, market, is_own=True)


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

    for i, (step_name, field, title, hint) in enumerate(_FACTOR_STEPS):
        if step != step_name:
            continue
        state["factors"][field] = text
        if i + 1 < len(_FACTOR_STEPS):
            next_step_name, _, next_title, next_hint = _FACTOR_STEPS[i + 1]
            state["step"] = next_step_name
            await update.effective_message.reply_text(f"{next_title}: опишите — {next_hint}.")
        else:
            await update.effective_message.reply_text("Факторы записаны.")
            await _finish_and_maybe_offer_own(update.effective_message, user_id)
        return True

    return False
