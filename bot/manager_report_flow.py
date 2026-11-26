"""Новый отчет управляющего: команды, сохраненная анкета, PDF и доставка."""
import asyncio
import copy
import logging
import re
from uuid import uuid4
from datetime import date, datetime, timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from config.timeutil import today as tz_today
from manager_report import storage, lifecycle
from manager_report.model import PLAN_FIELDS, with_answers, deviations
from manager_report.questions import question_list, parse_answer, parse_plan_value
from manager_report.service import collect, OpenMonthError, MissingPlansError, before
from monitoring.managers import get_manager, get_markets_for_manager, get_market_supervisor
from monitoring.markets import list_markets, get_market
from monitoring.db import get_connection

logger=logging.getLogger(__name__)
_busy=set()


def is_roman(uid):
    return settings.ROMAN_TELEGRAM_ID is not None and str(uid)==str(settings.ROMAN_TELEGRAM_ID)


def available(uid):
    if is_roman(uid):return [m for m in list_markets() if m.get('surf_spot_key')]
    manager=get_manager(uid)
    if not manager or manager['status']!='active' or manager['position']!='Управляющий':return []
    return [m for m in get_markets_for_manager(uid) if m.get('surf_spot_key')]


def previous_month():
    return (tz_today().replace(day=1)-timedelta(days=1)).strftime('%Y-%m')


def valid_month(month):
    return bool(re.fullmatch(r'(?:[1-9]\d{3})-(?:0[1-9]|1[0-2])',month))


def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton('Отмена диалога',callback_data='mr:cancel')]])


async def send_text(message,text,reply_markup=None):
    parts=[]
    while len(text)>3500:
        split=text.rfind('\n',0,3500)
        if split<1:split=3500
        parts.append(text[:split]);text=text[split:].lstrip('\n')
    parts.append(text)
    for i,part in enumerate(parts):
        await message.reply_text(part,reply_markup=reply_markup if i==len(parts)-1 else None)


def _questions(report):
    tasks=report['model'].get('previous_tasks',[]) or lifecycle.accepted_tasks(report['market_id'],before(report['month']))
    previous={'status':'completed','answers':{'next_tasks':tasks}} if len(tasks)==5 else None
    return question_list(report,previous,report['model'].get('planned_events',[]))


def _report_keyboard(report):
    token=f"{report['id']}:{report['revision']}"
    if report['status']=='delivery_failed':
        return InlineKeyboardMarkup([[InlineKeyboardButton('Повторить недоставленным',callback_data=f'mr:send:{token}')]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Подтвердить и отправить',callback_data=f'mr:send:{token}')],
        [InlineKeyboardButton('✏️ Изменить ответы',callback_data=f"mr:edit:{report['id']}")],
    ])


async def on_manager_report_command(update,context):
    uid=update.effective_user.id;message=update.effective_message
    if not available(uid):
        await message.reply_text('Отчет доступен Роману и подтвержденному Управляющему своего проекта.')
        return
    args=context.args or [];month=args[0] if args else previous_month()
    if len(args)>1 or not valid_month(month):
        await message.reply_text('Укажи отчетный месяц: /manager_report ГГГГ-ММ.')
        return
    dialog=storage.get_dialog(uid)
    if dialog and dialog.get('kind')=='report':
        report=storage.get_report(dialog['report_id'])
        if report and report['month']==month and report['status'] in ('collecting','review','delivery_failed'):
            if report['market_id'] not in {m['id'] for m in available(uid)}:
                await message.reply_text('Доступ к проекту изменился.');return
            await _ask(message,context,uid,report);return
        await message.reply_text('Уже открыт другой отчет. Закончи его или нажми «Отмена диалога», затем запусти нужный месяц.',reply_markup=cancel_keyboard())
        return
    markets=available(uid)
    if len(markets)==1:
        await _start(message,context,uid,markets[0],month)
    else:
        await message.reply_text('Выбери проект для отчета за '+month,reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(m['name'],callback_data=f"mr:project:{m['id']}:{month}")] for m in markets]))


async def _start(message,context,uid,market,month):
    if market['id'] not in {m['id'] for m in available(uid)}:return
    if not valid_month(month):
        await message.reply_text('Нужен месяц в формате ГГГГ-ММ.');return
    report=storage.find_report(market['id'],month)
    if report:
        if report['status']=='completed':
            await _show_saved(message,context,uid,report);return
        if report['reporter_id']!=uid:
            await message.reply_text('Этот отчет уже заполняет другой сотрудник. Новый черновик поверх него не создается.');return
        storage.set_dialog(uid,dict(kind='report',report_id=report['id']))
        await _ask(message,context,uid,report);return
    if get_market_supervisor(market['id']) is None:
        await message.reply_text('Сначала назначьте подтвержденного Управляющего этого проекта через /employees.');return
    key=(market['id'],month)
    if key in _busy:
        await message.reply_text('Данные этого отчета уже загружаются. Повтори команду после завершения.');return
    _busy.add(key)
    try:
        await message.reply_text('Проверяю закрытие месяца в НИМБе. Если месяц закрыт, загружу данные отчета — это может занять несколько минут.')
        plan=storage.get_plan(market['id'],month)
        try:
            model=await asyncio.to_thread(collect,market['surf_spot_key'],month,plan)
        except OpenMonthError:
            await message.reply_text('Месяц '+month+' не закрыт в НИМБе. Отчет не начат. После закрытия нажми повторную проверку.',reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('Проверить снова',callback_data=f"mr:project:{market['id']}:{month}")]]));return
        except MissingPlansError:
            await message.reply_text('Для этого спота Роман еще не настроил планы гостевых KPI. Нужна команда /manager_kpi_plan '+month+'. Отчет не начат.');return
        except Exception:
            logger.exception('Не удалось получить данные нового отчета market=%s month=%s',market['id'],month)
            await message.reply_text('Не удалось подтвердить закрытие месяца или получить полные данные НИМБа. Анкета не начата. Повтори команду позже; статус не считается закрытым при ошибке.');return
        # История наполненности возможна только из ранее завершенного отчета, не из чека/гостей.
        last_year=storage.find_report(market['id'],f'{int(month[:4])-1}{month[4:]}')
        if last_year and last_year['status']=='completed':
            old=with_answers(last_year['model'],last_year['answers'])
            ly_basket=next(r['fact'] for r in old['metrics'] if r['key']=='basket')
            next(r for r in model['metrics'] if r['key']=='basket')['last_year']=ly_basket
        if market['id'] not in {m['id'] for m in available(uid)}:
            await message.reply_text('Доступ к проекту изменился во время загрузки.');return
        model['previous_tasks']=copy.deepcopy(lifecycle.accepted_tasks(market['id'],before(month)))
        conn=get_connection()
        try:
            model['planned_events']=[dict(r) for r in conn.execute('SELECT event_date,title FROM spot_event WHERE market_id=? AND substr(event_date,1,7)=? ORDER BY event_date,event_time', (market['id'],month))]
        finally:
            conn.close()
        report=storage.create_report(market['id'],month,uid,model)
        if report['reporter_id']!=uid:
            await message.reply_text('Отчет уже начал другой сотрудник.');return
        storage.set_dialog(uid,dict(kind='report',report_id=report['id']))
        if model.get('warnings'):
            await send_text(message,'Проверка данных:\n'+'\n'.join('• '+w for w in model['warnings']))
        await _ask(message,context,uid,report)
    finally:
        _busy.discard(key)


async def _ask(message,context,uid,report):
    if report['status']=='completed':
        await _show_saved(message,context,uid,report);return
    if report['status']=='delivery_failed':
        await message.reply_text('Доставка не завершена. Уже доставленным получателям повторно не отправляем.',reply_markup=_report_keyboard(report));return
    questions=_questions(report)
    for q in questions:
        if q['key'] not in report['answers']:
            storage.set_dialog(uid,dict(kind='report',report_id=report['id'],field=q['key']))
            await send_text(message,f"{get_market(report['market_id'])['name']} · {report['month']}\n\n"+q['prompt'],cancel_keyboard())
            return
    await _preview(message,context,uid,report)


async def _preview(message,context,uid,report):
    from manager_report.pdf import build_report, LayoutError
    model=with_answers(report['model'],report['answers'])
    if set(i['id'] for i in deviations(model))!=set(idx for row in report['answers'].get('deviations',[]) for idx in row['ids']):
        await message.reply_text('После изменения данных нужно повторно объяснить отклонения.');return
    directory=Path(settings.MONITORING_DB_PATH).parent/'manager_reports'
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=directory/f"report_{report['market_id']}_{report['month']}_r{report['revision']}_{uuid4().hex[:8]}.pdf"
    try:
        await asyncio.to_thread(build_report,report['model'],report['answers'],get_market(report['market_id']),path,provided_on=lifecycle.now().date().isoformat())
    except LayoutError as e:
        path.unlink(missing_ok=True)
        await message.reply_text(str(e),reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('Сократить / сгруппировать ответы',callback_data=f"mr:edit:{report['id']}")]]));return
    except Exception:
        path.unlink(missing_ok=True);logger.exception('Ошибка PDF отчета %s',report['id'])
        await message.reply_text('Не удалось собрать PDF. Ответы сохранены. Повтори /manager_report '+report['month']);return
    if not storage.mark_review(report['id'],report['revision'],path):
        path.unlink(missing_ok=True)
        await message.reply_text('Версия отчета изменилась. Открой отчет заново.');return
    report=storage.get_report(report['id'])
    storage.set_dialog(uid,dict(kind='report',report_id=report['id'],field=None))
    with path.open('rb') as document:
        await context.bot.send_document(chat_id=uid,document=document,caption='Предпросмотр: '+get_market(report['market_id'])['name']+' · '+report['month']+'\nПроверь все 11 страниц. После подтверждения PDF получат Роман и Управляющий.',reply_markup=_report_keyboard(report))


async def _show_saved(message,context,uid,report):
    if not report.get('pdf_path') or not Path(report['pdf_path']).is_file():
        await message.reply_text('Сохраненный PDF недоступен. Обратитесь к Роману.');return
    from bot.manager_report_lifecycle import decision_keyboard
    with Path(report['pdf_path']).open('rb') as document:
        await context.bot.send_document(chat_id=uid,document=document,caption='Предоставленный отчет · '+report['month'],reply_markup=decision_keyboard(report) if is_roman(uid) else None)


async def on_manager_kpi_plan_command(update,context):
    uid=update.effective_user.id;message=update.effective_message
    if not is_roman(uid):
        await message.reply_text('Планы гостевых KPI настраивает только Роман.');return
    args=context.args or [];month=args[0] if args else previous_month()
    if len(args)>1 or not valid_month(month):
        await message.reply_text('Формат: /manager_kpi_plan ГГГГ-ММ — месяц начала действия плана.');return
    await message.reply_text('Планы будут действовать с '+month+' до следующего изменения. Выбери спот:',reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton(m['name'],callback_data=f"mr:plan:{m['id']}:{month}")] for m in available(uid)]))


async def _ask_plan(message,uid,state):
    index=len(state['values']);fields=list(PLAN_FIELDS)
    if index==len(fields):
        await send_text(message,'Планы для '+get_market(state['market_id'])['name']+' с '+state['month']+':\n'+
                        '\n'.join(PLAN_FIELDS[k][0]+': '+str(v) for k,v in state['values'].items()),
                        InlineKeyboardMarkup([[InlineKeyboardButton('Сохранить планы',callback_data='mr:saveplan')],[InlineKeyboardButton('Отмена',callback_data='mr:cancel')]]));return
    key=fields[index]
    await message.reply_text(f'{index+1}/{len(fields)}. '+PLAN_FIELDS[key][0]+'\nПришли плановое значение. Ноль допустим; пропуск не считается нулем.',reply_markup=cancel_keyboard())


async def on_manager_report_reply(update,context):
    from bot.manager_report_lifecycle import reply as lifecycle_reply
    if await lifecycle_reply(update,context):return True
    uid=update.effective_user.id;state=storage.get_dialog(uid)
    if not state:return False
    message=update.effective_message;text=(message.text or '').strip()
    if text.lower() in ('отмена','/cancel'):
        storage.set_dialog(uid,None);await message.reply_text('Диалог закрыт. Черновик отчета и ответы сохранены.');return True
    if state['kind']=='plan':
        if not is_roman(uid):storage.set_dialog(uid,None);return True
        fields=list(PLAN_FIELDS)
        if len(state['values'])==len(fields):
            await message.reply_text('Сохрани планы кнопкой или нажми «Отмена».');return True
        key=fields[len(state['values'])]
        try:value=parse_plan_value(PLAN_FIELDS[key][1],text)
        except ValueError as e:await message.reply_text(str(e));return True
        state['values'][key]=value;storage.set_dialog(uid,state);await _ask_plan(message,uid,state);return True
    report=storage.get_report(state['report_id'])
    if not report or report['reporter_id']!=uid or report['market_id'] not in {m['id'] for m in available(uid)}:
        storage.set_dialog(uid,None);await message.reply_text('Отчет или доступ к нему больше недоступен.');return True
    if report['status'] not in ('collecting','review'):
        await _ask(message,context,uid,report);return True
    q=next((q for q in _questions(report) if q['key']==state.get('field')),None)
    if q is None:
        await message.reply_text('Для правки выбери поле кнопкой.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Изменить ответы',callback_data=f"mr:edit:{report['id']}")]]));return True
    try:value=parse_answer(q,text,report)
    except ValueError as e:await message.reply_text(str(e));return True
    answers=copy.deepcopy(report['answers']);answers[q['key']]=value
    if q['key']=='dishes':answers.pop('deviations',None)
    if not storage.save_answers(report['id'],answers,report['revision']):
        await message.reply_text('Отчет уже изменен или подтвержден. Открой его заново.');return True
    await _ask(message,context,uid,storage.get_report(report['id']));return True


async def on_manager_report_callback(update,context):
    query=update.callback_query;uid=query.from_user.id;parts=query.data.split(':');action=parts[1]
    if action=='cancel':
        storage.set_dialog(uid,None);await query.answer();await query.message.reply_text('Диалог закрыт. Черновик сохранен.');return
    if action=='plan':
        if not is_roman(uid):await query.answer('Только Роман',show_alert=True);return
        mid=int(parts[2]);month=parts[3]
        if mid not in {m['id'] for m in available(uid)} or not valid_month(month):await query.answer('Недоступно',show_alert=True);return
        state=dict(kind='plan',market_id=mid,month=month,values={})
        storage.set_dialog(uid,state);await query.answer();await _ask_plan(query.message,uid,state);return
    if action=='saveplan':
        state=storage.get_dialog(uid)
        if not is_roman(uid) or not state or state['kind']!='plan' or set(state['values'])!=set(PLAN_FIELDS):
            await query.answer('Настройка неактуальна',show_alert=True);return
        storage.save_plan(state['market_id'],state['month'],state['values'],uid);storage.set_dialog(uid,None)
        await query.answer();await query.message.reply_text('Планы сохранены для спота. Ранее начатые и подтвержденные отчеты сохраняют свои версии планов.');return
    if action=='project':
        market=next((m for m in available(uid) if m['id']==int(parts[2])),None)
        if not market:await query.answer('Нет доступа',show_alert=True);return
        await query.answer();await _start(query.message,context,uid,market,parts[3]);return
    if action not in ('edit','field','send'):
        await query.answer();return
    report=storage.get_report(int(parts[2]))
    if not report or report['reporter_id']!=uid or report['market_id'] not in {m['id'] for m in available(uid)}:
        await query.answer('Нет доступа к этому отчету',show_alert=True);return
    if action in ('edit','field'):
        if report['status'] not in ('collecting','review'):
            await query.answer('Подтвержденный отчет не редактируется',show_alert=True);return
        await query.answer()
        if action=='edit':
            await query.message.reply_text('Выбери ответ для изменения:',reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(q['label'],callback_data=f"mr:field:{report['id']}:{q['key']}")] for q in _questions(report)]));return
        q=next((q for q in _questions(report) if q['key']==parts[3]),None)
        if q is None:await query.message.reply_text('Поле больше не требуется.');return
        storage.set_dialog(uid,dict(kind='report',report_id=report['id'],field=q['key']))
        await send_text(query.message,q['prompt'],cancel_keyboard());return
    await query.answer()
    await _deliver(query.message,context,uid,report,int(parts[3]))


async def _deliver(message,context,uid,report,revision):
    supervisor=get_market_supervisor(report['market_id'])
    if not settings.ROMAN_TELEGRAM_ID or supervisor is None:
        await message.reply_text('Нужны Роман и действующий Управляющий проекта. Отчет не отправлен.');return
    if report['revision']!=revision or not report.get('pdf_path') or not Path(report['pdf_path']).is_file():
        await message.reply_text('Это устаревший предпросмотр. Открой отчет снова.');return
    if report['status']=='review' and datetime.fromisoformat(report['updated_at']).astimezone(lifecycle.MOSCOW).date()!=lifecycle.now().date():
        await message.reply_text('Наступил новый день. Обновлю дату предоставления на обложке — проверь и подтверди новый предпросмотр.')
        await _preview(message,context,uid,report);return
    if not storage.claim_delivery(report['id'],revision):
        await message.reply_text('Эта версия уже подтверждена или отправляется.');return
    try:
        submitted=lifecycle.submit(report['id'],revision)
    except Exception:
        logger.exception('Не зафиксировано предоставление отчета %s',report['id'])
        submitted=False
    if not submitted:
        storage.set_delivery_status(report['id'],'delivery_failed')
        await message.reply_text('Не удалось зафиксировать предоставление отчета. Повтори отправку.');return
    from bot.manager_report_lifecycle import decision_keyboard
    recipients={int(settings.ROMAN_TELEGRAM_ID),supervisor['telegram_user_id']}
    succeeded=storage.delivered_to(report['id'],revision)
    for recipient in sorted(recipients-succeeded):
        try:
            with Path(report['pdf_path']).open('rb') as document:
                await context.bot.send_document(chat_id=recipient,document=document,
                    caption='Отчет управляющего · '+get_market(report['market_id'])['name']+' · '+report['month'],
                    reply_markup=decision_keyboard(report) if is_roman(recipient) else None)
            storage.record_delivery(report['id'],revision,recipient)
        except Exception:
            logger.exception('Не завершена доставка отчета %s',report['id'])
            storage.set_delivery_status(report['id'],'delivery_failed')
            await message.reply_text('Не удалось доставить PDF обоим получателям. Черновик и сведения об успешной доставке сохранены.',reply_markup=_report_keyboard(storage.get_report(report['id'])));return
    storage.set_delivery_status(report['id'],'completed');storage.set_dialog(uid,None)
    await message.reply_text('✅ Отчет предоставлен: PDF отправлен Роману и Управляющему. Ожидается решение Романа.')
