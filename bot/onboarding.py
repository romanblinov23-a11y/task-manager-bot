import json
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.ext import ContextTypes

from config.settings import OWNER_TELEGRAM_IDS, ROMAN_CHAT_NAME, ROMAN_TELEGRAM_ID
from monitoring.constants import MANAGER_POSITIONS
from monitoring.managers import get_manager, get_market_supervisor, is_owner, list_managers, register_manager
from monitoring.markets import get_market, list_markets

_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "known_users.json"

# user_id (str) -> {"username": str|None, "full_name": str, "real_name": str|None, "onboarded": bool}
_known_users: dict[str, dict] = {}
# user_id (str) -> [chat_id, ...] — группы, где этот человек писал сообщения
_seen_in_chats: dict[str, list[int]] = {}
# user_id (str), которым сейчас задан вопрос "как тебя зовут?"
_awaiting_name: set[str] = set()
# user_id (str) -> {"step": "project"|"name"|"role", "market_id", "market_name", "real_name"}
# состояние диалога первичного онбординга (проект → имя → роль)
_pending_onboarding: dict[str, dict] = {}


def _load() -> None:
    global _known_users, _seen_in_chats
    if _STORE_PATH.exists():
        data = json.loads(_STORE_PATH.read_text())
        _known_users = data.get("known_users", {})
        _seen_in_chats = data.get("seen_in_chats", {})


def _save() -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps(
            {"known_users": _known_users, "seen_in_chats": _seen_in_chats},
            ensure_ascii=False,
            indent=2,
        )
    )


_load()


def is_onboarded(user_id: int) -> bool:
    """True, если этот пользователь уже открыл диалог с ботом и боту можно
    писать ему напрямую в личку."""
    return _known_users.get(str(user_id), {}).get("onboarded", False)


def get_username(user_id: int) -> str | None:
    """Telegram username пользователя, если бот его видел в группе."""
    return _known_users.get(str(user_id), {}).get("username")


def get_display_name(user_id: int) -> str:
    """Отображаемое имя: real_name → full_name → @username → ID."""
    info = _known_users.get(str(user_id), {})
    return (
        info.get("real_name")
        or info.get("full_name")
        or (f"@{info['username']}" if info.get("username") else str(user_id))
    )


def get_onboarded_employees() -> list[dict]:
    """Все сотрудники, прошедшие онбординг, с их данными — для /employees → «📋 Все, кто писал боту»."""
    result = []
    for user_id, info in _known_users.items():
        if not info.get("onboarded"):
            continue
        result.append(
            {
                "user_id": int(user_id),
                "real_name": info.get("real_name") or "",
                "full_name": info.get("full_name") or "",
                "username": info.get("username") or "",
                "chats": _seen_in_chats.get(user_id, []),
            }
        )
    return result


def record_group_member(chat_id: int, user: User | None) -> None:
    """Запоминает сотрудников и рабочие чаты для регистрации и рассылок."""
    if user is None or user.is_bot:
        return

    user_id = str(user.id)

    _known_users.setdefault(user_id, {})
    _known_users[user_id]["username"] = user.username
    _known_users[user_id]["full_name"] = user.full_name

    if str(user.id) == str(ROMAN_TELEGRAM_ID):
        _known_users[user_id]["real_name"] = ROMAN_CHAT_NAME
        _known_users[user_id]["onboarded"] = True

    chats = _seen_in_chats.setdefault(user_id, [])
    if chat_id not in chats:
        chats.append(chat_id)
    _save()


def _project_choice_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"onb_project:{m['id']}")] for m in markets])


def _role_choice_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for i in range(0, len(MANAGER_POSITIONS), 2):
        row = [InlineKeyboardButton(MANAGER_POSITIONS[i], callback_data=f"onb_role:{i}")]
        if i + 1 < len(MANAGER_POSITIONS):
            row.append(InlineKeyboardButton(MANAGER_POSITIONS[i + 1], callback_data=f"onb_role:{i + 1}"))
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


async def _start_project_selection(message, user_id: str, *, intro: str | None = None) -> None:
    markets = list_markets()
    _pending_onboarding[user_id] = {"step": "project"}
    text = intro or "Привет! Я Енисей — бот мониторинга и отчётности.\n\n"
    await message.reply_text(f"{text}В каком проекте ты трудишься?", reply_markup=_project_choice_keyboard(markets))


class _DirectMessenger:
    """Адаптер с интерфейсом .reply_text для отправки НОВОГО сообщения по
    chat_id — нужен, чтобы владелец мог инициировать выбор проекта/роли у
    сотрудника, который уже писал боту раньше (есть в known_users.json), но
    так и не завершил регистрацию в модуле мониторинга (см. request_registration)."""

    def __init__(self, bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    async def reply_text(self, text, reply_markup=None):
        return await self._bot.send_message(chat_id=self._chat_id, text=text, reply_markup=reply_markup)


def list_legacy_employees() -> list[dict]:
    """Сотрудники, которые уже писали боту (известны боту, есть в
    known_users.json), но не завершили выбор проекта/роли для модуля
    мониторинга — у них нет записи в manager. Владелец видит их
    в /employees и может донбордить вручную, не дожидаясь, пока они сами
    снова напишут боту."""
    result = []
    for user_id, info in _known_users.items():
        if not info.get("onboarded"):
            continue
        uid = int(user_id)
        if is_owner(uid) or get_manager(uid) is not None:
            continue
        name = info.get("real_name") or info.get("full_name") or (f"@{info['username']}" if info.get("username") else user_id)
        result.append({"user_id": uid, "name": name})
    return result


def remove_legacy_employee(user_id: int) -> bool:
    """Убирает из known_users.json того, кто писал боту, но не выбрал
    проект/роль для мониторинга (тестовый аккаунт, ошибка и т.п.). Если
    человек напишет боту снова — онбординг запустится заново с чистого
    листа. Возвращает False, если такой записи уже нет."""
    uid = str(user_id)
    if uid not in _known_users:
        return False
    del _known_users[uid]
    _seen_in_chats.pop(uid, None)
    _save()
    return True


async def request_registration(bot, user_id: int) -> None:
    """Владелец просит сотрудника, который уже писал боту, но не выбрал
    проект/роль для мониторинга, — пройти этот шаг сейчас. Тот же диалог,
    что и при обычном /start, только запущен владельцем, а не самим
    сотрудником при первом сообщении."""
    messenger = _DirectMessenger(bot, user_id)
    await _start_project_selection(
        messenger,
        str(user_id),
        intro="Владелец просит выбрать проект и роль для модуля мониторинга конкурентов.\n\n",
    )


async def on_employee_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Любое личное сообщение сотрудника боту открывает возможность писать
    ему в личку (раздел 4: "/start или любое сообщение"), а для новых
    пользователей запускает онбординг: проект → имя → роль."""
    user = update.effective_user
    user_id = str(user.id)

    if user_id in _awaiting_name:
        await _handle_name_reply(update)
        return

    pending = _pending_onboarding.get(user_id)
    if pending and pending.get("step") == "role":
        await update.effective_message.reply_text("Выбери роль кнопкой выше 🙂")
        return

    _known_users.setdefault(user_id, {})
    _known_users[user_id]["username"] = user.username
    _known_users[user_id]["full_name"] = user.full_name
    _known_users[user_id]["onboarded"] = True
    _save()

    if not is_owner(user.id) and get_manager(user.id) is None:
        await _start_project_selection(update.effective_message, user_id)
        return



async def on_project_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    pending = _pending_onboarding.get(user_id)
    if not pending or pending.get("step") != "project":
        await query.answer()
        return

    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Проект не найден", show_alert=True)
        return

    pending["market_id"] = market_id
    pending["market_name"] = market["name"]
    pending["step"] = "name"
    await query.answer()
    await query.edit_message_text(f"Проект: {market['name']}")

    _awaiting_name.add(user_id)
    await query.message.reply_text("Как тебя зовут?")


async def _handle_name_reply(update: Update) -> None:
    user_id = str(update.effective_user.id)
    real_name = update.effective_message.text.strip()

    _awaiting_name.discard(user_id)
    _known_users.setdefault(user_id, {})
    _known_users[user_id]["real_name"] = real_name
    _save()

    pending = _pending_onboarding.get(user_id)
    if pending and pending.get("step") == "name":
        pending["real_name"] = real_name
        pending["step"] = "role"
        await update.effective_message.reply_text(
            f"Спасибо! Записал тебя как «{real_name}».\n\nКакая у тебя роль?",
            reply_markup=_role_choice_keyboard(),
        )
        return

    await update.effective_message.reply_text(f"Спасибо! Записал тебя как «{real_name}».")


async def on_role_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    pending = _pending_onboarding.get(user_id)
    if not pending or pending.get("step") != "role":
        await query.answer()
        return

    index = int(query.data.split(":", 1)[1])
    if index < 0 or index >= len(MANAGER_POSITIONS):
        await query.answer()
        return
    position = MANAGER_POSITIONS[index]

    await query.answer()
    await query.edit_message_text(f"Роль: {position}")

    register_manager(
        telegram_user_id=int(user_id),
        name=pending["real_name"],
        position=position,
        market_id=pending["market_id"],
    )
    market_id = pending["market_id"]
    market_name = pending["market_name"]
    del _pending_onboarding[user_id]

    supervisor = get_market_supervisor(market_id, exclude_telegram_user_id=int(user_id))
    if supervisor:
        await query.message.reply_text(
            f"Заявка отправлена управляющему проекта «{market_name}»: «{position}». "
            "После его одобрения её увидит Рома — как только подтвердит, расскажу подробнее, как я работаю."
        )
        await notify_supervisor_of_pending(
            context, int(user_id), pending["real_name"], position, market_name, supervisor["telegram_user_id"]
        )
    else:
        await query.message.reply_text(
            f"Заявка отправлена Роме: «{position}» на проекте «{market_name}». "
            "Как только подтвердит — расскажу подробнее, как я работаю."
        )
        await notify_owners_of_pending(context, int(user_id), pending["real_name"], position, market_name)


def _approval_keyboard(telegram_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"mgr_approve:{telegram_user_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"mgr_reject:{telegram_user_id}"),
            ]
        ]
    )


def _supervisor_approval_keyboard(telegram_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"mgr_svapprove:{telegram_user_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"mgr_svreject:{telegram_user_id}"),
            ]
        ]
    )


async def notify_owners_of_pending(
    context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int, name: str, position: str, market_name: str
) -> None:
    """Финальный шаг согласования — владелец. Вызывается либо сразу (если
    на рынке нет Управляющего), либо после того, как Управляющий рынка
    одобрил заявку (см. notify_supervisor_of_pending, bot.manager_admin.
    on_manager_supervisor_approve)."""
    if position == "Стажёр":
        approve_note = "При подтверждении блоки бота не выдаются — вместо этого начнётся программа онбординга."
    else:
        approve_note = "При подтверждении будут выданы блоки бота по умолчанию — доступные блоки можно изменить позже через /employees."
    text = (
        f"🆕 Новая заявка на доступ к боту:\n"
        f"{name} — «{position}», проект «{market_name}».\n"
        f"Telegram ID: {telegram_user_id}\n\n"
        f"{approve_note}"
    )
    for owner_id in OWNER_TELEGRAM_IDS:
        try:
            await context.bot.send_message(
                chat_id=int(owner_id), text=text, reply_markup=_approval_keyboard(telegram_user_id)
            )
        except Exception:
            logging.getLogger(__name__).exception("Не удалось уведомить владельца %s о новой заявке", owner_id)


async def notify_supervisor_of_pending(
    context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int, name: str, position: str, market_name: str, supervisor_id: int
) -> None:
    """Первый шаг согласования, когда на рынке есть Управляющий: заявка
    сначала идёт ему, и только после его одобрения — владельцу
    (notify_owners_of_pending). Без Управляющего на рынке шаг пропускается,
    заявка сразу идёт владельцу — см. on_role_choice."""
    text = (
        f"🆕 Новая заявка на доступ к боту на твоём проекте «{market_name}»:\n"
        f"{name} — «{position}».\n\n"
        "Сначала нужно твоё одобрение, затем окончательно подтвердит Рома."
    )
    try:
        await context.bot.send_message(chat_id=supervisor_id, text=text, reply_markup=_supervisor_approval_keyboard(telegram_user_id))
    except Exception:
        logging.getLogger(__name__).exception("Не удалось уведомить управляющего %s о новой заявке", supervisor_id)


def find_user_id_by_username(username: str) -> int | None:
    """Ищет Telegram ID по username среди тех, кого бот уже где-то видел
    (писали в привязанной группе или в личку боту) — Telegram не отдаёт
    ботам ID по username напрямую, обойти это нельзя, только через уже
    накопленный known_users.json."""
    normalized = username.lstrip("@").strip().lower()
    user_id = next(
        (uid for uid, info in _known_users.items() if (info.get("username") or "").lower() == normalized),
        None,
    )
    return int(user_id) if user_id is not None else None


async def on_force_onboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда Романа в личке: /onboard <username> — принудительно онбордит
    сотрудника без ожидания, что он сам напишет боту. Работает только для
    тех, кого бот уже где-то видел (хотя бы раз написал в привязанной
    группе) — Telegram не отдаёт ID по username без этого, обойти нельзя."""
    if str(update.effective_user.id) != str(ROMAN_TELEGRAM_ID):
        return

    if not context.args:
        await update.effective_message.reply_text("Использование: /onboard <username>\nНапример: /onboard ivan_petrov")
        return

    username = context.args[0].lstrip("@").strip().lower()
    user_id_int = find_user_id_by_username(username)
    user_id = str(user_id_int) if user_id_int is not None else None

    if user_id is None:
        await update.effective_message.reply_text(
            f"Не нашёл @{username} среди тех, кто писал в привязанных группах. "
            "Telegram не даёт ботам искать пользователя по username напрямую — "
            "попросите сотрудника один раз написать что-нибудь в рабочем чате "
            "или боту в личку, и повторите команду."
        )
        return

    _known_users[user_id]["onboarded"] = True
    _save()

    full_name = _known_users[user_id].get("real_name") or _known_users[user_id].get("full_name") or username
    await update.effective_message.reply_text(f"✅ {full_name} (@{username}) зарегистрирован.")


_COMMANDS_TEXT = (
    "Доступные команды:\n"
    "/employees — сотрудники бота по проекту: роли, блоки\n"
    "/chats — чаты по проекту: рабочий и рассылки отчётов\n"
    "/add_project — добавить проект/точку Surf\n"
    "/market_settings — настройка рынка: ПДн, финпартнёры, чаты отчётов\n"
    "/dashboard_market — дашборд по рынку (или агрегированно по всем)\n"
    "/manager_report — ежемесячный отчет управляющего\n"
    "/manager_kpi_plan — планы гостевых KPI по спотам\n"
    "/view_reports — отчёты по сменам\n"
    "/spot_events — мероприятия проекта на неделю\n"
    "/shift_dashboard — дашборд смен\n"
    "/founders_report — отчёт для учредителей\n"
    "/revenue — выручка, план/факт и P&L\n"
    "/messaging — написать сотруднику, в чат или разослать группе\n"
    "/register_project <проект> — привязать текущую группу к проекту (вызывать внутри группы)\n"
    "/onboard <username> — принудительно онбордить сотрудника"
)


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.menu import allowed, show_menu
    if allowed(update.effective_user.id):
        await show_menu(update.effective_message, update.effective_user.id)
        return
    if str(update.effective_user.id) == str(ROMAN_TELEGRAM_ID):
        await update.effective_message.reply_text(
            "Привет, Роман! Выбери нужный раздел мониторинга и отчётности в меню.\n\n"
            + _COMMANDS_TEXT
        )
        return
    await on_employee_message(update, context)


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.menu import allowed, show_menu
    if allowed(update.effective_user.id):
        await show_menu(update.effective_message, update.effective_user.id)
        return
    if str(update.effective_user.id) != str(ROMAN_TELEGRAM_ID):
        return
    await update.effective_message.reply_text(_COMMANDS_TEXT)
