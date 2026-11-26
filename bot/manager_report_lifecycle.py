"""Расписание по Москве, статусы управляющих и принятие отчетов Романом."""
import asyncio
import json
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import settings
from manager_report import storage, lifecycle as life
from manager_report.questions import bounded
from monitoring.markets import list_markets, get_market
from monitoring.managers import get_market_supervisor

logger=logging.getLogger(__name__)
_dispatching=False
_scheduling=False


def roman(uid):return settings.ROMAN_TELEGRAM_ID is not None and str(uid)==str(settings.ROMAN_TELEGRAM_ID)


def keyboard(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label,callback_data=callback)] for label,callback in buttons]) if buttons else None


def decision_keyboard(report):
    from manager_report.lifecycle import workflow
    w=workflow(report['id'])
    if not w or w['state']!='submitted' or w['revision']!=report['revision']:return None
    token=f"{report['id']}:{report['revision']}"
    return keyboard([('Принять отчет','ml:accept:'+token),('Передать ОС управляющему и принять отчет','ml:feedback:'+token),('Вернуть на доработку','ml:rework:'+token)])


async def dispatch(bot):
    global _dispatching
    if _dispatching:return
    _dispatching=True
    try:
        for row in life.pending():
            if not life.notice_is_current(row):
                life.mark_sent(row['notice_key']);continue
            try:
                await bot.send_message(chat_id=row['recipient'],text=row['text'],reply_markup=keyboard(json.loads(row['buttons'])))
                life.mark_sent(row['notice_key'])
            except Exception:logger.exception('Не доставлено уведомление отчета %s',row['notice_key'])
    finally:_dispatching=False


def check_closure(spot,month):
    from manager_report.service import SURF_EMAIL,SURF_PASSWORD
    from revenue.surfcoffee_client import SurfCoffeeClient
    client=SurfCoffeeClient(SURF_EMAIL,SURF_PASSWORD)
    try:
        client.login()
        return 'месяц закрыт' if client.get_month_admission(spot,month)['closed'] else 'месяц открыт'
    finally:client.close()


async def schedule(bot,at=None):
    global _scheduling
    if _scheduling:return
    _scheduling=True
    try:
        at=(at or life.now()).astimezone(life.MOSCOW)
        first=at.date().replace(day=1)
        from datetime import timedelta
        month=(first-timedelta(days=1)).strftime('%Y-%m')
        roman_id=settings.ROMAN_TELEGRAM_ID
        cycle_active=life.active_cycle(month,at)
        for market in list_markets() if cycle_active else []:
            if not market.get('surf_spot_key'):continue
            manager=get_market_supervisor(market['id'])
            if not manager:continue
            uid=manager['telegram_user_id'];mid=market['id']
            report=storage.find_report(mid,month)
            w=life.workflow(report['id']) if report else None
            provided=w is not None
            prefix=f'month:{mid}:{month}'
            label=f"{market['name']} · {month}"
            # После перезапуска актуальные события догоняются, устаревшее напоминание не шлем.
            if at.day<=3 and (at.day>1 or at.hour>=10) and not provided:
                life.queue(prefix+f':reminder:{uid}',uid,f'Ежемесячный отчет · {label}\nПредоставь отчет до 5-го числа, 18:00 МСК. Заполнение доступно после закрытия месяца в НИМБе.', [('Начать / продолжить отчет',f'mr:project:{mid}:{month}')])
            if at.day>=4 and (at.day>4 or at.hour>=18) and not provided:
                life.queue(prefix+f':status:{uid}',uid,f'Статус отчета · {label}\nЗакрыт ли месяц? Успеешь предоставить отчет до 5-го, 18:00 МСК? Ответ будет передан Роману.',[('Сообщить статус',f'ml:status:{mid}:{month}')])
            due=life.deadline(month)
            if at>=due and (not provided or datetime.fromisoformat(w['first_submitted_at'])>due):
                stage={'collecting':'черновик не завершен','review':'PDF ожидает подтверждения','sending':'идет доставка','delivery_failed':'ошибка доставки'}.get(report['status'] if report else '', 'заполнение не начато')
                if provided:
                    stage='предоставлен с опозданием '+datetime.fromisoformat(w['first_submitted_at']).astimezone(life.MOSCOW).strftime('%d.%m.%Y %H:%M')+' МСК'
                for recipient in {uid,int(roman_id)} if roman_id else {uid}:
                    life.queue(prefix+f':overdue:{recipient}',recipient,f'⚠️ Просрочен отчет · {label}\nУправляющий: {manager["name"]}\nСрок: {due:%d.%m.%Y %H:%M} МСК. Статус: {stage}.'+('' if provided else '\nОтчет можно завершить и отправить.'), [('Открыть отчет',f'mr:project:{mid}:{month}')])
        for w in life.reworks():
            if not w['rework_due'] or at<datetime.fromisoformat(w['rework_due']):continue
            manager=get_market_supervisor(w['market_id'])
            if not manager:continue
            market=get_market(w['market_id'])
            for recipient in {manager['telegram_user_id'],int(roman_id)} if roman_id else {manager['telegram_user_id']}:
                life.queue(f"rework:{w['report_id']}:{w['revision']}:{recipient}",recipient,f"⚠️ Просрочена доработка · {market['name']} · {w['month']}\nСрок исправления: {datetime.fromisoformat(w['rework_due']).astimezone(life.MOSCOW):%d.%m.%Y %H:%M} МСК.")
        await dispatch(bot)
    finally:_scheduling=False


async def callback(update,context):
    q=update.callback_query;uid=q.from_user.id;parts=q.data.split(':');action=parts[1]
    if action=='cancel':
        life.dialog(uid,None);await q.answer();await q.message.reply_text('Действие отменено.');return
    if action=='status':
        mid=int(parts[2]);month=parts[3];manager=get_market_supervisor(mid)
        from bot.manager_report_flow import valid_month
        if not manager or manager['telegram_user_id']!=uid or not valid_month(month):
            await q.answer('Нет доступа',show_alert=True);return
        if not settings.ROMAN_TELEGRAM_ID:
            await q.answer('Получатель статусов не настроен',show_alert=True);return
        await q.answer()
        market=get_market(mid)
        try:closure=await asyncio.to_thread(check_closure,market['surf_spot_key'],month)
        except Exception:
            logger.exception('Не удалось проверить закрытие для статуса')
            closure='статус недоступен, закрытие не подтверждено'
        life.dialog(uid,dict(kind='status',market_id=mid,month=month,closure=closure))
        await q.message.reply_text('По НИМБу: '+closure+'.\nПришли статус по форме:\nзакрыт / открыт / не знаю | успеваю / не успеваю\n\nЕсли не успеваешь, добавь еще три поля:\nпричина | какая помощь нужна | ДД.ММ.ГГГГ ЧЧ:ММ\n\nВремя московское. Ответ целиком передам Роману.',reply_markup=keyboard([('Отмена','ml:cancel')]))
        return
    if action not in ('accept','feedback','rework') or not roman(uid):
        await q.answer('Только Роман',show_alert=True);return
    report=storage.get_report(int(parts[2]));revision=int(parts[3])
    w=life.workflow(report['id']) if report else None
    if not report or report['revision']!=revision or report['status']!='completed' or not w or w['revision']!=revision or w['state']!='submitted':
        await q.answer('Решение уже принято или версия устарела',show_alert=True);return
    await q.answer()
    if action=='accept':
        life.dialog(uid,None)
        await decide(q.message,context,report,revision,action);return
    life.dialog(uid,dict(kind=action,report_id=report['id'],revision=revision))
    text='Напиши обратную связь управляющему. После отправки комментария отчет будет принят, а задачи закреплены.' if action=='feedback' else 'Напиши замечания и срок исправления:\nДД.ММ.ГГГГ ЧЧ:ММ | что нужно исправить\nВремя московское.'
    await q.message.reply_text(text,reply_markup=keyboard([('Отмена','ml:cancel')]))


async def decide(message,context,report,revision,action,comment='',due=None):
    supervisor=get_market_supervisor(report['market_id'])
    if not supervisor:
        await message.reply_text('Не назначен действующий управляющий. Решение не сохранено.');return False
    recipients={supervisor['telegram_user_id']}
    label=f"Отчет · {get_market(report['market_id'])['name']} · {report['month']}"
    if not life.decide(report['id'],revision,action,comment,due,recipients,label):
        await message.reply_text('Решение уже принято или версия изменилась.');return False
    await message.reply_text('Отчет возвращен на доработку. История и исходная дата предоставления сохранены.' if action=='rework' else '✅ Отчет принят. Пять фокусных задач закреплены.')
    await dispatch(context.bot)
    return True


async def reply(update,context):
    uid=update.effective_user.id;state=life.dialog(uid)
    if not state:return False
    message=update.effective_message;text=(message.text or '').strip()
    if text.startswith('/') and text!='/cancel':return False
    if text.lower() in ('отмена','/cancel'):
        life.dialog(uid,None);await message.reply_text('Действие отменено.');return True
    if state['kind']=='status':
        manager=get_market_supervisor(state['market_id'])
        if not manager or manager['telegram_user_id']!=uid or not settings.ROMAN_TELEGRAM_ID:
            life.dialog(uid,None);await message.reply_text('Доступ изменился. Статус не отправлен.');return True
        try:
            answer=bounded(text,2500,'Статус отчета')
            fields=[v.strip() for v in answer.split('|')]
            if len(fields)<2 or fields[0].lower() not in ('закрыт','открыт','не знаю') or fields[1].lower() not in ('успеваю','не успеваю'):
                raise ValueError('Формат: закрыт / открыт / не знаю | успеваю / не успеваю.')
            if fields[1].lower()=='не успеваю':
                if len(fields)!=5 or not all(fields):raise ValueError('Если не успеваешь: статус месяца | не успеваю | причина | помощь | ДД.ММ.ГГГГ ЧЧ:ММ. Если помощь не нужна, напиши «не нужна».')
                try:datetime.strptime(fields[4],'%d.%m.%Y %H:%M')
                except ValueError:raise ValueError('Ожидаемый срок укажи полностью: ДД.ММ.ГГГГ ЧЧ:ММ, время московское.') from None
            elif len(fields)!=2:raise ValueError('Если успеваешь, достаточно двух полей: статус месяца | успеваю.')
        except ValueError as e:await message.reply_text(str(e));return True
        life.save_status(state['market_id'],state['month'],uid,dict(name=manager['name'],closure=state['closure'],answer=answer),settings.ROMAN_TELEGRAM_ID,get_market(state['market_id'])['name'])
        life.dialog(uid,None);await dispatch(context.bot)
        await message.reply_text('Ответ сохранен для передачи Роману. Если доставка временно недоступна, бот повторит ее автоматически.');return True
    if not roman(uid):life.dialog(uid,None);return True
    report=storage.get_report(state['report_id'])
    if not report:life.dialog(uid,None);return True
    due=None
    try:
        if state['kind']=='rework':
            value,sep,text=text.partition('|')
            if not sep:raise ValueError('Формат: ДД.ММ.ГГГГ ЧЧ:ММ | замечания.')
            try:due_dt=datetime.strptime(value.strip(),'%d.%m.%Y %H:%M').replace(tzinfo=life.MOSCOW)
            except ValueError:raise ValueError('Срок: ДД.ММ.ГГГГ ЧЧ:ММ, время московское.') from None
            if due_dt<=life.now():raise ValueError('Срок исправления должен быть в будущем.')
            due=due_dt.isoformat()
        comment=bounded(text,2500,'Обратная связь')
    except ValueError as e:await message.reply_text(str(e));return True
    await decide(message,context,report,state['revision'],state['kind'],comment,due)
    life.dialog(uid,None)
    return True
