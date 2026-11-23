import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import spot_events as flow
from bot.manager_admin import _commands_for_manager
from bot.shift_reports import render_team_morning_message
from monitoring.managers import register_manager, approve_manager
from monitoring.markets import list_markets
from monitoring.spot_events import events_for_day, replace_week


MONDAY = '2026-11-23'


def test_parser_validates_entire_week_and_derives_dates():
    events = flow.parse_events('29.11.2026 | 19:00 | Лекция | 20\n23.11.2026 | 9:00 | Каппинг | 0', MONDAY)
    assert [e['date'] for e in events] == [MONDAY, '2026-11-29']
    assert events[0]['time'] == '09:00'
    for text in ('30.11.2026 | 09:00 | Встреча | 2', '23.11.2026 | 24:00 | Встреча | 2',
                 '23.11.2026 | 12:60 | Встреча | 2', '23.11.2026 | 12:00 | Встреча | -2',
                 '23.11.2026 | 12:00 | Встреча | 1.5', '31.11.2026 | 12:00 | Встреча | 2', ''):
        with pytest.raises(ValueError):
            flow.parse_events(text, MONDAY)


def test_week_replacement_is_scoped_and_clearable():
    first, second = list_markets()[:2]
    events = flow.parse_events('23.11.2026 | 18:00 | Каппинг | 15', MONDAY)
    replace_week(first['id'], MONDAY, events, 99)
    replace_week(second['id'], MONDAY, events, 99)
    later = flow.parse_events('30.11.2026 | 18:00 | Лекция | 25', '2026-11-30')
    replace_week(first['id'], '2026-11-30', later, 99)
    replace_week(first['id'], MONDAY, [], 99)
    assert not events_for_day(first['id'], MONDAY)
    assert len(events_for_day(second['id'], MONDAY)) == 1
    assert len(events_for_day(first['id'], '2026-11-30')) == 1


def test_morning_events_are_scoped_escaped_and_work_without_sales_plan():
    first, second = list_markets()[:2]
    events = flow.parse_events('23.11.2026 | 18:00 | Каппинг <кофе> | 15', MONDAY)
    replace_week(first['id'], MONDAY, events, 99)
    text = render_team_morning_message(first, MONDAY, None)
    assert 'Понедельник, 23.11.2026' in text
    assert '18:00 — Каппинг &lt;кофе&gt;; ожидаем 15 чел.' in text
    assert render_team_morning_message(second, MONDAY, None) is None
    assert render_team_morning_message(first, '2026-11-24', None) is None
    with_plan = render_team_morning_message(first, MONDAY, dict(revenue_plan=1000, checks_plan=10))
    assert 'Выручка: 1 000,00' in with_plan
    assert 'Каппинг' in with_plan


def _update(uid, text='', callback=None):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    query = SimpleNamespace(from_user=SimpleNamespace(id=uid), data=callback, message=message,
                            answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid), effective_message=message, callback_query=query)


def test_flow_preview_confirm_and_recheck_project_access(monkeypatch):
    asyncio.run(_flow_scenario(monkeypatch))


async def _flow_scenario(monkeypatch):
    market = list_markets()[0]
    monkeypatch.setattr(flow, 'is_owner', lambda uid: False)
    uid = 889
    register_manager(uid, 'Маркетолог', 'Маркетолог', market['id'])
    assert not flow.available_markets(uid)  # pending
    approve_manager(uid)
    assert [m['id'] for m in flow.available_markets(uid)] == [market['id']]
    await flow.on_events_command(_update(uid), None)
    await flow.on_events_callback(_update(uid, callback='events:mode:replace'), None)
    await flow.on_events_reply(_update(uid, '23.11.2026'), None)
    update = _update(uid, '23.11.2026 | 18:00 | Каппинг | 15')
    await flow.on_events_reply(update, None)
    assert 'понедельник' in update.effective_message.reply_text.call_args.args[0]
    assert not events_for_day(market['id'], MONDAY)  # preview does not save
    token = flow._pending[uid]['token']
    await flow.on_events_callback(_update(uid, callback='events:save:old'), None)
    assert not events_for_day(market['id'], MONDAY)
    await flow.on_events_callback(_update(uid, callback=f'events:save:{token}'), None)
    assert len(events_for_day(market['id'], MONDAY)) == 1
    # Crafted callback for an unassigned project must not start a session.
    await flow.on_events_callback(_update(uid, callback=f"events:market:{list_markets()[1]['id']}"), None)
    assert uid not in flow._pending
    await flow.on_events_command(_update(uid), None)
    await flow.on_events_callback(_update(uid, callback='events:mode:replace'), None)
    await flow.on_events_reply(_update(uid, '23.11.2026'), None)
    await flow.on_events_reply(_update(uid, 'нет мероприятий'), None)
    token = flow._pending[uid]['token']
    monkeypatch.setattr(flow, 'available_markets', lambda _: [])
    await flow.on_events_callback(_update(uid, callback=f'events:save:{token}'), None)
    assert len(events_for_day(market['id'], MONDAY)) == 1
    flow._pending.clear()


def test_marketer_menu():
    market = list_markets()[0]
    register_manager(889, 'Маркетолог', 'Маркетолог', market['id'])
    approve_manager(889)
    commands = _commands_for_manager(dict(telegram_user_id=889, position='Маркетолог', status='active'))
    assert 'spot_events' in [c.command for c in commands]


def test_additions_preserve_existing_plan_and_are_idempotent():
    from monitoring.spot_events import add_events, week_submitted
    market = list_markets()[0]
    original = flow.parse_events('23.11.2026 | 18:00 | Каппинг | 15', MONDAY)
    extra = flow.parse_events('23.11.2026 | 19:00 | Лекция | 20', MONDAY)
    replace_week(market['id'], MONDAY, original, 99)
    assert add_events(market['id'], original + extra, 99) == 1
    assert add_events(market['id'], extra, 99) == 0
    assert len(events_for_day(market['id'], MONDAY)) == 2
    assert week_submitted(market['id'], MONDAY)


def test_force_next_week_button_and_add_mode(monkeypatch):
    async def scenario():
        from datetime import date
        uid = 777
        market = list_markets()[0]
        monkeypatch.setattr(flow, 'available_markets', lambda _: [market])
        monkeypatch.setattr(flow, 'tz_today', lambda: date(2026, 11, 23))
        await flow.on_events_command(_update(uid), None)
        await flow.on_events_callback(_update(uid, callback=f"events:next:{market['id']}"), None)
        assert flow._pending[uid]['week'] == '2026-11-30'
        assert flow._pending[uid]['step'] == 'list'
        await flow.on_events_reply(_update(uid, '30.11.2026 | 18:00 | Каппинг | 15'), None)
        token = flow._pending[uid]['token']
        await flow.on_events_callback(_update(uid, callback=f'events:save:{token}'), None)
        await flow.on_events_command(_update(uid), None)
        await flow.on_events_callback(_update(uid, callback='events:mode:add'), None)
        await flow.on_events_reply(_update(uid, '30.11.2026'), None)
        await flow.on_events_reply(_update(uid, '30.11.2026 | 19:00 | Лекция | 10'), None)
        token = flow._pending[uid]['token']
        await flow.on_events_callback(_update(uid, callback=f'events:save:{token}'), None)
        assert len(events_for_day(market['id'], '2026-11-30')) == 2
    asyncio.run(scenario())


def test_sunday_requests_escalations_and_preloaded_empty_week(monkeypatch):
    from datetime import datetime
    from bot import spot_event_schedule as schedule
    from monitoring.spot_events import week_submitted

    async def scenario():
        market = list_markets()[0]
        register_manager(777, 'Маркетолог', 'Маркетолог', market['id'])
        approve_manager(777)
        register_manager(778, 'Управляющий', 'Управляющий', market['id'])
        approve_manager(778)
        bot = SimpleNamespace(send_message=AsyncMock())
        monkeypatch.setattr(schedule, 'ROMAN_TELEGRAM_ID', 779)
        monkeypatch.setattr(schedule, 'tz_now', lambda: datetime(2026, 11, 29, 8, 59))
        await schedule.send_event_schedule_notices(bot)
        bot.send_message.assert_not_called()
        monkeypatch.setattr(schedule, 'tz_now', lambda: datetime(2026, 11, 29, 9, 0))
        await schedule.send_event_schedule_notices(bot)
        await schedule.send_event_schedule_notices(bot)
        assert [c.kwargs['chat_id'] for c in bot.send_message.call_args_list] == [777]
        monkeypatch.setattr(schedule, 'tz_now', lambda: datetime(2026, 11, 29, 18, 0))
        await schedule.send_event_schedule_notices(bot)
        await schedule.send_event_schedule_notices(bot)
        assert [c.kwargs['chat_id'] for c in bot.send_message.call_args_list] == [777, 778]
        monkeypatch.setattr(schedule, 'tz_now', lambda: datetime(2026, 11, 29, 21, 0))
        await schedule.send_event_schedule_notices(bot)
        await schedule.send_event_schedule_notices(bot)
        assert [c.kwargs['chat_id'] for c in bot.send_message.call_args_list] == [777, 778, 779]
        replace_week(market['id'], '2026-12-07', [], 777)
        assert week_submitted(market['id'], '2026-12-07')
        monkeypatch.setattr(schedule, 'tz_now', lambda: datetime(2026, 12, 6, 21, 0))
        await schedule.send_event_schedule_notices(bot)
        assert bot.send_message.call_count == 3  # explicit empty plan suppresses all notices
        monkeypatch.setattr(schedule, 'tz_now', lambda: datetime(2026, 12, 7, 21, 0))
        await schedule.send_event_schedule_notices(bot)
        assert bot.send_message.call_count == 3  # weekdays do not escalate
    asyncio.run(scenario())


def test_failed_notice_retried_and_submission_stops_escalation(monkeypatch):
    from datetime import datetime
    from bot import spot_event_schedule as schedule

    async def scenario():
        market = list_markets()[0]
        register_manager(777, 'Маркетолог', 'Маркетолог', market['id'])
        approve_manager(777)
        register_manager(778, 'Управляющий', 'Управляющий', market['id'])
        approve_manager(778)
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[RuntimeError('offline'), None]))
        monkeypatch.setattr(schedule, 'tz_now', lambda: datetime(2026, 11, 29, 9, 0))
        await schedule.send_event_schedule_notices(bot)
        await schedule.send_event_schedule_notices(bot)
        assert bot.send_message.call_count == 2
        replace_week(market['id'], '2026-11-30', [], 777)
        monkeypatch.setattr(schedule, 'tz_now', lambda: datetime(2026, 11, 29, 21, 0))
        await schedule.send_event_schedule_notices(bot)
        assert bot.send_message.call_count == 2
    asyncio.run(scenario())
