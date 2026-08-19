from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.constants import MANAGER_POSITIONS
from monitoring.db import reset_market_players
from monitoring.managers import (
    approve_manager,
    get_manager,
    get_markets_for_manager,
    is_owner,
    list_managers,
    reject_manager,
    remove_manager,
    restore_manager,
    set_manager_market,
    set_manager_position,
)
from monitoring.markets import create_market, get_market, get_market_by_name, list_markets

# telegram_user_id (str) владельца -> True, если ждём от него название нового проекта
_awaiting_new_project: set[str] = set()

_STATUS_LABELS = {"pending": "🕓 Ожидает подтверждения", "active": "✅ Активен", "removed": "🚫 Доступ отозван"}


def _manager_list_keyboard(managers: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for m in managers:
        icon = {"pending": "🕓", "active": "✅", "removed": "🚫"}[m["status"]]
        markets_label = ", ".join(mk["name"] for mk in m["markets"]) or "—"
        buttons.append(
            [InlineKeyboardButton(f"{icon} {m['name']} — {markets_label}", callback_data=f"mgr_select:{m['telegram_user_id']}")]
        )
    return InlineKeyboardMarkup(buttons)


def _manager_card_text(manager: dict, markets: list[dict]) -> str:
    markets_label = ", ".join(m["name"] for m in markets) or "—"
    return (
        f"{manager['name']} (ID {manager['telegram_user_id']})\n"
        f"Роль: {manager['position'] or '—'}\n"
        f"Проект(ы): {markets_label}\n"
        f"Статус: {_STATUS_LABELS[manager['status']]}"
    )


def _manager_card_keyboard(manager: dict) -> InlineKeyboardMarkup:
    uid = manager["telegram_user_id"]
    rows = []
    if manager["status"] == "pending":
        rows.append(
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"mgr_approve:{uid}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"mgr_reject:{uid}"),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton("✏️ Роль", callback_data=f"mgr_role:{uid}"),
                InlineKeyboardButton("🔁 Проект", callback_data=f"mgr_market:{uid}"),
            ]
        )
        if manager["status"] == "active":
            rows.append([InlineKeyboardButton("🗑 Удалить", callback_data=f"mgr_remove:{uid}")])
        elif manager["status"] == "removed":
            rows.append([InlineKeyboardButton("♻️ Восстановить", callback_data=f"mgr_restore:{uid}")])
    rows.append([InlineKeyboardButton("↩️ К списку", callback_data="mgr_list")])
    return InlineKeyboardMarkup(rows)


def _role_pick_keyboard(uid: int) -> InlineKeyboardMarkup:
    buttons = []
    for i in range(0, len(MANAGER_POSITIONS), 2):
        row = [InlineKeyboardButton(MANAGER_POSITIONS[i], callback_data=f"mgr_setrole:{uid}:{i}")]
        if i + 1 < len(MANAGER_POSITIONS):
            row.append(InlineKeyboardButton(MANAGER_POSITIONS[i + 1], callback_data=f"mgr_setrole:{uid}:{i + 1}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"mgr_select:{uid}")])
    return InlineKeyboardMarkup(buttons)


def _market_pick_keyboard(uid: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(m["name"], callback_data=f"mgr_setmarket:{uid}:{m['id']}")] for m in list_markets()
    ]
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"mgr_select:{uid}")])
    return InlineKeyboardMarkup(buttons)


async def _reply_manager_list(message) -> None:
    managers = list_managers()
    if not managers:
        await message.reply_text("Пока нет ни одного менеджера.")
        return
    await message.reply_text("Менеджеры модуля мониторинга:", reply_markup=_manager_list_keyboard(managers))


async def on_managers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    await _reply_manager_list(update.effective_message)


async def on_manager_back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    await query.answer()
    managers = list_managers()
    if not managers:
        await query.edit_message_text("Пока нет ни одного менеджера.")
        return
    await query.edit_message_text("Менеджеры модуля мониторинга:", reply_markup=_manager_list_keyboard(managers))


async def on_manager_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Не найден", show_alert=True)
        return
    markets = get_markets_for_manager(uid)
    await query.answer()
    await query.edit_message_text(_manager_card_text(manager, markets), reply_markup=_manager_card_keyboard(manager))


async def on_manager_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Заявка уже неактуальна", show_alert=True)
        return
    approve_manager(uid)
    await query.answer("Подтверждено")
    await query.edit_message_text(f"✅ Доступ подтверждён для {manager['name']} (ID {uid}).")
    try:
        await context.bot.send_message(
            chat_id=uid,
            text="✅ Владелец подтвердил твою заявку! Теперь доступны /schedule, /add_competitor и /monitoring.",
        )
    except Exception:
        pass


async def on_manager_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Заявка уже неактуальна", show_alert=True)
        return
    reject_manager(uid)
    await query.answer("Отклонено")
    await query.edit_message_text(f"❌ Заявка {manager['name']} (ID {uid}) отклонена.")
    try:
        await context.bot.send_message(chat_id=uid, text="❌ Владелец отклонил заявку на доступ к мониторингу конкурентов.")
    except Exception:
        pass


async def on_manager_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    await query.answer()
    await query.edit_message_text("Выберите новую роль:", reply_markup=_role_pick_keyboard(uid))


async def on_manager_set_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    _, uid_str, index_str = query.data.split(":", 2)
    uid, index = int(uid_str), int(index_str)
    if index < 0 or index >= len(MANAGER_POSITIONS):
        await query.answer()
        return
    position = MANAGER_POSITIONS[index]
    set_manager_position(uid, position)
    manager = get_manager(uid)
    markets = get_markets_for_manager(uid)
    await query.answer("Роль изменена")
    await query.edit_message_text(_manager_card_text(manager, markets), reply_markup=_manager_card_keyboard(manager))
    try:
        await context.bot.send_message(chat_id=uid, text=f"Владелец изменил твою роль на «{position}».")
    except Exception:
        pass


async def on_manager_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    await query.answer()
    await query.edit_message_text("Выберите новый проект:", reply_markup=_market_pick_keyboard(uid))


async def on_manager_set_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    _, uid_str, market_id_str = query.data.split(":", 2)
    uid, market_id = int(uid_str), int(market_id_str)
    market = get_market(market_id)
    if not market:
        await query.answer("Проект не найден", show_alert=True)
        return
    set_manager_market(uid, market_id)
    manager = get_manager(uid)
    markets = get_markets_for_manager(uid)
    await query.answer("Проект изменён")
    await query.edit_message_text(_manager_card_text(manager, markets), reply_markup=_manager_card_keyboard(manager))
    try:
        await context.bot.send_message(chat_id=uid, text=f"Владелец переназначил тебя на проект «{market['name']}».")
    except Exception:
        pass


async def on_manager_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        f"Точно удалить доступ у {manager['name']} (ID {uid})? История его снятий и наблюдений сохранится.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🗑 Да, удалить", callback_data=f"mgr_remove_confirm:{uid}"),
                    InlineKeyboardButton("↩️ Отмена", callback_data=f"mgr_select:{uid}"),
                ]
            ]
        ),
    )


async def on_manager_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Не найден", show_alert=True)
        return
    remove_manager(uid)
    await query.answer("Удалено")
    await query.edit_message_text(f"🚫 Доступ отозван у {manager['name']} (ID {uid}).")
    try:
        await context.bot.send_message(chat_id=uid, text="🚫 Владелец отозвал твой доступ к модулю мониторинга конкурентов.")
    except Exception:
        pass


async def on_manager_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Не найден", show_alert=True)
        return
    restore_manager(uid)
    manager = get_manager(uid)
    markets = get_markets_for_manager(uid)
    await query.answer("Восстановлено")
    await query.edit_message_text(_manager_card_text(manager, markets), reply_markup=_manager_card_keyboard(manager))
    try:
        await context.bot.send_message(chat_id=uid, text="✅ Владелец восстановил твой доступ к модулю мониторинга конкурентов.")
    except Exception:
        pass


async def on_add_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    _awaiting_new_project.add(str(update.effective_user.id))
    await update.effective_message.reply_text("Название нового проекта/точки Surf?")


async def on_manager_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текстовый ответ на /add_project, если он ожидается от этого
    пользователя. Возвращает True, если сообщение обработано (по конвенции
    остальных «claim or pass» хендлеров в on_private_text)."""
    user_id = str(update.effective_user.id)
    if user_id not in _awaiting_new_project:
        return False

    _awaiting_new_project.discard(user_id)
    name = update.effective_message.text.strip()
    if not name:
        await update.effective_message.reply_text("Пустое название, отменил добавление проекта.")
        return True
    if get_market_by_name(name):
        await update.effective_message.reply_text(f"Проект «{name}» уже существует.")
        return True

    create_market(name)
    await update.effective_message.reply_text(
        f"✅ Проект «{name}» добавлен — теперь появится в онбординге через /start."
    )
    return True


def _reset_market_pick_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(m["name"], callback_data=f"reset_monitoring_market:{m['id']}")] for m in list_markets()]
    )


def _reset_confirm_keyboard(market_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Да, обнулить", callback_data=f"reset_monitoring_confirm:{market_id}"),
                InlineKeyboardButton("Отмена", callback_data="reset_monitoring_cancel"),
            ]
        ]
    )


async def on_reset_monitoring_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка.")
        return
    await update.effective_message.reply_text(
        "По какому рынку обнулить данные конкурентов (снятия, факторы, наблюдения)? "
        "Менеджеры и их привязка к рынку не тронутся.",
        reply_markup=_reset_market_pick_keyboard(),
    )


async def on_reset_monitoring_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await query.edit_message_text(
        f"⚠️ Удалить всех конкурентов, их факторы, снятия и наблюдения на рынке «{market['name']}»? "
        "Менеджеры и расписание мониторинга останутся. Действие необратимо.",
        reply_markup=_reset_confirm_keyboard(market_id),
    )


async def on_reset_monitoring_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer("Обнуляю…")
    reset_market_players(market_id)
    await query.edit_message_text(f"✅ Данные конкурентов на рынке «{market['name']}» обнулены. Можно добавлять заново через /add_competitor.")


async def on_reset_monitoring_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    await query.answer()
    await query.edit_message_text("Отменено, ничего не удалено.")
