import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from manager_report import storage,lifecycle as life
from bot import manager_report_lifecycle as flow
from monitoring.markets import list_markets,set_market_surf_spot_key
from monitoring.managers import register_manager,approve_manager,remove_manager


def at(value):return datetime.fromisoformat(value).replace(tzinfo=life.MOSCOW)


def setup(monkeypatch):
    monkeypatch.setattr(flow.settings,'ROMAN_TELEGRAM_ID','42')
    monkeypatch.setattr(life,'stamp',lambda:at('2026-11-05T12:00').isoformat())
    market=list_markets()[0];set_market_surf_spot_key(market['id'],'okko')
    register_manager(99,'Иванов Иван Иванович','Управляющий',market['id']);approve_manager(99)
    return market


def update(uid,text='',callback=''):
    message=SimpleNamespace(text=text,reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid),effective_message=message,callback_query=SimpleNamespace(data=callback,from_user=SimpleNamespace(id=uid),message=message,answer=AsyncMock()))


def submitted(market,tmp_path):
    r=storage.create_report(market['id'],'2026-10',99,{'month':'2026-10'})
    answers={'next_tasks':[{'title':str(i),'deadline':'2026-11-20'} for i in range(5)]}
    storage.save_answers(r['id'],answers,1)
    path=tmp_path/'r2.pdf';path.write_bytes(b'original-pdf')
    storage.mark_review(r['id'],2,path)
    assert storage.claim_delivery(r['id'],2)
    assert life.submit(r['id'],2)
    storage.set_delivery_status(r['id'],'completed')
    return storage.get_report(r['id'])


def bot():return SimpleNamespace(send_message=AsyncMock())


def test_moscow_deadline_year_boundary():
    assert life.deadline('2026-12')==at('2027-01-05T18:00')


def test_first_reminder_exact_time_and_persistent_dedup(monkeypatch):
    setup(monkeypatch);b=bot()
    async def run():
        await flow.schedule(b,at('2026-11-01T09:59'));b.send_message.assert_not_awaited()
        await flow.schedule(b,at('2026-11-01T10:00'))
        assert b.send_message.await_count==1
        assert b.send_message.call_args.kwargs['chat_id']==99
        assert '2026-10' in b.send_message.call_args.kwargs['text']
        from monitoring.db import init_schema
        init_schema()
        await flow.schedule(b,at('2026-11-01T10:01'))
        assert b.send_message.await_count==1
    asyncio.run(run())


def test_fourth_status_request_at_18_not_before(monkeypatch):
    setup(monkeypatch);b=bot()
    async def run():
        await flow.schedule(b,at('2026-11-04T17:59'));b.send_message.assert_not_awaited()
        await flow.schedule(b,at('2026-11-04T18:00'))
        assert b.send_message.await_count==1
        assert 'Статус отчета' in b.send_message.call_args.kwargs['text']
        await flow.schedule(b,at('2026-11-04T18:01'));assert b.send_message.await_count==1
    asyncio.run(run())


def test_overdue_escalates_to_both_once_and_late_draft_allowed(monkeypatch):
    m=setup(monkeypatch);b=bot()
    async def run():
        await flow.schedule(b,at('2026-11-04T18:00'));b.send_message.reset_mock()
        await flow.schedule(b,at('2026-11-05T17:59'));b.send_message.assert_not_awaited()
        await flow.schedule(b,at('2026-11-05T18:00'))
        assert {c.kwargs['chat_id'] for c in b.send_message.call_args_list}=={42,99}
        assert all('Просрочен' in c.kwargs['text'] for c in b.send_message.call_args_list)
        await flow.schedule(b,at('2026-11-06T18:00'));assert b.send_message.await_count==2
        assert storage.create_report(m['id'],'2026-10',99,{})['status']=='collecting'
    asyncio.run(run())


def test_confirmed_submission_even_delivery_failure_is_not_overdue(monkeypatch,tmp_path):
    m=setup(monkeypatch);r=submitted(m,tmp_path);b=bot()
    storage.set_delivery_status(r['id'],'delivery_failed')
    asyncio.run(flow.schedule(b,at('2026-11-05T18:00')))
    b.send_message.assert_not_awaited()
    assert life.accepted_tasks(m['id'],'2026-10')==[]


def test_status_answers_forwarded_to_roman_and_unknown_is_not_closed(monkeypatch):
    m=setup(monkeypatch);b=bot();context=SimpleNamespace(bot=b)
    def unavailable(*a):raise RuntimeError('offline')
    monkeypatch.setattr(flow,'check_closure',unavailable)
    async def run():
        await flow.callback(update(99,callback=f"ml:status:{m['id']}:2026-10"),context)
        assert 'не подтверждено' in life.dialog(99)['closure']
        await flow.reply(update(99,'открыт | не успеваю | нужна сверка | помощь с НИМБом | 06.11.2026 12:00'),context)
        assert b.send_message.await_count==1
        sent=b.send_message.call_args.kwargs
        assert sent['chat_id']==42 and 'Иванов Иван Иванович' in sent['text']
        assert 'не успеваю' in sent['text'] and 'не подтверждено' in sent['text']
        assert life.dialog(99) is None
    asyncio.run(run())


def test_accept_only_roman_current_version_and_tasks_lock_on_accept(monkeypatch,tmp_path):
    m=setup(monkeypatch);r=submitted(m,tmp_path);context=SimpleNamespace(bot=bot())
    async def run():
        assert life.accepted_tasks(m['id'],'2026-10')==[]
        await flow.callback(update(99,callback=f"ml:accept:{r['id']}:2"),context)
        assert life.workflow(r['id'])['state']=='submitted'
        await flow.callback(update(42,callback=f"ml:accept:{r['id']}:1"),context)
        assert life.workflow(r['id'])['state']=='submitted'
        await flow.callback(update(42,callback=f"ml:accept:{r['id']}:2"),context)
        assert life.workflow(r['id'])['state']=='accepted'
        assert len(life.accepted_tasks(m['id'],'2026-10'))==5
        await flow.callback(update(42,callback=f"ml:rework:{r['id']}:2"),context)
        assert life.dialog(42) is None
        assert context.bot.send_message.await_count==1
    asyncio.run(run())


def test_feedback_accepts_and_is_queued_until_delivered(monkeypatch,tmp_path):
    m=setup(monkeypatch);r=submitted(m,tmp_path);b=bot();context=SimpleNamespace(bot=b)
    b.send_message.side_effect=RuntimeError('Telegram offline')
    async def run():
        await flow.callback(update(42,callback=f"ml:feedback:{r['id']}:2"),context)
        assert life.workflow(r['id'])['state']=='submitted'
        await flow.reply(update(42,'Хороший результат. Сосредоточься на обучении команды.'),context)
        assert life.workflow(r['id'])['state']=='accepted'
        assert len(life.pending())==1
        b.send_message.side_effect=None
        await flow.dispatch(b)
        assert life.pending()==[]
        assert 'обучении команды' in b.send_message.call_args.kwargs['text']
        assert len(life.accepted_tasks(m['id'],'2026-10'))==5
    asyncio.run(run())


def test_rework_preserves_version_and_submission_then_resubmits(monkeypatch,tmp_path):
    m=setup(monkeypatch);r=submitted(m,tmp_path);first=life.workflow(r['id'])['first_submitted_at']
    monkeypatch.setattr(life,'now',lambda:at('2026-11-06T09:00'))
    context=SimpleNamespace(bot=bot())
    async def run():
        await flow.callback(update(42,callback=f"ml:rework:{r['id']}:2"),context)
        await flow.reply(update(42,'07.11.2026 18:00 | Уточни причины отклонений.'),context)
        updated=storage.get_report(r['id'])
        assert updated['status']=='collecting' and updated['revision']==3
        assert updated['answers']==r['answers'] and updated['pdf_path'] is None
        assert life.workflow(r['id'])['state']=='rework'
        assert life.accepted_tasks(m['id'],'2026-10')==[]
        assert life.workflow(r['id'])['first_submitted_at']==first
        assert open(r['pdf_path'],'rb').read()==b'original-pdf'
        p=tmp_path/'r3.pdf';p.write_bytes(b'corrected-pdf')
        storage.mark_review(r['id'],3,p);storage.claim_delivery(r['id'],3)
        life.submit(r['id'],3);storage.set_delivery_status(r['id'],'completed')
        assert life.workflow(r['id'])['first_submitted_at']==first
        assert life.workflow(r['id'])['state']=='submitted'
        assert not life.decide(r['id'],2,'accept')
        assert life.decide(r['id'],3,'accept')
    asyncio.run(run())


def test_rework_deadline_separate_and_notified_once(monkeypatch,tmp_path):
    m=setup(monkeypatch);r=submitted(m,tmp_path)
    life.decide(r['id'],2,'rework','Исправить',at('2026-12-07T18:00').isoformat())
    b=bot()
    async def run():
        await flow.schedule(b,at('2026-12-07T17:59'))
        # Исключаем уведомления о новом ежемесячном отчете.
        before=b.send_message.await_count
        await flow.schedule(b,at('2026-12-07T18:00'))
        assert b.send_message.await_count==before+2
        assert all('доработка' in c.kwargs['text'] for c in b.send_message.call_args_list[-2:])
        await flow.schedule(b,at('2026-12-07T18:01'));assert b.send_message.await_count==before+2
    asyncio.run(run())


def test_cancel_feedback_and_revoked_status_access(monkeypatch,tmp_path):
    m=setup(monkeypatch);r=submitted(m,tmp_path);context=SimpleNamespace(bot=bot())
    async def run():
        await flow.callback(update(42,callback=f"ml:feedback:{r['id']}:2"),context)
        await flow.reply(update(42,'отмена'),context)
        assert life.workflow(r['id'])['state']=='submitted'
        life.dialog(99,dict(kind='status',market_id=m['id'],month='2026-10',closure='месяц открыт'))
        remove_manager(99)
        await flow.reply(update(99,'Успеваю'),context)
        context.bot.send_message.assert_not_awaited()
    asyncio.run(run())


def test_new_installation_does_not_backfill_past_deadline(monkeypatch):
    setup(monkeypatch);b=bot()
    asyncio.run(flow.schedule(b,at('2026-11-26T18:00')))
    b.send_message.assert_not_awaited()


def test_unsent_overdue_is_discarded_after_submission(monkeypatch,tmp_path):
    m=setup(monkeypatch);b=bot();b.send_message.side_effect=RuntimeError('offline')
    async def run():
        await flow.schedule(b,at('2026-11-05T18:00'))
        assert life.pending()
        submitted(m,tmp_path)
        b.send_message.side_effect=None;b.send_message.reset_mock()
        await flow.dispatch(b)
        b.send_message.assert_not_awaited()
        assert not life.pending()
    asyncio.run(run())


def test_status_and_feedback_dialogs_survive_restart_and_do_not_replace_report(monkeypatch):
    m=setup(monkeypatch);context=SimpleNamespace(bot=bot())
    storage.set_dialog(99,{'kind':'report','report_id':55,'field':'summary'})
    life.dialog(99,dict(kind='status',market_id=m['id'],month='2026-10',closure='месяц закрыт'))
    from monitoring.db import init_schema
    init_schema()
    async def run():
        await flow.reply(update(99,'закрыт | не успеваю'),context)
        assert life.dialog(99) is not None
        context.bot.send_message.assert_not_awaited()
        await flow.reply(update(99,'закрыт | успеваю'),context)
        assert life.dialog(99) is None
        assert storage.get_dialog(99)['field']=='summary'
        assert context.bot.send_message.call_args.kwargs['chat_id']==42
    asyncio.run(run())


def test_invalid_rework_deadline_keeps_report_submitted(monkeypatch,tmp_path):
    m=setup(monkeypatch);r=submitted(m,tmp_path)
    monkeypatch.setattr(life,'now',lambda:at('2026-11-06T09:00'))
    context=SimpleNamespace(bot=bot())
    async def run():
        await flow.callback(update(42,callback=f"ml:rework:{r['id']}:2"),context)
        await flow.reply(update(42,'05.11.2026 18:00 | Уточни расходы'),context)
        assert life.workflow(r['id'])['state']=='submitted' and life.dialog(42)
        context.bot.send_message.assert_not_awaited()
    asyncio.run(run())


def test_late_submission_still_reports_missed_deadline(monkeypatch,tmp_path):
    m=setup(monkeypatch);b=bot()
    async def run():
        await flow.schedule(b,at('2026-11-04T18:00'))
        b.send_message.reset_mock()
        monkeypatch.setattr(life,'stamp',lambda:at('2026-11-05T18:05').isoformat())
        submitted(m,tmp_path)
        await flow.schedule(b,at('2026-11-05T18:06'))
        assert b.send_message.await_count==2
        assert all('предоставлен с опозданием' in c.kwargs['text'] for c in b.send_message.call_args_list)
    asyncio.run(run())


def test_next_report_uses_only_accepted_previous_version(monkeypatch,tmp_path):
    from bot import manager_report_flow as report_flow
    m=setup(monkeypatch);r=submitted(m,tmp_path)
    next_report={'market_id':m['id'],'month':'2026-11','model':{'previous_tasks':[]},'answers':{}}
    captured=[]
    monkeypatch.setattr(report_flow,'question_list',lambda report,previous,events:captured.append(previous))
    report_flow._questions(next_report)
    assert captured[-1] is None
    assert life.decide(r['id'],2,'accept')
    report_flow._questions(next_report)
    assert captured[-1]['answers']['next_tasks']==r['answers']['next_tasks']
