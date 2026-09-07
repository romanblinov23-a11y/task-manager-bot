from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.consent_flow import send_consent_request
from config.settings import ROMAN_TELEGRAM_ID
from monitoring.constants import TRAINEE_ONBOARDING_STAGES
from monitoring.managers import (
    get_manager,
    get_market_supervisor,
    get_markets_for_manager,
    get_onboarding_stage,
    is_owner,
    set_manager_position,
    set_onboarding_stage,
)
from monitoring.markets import get_market
from personal_data.consent import has_consent


def _is_market_supervisor(user_id: int, market_id: int) -> bool:
    supervisor = get_market_supervisor(market_id)
    return bool(supervisor and supervisor["telegram_user_id"] == user_id)


def _can_manage_trainee(user_id: int, market_id: int | None) -> bool:
    if is_owner(user_id):
        return True
    return market_id is not None and _is_market_supervisor(user_id, market_id)


def _first_market_id(uid: int) -> int | None:
    markets = get_markets_for_manager(uid)
    return markets[0]["id"] if markets else None


async def start_trainee_track(bot: Bot, uid: int) -> None:
    """Запускается сразу после того, как владелец подтвердил заявку
    сотрудника с позицией «Стажёр» (см. bot.manager_admin.on_manager_approve),
    а также повторно — как только для рынка становится готов текст согласия
    (см. bot.consent_flow._request_consents_for_market). Вместо обычных
    блоков и регламентов стажёр сначала должен дать согласие на обработку
    персональных данных (см. bot.consent_flow.send_consent_request и
    on_pdn_consent — согласие теперь общее для всех должностей, не только
    стажёров), и только потом получает первый этап программы онбординга."""
    if has_consent(uid):
        await _begin_stages(bot, uid)
        return

    sent = await send_consent_request(bot, uid)
    if sent:
        return

    manager = get_manager(uid)
    market_id = _first_market_id(uid)
    market = get_market(market_id) if market_id else None
    market_label = f"«{market['name']}»" if market else "неизвестного рынка"
    name_label = manager["name"] if manager else str(uid)
    try:
        await bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text=(
                f"⚠️ Для {market_label} не готов текст согласия (заполните /set_operator или загрузите "
                f"текст от юристов через /set_consent_text) — {name_label} не может получить согласие "
                "на обработку персональных данных, пока это не сделано."
            ),
        )
    except Exception:
        pass


def _stage_label(stage_index: int) -> str:
    stage = TRAINEE_ONBOARDING_STAGES[stage_index]
    return f"Этап {stage_index + 1} из {len(TRAINEE_ONBOARDING_STAGES)}: {stage['title']}"


async def _send_stage_to_trainee(bot: Bot, uid: int, stage_index: int) -> None:
    stage = TRAINEE_ONBOARDING_STAGES[stage_index]
    try:
        await bot.send_message(chat_id=uid, text=f"{_stage_label(stage_index)}\n\n{stage['content']}")
    except Exception:
        pass


def _supervisor_stage_keyboard(uid: int, stage_index: int) -> InlineKeyboardMarkup:
    is_last = stage_index == len(TRAINEE_ONBOARDING_STAGES) - 1
    label = "🎓 Присвоить статус «Бариста»" if is_last else "➡️ Перевести на следующий этап"
    callback = f"trainee_graduate:{uid}" if is_last else f"trainee_advance:{uid}"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback)]])


async def _notify_supervisor_stage(bot: Bot, uid: int, market_id: int | None, stage_index: int) -> None:
    manager = get_manager(uid)
    if not manager:
        return
    market = get_market(market_id) if market_id else None
    market_note = f" на «{market['name']}»" if market else ""
    supervisor = get_market_supervisor(market_id) if market_id else None
    target_id = supervisor["telegram_user_id"] if supervisor else ROMAN_TELEGRAM_ID
    try:
        await bot.send_message(
            chat_id=target_id,
            text=(
                f"👩‍🍳 {manager['name']}{market_note} изучает: {_stage_label(stage_index)}.\n"
                "Когда убедишься, что материал усвоен — переведи дальше."
            ),
            reply_markup=_supervisor_stage_keyboard(uid, stage_index),
        )
    except Exception:
        pass


async def _begin_stages(bot: Bot, uid: int) -> None:
    set_onboarding_stage(uid, 0)
    await _send_stage_to_trainee(bot, uid, 0)
    await _notify_supervisor_stage(bot, uid, _first_market_id(uid), 0)


async def on_trainee_advance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Управляющий/наставник (или владелец) подтверждает, что стажёр
    усвоил текущий этап — открывает следующий. Сам стажёр продвинуть себя
    не может."""
    query = update.callback_query
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Стажёр не найден", show_alert=True)
        return
    market_id = _first_market_id(uid)
    if not _can_manage_trainee(query.from_user.id, market_id):
        await query.answer()
        return

    current_stage = get_onboarding_stage(uid)
    next_stage = current_stage + 1
    if next_stage >= len(TRAINEE_ONBOARDING_STAGES):
        await query.answer()
        return

    await query.answer("Переведено на следующий этап")
    await query.edit_message_reply_markup(reply_markup=None)
    set_onboarding_stage(uid, next_stage)
    await _send_stage_to_trainee(context.bot, uid, next_stage)
    await _notify_supervisor_stage(context.bot, uid, market_id, next_stage)


async def on_trainee_graduate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Финальный шаг: присваивает позицию «Бариста». Блоки бота при этом не
    меняются — дальнейший перевод в «Менеджер» с выдачей блоков делается
    вручную через /managers, как для любой другой позиции."""
    query = update.callback_query
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Стажёр не найден", show_alert=True)
        return
    market_id = _first_market_id(uid)
    if not _can_manage_trainee(query.from_user.id, market_id):
        await query.answer()
        return

    await query.answer("Готово 🎉")
    await query.edit_message_reply_markup(reply_markup=None)
    set_manager_position(uid, "Бариста")
    try:
        await context.bot.send_message(
            chat_id=uid, text="🎉 Поздравляем, теперь ты бариста! Дальнейшие шаги обсудишь с управляющим лично."
        )
    except Exception:
        pass
    await query.message.reply_text(f"✅ {manager['name']} теперь «Бариста».")
