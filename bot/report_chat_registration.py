from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.managers import is_owner
from monitoring.markets import get_market, list_markets
from monitoring.shift_reports import set_report_chat

_ROLE_LABELS = {"finance": "💰 Финпартнёры", "team": "👥 Команда точки"}

# telegram_user_id (str) владельца -> {"market_id", "role", "chat_id", "market_name"} —
# ждём текст, кого тегнуть первой строкой в отчёте для этого чата
_awaiting_mention: dict[str, dict] = {}


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"shrc_market:{m['id']}")] for m in markets])


def _role_pick_keyboard(market_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"shrc_role:{market_id}:{role}")] for role, label in _ROLE_LABELS.items()]
    )


async def on_register_report_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/register_report_chat — владелец вызывает внутри группы, чтобы
    привязать её как получателя ежедневного отчёта по смене (не как
    рабочий чат проекта — сюда бот только рассылает готовые отчёты,
    переписку не разбирает на задачи)."""
    if not is_owner(update.effective_user.id):
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("Эта команда работает только внутри группового чата.")
        return

    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка — сначала /add_project в личке боту.")
        return

    await update.effective_message.reply_text(
        "Для какого рынка этот чат будет получать отчёты?", reply_markup=_market_pick_keyboard(markets)
    )


async def on_register_report_chat_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await query.edit_message_text(f"Рынок: {market['name']}. Какой это чат?", reply_markup=_role_pick_keyboard(market_id))


async def on_register_report_chat_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return

    _, market_id_str, role = query.data.split(":")
    market_id = int(market_id_str)
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return

    chat_id = query.message.chat.id
    await query.answer()

    if role == "finance":
        # Спрашиваем в личке владельца, а не в самой группе — там сидят
        # сами финпартнёры, и настроечный диалог не должен идти у них на виду.
        owner_id = query.from_user.id
        _awaiting_mention[str(owner_id)] = {
            "market_id": market_id,
            "role": role,
            "chat_id": chat_id,
            "market_name": market["name"],
        }
        await query.edit_message_text(f"✅ Этот чат привязан для «{market['name']}» — {_ROLE_LABELS[role]}. Донастрою в личке.")
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=(
                    f"Кого тегнуть первой строкой в отчёте для чата «{market['name']}» (например, "
                    "@Motus_control_group_bot)? Если не нужно — напишите «нет»."
                ),
            )
        except Exception:
            await query.message.reply_text(
                "Не смог написать вам в личку — откройте диалог со мной (/start) и повторите /register_report_chat."
            )
        return

    set_report_chat(market_id, role, chat_id)
    await query.edit_message_text(f"✅ Этот чат будет получать отчёты «{market['name']}» — {_ROLE_LABELS[role]}.")


async def on_register_report_chat_mention_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текст, кого тегать первой строкой отчёта для финпартнёров.
    Возвращает True, если сообщение обработано — по конвенции остальных
    claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_mention.pop(owner_id, None)
    if not state:
        return False

    text = (update.effective_message.text or "").strip()
    mention = "" if text.lower() in ("нет", "не надо", "-") else text
    if mention and not mention.startswith("@"):
        mention = f"@{mention}"

    set_report_chat(state["market_id"], state["role"], state["chat_id"], mention)
    suffix = f" Первой строкой будет: {mention}" if mention else ""
    await update.effective_message.reply_text(
        f"✅ Этот чат будет получать отчёты «{state['market_name']}» — {_ROLE_LABELS[state['role']]}.{suffix}"
    )
    return True
