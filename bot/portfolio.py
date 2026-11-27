"""Личная ссылка на живой портфель и настройка доли УК по проекту."""
from uuid import uuid4
from telegram import InlineKeyboardButton,InlineKeyboardMarkup
from config import settings
from bot.menu import is_roman
from monitoring.markets import get_market
from monitoring.broadcasts import pause
from portfolio import storage
from manager_report.model import number


async def on_portfolio_command(update,context):
    uid=update.effective_user.id
    if not is_roman(uid):
        await update.effective_message.reply_text('Финансовый портфель доступен только Роману.');return
    if not settings.PUBLIC_BASE_URL:
        await update.effective_message.reply_text('Публичный адрес дашборда еще не настроен.');return
    from portfolio.service import request_portfolio
    from config.timeutil import today
    request_portfolio(today().strftime('%Y-%m'))
    url=settings.PUBLIC_BASE_URL.rstrip('/')+'/portfolio?token='+storage.token()
    await update.effective_message.reply_text('Живой портфель проектов и дивидендный поток УК Havana Standart. Во вкладках можно выбирать месяц; доли настраиваются в параметрах проекта.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📊 Открыть финансовый портфель',url=url)]]))


async def on_portfolio_callback(update,context):
    q=update.callback_query;uid=q.from_user.id
    if not is_roman(uid):await q.answer('Только Роман',show_alert=True);return
    parts=q.data.split(':')
    if parts[1]=='cancel':storage.dialog(uid,None);await q.answer('Отменено');return
    if parts[1]=='share':
        market=get_market(int(parts[2]))
        if not market:await q.answer('Проект не найден',show_alert=True);return
        pause(uid);storage.dialog(uid,{'market_id':market['id'],'nonce':uuid4().hex[:12]})
        await q.answer()
        await q.message.reply_text(f"{market['name']}\nДоля УК Havana Standart: {storage.ownership(market):g}%.\nПришли долю от 0 до 100%. Настройка применяется ко всей истории дивидендного дашборда; показатели самого проекта остаются полными.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отмена',callback_data='pf:cancel')]]));return
    if parts[1]=='save':
        state=storage.dialog(uid)
        if not state or state.get('nonce')!=parts[2] or 'percent' not in state:await q.answer('Настройка устарела',show_alert=True);return
        market=get_market(state['market_id'])
        if not market:await q.answer('Проект не найден',show_alert=True);return
        storage.set_ownership(market,state['percent'],uid);storage.dialog(uid,None)
        await q.answer('Сохранено')
        await q.message.reply_text(f"{market['name']}: доля УК Havana Standart — {state['percent']:g}%. Дивидендный поток за всю историю пересчитывается с этой долей. PDF-отчеты управляющих сохраняют показатели всего проекта.")


async def on_portfolio_reply(update,context):
    uid=update.effective_user.id;state=storage.dialog(uid)
    if not state:return False
    if not is_roman(uid):storage.dialog(uid,None);return True
    text=(update.effective_message.text or '').strip()
    if text.lower() in ('отмена','/cancel'):
        storage.dialog(uid,None);await update.effective_message.reply_text('Настройка доли отменена.');return True
    value=number(text)
    if value is None or not 0<=value<=100:
        await update.effective_message.reply_text('Пришли число от 0 до 100, например 50 или 50,5.');return True
    state.update(percent=value,nonce=uuid4().hex[:12]);storage.dialog(uid,state)
    market=get_market(state['market_id'])
    await update.effective_message.reply_text(f"{market['name']}: установить долю УК Havana Standart {value:g}% на всю историю дашборда?",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Сохранить',callback_data='pf:save:'+state['nonce'])],[InlineKeyboardButton('Отмена',callback_data='pf:cancel')]]))
