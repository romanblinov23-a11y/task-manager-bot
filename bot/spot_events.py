"""Загрузка мероприятий маркетологом: проект → неделя → список → подтверждение."""
import re
from datetime import date, timedelta
from secrets import token_hex

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config.timeutil import fmt_date
from config.timeutil import today as tz_today
from monitoring.managers import get_manager, get_markets_for_manager, is_owner
from monitoring.markets import list_markets
from monitoring.spot_events import add_events, replace_week

_WEEKDAYS = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
_pending: dict[int, dict] = {}


def available_markets(uid: int) -> list[dict]:
    if is_owner(uid):
        return list_markets()
    manager = get_manager(uid)
    if not manager or manager['status'] != 'active' or manager['position'] not in ('Маркетолог', 'Управляющий'):
        return []
    return get_markets_for_manager(uid)


def _date(text: str) -> date:
    if not re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", text.strip()):
        raise ValueError("Нужна дата ДД.ММ.ГГГГ")
    day, month, year = map(int, text.strip().split('.'))
    return date(year, month, day)


def parse_events(text: str, week_start: str) -> list[dict]:
    start = date.fromisoformat(week_start)
    events = []
    seen = set()
    counts = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw_date, raw_time, title, participants = [p.strip() for p in line.split('|')]
            day = _date(raw_date)
            if not start <= day < start + timedelta(days=7):
                raise ValueError('дата вне выбранной недели')
            if not re.fullmatch(r'(?:[01]?\d|2[0-3]):[0-5]\d', raw_time):
                raise ValueError('время должно быть ЧЧ:ММ, от 00:00 до 23:59')
            if not title or len(title) > 100:
                raise ValueError('название должно содержать от 1 до 100 символов')
            if not re.fullmatch(r'\d{1,6}', participants):
                raise ValueError('участники — целое неотрицательное число')
            time = raw_time.zfill(5)
            identity = (day, time, title.casefold())
            if identity in seen:
                raise ValueError('мероприятие повторяется')
            seen.add(identity)
            counts[day] = counts.get(day, 0) + 1
            if counts[day] > 8:
                raise ValueError('не более 8 мероприятий в день')
            events.append(dict(date=day.isoformat(), time=time, title=title, participants=int(participants)))
        except (ValueError, TypeError) as error:
            raise ValueError(f'Строка {number}: {error}. Формат: ДД.ММ.ГГГГ | ЧЧ:ММ | название | участники') from error
    if not events:
        raise ValueError('Пришли список или напиши «нет мероприятий», чтобы очистить выбранную неделю.')
    return sorted(events, key=lambda e: (e['date'], e['time'], e['title']))


def _cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton('Отмена', callback_data='events:cancel')]])


async def _ask_mode(message, uid, market):
    _pending[uid] = dict(market=market, step='mode')
    await message.reply_text(f"Мероприятия «{market['name']}». Что сделать?", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton('📅 Загрузить следующую неделю', callback_data=f"events:next:{market['id']}")],
        [InlineKeyboardButton('➕ Добавить мероприятия', callback_data='events:mode:add')],
        [InlineKeyboardButton('📅 Заменить план недели', callback_data='events:mode:replace')],
        [InlineKeyboardButton('📗 Регламент маркетолога', callback_data='reg_view:spot_events')],
        [InlineKeyboardButton('Отмена', callback_data='events:cancel')],
    ]))


async def _ask_week(message, uid, market, mode):
    _pending[uid] = dict(market=market, step='week', mode=mode)
    monday = tz_today() - timedelta(days=tz_today().weekday())
    await message.reply_text(
        f"Мероприятия «{market['name']}». Пришли дату понедельника нужной недели в формате ДД.ММ.ГГГГ. "
        f"Например: {monday.strftime('%d.%m.%Y')}. "
        + ("Новые события добавятся к сохранённым; совпадения по дате, времени и названию будут пропущены."
           if mode == "add" else "После подтверждения план этой недели будет заменён целиком."),
        reply_markup=_cancel_keyboard(),
    )


def next_monday() -> date:
    today = tz_today()
    return today + timedelta(days=7 - today.weekday())


async def _ask_list(message, state, monday):
    state.update(week=monday.isoformat(), step='list')
    await message.reply_text(
        f"Неделя {fmt_date(state['week'])}–{(monday + timedelta(days=6)).strftime('%d.%m.%Y')}.\n"
        'Пришли по одному мероприятию на строку:\n'
        f"{monday.strftime('%d.%m.%Y')} | 18:00 | Каппинг | 15\n"
        'День недели вычислю по дате. Время — местное время спота. '
        + ('Если событий нет, напиши «нет мероприятий».' if state['mode'] == 'replace' else
           'Пришли только новые мероприятия — сохранённые останутся без изменений.'), reply_markup=_cancel_keyboard())


async def on_events_command(update, context):
    uid = update.effective_user.id
    markets = available_markets(uid)
    if not markets:
        await update.effective_message.reply_text('Загружать мероприятия может владелец, активный Маркетолог или Управляющий назначенного проекта.')
        return
    _pending.pop(uid, None)
    if len(markets) == 1:
        await _ask_mode(update.effective_message, uid, markets[0])
    else:
        await update.effective_message.reply_text('Для какого проекта загружаем мероприятия?', reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(m['name'], callback_data=f"events:market:{m['id']}")] for m in markets
        ]))


async def on_events_callback(update, context):
    query = update.callback_query
    uid = query.from_user.id
    action = query.data.split(':')
    if action[1] == 'cancel':
        _pending.pop(uid, None)
        await query.answer()
        await query.edit_message_text('Загрузка отменена. Сохранённый план не изменён.')
        return
    markets = {m['id']: m for m in available_markets(uid)}
    if action[1] == 'next':
        market = markets.get(int(action[2]))
        if not market:
            await query.answer('Нет доступа к проекту', show_alert=True)
            return
        await query.answer()
        state = dict(market=market, mode='replace')
        _pending[uid] = state
        monday = date.fromisoformat(action[3]) if len(action) > 3 else next_monday()
        await _ask_list(query.message, state, monday)
        return
    if action[1] == 'market':
        market = markets.get(int(action[2]))
        if not market:
            await query.answer('Нет доступа к проекту', show_alert=True)
            return
        await query.answer()
        await _ask_mode(query.message, uid, market)
        return
    state = _pending.get(uid)
    if action[1] == 'mode':
        if not state or state['market']['id'] not in markets or action[2] not in ('add', 'replace'):
            await query.answer('Нет активной загрузки или доступа', show_alert=True)
            return
        await query.answer()
        await _ask_week(query.message, uid, state['market'], action[2])
        return
    if not state or state['market']['id'] not in markets or state.get('step') != 'confirm' or action[2] != state['token']:
        await query.answer('Загрузка неактуальна. Открой /spot_events заново.', show_alert=True)
        return
    try:
        if state['mode'] == 'add':
            added = add_events(state['market']['id'], state['events'], uid)
            result = f"Добавлено мероприятий: {added}. Остальной план сохранён."
        else:
            replace_week(state['market']['id'], state['week'], state['events'], uid)
            result = 'План недели сохранён.'
    except ValueError as error:
        await query.answer(str(error), show_alert=True)
        return
    _pending.pop(uid, None)
    await query.answer()
    await query.edit_message_text(f'✅ {result} События на сегодня попадут в следующее утреннее сообщение команды.')


async def on_events_reply(update, context) -> bool:
    uid = update.effective_user.id
    state = _pending.get(uid)
    if not state:
        return False
    if state['market']['id'] not in {m['id'] for m in available_markets(uid)}:
        _pending.pop(uid, None)
        await update.effective_message.reply_text('Доступ к проекту отозван.')
        return True
    text = (update.effective_message.text or '').strip()
    if state['step'] == 'mode':
        await update.effective_message.reply_text('Выбери кнопкой: добавить мероприятия или заменить план недели.')
        return True
    if state['step'] == 'week':
        try:
            monday = _date(text)
            if monday.weekday() != 0:
                raise ValueError('Нужна дата понедельника')
        except ValueError:
            await update.effective_message.reply_text('Пришли дату понедельника в формате ДД.ММ.ГГГГ.')
            return True
        await _ask_list(update.effective_message, state, monday)
        return True
    if state['step'] == 'confirm':
        await update.effective_message.reply_text('Подтверди сохранение кнопкой или нажми «Отмена».')
        return True
    try:
        events = [] if text.lower() == 'нет мероприятий' and state['mode'] == 'replace' else parse_events(text, state['week'])
    except ValueError as error:
        await update.effective_message.reply_text(str(error))
        return True
    token = token_hex(4)
    state.update(events=events, step='confirm', token=token)
    verb = "Добавить мероприятия" if state["mode"] == "add" else "Заменить план"
    lines = [f"{verb} «{state['market']['name']}» за неделю с {fmt_date(state['week'])}?", '']
    lines += [f"{fmt_date(e['date'])}, {_WEEKDAYS[date.fromisoformat(e['date']).weekday()]} | {e['time']} | {e['title']} | {e['participants']} чел." for e in events]
    if not events:
        lines.append('Мероприятий нет. Сохранённые события этой недели будут удалены.')
    # Telegram ограничивает длину сообщения; кнопка подтверждения — после всего предпросмотра.
    preview = '\n'.join(lines)
    while len(preview) > 3500:
        cut = preview.rfind('\n', 0, 3500)
        await update.effective_message.reply_text(preview[:cut])
        preview = preview[cut + 1:]
    await update.effective_message.reply_text(preview, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Сохранить', callback_data=f'events:save:{token}'),
         InlineKeyboardButton('Отмена', callback_data='events:cancel')]
    ]))
    return True
