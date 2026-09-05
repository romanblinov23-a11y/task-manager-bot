import re
from datetime import timedelta

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import ROMAN_TELEGRAM_ID
from config.timeutil import fmt_date, parse_date
from config.timeutil import today as tz_today
from monitoring.managers import (
    get_managers_for_market,
    get_market_supervisor,
    get_markets_for_manager,
    is_meetings_editor,
    is_owner,
    meetings_enabled_for_market,
)
from monitoring.markets import get_market, list_markets
from monitoring.meetings import (
    create_or_get_instance,
    get_instance,
    get_meeting_schedule,
    list_meeting_schedules_for_weekday,
    reschedule_instance,
    save_instance_agenda,
    set_instance_invite_roman,
    set_instance_status,
    set_meeting_schedule,
)
from monitoring.shift_reports import get_report_chat

_WEEKDAY_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

_MEETING_TYPE_LABELS = {"team": "Управляющий + вся команда", "managers": "Управляющий + менеджеры"}

# telegram_user_id (str) -> {"market_id": int, "meeting_type": str} — ждём
# «день недели время» для настройки ритма собрания
_awaiting_schedule_input: dict[str, dict] = {}

# telegram_user_id (str) -> {"instance_id": int} — ждём текст повестки
_awaiting_agenda: dict[str, dict] = {}

# telegram_user_id (str) -> {"instance_id": int} — ждём новую дату/время переноса
_awaiting_postpone: dict[str, dict] = {}


def _is_market_supervisor(user_id: int, market_id: int) -> bool:
    supervisor = get_market_supervisor(market_id)
    return bool(supervisor and supervisor["telegram_user_id"] == user_id)


def _available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _parse_schedule_input(text: str) -> tuple[int, str] | None:
    parts = text.strip().split()
    if len(parts) != 2:
        return None
    weekday_raw, time_raw = parts
    weekday_raw = weekday_raw.strip().lower()
    if weekday_raw not in _WEEKDAY_RU:
        return None
    if not re.match(r"^\d{1,2}:\d{2}$", time_raw):
        return None
    return _WEEKDAY_RU.index(weekday_raw), time_raw


def _parse_postpone_input(text: str) -> tuple[str, str] | None:
    parts = text.strip().rsplit(maxsplit=1)
    if len(parts) != 2:
        return None
    date_raw, time_raw = parts
    if not re.match(r"^\d{1,2}:\d{2}$", time_raw):
        return None
    date_iso = parse_date(date_raw)
    if not date_iso:
        return None
    return date_iso, time_raw


def _meeting_type_keyboard(market_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"meetsched_type:{market_id}:{key}")] for key, label in _MEETING_TYPE_LABELS.items()]
    )


async def on_set_meeting_schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_meeting_schedule — Управляющий (или владелец) задаёт
    еженедельный ритм (день недели + время) для одного или обоих типов
    собраний на своём рынке."""
    user = update.effective_user
    if not is_meetings_editor(user.id):
        await update.effective_message.reply_text(
            "Настраивать ритм собраний может только владелец или Управляющий с выданным блоком «Собрания»."
        )
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await update.effective_message.reply_text(
            f"Рынок: {markets[0]['name']}. Какое собрание настраиваем?", reply_markup=_meeting_type_keyboard(markets[0]["id"])
        )
        return

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"meetsched_market:{m['id']}")] for m in markets])
    await update.effective_message.reply_text("По какому рынку настраиваем собрания?", reply_markup=keyboard)


async def on_meeting_schedule_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_meetings_editor(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    allowed_ids = {m["id"] for m in _available_markets(query.from_user.id)}
    if not market or market_id not in allowed_ids:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}. Какое собрание настраиваем?", reply_markup=_meeting_type_keyboard(market_id))


async def on_meeting_schedule_type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_meetings_editor(query.from_user.id):
        await query.answer()
        return
    _, market_id_str, meeting_type = query.data.split(":")
    market_id = int(market_id_str)
    market = get_market(market_id)
    allowed_ids = {m["id"] for m in _available_markets(query.from_user.id)}
    if not market or market_id not in allowed_ids:
        await query.answer("Рынок не найден", show_alert=True)
        return

    await query.answer()
    current = get_meeting_schedule(market_id, meeting_type)
    current_note = f"\n\nСейчас: {_WEEKDAY_RU[current['weekday']].capitalize()} {current['time']}" if current else ""
    _awaiting_schedule_input[str(query.from_user.id)] = {"market_id": market_id, "meeting_type": meeting_type}
    await query.edit_message_text(
        f"«{_MEETING_TYPE_LABELS[meeting_type]}» на «{market['name']}».\n\n"
        f"Когда обычно проходит? День недели и время одним сообщением, например: понедельник 15:00{current_note}"
    )


async def on_meeting_schedule_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает «день недели время» для ритма собрания. Возвращает True,
    если сообщение обработано — по конвенции остальных claim-хендлеров в
    on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_schedule_input.get(owner_id)
    if not state:
        return False

    text = update.effective_message.text or ""
    parsed = _parse_schedule_input(text)
    if not parsed:
        await update.effective_message.reply_text("🤔 Не понял. Формат: день недели и время через пробел, например: понедельник 15:00")
        return True

    weekday, time_str = parsed
    del _awaiting_schedule_input[owner_id]
    set_meeting_schedule(state["market_id"], state["meeting_type"], weekday, time_str)
    market = get_market(state["market_id"])
    await update.effective_message.reply_text(
        f"✅ «{_MEETING_TYPE_LABELS[state['meeting_type']]}» на «{market['name']}» — теперь {_WEEKDAY_RU[weekday].capitalize()} {time_str}."
    )
    return True


def _confirm_keyboard(instance_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, состоится", callback_data=f"meet_confirm:{instance_id}")],
            [InlineKeyboardButton("❌ Отменяется", callback_data=f"meet_cancel:{instance_id}")],
            [InlineKeyboardButton("📅 Переносится", callback_data=f"meet_postpone:{instance_id}")],
        ]
    )


async def send_meeting_confirmations(bot: Bot) -> None:
    """10:00 каждый день (см. main.py) — если завтра по настроенному ритму
    рынка должно быть собрание, заводит экземпляр (если ещё нет) и просит
    Управляющего подтвердить/отменить/перенести. Рынки без Управляющего с
    выданным блоком «Собрания» пропускаются (meetings_enabled_for_market)."""
    tomorrow = tz_today() + timedelta(days=1)
    tomorrow_iso = tomorrow.isoformat()
    for schedule in list_meeting_schedules_for_weekday(tomorrow.weekday()):
        market_id = schedule["market_id"]
        if not meetings_enabled_for_market(market_id):
            continue
        market = get_market(market_id)
        supervisor = get_market_supervisor(market_id)
        if not market or not supervisor:
            continue

        instance = create_or_get_instance(market_id, schedule["meeting_type"], tomorrow_iso, schedule["time"])
        if instance["status"] != "pending_confirmation":
            continue

        label = _MEETING_TYPE_LABELS[schedule["meeting_type"]]
        try:
            await bot.send_message(
                chat_id=supervisor["telegram_user_id"],
                text=(
                    f"📅 Завтра ({fmt_date(tomorrow_iso)}) должно быть собрание «{label}» на «{market['name']}» "
                    f"в {schedule['time']}. Оно состоится?"
                ),
                reply_markup=_confirm_keyboard(instance["id"]),
            )
        except Exception:
            pass


def _invite_roman_keyboard(instance_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Да", callback_data=f"meet_inviteroman:{instance_id}:yes"), InlineKeyboardButton("Нет", callback_data=f"meet_inviteroman:{instance_id}:no")]]
    )


async def _ask_invite_roman(message, instance_id: int) -> None:
    await message.reply_text("Нужно ли на этом собрании присутствие Ромы?", reply_markup=_invite_roman_keyboard(instance_id))


async def on_meeting_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    instance_id = int(query.data.split(":", 1)[1])
    instance = get_instance(instance_id)
    if not instance:
        await query.answer("Собрание не найдено", show_alert=True)
        return
    if not (is_owner(query.from_user.id) or _is_market_supervisor(query.from_user.id, instance["market_id"])):
        await query.answer()
        return

    set_instance_status(instance_id, "confirmed")
    await query.answer("Отмечено")
    await query.edit_message_reply_markup(reply_markup=None)
    await _ask_invite_roman(query.message, instance_id)


async def on_meeting_invite_roman_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, instance_id_str, choice = query.data.split(":")
    instance_id = int(instance_id_str)
    instance = get_instance(instance_id)
    if not instance:
        await query.answer("Собрание не найдено", show_alert=True)
        return
    if not (is_owner(query.from_user.id) or _is_market_supervisor(query.from_user.id, instance["market_id"])):
        await query.answer()
        return

    set_instance_invite_roman(instance_id, choice == "yes")
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    _awaiting_agenda[str(query.from_user.id)] = {"instance_id": instance_id}
    await query.message.reply_text("Составь повестку собрания одним сообщением:")


async def on_meeting_agenda_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текст повестки и сразу рассылает собрание нужной аудитории.
    Возвращает True, если сообщение обработано — по конвенции остальных
    claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_agenda.get(owner_id)
    if not state:
        return False

    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("🤔 Повестка не может быть пустой — напиши хотя бы коротко:")
        return True

    del _awaiting_agenda[owner_id]
    save_instance_agenda(state["instance_id"], text)
    set_instance_status(state["instance_id"], "dispatched")
    instance = get_instance(state["instance_id"])
    await _dispatch_meeting(context.bot, instance)
    await update.effective_message.reply_text("✅ Повестка сохранена, разослал всем нужным людям.")
    return True


async def on_meeting_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    instance_id = int(query.data.split(":", 1)[1])
    instance = get_instance(instance_id)
    if not instance:
        await query.answer("Собрание не найдено", show_alert=True)
        return
    if not (is_owner(query.from_user.id) or _is_market_supervisor(query.from_user.id, instance["market_id"])):
        await query.answer()
        return

    set_instance_status(instance_id, "cancelled")
    await query.answer("Отменено")
    await query.edit_message_reply_markup(reply_markup=None)
    instance = get_instance(instance_id)
    await _dispatch_meeting(context.bot, instance)
    await query.message.reply_text("Хорошо, сообщил об отмене.")


async def on_meeting_postpone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    instance_id = int(query.data.split(":", 1)[1])
    instance = get_instance(instance_id)
    if not instance:
        await query.answer("Собрание не найдено", show_alert=True)
        return
    if not (is_owner(query.from_user.id) or _is_market_supervisor(query.from_user.id, instance["market_id"])):
        await query.answer()
        return

    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    _awaiting_postpone[str(query.from_user.id)] = {"instance_id": instance_id}
    await query.message.reply_text("На какую дату и время переносим? Одним сообщением, например: 15.09 16:00")


async def on_meeting_postpone_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает новую дату/время переноса, дальше — тот же путь, что и при
    подтверждении (вопрос про Рому, потом повестка). Возвращает True, если
    сообщение обработано — по конвенции остальных claim-хендлеров в
    on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_postpone.get(owner_id)
    if not state:
        return False

    text = update.effective_message.text or ""
    parsed = _parse_postpone_input(text)
    if not parsed:
        await update.effective_message.reply_text("🤔 Не понял дату/время. Напиши одним сообщением, например: 15.09 16:00")
        return True

    del _awaiting_postpone[owner_id]
    new_date, new_time = parsed
    reschedule_instance(state["instance_id"], new_date, new_time)
    set_instance_status(state["instance_id"], "confirmed")
    await update.effective_message.reply_text(f"Перенёс на {fmt_date(new_date)} {new_time}.")
    await _ask_invite_roman(update.effective_message, state["instance_id"])
    return True


_TEAM_ATTENDANCE_NOTE = (
    "🤝 Напоминаю: собрание — часть рабочего процесса и командообразования. Явка обязательна, так мы станем "
    "сильнее гораздо быстрее. Если у тебя есть уважительная причина отсутствия — сообщи её здесь в чате, "
    "чтобы вся команда была в курсе и не беспокоилась о твоём отсутствии."
)

_MANAGERS_ATTENDANCE_NOTE = (
    "🙏 Понимаю, что ситуации могут быть разными и, возможно, у тебя есть уважительная причина отсутствия. "
    "Если таковая имеется — дай знать Управляющему в лс. Но помни: регулярность наших встреч приводит нас "
    "к ощутимым результатам — и гораздо быстрее! 🚀"
)


def _render_meeting_message(instance: dict, recipient_name: str | None = None) -> str:
    date_time = f"{fmt_date(instance['meeting_date'])} в {instance['meeting_time']}"
    is_team = instance["meeting_type"] == "team"

    if instance["status"] == "cancelled":
        if is_team:
            return (
                f"👋 Привет, Серферы! Собрание, которое планировалось {date_time}, отменяется ❌ — как только "
                "появится новая дата, сообщу отдельно здесь в чате. 🙌"
            )
        return (
            f"👋 Привет, {recipient_name}! Управляющий сообщает: запланированное собрание {date_time} "
            "отменяется ❌. Как только появится новая дата — сообщим отдельно."
        )

    if instance.get("rescheduled"):
        if is_team:
            intro = f"👋 Привет, Серферы! Собрание переносится 🔄 — новая дата и время: {date_time}. Чтобы вам было удобно подготовиться, вот повестка:"
        else:
            intro = (
                f"👋 Привет, {recipient_name}! Управляющий перенёс собрание 🔄 — новая дата и время: {date_time}. "
                "Вот повестка, чтобы оно прошло эффективнее:"
            )
    elif is_team:
        intro = f"👋 Привет, Серферы! Напоминаю, что у нас с вами запланировано собрание {date_time}. Чтобы вам было удобно к нему подготовиться, вот повестка:"
    else:
        intro = f"👋 Привет, {recipient_name}! Управляющий подтвердил запланированное собрание {date_time} ✅, вот повестка, чтобы оно прошло эффективнее:"

    note = _TEAM_ATTENDANCE_NOTE if is_team else _MANAGERS_ATTENDANCE_NOTE
    return f"{intro}\n\n{instance['agenda']}\n\n{note}"


async def _dispatch_meeting(bot: Bot, instance: dict) -> None:
    """Рассылает собрание нужной аудитории: команде — в чат, куда уходят
    отчёты для команды; менеджерам — лично в личку каждому активному
    менеджеру рынка (кроме самого Управляющего), с обращением по имени.
    Отдельно, если запрошено — приглашает Романа с той же повесткой."""
    market = get_market(instance["market_id"])
    if not market:
        return

    if instance["meeting_type"] == "team":
        team_chat = get_report_chat(instance["market_id"], "team")
        if team_chat:
            try:
                await bot.send_message(
                    chat_id=team_chat["chat_id"],
                    text=_render_meeting_message(instance),
                    message_thread_id=team_chat.get("message_thread_id"),
                )
            except Exception:
                pass
    else:
        supervisor = get_market_supervisor(instance["market_id"])
        supervisor_id = supervisor["telegram_user_id"] if supervisor else None
        for manager in get_managers_for_market(instance["market_id"]):
            if manager["status"] != "active" or manager["telegram_user_id"] == supervisor_id:
                continue
            try:
                await bot.send_message(
                    chat_id=manager["telegram_user_id"], text=_render_meeting_message(instance, manager["name"])
                )
            except Exception:
                pass

    if instance["status"] != "cancelled" and instance.get("invite_roman"):
        supervisor = get_market_supervisor(instance["market_id"])
        supervisor_name = supervisor["name"] if supervisor else "Управляющий"
        label = _MEETING_TYPE_LABELS[instance["meeting_type"]]
        date_time = f"{fmt_date(instance['meeting_date'])} в {instance['meeting_time']}"
        try:
            await bot.send_message(
                chat_id=ROMAN_TELEGRAM_ID,
                text=(
                    f"🙋 {supervisor_name} просит твоего присутствия на собрании «{label}» на «{market['name']}»: "
                    f"{date_time}.\n\nПовестка:\n{instance['agenda']}"
                ),
            )
        except Exception:
            pass
