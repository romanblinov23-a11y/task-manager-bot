import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.regulations import MARKETER_REGULATION, on_regulations_command, on_regulations_view
from monitoring.managers import register_manager, approve_manager
from monitoring.markets import list_markets


def test_marketer_sees_and_reads_regulation_without_reporting_blocks(monkeypatch):
    async def scenario():
        market = list_markets()[0]
        uid = 989
        register_manager(uid, 'Маркетолог', 'Маркетолог', market['id'])
        approve_manager(uid)
        import bot.spot_events as events
        monkeypatch.setattr(events, 'is_owner', lambda _: False)
        message = SimpleNamespace(reply_text=AsyncMock())
        query = SimpleNamespace(from_user=SimpleNamespace(id=uid), data='reg_view:spot_events', message=message, answer=AsyncMock())
        update = SimpleNamespace(effective_user=query.from_user, effective_message=message, callback_query=query)
        await on_regulations_command(update, None)
        keyboard = message.reply_text.call_args.kwargs['reply_markup']
        assert any(b.callback_data == 'reg_view:spot_events' for row in keyboard.inline_keyboard for b in row)
        message.reply_text.reset_mock()
        await on_regulations_view(update, None)
        text = '\n\n'.join(c.args[0] for c in message.reply_text.call_args_list)
        assert text == MARKETER_REGULATION
        for phrase in ('09:00', '18:00', '21:00', 'нет мероприятий', 'Добавить мероприятия', 'Заменить план недели', 'Загрузить следующую неделю'):
            assert phrase in text
    asyncio.run(scenario())


def test_unassigned_employee_cannot_open_marketer_regulation(monkeypatch):
    async def scenario():
        market = list_markets()[0]
        uid = 988
        register_manager(uid, 'Бариста', 'Бариста', market['id'])
        approve_manager(uid)
        import bot.spot_events as events
        monkeypatch.setattr(events, 'is_owner', lambda _: False)
        message = SimpleNamespace(reply_text=AsyncMock())
        query = SimpleNamespace(from_user=SimpleNamespace(id=uid), data='reg_view:spot_events', message=message, answer=AsyncMock())
        await on_regulations_view(SimpleNamespace(callback_query=query), None)
        message.reply_text.assert_not_called()
        assert query.answer.call_args.kwargs['show_alert']
    asyncio.run(scenario())


def test_regulation_sent_after_owner_approval_then_acknowledged(monkeypatch):
    from bot import manager_admin as admin
    from bot.regulations import send_next_regulation
    from monitoring.managers import get_manager, set_manager_blocks
    from monitoring.spot_events import marketer_regulation_read

    async def scenario():
        uid, owner = 987, 986
        market = list_markets()[0]
        register_manager(uid, 'Маркетолог', 'Маркетолог', market['id'])
        set_manager_blocks(uid, [])
        message = SimpleNamespace(reply_text=AsyncMock())
        assert not await send_next_regulation(message, uid)
        message.reply_text.assert_not_called()
        monkeypatch.setattr(admin, 'is_owner', lambda value: value == owner)
        monkeypatch.setattr(admin, '_notify_approved', AsyncMock())
        monkeypatch.setattr(admin, 'send_consent_request', AsyncMock())
        monkeypatch.setattr(admin, '_pending_blocks', {str(owner): dict(uid=uid, selected=set(), mode='approve')})
        bot = SimpleNamespace(send_message=AsyncMock(), set_my_commands=AsyncMock())
        query = SimpleNamespace(from_user=SimpleNamespace(id=owner), data=f'mgr_blocksdone:{uid}',
                                answer=AsyncMock(), edit_message_text=AsyncMock())
        await admin.on_manager_blocks_done(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot))
        assert get_manager(uid)['status'] == 'active'
        messages = [c for c in bot.send_message.call_args_list if c.kwargs.get('chat_id') == uid]
        assert any(c.kwargs['text'] in MARKETER_REGULATION for c in messages)
        assert messages[-1].kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'reg_ack:spot_events'
        assert not marketer_regulation_read(uid)
        query = SimpleNamespace(from_user=SimpleNamespace(id=uid), data='reg_ack:spot_events',
                                answer=AsyncMock(), edit_message_reply_markup=AsyncMock())
        await admin.on_regulation_ack(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot))
        assert marketer_regulation_read(uid)
        assert not await send_next_regulation(message, uid)  # persisted, not sent again
        import monitoring.db as db
        db.init_schema()  # restart/migration preserves acknowledgement
        assert marketer_regulation_read(uid)
    asyncio.run(scenario())
