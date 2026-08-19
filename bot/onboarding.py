import json
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.ext import ContextTypes

from config.chats import get_chats_for_project, get_project_for_chat
from config.settings import OWNER_TELEGRAM_IDS, ROMAN_CHAT_NAME, ROMAN_TELEGRAM_ID
from bot.regulations import send_regulations
from monitoring.constants import MANAGER_POSITIONS
from monitoring.managers import get_manager, is_owner, register_manager
from monitoring.markets import get_market, list_markets
from tasks.tasks import get_all_tasks, update_task

_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "known_users.json"

# user_id (str) -> {"username": str|None, "full_name": str, "real_name": str|None, "onboarded": bool}
_known_users: dict[str, dict] = {}
# user_id (str) -> [chat_id, ...] — группы, где этот человек писал сообщения
_seen_in_chats: dict[str, list[int]] = {}
# "{project}|{normalized_assignee}" -> telegram_id — уже разрешённые сопоставления
_resolved: dict[str, int] = {}
# user_id (str), которым сейчас задан вопрос "как тебя зовут?"
_awaiting_name: set[str] = set()
# user_id (str) -> {"step": "project"|"name"|"role", "market_id", "market_name", "real_name"}
# состояние диалога первичного онбординга (проект → имя → роль)
_pending_onboarding: dict[str, dict] = {}


def _load() -> None:
    global _known_users, _seen_in_chats, _resolved
    if _STORE_PATH.exists():
        data = json.loads(_STORE_PATH.read_text())
        _known_users = data.get("known_users", {})
        _seen_in_chats = data.get("seen_in_chats", {})
        _resolved = data.get("resolved", {})


def _save() -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps(
            {"known_users": _known_users, "seen_in_chats": _seen_in_chats, "resolved": _resolved},
            ensure_ascii=False,
            indent=2,
        )
    )


_load()


def _normalize(name: str) -> str:
    return name.strip().lower()


def _name_matches(candidate: str, user: dict) -> bool:
    """Сравнивает имя assignee с известными данными о сотруднике. real_name
    (то, как сотрудник сам назвался при онбординге) приоритетнее
    full_name из Telegram-профиля — у многих там не настоящее имя."""
    candidate_norm = _normalize(candidate)
    if not candidate_norm:
        return False

    for field in ("real_name", "full_name"):
        value_norm = _normalize(user.get(field) or "")
        if not value_norm:
            continue
        if candidate_norm == value_norm:
            return True
        first_word = value_norm.split()[0] if value_norm else ""
        if candidate_norm == first_word:
            return True

    username = user.get("username")
    if username and candidate_norm == _normalize(username):
        return True
    return False


def find_homonyms(project: str, assignee_name: str) -> list[dict]:
    """Все онбордившиеся сотрудники, видимые в чатах этого проекта, чьё имя
    совпадает с assignee_name. Если их больше одного — коллизия одинаковых
    имён, которую нельзя разрешать автоматически (см. обсуждение раздела 9.1
    в чате с Романом 2026-06-29)."""
    project_chats = set(get_chats_for_project(project))
    candidates = []
    for user_id, chats in _seen_in_chats.items():
        if not project_chats.intersection(chats):
            continue
        user = _known_users.get(user_id, {})
        if not user.get("onboarded"):
            continue
        if _name_matches(assignee_name, user):
            candidates.append(
                {
                    "user_id": int(user_id),
                    "full_name": user.get("real_name") or user.get("full_name") or "",
                    "username": user.get("username") or "",
                }
            )
    return candidates


def _try_match_and_backfill(user_id: int) -> list[tuple[str, str]]:
    """Сопоставляет онбордившегося сотрудника с именами assignee в таблицах
    проектов, где его видели в группе, и проставляет assignee_telegram_id
    на уже существующих задачах (раздел 4, 9.1 PROJECT_SPEC.md). При
    коллизии одинаковых имён автоматическое назначение пропускается — его
    разрешит Роман явно при подтверждении следующей задачи на это имя."""
    user = _known_users.get(str(user_id))
    if not user:
        return []

    chats = _seen_in_chats.get(str(user_id), [])
    projects = {get_project_for_chat(chat_id) for chat_id in chats}
    projects.discard(None)

    matched: list[tuple[str, str]] = []
    for project in projects:
        for task in get_all_tasks(project):
            assignee = task.get("assignee", "")
            if not assignee or task.get("assignee_telegram_id"):
                continue
            if not _name_matches(assignee, user):
                continue
            if len(find_homonyms(project, assignee)) > 1:
                continue
            update_task(project, task["task_id"], assignee_telegram_id=str(user_id))
            matched.append((project, assignee))
            _resolved[f"{project}|{_normalize(assignee)}"] = user_id
    if matched:
        _save()
    return matched


def find_telegram_id_for_assignee(project: str, assignee_name: str) -> int | None:
    """Используется при создании новой задачи, чтобы сразу проставить
    assignee_telegram_id, если сотрудник уже онбордился ранее."""
    return _resolved.get(f"{project}|{_normalize(assignee_name)}")


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
    """Все сотрудники, прошедшие онбординг, с их данными — для команды /onboarded."""
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
    """Пассивно запоминает, кто писал в группе — нужно для последующего
    сопоставления Telegram-аккаунта с именем в таблице при онбординге.
    Для Романа сразу ставим onboarded=True и real_name из ROMAN_CHAT_NAME,
    чтобы задачи на него резолвились через нормальный механизм сопоставления
    (а не через хардкод по вариантам имени, который путает однофамильцев)."""
    if user is None or user.is_bot:
        return

    user_id = str(user.id)
    is_new_chat = chat_id not in _seen_in_chats.get(user_id, [])

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

    if _known_users[user_id].get("onboarded") and is_new_chat:
        _try_match_and_backfill(user.id)


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
    text = intro or "Привет! Я Енисей — бот-менеджер задач и мониторинга конкурентов.\n\n"
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
    """Сотрудники, которые уже онбордились для задач (известны боту, есть в
    known_users.json/список /onboarded), но не завершили выбор проекта/роли
    для модуля мониторинга — у них нет записи в manager. Владелец видит их
    в /managers и может донбордить вручную, не дожидаясь, пока они сами
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
    already_contacted = _known_users[user_id].get("onboarded", False)
    _known_users[user_id]["onboarded"] = True
    _save()

    if not is_owner(user.id):
        manager = get_manager(user.id)
        if manager is None or manager["status"] == "removed":
            await _start_project_selection(update.effective_message, user_id)
            return

    if already_contacted:
        return

    await _greet_after_onboarding(update.effective_message, user.id)


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
    await _greet_after_onboarding(update.effective_message, int(user_id))


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
    market_name = pending["market_name"]
    del _pending_onboarding[user_id]

    await query.message.reply_text(
        f"Заявка отправлена владельцу: «{position}» на проекте «{market_name}». "
        "Как только подтвердят — станут доступны /schedule, /add_competitor и /monitoring."
    )
    await _notify_owners_of_pending(context, int(user_id), pending["real_name"], position, market_name)
    await send_regulations(query.message, position)
    await _greet_after_onboarding(query.message, int(user_id))


def _approval_keyboard(telegram_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"mgr_approve:{telegram_user_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"mgr_reject:{telegram_user_id}"),
            ]
        ]
    )


async def _notify_owners_of_pending(
    context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int, name: str, position: str, market_name: str
) -> None:
    text = (
        f"🆕 Новая заявка на доступ к боту:\n"
        f"{name} — «{position}», проект «{market_name}».\n"
        f"Telegram ID: {telegram_user_id}\n\n"
        "При подтверждении будут выданы оба блока бота (задачи и мониторинг) — доступные блоки "
        "можно изменить позже через /managers."
    )
    for owner_id in OWNER_TELEGRAM_IDS:
        try:
            await context.bot.send_message(
                chat_id=int(owner_id), text=text, reply_markup=_approval_keyboard(telegram_user_id)
            )
        except Exception:
            logging.getLogger(__name__).exception("Не удалось уведомить владельца %s о новой заявке", owner_id)


async def _greet_after_onboarding(message, user_id: int) -> None:
    matched = _try_match_and_backfill(user_id)
    if matched:
        await message.reply_text(
            "Вижу, что на тебя уже есть задача в таблице — буду писать сюда, если "
            "понадобится спросить про статус или сроки."
        )
    else:
        await message.reply_text(
            "Как только появится задача на твоё имя, напишу сюда, чтобы спросить про статус."
        )


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
    user_id = next(
        (uid for uid, info in _known_users.items() if (info.get("username") or "").lower() == username),
        None,
    )

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
    matched = _try_match_and_backfill(int(user_id))
    if matched:
        details = "; ".join(f"{project}: {name}" for project, name in matched)
        await update.effective_message.reply_text(f"✅ {full_name} (@{username}) онбордён. Привязаны задачи — {details}.")
    else:
        await update.effective_message.reply_text(
            f"✅ {full_name} (@{username}) онбордён. Подходящих задач пока не нашёл — "
            "привяжутся автоматически, когда появятся."
        )


_COMMANDS_TEXT = (
    "Доступные команды:\n"
    "/status <проект> — открытые задачи по проекту\n"
    "/employee <имя> — задачи сотрудника по всем проектам\n"
    "/stuck — задачи с 2+ переносами или давно без обновления\n"
    "/needhelp — задачи, где просили помощи\n"
    "/onboarded — кто прошёл онбординг и какие чаты привязаны к проектам\n"
    "/weekly — еженедельная аналитика по запросу\n"
    "/register_project <проект> — привязать текущую группу к проекту (вызывать внутри группы)\n"
    "/onboard <username> — принудительно онбордить сотрудника"
)


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) == str(ROMAN_TELEGRAM_ID):
        await update.effective_message.reply_text(
            "Привет, Роман! Готов: пишите сюда задачи и протоколы встреч (текстом или "
            "файлом), либо тегайте меня в рабочих чатах — соберу оттуда договорённости.\n\n"
            + _COMMANDS_TEXT
        )
        return
    await on_employee_message(update, context)


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) != str(ROMAN_TELEGRAM_ID):
        return
    await update.effective_message.reply_text(_COMMANDS_TEXT)
