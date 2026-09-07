from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import ROMAN_TELEGRAM_ID
from monitoring.managers import (
    get_manager,
    get_market_supervisor,
    get_markets_for_manager,
    is_owner,
    list_managers,
)
from monitoring.markets import get_market, has_consent_text_ready, list_markets
from personal_data.consent import has_consent, record_consent, render_consent_text


def _first_market_id(uid: int) -> int | None:
    markets = get_markets_for_manager(uid)
    return markets[0]["id"] if markets else None


def _consent_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Согласен(на)", callback_data=f"pdn_consent:{uid}:yes"),
                InlineKeyboardButton("❌ Не согласен(на)", callback_data=f"pdn_consent:{uid}:no"),
            ]
        ]
    )


async def send_consent_request(bot: Bot, uid: int) -> bool:
    """Отправляет запрос согласия на обработку ПДн, если ещё не дано и рынок
    к этому готов (см. monitoring.markets.has_consent_text_ready). Общая для
    новых стажёров (см. bot.trainee_onboarding.start_trainee_track) и для
    уже работающих сотрудников (см. request_consents_for_market ниже) — не
    зависит от должности. Возвращает True, если сообщение отправлено."""
    if has_consent(uid):
        return False
    market_id = _first_market_id(uid)
    market = get_market(market_id) if market_id else None
    if not market or not has_consent_text_ready(market):
        return False
    try:
        await bot.send_message(chat_id=uid, text=render_consent_text(market), reply_markup=_consent_keyboard(uid))
        return True
    except Exception:
        return False


async def on_pdn_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, uid_str, choice = query.data.split(":")
    uid = int(uid_str)
    if query.from_user.id != uid:
        await query.answer()
        return

    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    manager = get_manager(uid)

    if choice == "no":
        await query.message.reply_text(
            "Понимаю. Без согласия на обработку персональных данных бот не сможет продолжать хранить твои данные — "
            "свяжись с управляющим точки, чтобы обсудить это лично."
        )
        if manager:
            market_id = _first_market_id(uid)
            supervisor = get_market_supervisor(market_id) if market_id else None
            target_id = supervisor["telegram_user_id"] if supervisor else ROMAN_TELEGRAM_ID
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"⚠️ {manager['name']} не дал(а) согласие на обработку персональных данных.",
                )
            except Exception:
                pass
        return

    market_id = _first_market_id(uid)
    market = get_market(market_id) if market_id else None
    if not market:
        await query.message.reply_text("Не нашёл твой рынок — напиши управляющему, разберёмся.")
        return

    record_consent(uid, market_id, render_consent_text(market))

    if manager and manager["position"] == "Стажёр":
        # Стажёру согласие открывает первый этап программы онбординга —
        # см. bot.trainee_onboarding (импорт здесь же, чтобы не заводить
        # цикл: trainee_onboarding сам обращается к send_consent_request).
        from bot.trainee_onboarding import _begin_stages

        await query.message.reply_text("Спасибо! Начинаем программу онбординга.")
        await _begin_stages(context.bot, uid)
        return

    await query.message.reply_text("Спасибо! Согласие записано.")


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"reqconsent_market:{m['id']}")] for m in markets])


async def _request_consents_for_market(bot: Bot, market_id: int) -> int:
    """Шлёт запрос согласия всем активным сотрудникам рынка (любая
    должность), у кого его ещё нет — вызывается и вручную (/request_consents),
    и автоматически, как только для рынка становится готов текст согласия
    (см. bot.market_operator, /set_operator и /set_consent_text)."""
    sent = 0
    for manager in list_managers():
        if manager["status"] != "active":
            continue
        if not any(m["id"] == market_id for m in manager["markets"]):
            continue
        if await send_consent_request(bot, manager["telegram_user_id"]):
            sent += 1
    return sent


async def on_request_consents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/request_consents — владелец вручную рассылает запрос согласия на
    обработку ПДн всем активным сотрудникам рынка, у кого его ещё нет —
    для уже работающих сотрудников, заведённых до появления текста
    согласия (обычно достаточно /set_operator или /set_consent_text —
    они делают это автоматически, эта команда — на случай, если нужно
    повторить вручную)."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка.")
        return
    if len(markets) == 1:
        sent = await _request_consents_for_market(context.bot, markets[0]["id"])
        await update.effective_message.reply_text(f"Разослал запрос согласия {sent} сотрудникам «{markets[0]['name']}».")
        return
    await update.effective_message.reply_text("По какому рынку разослать запрос согласия?", reply_markup=_market_pick_keyboard(markets))


async def on_request_consents_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    sent = await _request_consents_for_market(context.bot, market_id)
    await query.edit_message_text(f"Разослал запрос согласия {sent} сотрудникам «{market['name']}».")
