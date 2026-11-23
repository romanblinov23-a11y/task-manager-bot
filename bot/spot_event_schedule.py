"""Воскресные запросы плана следующей недели и эскалации 09/18/21."""
import logging
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import ROMAN_TELEGRAM_ID
from config.timeutil import fmt_date, now as tz_now
from monitoring.managers import get_managers_for_market, get_market_supervisor
from monitoring.markets import list_markets
from monitoring.spot_events import mark_notice_sent, notice_sent, week_submitted

logger = logging.getLogger(__name__)


async def send_event_schedule_notices(bot) -> None:
    now = tz_now()
    if now.weekday() != 6 or now.hour < 9:
        return
    week = (now.date() + timedelta(days=1)).isoformat()
    for market in list_markets():
        marketers = [m for m in get_managers_for_market(market['id'])
                     if m['status'] == 'active' and m['position'] == 'Маркетолог']
        # Сценарий включён для проектов, которым назначен маркетолог.
        if not marketers or week_submitted(market['id'], week):
            continue
        period = f"{fmt_date(week)}–{(now.date() + timedelta(days=7)).strftime('%d.%m.%Y')}"
        stages = [('request', [m['telegram_user_id'] for m in marketers],
                   f"📅 Загрузи мероприятия «{market['name']}» на неделю {period} до 18:00 сегодня. "
                   'Если мероприятий нет, отправь «нет мероприятий» и подтверди сохранение.')]
        if now.hour >= 18:
            supervisor = get_market_supervisor(market['id'])
            if supervisor:
                stages.append(('supervisor', [supervisor['telegram_user_id']],
                               f"⚠️ Маркетолог не загрузил мероприятия «{market['name']}» на неделю {period} к 18:00. "
                               'Попроси загрузить план или внеси его самостоятельно. В 21:00 при отсутствии плана уведомлю Рому.'))
        if now.hour >= 21:
            stages.append(('owner', [int(ROMAN_TELEGRAM_ID)],
                           f"⚠️ К 21:00 не внесён план мероприятий «{market['name']}» на неделю {period}. "
                           'Срок загрузки маркетологом и срок эскалации управляющему прошли.'))
        for stage, recipients, text in stages:
            for uid in recipients:
                if week_submitted(market['id'], week):
                    break
                if notice_sent(market['id'], week, stage, uid):
                    continue
                try:
                    await bot.send_message(chat_id=uid, text=text, reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton('📅 Загрузить следующую неделю', callback_data=f"events:next:{market['id']}:{week}")]
                    ]))
                except Exception:
                    logger.exception('Не удалось отправить уведомление о плане мероприятий проекта %s', market['id'])
                    continue
                mark_notice_sent(market['id'], week, stage, uid)
