from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.market_schedule import start_schedule_flow
from bot.onboarding import get_onboarded_employees, list_legacy_employees, remove_legacy_employee, request_registration
from bot.regulations import send_next_regulation
from config.chats import get_all_bindings, get_project_for_chat, register_chat, unregister_chat
from monitoring.constants import AVAILABLE_BLOCKS, BLOCK_LABELS, BLOCK_MONITORING, BLOCK_TASKS, MANAGER_POSITIONS
from monitoring.db import reset_market_players
from monitoring.managers import (
    acknowledge_block,
    approve_manager,
    get_acknowledged_blocks,
    get_manager,
    get_manager_blocks,
    get_market_supervisor,
    get_markets_for_manager,
    is_owner,
    list_managers,
    reject_manager,
    remove_manager,
    set_manager_blocks,
    set_manager_market,
    set_manager_position,
)
from monitoring.markets import create_market, get_market, get_market_by_name, list_markets

# telegram_user_id (str) владельца -> True, если ждём от него название нового проекта
_awaiting_new_project: set[str] = set()

# telegram_user_id (str) владельца -> {"uid": int, "selected": set[str]} — сессия редактирования блоков
_pending_blocks: dict[str, dict] = {}

_STATUS_LABELS = {"pending": "🕓 Ожидает подтверждения", "active": "✅ Активен"}


class _ChatMessenger:
    """Адаптер с интерфейсом .reply_text для отправки НОВОГО сообщения по
    chat_id (а не ответа на существующее) — нужен, чтобы переиспользовать
    bot.market_schedule.start_schedule_flow сразу после подтверждения
    нового Управляющего, без правки самого market_schedule.py."""

    def __init__(self, bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    async def reply_text(self, text, reply_markup=None):
        return await self._bot.send_message(chat_id=self._chat_id, text=text, reply_markup=reply_markup)


def _commands_for_manager(manager: dict) -> list[BotCommand]:
    """Меню команд в Telegram, положенное этому сотруднику — зависит от
    выданных блоков и (для блока «Мониторинг») от должности: командами
    редактирования списка конкурентов и расписания пользуется только
    Управляющий, остальные роли ходят только на сам мониторинг. Блок
    открывается в меню только после того, как выдан владельцем И сотрудник
    подтвердил, что прочитал его регламент (см. send_next_regulation)."""
    uid = manager["telegram_user_id"]
    blocks = set(get_manager_blocks(uid)) & set(get_acknowledged_blocks(uid))
    commands: list[BotCommand] = []
    if BLOCK_TASKS in blocks:
        commands.append(BotCommand("mytasks", "Мои задачи в работе"))
    if BLOCK_MONITORING in blocks:
        if manager["position"] == "Управляющий":
            commands.append(BotCommand("add_competitor", "Добавить конкурента на рынок"))
            commands.append(BotCommand("close_competitor", "Закрыть/открыть конкурента"))
            commands.append(BotCommand("schedule", "Настроить дни мониторинга рынка"))
            commands.append(BotCommand("set_shift_schedule", "Загрузить график смен на 2 недели"))
        commands.append(BotCommand("monitoring", "Провести мониторинг конкурентов"))
        commands.append(BotCommand("dashboard_market", "Дашборд по рынку"))
    if commands:
        commands.append(BotCommand("regulations", "Регламенты работы с ботом"))
    return commands


async def sync_employee_commands(bot, uid: int) -> None:
    """Обновляет персональное меню команд сотрудника в Telegram под его
    текущие блоки/роль/статус. Если доступа нет (ещё не подтверждён или
    удалён) — персональное меню снимается, и он видит только дефолтное
    (/start, см. main.py), пока владелец не подтвердит доступ."""
    manager = get_manager(uid)
    if not manager or manager["status"] != "active":
        try:
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=uid))
        except Exception:
            pass
        return
    commands = _commands_for_manager(manager)
    try:
        if commands:
            await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=uid))
        else:
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=uid))
    except Exception:
        pass


def _manager_list_keyboard(managers: list[dict], legacy: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for m in managers:
        icon = {"pending": "🕓", "active": "✅"}[m["status"]]
        markets_label = ", ".join(mk["name"] for mk in m["markets"]) or "—"
        buttons.append(
            [InlineKeyboardButton(f"{icon} {m['name']} — {markets_label}", callback_data=f"mgr_select:{m['telegram_user_id']}")]
        )
    for e in legacy:
        buttons.append(
            [
                InlineKeyboardButton(f"📨 {e['name']} — не выбрал(а) проект", callback_data=f"mgr_nudge:{e['user_id']}"),
                InlineKeyboardButton("🗑", callback_data=f"mgr_legacy_remove:{e['user_id']}"),
            ]
        )
    buttons.append([InlineKeyboardButton("💬 Чаты", callback_data="mgr_chats")])
    buttons.append([InlineKeyboardButton("📋 Все, кто писал боту", callback_data="mgr_onboarded")])
    return InlineKeyboardMarkup(buttons)


def _manager_card_text(manager: dict, markets: list[dict]) -> str:
    markets_label = ", ".join(m["name"] for m in markets) or "—"
    blocks_label = ", ".join(BLOCK_LABELS[b] for b in get_manager_blocks(manager["telegram_user_id"])) or "нет"
    return (
        f"{manager['name']} (ID {manager['telegram_user_id']})\n"
        f"Роль: {manager['position'] or '—'}\n"
        f"Проект(ы): {markets_label}\n"
        f"Блоки: {blocks_label}\n"
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
    rows.append([InlineKeyboardButton("🧩 Блоки", callback_data=f"mgr_blocks:{uid}")])
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


def _blocks_keyboard(uid: int, selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for block_key in AVAILABLE_BLOCKS:
        mark = "✅ " if block_key in selected else "⬜ "
        rows.append([InlineKeyboardButton(f"{mark}{BLOCK_LABELS[block_key]}", callback_data=f"mgr_toggleblock:{uid}:{block_key}")])
    rows.append([InlineKeyboardButton("✔️ Готово", callback_data=f"mgr_blocksdone:{uid}")])
    return InlineKeyboardMarkup(rows)


def _list_view_text(managers: list[dict], legacy: list[dict]) -> str:
    if not managers and not legacy:
        return "Пока нет ни одного сотрудника с ролью — но ниже можно посмотреть привязки чатов и всех, кто писал боту."
    text = "Сотрудники бота:"
    if legacy:
        text += (
            "\n\n📨 — уже писали боту раньше, но не выбрали проект и роль для мониторинга. "
            "Нажмите на имя, чтобы прислать им этот вопрос сейчас."
        )
    return text


async def _reply_manager_list(message) -> None:
    managers = list_managers()
    legacy = list_legacy_employees()
    await message.reply_text(_list_view_text(managers, legacy), reply_markup=_manager_list_keyboard(managers, legacy))


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
    legacy = list_legacy_employees()
    await query.edit_message_text(_list_view_text(managers, legacy), reply_markup=_manager_list_keyboard(managers, legacy))


async def on_manager_nudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    user_id = int(query.data.split(":", 1)[1])
    await query.answer("Отправляю…")
    try:
        await request_registration(context.bot, user_id)
    except Exception:
        await query.edit_message_text("Не получилось отправить — возможно, сотрудник ещё ни разу не писал боту в личку.")
        return
    await query.edit_message_text(f"📨 Отправил(а) вопрос про проект и роль сотруднику (ID {user_id}).")


async def on_manager_legacy_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    user_id = int(query.data.split(":", 1)[1])
    await query.answer()
    await query.edit_message_text(
        f"Удалить сотрудника (ID {user_id}) из списка? Он не потеряет доступ к трекеру задач — "
        "просто сможет позже снова написать боту и пройти онбординг заново.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🗑 Да, удалить", callback_data=f"mgr_legacy_remove_confirm:{user_id}"),
                    InlineKeyboardButton("↩️ Отмена", callback_data="mgr_list"),
                ]
            ]
        ),
    )


async def on_manager_legacy_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    user_id = int(query.data.split(":", 1)[1])
    removed = remove_legacy_employee(user_id)
    await query.answer("Удалено" if removed else "Уже не найден")
    managers = list_managers()
    legacy = list_legacy_employees()
    await query.edit_message_text(_list_view_text(managers, legacy), reply_markup=_manager_list_keyboard(managers, legacy))


def _chat_bindings_keyboard(bindings: list[tuple[int, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{project} — {chat_id}", callback_data=f"mgr_chat_select:{chat_id}")]
        for chat_id, project, _source in bindings
    ]
    rows.append([InlineKeyboardButton("↩️ К списку", callback_data="mgr_list")])
    return InlineKeyboardMarkup(rows)


def _chat_card_text(chat_id: int, project: str, source: str) -> str:
    source_label = "из .env (CHAT_PROJECT_MAP)" if source == "env" else "через /register_project"
    return f"Чат {chat_id}\nПроект: {project}\nИсточник: {source_label}"


def _chat_card_keyboard(chat_id: int, source: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🔁 Сменить проект", callback_data=f"mgr_chat_market:{chat_id}")]]
    if source == "runtime":
        rows.append([InlineKeyboardButton("🗑 Отвязать", callback_data=f"mgr_chat_unbind:{chat_id}")])
    rows.append([InlineKeyboardButton("↩️ К чатам", callback_data="mgr_chats")])
    return InlineKeyboardMarkup(rows)


def _chat_market_pick_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(m["name"], callback_data=f"mgr_chat_setmarket:{chat_id}:{m['id']}")] for m in list_markets()
    ]
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"mgr_chat_select:{chat_id}")])
    return InlineKeyboardMarkup(buttons)


async def on_manager_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    await query.answer()
    bindings = get_all_bindings()
    if not bindings:
        await query.edit_message_text(
            "Нет привязанных чатов. Чтобы привязать группу к проекту — вызовите /register_project внутри неё.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ К списку", callback_data="mgr_list")]]),
        )
        return
    await query.edit_message_text(
        "💬 Привязанные чаты. Чтобы привязать новый — вызовите /register_project внутри него.",
        reply_markup=_chat_bindings_keyboard(bindings),
    )


async def on_manager_chat_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    chat_id = int(query.data.split(":", 1)[1])
    binding = next((b for b in get_all_bindings() if b[0] == chat_id), None)
    if not binding:
        await query.answer("Не найден", show_alert=True)
        return
    await query.answer()
    _, project, source = binding
    await query.edit_message_text(_chat_card_text(chat_id, project, source), reply_markup=_chat_card_keyboard(chat_id, source))


async def on_manager_chat_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    chat_id = int(query.data.split(":", 1)[1])
    await query.answer()
    await query.edit_message_text("Выберите проект для этого чата:", reply_markup=_chat_market_pick_keyboard(chat_id))


async def on_manager_chat_set_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    _, chat_id_str, market_id_str = query.data.split(":", 2)
    chat_id, market_id = int(chat_id_str), int(market_id_str)
    market = get_market(market_id)
    if not market:
        await query.answer("Проект не найден", show_alert=True)
        return
    register_chat(chat_id, market["name"])
    await query.answer("Привязано")
    await query.edit_message_text(
        _chat_card_text(chat_id, market["name"], "runtime"), reply_markup=_chat_card_keyboard(chat_id, "runtime")
    )


async def on_manager_chat_unbind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    chat_id = int(query.data.split(":", 1)[1])
    await query.answer()
    await query.edit_message_text(
        f"Точно отвязать чат {chat_id} от проекта?",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🗑 Да, отвязать", callback_data=f"mgr_chat_unbind_confirm:{chat_id}"),
                    InlineKeyboardButton("↩️ Отмена", callback_data=f"mgr_chat_select:{chat_id}"),
                ]
            ]
        ),
    )


async def on_manager_chat_unbind_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    chat_id = int(query.data.split(":", 1)[1])
    unregister_chat(chat_id)
    await query.answer("Отвязано")
    bindings = get_all_bindings()
    if not bindings:
        await query.edit_message_text(
            "Нет привязанных чатов. Чтобы привязать группу к проекту — вызовите /register_project внутри неё.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ К списку", callback_data="mgr_list")]]),
        )
        return
    await query.edit_message_text("💬 Привязанные чаты:", reply_markup=_chat_bindings_keyboard(bindings))


async def on_manager_onboarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«📋 Все, кто писал боту» — читает то же, что раньше показывала
    упразднённая команда /onboarded: все, кого бот хоть раз видел в личке
    (трекер задач), с их именем и проектом, определённым по чатам, где их
    видели."""
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    await query.answer()
    employees = get_onboarded_employees()
    lines = ["📋 Все, кто хоть раз писал боту:"]
    if not employees:
        lines.append("Пока никто не онбордился.")
    else:
        for emp in employees:
            display_name = emp["real_name"] or emp["full_name"] or "(без имени)"
            username_part = f"@{emp['username']}" if emp["username"] else "без username"
            projects = sorted({p for c in emp["chats"] if (p := get_project_for_chat(c))})
            projects_part = ", ".join(projects) if projects else "не видели в привязанных чатах"
            lines.append(f"- {display_name} ({username_part}, ID {emp['user_id']}) — {projects_part}")
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ К списку", callback_data="mgr_list")]]),
    )


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


async def _check_supervisor_conflict(query, uid: int) -> bool:
    """True, если назначение uid Управляющим блокируется конфликтом — на
    каком-то из его рынков уже есть другой активный Управляющий (у рынка
    может быть только один). Уже показывает алерт владельцу, если нашёл."""
    for market in get_markets_for_manager(uid):
        existing = get_market_supervisor(market["id"], exclude_telegram_user_id=uid)
        if existing:
            await query.answer(
                f"На проекте «{market['name']}» (он же рынок для мониторинга) уже есть Управляющий — {existing['name']}. "
                "Сначала смените его роль через /managers, потом назначайте нового.",
                show_alert=True,
            )
            return True
    return False


async def _notify_approved(bot, manager: dict, uid: int) -> None:
    blocks = get_manager_blocks(uid)
    if blocks:
        labels = ", ".join(BLOCK_LABELS[b] for b in blocks)
        text = f"✅ Рома подтвердил твою заявку! Теперь тебе доступны опции: {labels}."
    else:
        text = "✅ Рома подтвердил твою заявку, но пока без выданных блоков — уточни у Ромы."
    try:
        await bot.send_message(chat_id=uid, text=text)
    except Exception:
        pass


async def _prompt_new_supervisor_schedule(bot, uid: int) -> None:
    """Сразу после того, как человек становится Управляющим рынка,
    спрашиваем у него дни недели для мониторинга — не нужно ждать, пока он
    сам вспомнит про /schedule."""
    markets = get_markets_for_manager(uid)
    if not markets:
        return
    market = markets[0]
    messenger = _ChatMessenger(bot, uid)
    await messenger.reply_text(f"Вы — Управляющий проекта «{market['name']}» (он же рынок для мониторинга). Выберите дни недели для мониторинга конкурентов:")
    await start_schedule_flow(messenger, str(uid), market)


async def on_manager_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Подтверждение заявки — сначала владелец выбирает блоки (см.
    on_manager_blocks_done, mode="approve"): статус становится 'active',
    сотруднику открываются команды и приходят регламенты только ПОСЛЕ
    того, как блоки выбраны и нажато «Готово», а не сразу по клику
    «Подтвердить»."""
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Заявка уже неактуальна", show_alert=True)
        return

    if manager["position"] == "Управляющий" and await _check_supervisor_conflict(query, uid):
        return

    owner_id = str(query.from_user.id)
    _pending_blocks[owner_id] = {"uid": uid, "selected": set(get_manager_blocks(uid)), "mode": "approve"}
    await query.answer()
    await query.edit_message_text(
        f"Какие блоки бота выдать {manager['name']}? Команды и регламенты откроются только после «Готово».",
        reply_markup=_blocks_keyboard(uid, _pending_blocks[owner_id]["selected"]),
    )


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
        await context.bot.send_message(chat_id=uid, text="❌ Владелец отклонил заявку на доступ к боту.")
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

    if position == "Управляющий" and await _check_supervisor_conflict(query, uid):
        return

    manager_before = get_manager(uid)
    previous_position = manager_before["position"] if manager_before else None

    set_manager_position(uid, position)
    manager = get_manager(uid)
    markets = get_markets_for_manager(uid)
    await query.answer("Роль изменена")
    await query.edit_message_text(_manager_card_text(manager, markets), reply_markup=_manager_card_keyboard(manager))
    await sync_employee_commands(context.bot, uid)
    try:
        await context.bot.send_message(chat_id=uid, text=f"Владелец изменил твою роль на «{position}».")
    except Exception:
        pass

    if position == "Управляющий" and previous_position != "Управляющий" and manager["status"] == "active":
        await _prompt_new_supervisor_schedule(context.bot, uid)


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

    manager = get_manager(uid)
    if manager and manager["position"] == "Управляющий":
        existing = get_market_supervisor(market_id, exclude_telegram_user_id=uid)
        if existing:
            await query.answer(
                f"На проекте «{market['name']}» (он же рынок для мониторинга) уже есть Управляющий — {existing['name']}. "
                "Сначала смените его роль через /managers.",
                show_alert=True,
            )
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

    if manager["position"] == "Управляющий" and manager["status"] == "active":
        await _prompt_new_supervisor_schedule(context.bot, uid)


async def on_manager_blocks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    manager = get_manager(uid)
    if not manager:
        await query.answer("Не найден", show_alert=True)
        return
    owner_id = str(query.from_user.id)
    _pending_blocks[owner_id] = {"uid": uid, "selected": set(get_manager_blocks(uid)), "mode": "edit"}
    await query.answer()
    await query.edit_message_text(
        f"Какие блоки бота доступны {manager['name']}?", reply_markup=_blocks_keyboard(uid, _pending_blocks[owner_id]["selected"])
    )


async def on_manager_toggle_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    _, uid_str, block_key = query.data.split(":", 2)
    uid = int(uid_str)
    owner_id = str(query.from_user.id)
    pending = _pending_blocks.get(owner_id)
    if not pending or pending["uid"] != uid:
        await query.answer("Сессия неактуальна, откройте карточку заново", show_alert=True)
        return
    if block_key in pending["selected"]:
        pending["selected"].discard(block_key)
    else:
        pending["selected"].add(block_key)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=_blocks_keyboard(uid, pending["selected"]))


async def on_manager_blocks_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    uid = int(query.data.split(":", 1)[1])
    owner_id = str(query.from_user.id)
    pending = _pending_blocks.get(owner_id)
    if not pending or pending["uid"] != uid:
        await query.answer("Сессия неактуальна", show_alert=True)
        return

    selected = [b for b in AVAILABLE_BLOCKS if b in pending["selected"]]
    mode = pending.get("mode", "edit")
    set_manager_blocks(uid, selected)
    del _pending_blocks[owner_id]

    if mode == "approve":
        approve_manager(uid)
        manager = get_manager(uid)
        blocks_label = ", ".join(BLOCK_LABELS[b] for b in selected) or "нет"
        await query.answer("Подтверждено")
        await query.edit_message_text(f"✅ Доступ подтверждён для {manager['name']} (ID {uid}). Блоки: {blocks_label}.")
        await _notify_approved(context.bot, manager, uid)
        await sync_employee_commands(context.bot, uid)
        await send_next_regulation(_ChatMessenger(context.bot, uid), uid)
        return

    manager = get_manager(uid)
    markets = get_markets_for_manager(uid)
    await query.answer("Сохранено")
    await query.edit_message_text(_manager_card_text(manager, markets), reply_markup=_manager_card_keyboard(manager))
    await sync_employee_commands(context.bot, uid)

    blocks_label = ", ".join(BLOCK_LABELS[b] for b in selected) or "ничего (доступ ко всем блокам отключён)"
    try:
        await context.bot.send_message(chat_id=uid, text=f"Владелец изменил доступные тебе блоки бота: {blocks_label}.")
    except Exception:
        pass
    await send_next_regulation(_ChatMessenger(context.bot, uid), uid)


async def on_regulation_ack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сотрудник подтвердил, что прочитал регламент своего блока (кнопка
    под текстом регламента). Открывает команды этого блока в личном меню и
    отправляет следующий регламент, если выдан ещё один блок, который пока
    не подтверждён. Когда подтверждать больше нечего — сообщает, что всё
    открыто, и (для Управляющего с блоком «Мониторинг») сразу предлагает
    выбрать дни недели для мониторинга."""
    query = update.callback_query
    uid = query.from_user.id
    block_key = query.data.split(":", 1)[1]
    await query.answer("Принято")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    acknowledge_block(uid, block_key)
    await sync_employee_commands(context.bot, uid)
    sent_next = await send_next_regulation(_ChatMessenger(context.bot, uid), uid)
    if sent_next:
        return

    await context.bot.send_message(
        chat_id=uid,
        text="Готово! Теперь тебе доступны все команды по твоим блокам — посмотри меню (кнопка ☰ рядом с полем ввода).",
    )
    manager = get_manager(uid)
    if manager and manager["position"] == "Управляющий" and BLOCK_MONITORING in get_manager_blocks(uid):
        await _prompt_new_supervisor_schedule(context.bot, uid)


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
    await sync_employee_commands(context.bot, uid)
    try:
        await context.bot.send_message(
            chat_id=uid,
            text="🚫 Владелец отозвал твой доступ к боту. Если понадобится вернуться — напиши /start и пройди онбординг заново.",
        )
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
