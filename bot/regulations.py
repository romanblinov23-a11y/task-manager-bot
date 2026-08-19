from telegram import InlineKeyboardButton, InlineKeyboardMarkup

TASK_TRACKER_REGULATION = """📘 Регламент 1: как работает бот-трекер задач

Я — Енисей, бот-менеджер задач. Помогаю Роме следить за статусами задач: слежу за договорённостями в рабочих чатах, личных сообщениях и протоколах встреч, фиксирую их и слежу за сроками.

Как задача попадает на вас:
— вас упомянули в рабочем чате с явным поручением, или
— владелец лично поставил вам задачу.
Ничего делать для этого не нужно — я сам нахожу такие договорённости и фиксирую их.

Что я буду писать вам в личку:
— когда подходит или проходит срок задачи, я спрошу о статусе: выполнено, всё ещё в работе, или нужен перенос;
— отвечайте свободным текстом, я сам разберу ответ;
— если из ответа не понятно, что со статусом, — переспрошу ещё раз, отвечайте по существу, пока не станет ясно.

Если нужен перенос срока — просто укажите новую дату в ответе.
Если нужна помощь — так и напишите, владелец получит уведомление сразу.

Команда /mytasks — посмотреть свои открытые задачи в любой момент.

Важно: чтобы я мог писать вам в личку, нужно хотя бы раз написать мне (вы уже это сделали, раз читаете это сообщение — всё в порядке)."""

MONITORING_REGULATION = """📗 Регламент 2: мониторинг конкурентов

Цель — раз в неделю оценивать интенсивность конкурентов и нашей точки Surf, чтобы считать ёмкость и долю рынка.

Как считать показатель:
На кассовом чеке есть сквозной номер нарастающим итогом.
(последний номер чека − номер при прошлом визите) ÷ число дней между визитами = среднее число чеков в день.
Я арифметику не делаю — только принимаю и сохраняю уже готовое число, которое вы присылаете.

Доступ:
После /start вы указываете проект, имя и роль — заявка уходит владельцу на подтверждение. Часть команд станет доступна только после его "Да".

Основные команды:
/add_competitor — добавить конкурента (или нашу точку Surf) на рынок: код, название, адрес, формат (навынос / посадка / кофе+кухня с полноценной посадкой), при желании сразу показатель и факторы формирования.
/schedule — выбрать дни недели, когда присылать задание на мониторинг.
/monitoring — пройти сам мониторинг: по каждой точке — показатель, дата снятия, заметные изменения (ремонт, акция, смена команды и т.д.).
/dashboard_market — интерактивный отчёт: ёмкость рынка, доля каждой точки, тренды, аномалии и рекомендации.
/close_competitor — закрыть точку, если конкурент закрылся (или открыть заново, если ошиблись).

Правило раз в неделю: мониторинг одного рынка нельзя проводить чаще раза в 7 дней — я не дам запустить его раньше срока.
Если что-то пропустили в мониторинге — я спрошу это же в следующий раз, ничего не потеряется."""


def get_regulations(role: str | None = None, blocks: list[str] | None = None) -> list[str]:
    """Регламенты, которые выдаются после того, как владелец подтвердил
    заявку и выдал блоки. role пока не влияет на контент — все получают
    одно и то же внутри своего блока, но сигнатура уже готова под будущую
    персонализацию текста по должности (Управляющий/Менеджер/Наставник/
    Маркетолог). blocks=None — вернуть оба регламента (используется для
    владельца, у которого нет записи в manager)."""
    docs = []
    if blocks is None or "tasks" in blocks:
        docs.append(TASK_TRACKER_REGULATION)
    if blocks is None or "monitoring" in blocks:
        docs.append(MONITORING_REGULATION)
    return docs


async def _send_long_text(message, text: str, limit: int = 3500, final_reply_markup=None) -> None:
    if len(text) <= limit:
        await message.reply_text(text, reply_markup=final_reply_markup)
        return
    chunks = []
    chunk = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{chunk}\n\n{paragraph}" if chunk else paragraph
        if len(candidate) > limit and chunk:
            chunks.append(chunk)
            chunk = paragraph
        else:
            chunk = candidate
    if chunk:
        chunks.append(chunk)
    for i, part in enumerate(chunks):
        markup = final_reply_markup if i == len(chunks) - 1 else None
        await message.reply_text(part, reply_markup=markup)


async def send_regulations(message, role: str | None = None, blocks: list[str] | None = None) -> None:
    for doc in get_regulations(role, blocks):
        await _send_long_text(message, doc)


# Порядок регламентов по блокам — совпадает с их нумерацией в текстах выше
# (Регламент 1 — tasks, Регламент 2 — monitoring) и с monitoring.constants.AVAILABLE_BLOCKS.
_REGULATION_BY_BLOCK = {"tasks": TASK_TRACKER_REGULATION, "monitoring": MONITORING_REGULATION}


def _ack_keyboard(block_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Ознакомился", callback_data=f"reg_ack:{block_key}")]])


async def send_next_regulation(message, telegram_user_id: int) -> bool:
    """Регламенты выдаются по одному, блок за блоком: следующий не
    отправляется, пока сотрудник не подтвердит прочтение предыдущего кнопкой
    «✅ Ознакомился» (см. bot.manager_admin.on_regulation_ack). Меню команд
    этого блока тоже открывается только после подтверждения. Возвращает
    True, если что-то отправлено — False, если все выданные блоки уже
    подтверждены (нечего слать)."""
    from monitoring.constants import AVAILABLE_BLOCKS
    from monitoring.managers import get_acknowledged_blocks, get_manager_blocks

    granted = get_manager_blocks(telegram_user_id)
    acknowledged = set(get_acknowledged_blocks(telegram_user_id))
    pending = [b for b in AVAILABLE_BLOCKS if b in granted and b not in acknowledged]
    if not pending:
        return False

    block_key = pending[0]
    await _send_long_text(message, _REGULATION_BY_BLOCK[block_key], final_reply_markup=_ack_keyboard(block_key))
    return True


async def on_regulations_command(update, context) -> None:
    """/regulations — перечитать регламенты в любой момент (выдаются
    автоматически один раз после подтверждения владельцем, эта команда —
    просто повтор, с учётом выданных блоков)."""
    from monitoring.managers import get_manager, get_manager_blocks

    manager = get_manager(update.effective_user.id)
    role = manager["position"] if manager else None
    blocks = get_manager_blocks(update.effective_user.id) if manager else None
    await send_regulations(update.effective_message, role, blocks)
