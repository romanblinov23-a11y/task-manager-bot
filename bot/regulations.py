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

MONITORING_REGULATION_SUPERVISOR = """📗 Регламент 2: мониторинг конкурентов (Управляющий)

Весь мониторинг рынка/проекта завязан на тебя — вот сквозной порядок действий.

1. Дни мониторинга — /schedule
Выбери дни недели, когда мне присылать тебе задание на мониторинг. В остальные дни я не напоминаю.

2. Игроки рынка — /add_competitor
Добавь всех конкурентов и нашу точку Surf: код, название, адрес, формат (навынос / посадка / кофе+кухня с полноценной посадкой). Факторы формирования (продукт, атмосфера, сервис, сила бренда, кадры) заполняются один раз при добавлении — дальше их трогать не нужно, пока в них правда что-то не изменилось (ремонт, новое меню, смена команды и т.п.), об этом — в пункте 4. Точка закрылась — /close_competitor (снова откроется той же командой, если ошиблись).

3. Задание на мониторинг
В выбранный день я пишу тебе одному (не всем сотрудникам проекта) и предлагаю выбор: пойти самому или делегировать. Делегируешь — я сам напишу выбранному сотруднику и пришлю ему кнопку начать мониторинг, тебе пересылать ничего не нужно.

4. Сам мониторинг — /monitoring
По каждой точке: показатель (среднее число чеков в день — считается как (номер чека сейчас − номер чека при прошлом визите) ÷ число дней между визитами; арифметику делаю я, тебе нужно только назвать текущий номер чека), дата снятия, заметные изменения. Если в факторах реально что-то поменялось — скажи об этом текстом, когда спрошу: я сам разберу и предложу, что обновить в карточке конкурента, ты подтвердишь.

5. Результат — /dashboard_market
Ёмкость рынка, доля каждой точки, тренды, аномалии (отклонение больше 20% от скользящей нормы) и что могло на них повлиять.

Правило раз в неделю: мониторинг одного рынка нельзя проводить чаще раза в 7 дней — я не дам запустить его раньше срока. Пропустили день — спрошу то же самое в следующий раз, ничего не потеряется."""

MONITORING_REGULATION_STAFF = """📗 Регламент 2: мониторинг конкурентов

Список конкурентов, расписание и факторы по рынку ведёт Управляющий твоего проекта — это не твоя задача.

Твоя часть: в день мониторинга Управляющий может делегировать поход тебе — тогда я пришлю личное сообщение с кнопкой «Начать мониторинг».

Сам мониторинг — /monitoring
По каждой точке нужно прислать: показатель (среднее число чеков в день — считается как (номер чека сейчас − номер чека при прошлом визите) ÷ число дней между визитами; арифметику делаю я, просто назови текущий номер чека), дату снятия, заметные изменения (ремонт, акция, смена команды и т.п.). Если увидел(а) что-то, что реально меняет расстановку сил на рынке, — скажи об этом текстом, когда спрошу про факторы: я сам разберу и предложу, что обновить, ты подтвердишь.

Посмотреть результат можно в любой момент — /dashboard_market: ёмкость рынка, доля каждой точки, тренды и аномалии.

Правило раз в неделю: мониторинг одного рынка нельзя проводить чаще раза в 7 дней."""


def _monitoring_regulation_for_role(role: str | None) -> str:
    return MONITORING_REGULATION_SUPERVISOR if role == "Управляющий" else MONITORING_REGULATION_STAFF


def get_regulations(role: str | None = None, blocks: list[str] | None = None) -> list[str]:
    """Регламенты, которые выдаются после того, как владелец подтвердил
    заявку и выдал блоки. Текст блока «Задачи» одинаков для всех, а блок
    «Мониторинг» зависит от должности: у Управляющего — весь процесс от
    расписания до делегирования, у остальных ролей — только их часть
    (что делать, когда Управляющий делегировал поход). blocks=None —
    вернуть оба регламента (используется для владельца, у которого нет
    записи в manager)."""
    docs = []
    if blocks is None or "tasks" in blocks:
        docs.append(TASK_TRACKER_REGULATION)
    if blocks is None or "monitoring" in blocks:
        docs.append(_monitoring_regulation_for_role(role))
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
    from monitoring.managers import get_acknowledged_blocks, get_manager, get_manager_blocks

    manager = get_manager(telegram_user_id)
    role = manager["position"] if manager else None
    granted = get_manager_blocks(telegram_user_id)
    acknowledged = set(get_acknowledged_blocks(telegram_user_id))
    pending = [b for b in AVAILABLE_BLOCKS if b in granted and b not in acknowledged]
    if not pending:
        return False

    block_key = pending[0]
    text = TASK_TRACKER_REGULATION if block_key == "tasks" else _monitoring_regulation_for_role(role)
    await _send_long_text(message, text, final_reply_markup=_ack_keyboard(block_key))
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
