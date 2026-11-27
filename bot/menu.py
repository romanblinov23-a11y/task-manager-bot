"""Постоянная панель и единый каталог команд с проверкой доступа при каждом нажатии."""
import hashlib
import json
import logging
from types import SimpleNamespace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from config import settings
from monitoring.db import get_connection
from monitoring.managers import get_manager

logger=logging.getLogger(__name__)

# command -> (подпись кнопки, раздел). Обработчики берутся из реальных CommandHandler.
CATALOG={
    'portfolio':('🏛 Финансовый портфель и дивиденды','reports'),
    'manager_report':('📄 Отчет управляющего за месяц','reports'),
    'view_reports':('🗂 Отчеты по сменам','reports'),
    'shift_dashboard':('📈 Дашборд смен','reports'),
    'dashboard_market':('📊 Дашборд рынка','reports'),
    'founders_report':('📑 Отчет для учредителей','reports'),
    'revenue':('💰 Выручка, план/факт и P&L','finance'),
    'manager_kpi_plan':('🎯 Планы гостевых KPI','finance'),
    'set_monthly_plan':('📅 План выручки и чеков','finance'),
    'set_writeoff_plan':('📉 Нормы списаний','finance'),
    'accounting':('🧾 Работа с системой учета','finance'),
    'employees':('👥 Сотрудники и доступы','team'),
    'spot_events':('🗓 Мероприятия проекта','team'),
    'set_meeting_schedule':('🕘 Расписание собраний','team'),
    'announce_meeting':('📣 Собрать повестку собрания','team'),
    'regulations':('📚 Регламенты','team'),
    'chats':('💬 Чаты по проектам','projects'),
    'monitor_chats':('🔔 Мониторинг сообщений в чатах','projects'),
    'add_project':('➕ Добавить проект','projects'),
    'market_settings':('🏢 Настройки проекта','projects'),
    'report_chats':('📬 Чаты рассылки отчетов','projects'),
    'message':('✉️ Написать сотруднику','messages'),
    'message_chat':('💬 Написать в чат','messages'),
    'broadcast':('📣 Сделать рассылку','messages'),
    'messaging':('📮 Все варианты отправки','messages'),
    'shift_report':('📝 Заполнить отчет по смене','shifts'),
    'set_shift_schedule':('📅 График смен','shifts'),
    'set_evening_report':('🌙 Настроить вечерний отчет','shifts'),
    'handover_report':('📋 Заполнить пересменку','shifts'),
    'set_handover_schedule':('🔄 График пересменок','shifts'),
    'set_handover_report':('⚙️ Настроить пересменку','shifts'),
    'reset_shift_report':('⚠️ Сбросить отчет по смене','shifts'),
    'reset_handover_report':('⚠️ Сбросить пересменку','shifts'),
    'monitoring':('🔎 Провести мониторинг','monitoring'),
    'add_competitor':('➕ Добавить конкурента','monitoring'),
    'close_competitor':('🔓 Открыть / закрыть конкурента','monitoring'),
    'schedule':('📅 Расписание мониторинга','monitoring'),
    'fix_reading':('✏️ Исправить дату снятия','monitoring'),
    'set_own_point':('📍 Своя точка на рынке','monitoring'),
    'reset_monitoring':('⚠️ Сбросить мониторинг','monitoring'),
}
CATEGORIES={
    'reports':'📊 Отчеты', 'finance':'💰 Финансы', 'team':'👥 Команда',
    'projects':'🏢 Проекты и чаты', 'messages':'📣 Сообщения',
    'settings':'⚙️ Настройки', 'shifts':'📝 Смены и пересменки', 'monitoring':'🔎 Мониторинг рынка',
}
HOME='🏠 Главное меню'
ALL='🧰 Все команды'
SHORTCUTS={
    '📄 Отчет за месяц':'manager_report','📝 Отчет по смене':'shift_report',
    '📋 Пересменка':'handover_report','🔎 Мониторинг':'monitoring',
    '🗓 Мероприятия':'spot_events','📊 Дашборд рынка':'dashboard_market',
    '📚 Регламенты':'regulations',
}


def is_roman(uid):return settings.ROMAN_TELEGRAM_ID is not None and str(uid)==str(settings.ROMAN_TELEGRAM_ID)


def allowed(uid):
    if is_roman(uid):return set(CATALOG)
    manager=get_manager(uid)
    if not manager or manager['status']!='active' or manager['position']!='Управляющий':return set()
    from bot.manager_admin import _commands_for_manager
    return {c.command for c in _commands_for_manager(manager) if c.command in CATALOG}


def reply_keyboard(uid):
    commands=allowed(uid)
    if not commands:return ReplyKeyboardRemove()
    if is_roman(uid):
        rows=[[CATEGORIES['reports'],CATEGORIES['finance']],[CATEGORIES['team'],CATEGORIES['projects']],
              [CATEGORIES['messages'],CATEGORIES['settings']],[HOME,'❓ Справка']]
    else:
        labels=[label for label,command in SHORTCUTS.items() if command in commands]
        rows=[labels[i:i+2] for i in range(0,len(labels),2)]
        rows.append([ALL,HOME])
    return ReplyKeyboardMarkup(rows,resize_keyboard=True,is_persistent=True,input_field_placeholder='Выбери действие или напиши ответ')


def signature(uid):
    commands=allowed(uid)
    if not commands:return None
    value={'version':1,'owner':is_roman(uid),'commands':sorted(commands),'keyboard':reply_keyboard(uid).to_dict()}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


async def sync_keyboard(bot,uid):
    """Рассылает панель только при первом включении или изменении доступных кнопок."""
    c=get_connection()
    try:
        row=c.execute('SELECT signature FROM bot_menu_keyboard WHERE user_id=?',(uid,)).fetchone()
        new=signature(uid)
        if (row['signature'] if row else None)==new:return
        text='Добавил кнопки под полем ввода. Отчеты, команда и настройки теперь под рукой. Главное меню можно открыть командой /menu.' if new else 'Доступные разделы изменились. Меню обновлено.'
        try:await bot.send_message(chat_id=uid,text=text,reply_markup=reply_keyboard(uid))
        except Exception as e:
            logger.warning('Не удалось обновить панель команд пользователя %s (%s)',uid,type(e).__name__);return
        with c:
            if new:c.execute('INSERT INTO bot_menu_keyboard VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET signature=excluded.signature',(uid,new))
            else:c.execute('DELETE FROM bot_menu_keyboard WHERE user_id=?',(uid,))
    finally:c.close()


def inline_menu(uid,category=None):
    commands=allowed(uid)
    if not commands:return None
    if not category:
        if is_roman(uid):
            return InlineKeyboardMarkup([[InlineKeyboardButton(CATEGORIES[key],callback_data='nav:cat:'+key)] for key in ('reports','finance','team','projects','messages','settings')])
        buttons=[[InlineKeyboardButton(CATALOG[key][0],callback_data='nav:cmd:'+key)] for key in CATALOG if key in commands]
    elif category=='settings':
        buttons=[[InlineKeyboardButton(CATEGORIES[key],callback_data='nav:cat:'+key)] for key in ('shifts','monitoring','projects') if any(v[1]==key and c in commands for c,v in CATALOG.items())]
    else:
        buttons=[[InlineKeyboardButton(label,callback_data='nav:cmd:'+command)] for command,(label,group) in CATALOG.items() if group==category and command in commands]
    if category:buttons.append([InlineKeyboardButton('⬅️ Главное меню',callback_data='nav:home')])
    return InlineKeyboardMarkup(buttons)


async def show_menu(message,uid):
    if not allowed(uid):
        await message.reply_text('Меню появится после подтверждения доступа управляющего.',reply_markup=ReplyKeyboardRemove());return
    await message.reply_text('Главное меню. Частые действия — на кнопках под полем ввода.',reply_markup=reply_keyboard(uid))
    if is_roman(uid):await message.reply_text('Выбери раздел:',reply_markup=inline_menu(uid))


async def on_menu_command(update,context):
    await show_menu(update.effective_message,update.effective_user.id)


class _Context:
    def __init__(self,original):self.original=original;self.args=[]
    def __getattr__(self,name):return getattr(self.original,name)


async def run_command(update,context,command):
    uid=update.effective_user.id
    if command not in allowed(uid):
        await update.effective_message.reply_text('Этот раздел больше недоступен. Открой /menu, чтобы обновить кнопки.',reply_markup=reply_keyboard(uid));return
    from portfolio.storage import dialog as portfolio_dialog
    portfolio_dialog(uid,None)
    if command!='broadcast':
        from monitoring.broadcasts import pause
        pause(uid)
    handler=context.application.bot_data.get('menu_commands',{}).get(command)
    if handler is None:
        await update.effective_message.reply_text('Открой раздел командой /'+command);return
    await handler(update,_Context(context))


async def on_menu_text(update,context):
    text=(update.effective_message.text or '').strip();uid=update.effective_user.id
    if is_roman(uid) and text.lower() in ('справка','❓ справка'):
        from bot.menu_help import show_help
        from monitoring.broadcasts import pause
        pause(uid)
        await show_help(update.effective_message,uid);return True
    if text not in SHORTCUTS and text not in CATEGORIES.values() and text not in (HOME,ALL):return False
    if not allowed(uid):
        await update.effective_message.reply_text('Меню недоступно: проверь доступ у владельца.',reply_markup=ReplyKeyboardRemove());return True
    from monitoring.broadcasts import pause
    pause(uid)
    if text==HOME:await show_menu(update.effective_message,uid)
    elif text==ALL:await update.effective_message.reply_text('Все доступные команды:',reply_markup=inline_menu(uid))
    elif text in CATEGORIES.values():
        category=next(k for k,v in CATEGORIES.items() if v==text)
        await update.effective_message.reply_text(CATEGORIES[category],reply_markup=inline_menu(uid,category))
    else:await run_command(update,context,SHORTCUTS[text])
    return True


async def on_menu_callback(update,context):
    q=update.callback_query;uid=q.from_user.id
    if q.message.chat.type!='private':await q.answer('Открой меню в личке с ботом',show_alert=True);return
    if not allowed(uid):await q.answer('Нет доступа',show_alert=True);return
    parts=q.data.split(':');action=parts[1]
    if action in ('help','cat','home'):
        from monitoring.broadcasts import pause
        pause(uid)
    if action=='help':
        if not is_roman(uid):await q.answer('Справка владельца недоступна',show_alert=True);return
        await q.answer()
        from bot.menu_help import show_help
        await show_help(q.message,uid,parts[2] if len(parts)>2 else None);return
    if action=='cmd':
        command=parts[2]
        if command not in allowed(uid):await q.answer('Раздел недоступен',show_alert=True);return
        await q.answer()
        # Используем реального нажавшего пользователя, а не автора сообщения бота.
        adapted=SimpleNamespace(effective_user=q.from_user,effective_message=q.message,effective_chat=q.message.chat,message=q.message,callback_query=None)
        await run_command(adapted,context,command);return
    category=parts[2] if action=='cat' and len(parts)==3 else None
    if category is not None and category not in CATEGORIES:await q.answer('Раздел не найден');return
    await q.answer()
    await q.edit_message_text(CATEGORIES[category] if category else 'Главное меню — выбери раздел:',reply_markup=inline_menu(uid,category))
