import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import ReplyKeyboardRemove
from bot import menu
from monitoring.managers import register_manager,approve_manager,set_manager_blocks,acknowledge_block,remove_manager,set_manager_position
from monitoring.markets import list_markets


def manager(monkeypatch):
    monkeypatch.setattr(menu.settings,'ROMAN_TELEGRAM_ID','42')
    uid=99;register_manager(uid,'Иванов Иван','Управляющий',list_markets()[0]['id']);approve_manager(uid)
    return uid


def update(uid,text='',callback=''):
    message=SimpleNamespace(text=text,chat=SimpleNamespace(type='private'),reply_text=AsyncMock())
    q=SimpleNamespace(from_user=SimpleNamespace(id=uid),data=callback,message=message,answer=AsyncMock(),edit_message_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid),effective_message=message,callback_query=q)


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def test_roman_menu_groups_and_all_commands_are_accessible(monkeypatch):
    monkeypatch.setattr(menu.settings,'ROMAN_TELEGRAM_ID','42')
    root=buttons(menu.inline_menu(42))
    assert len(root)==6
    available={b.callback_data.removeprefix('nav:cmd:') for category in menu.CATEGORIES for b in buttons(menu.inline_menu(42,category)) if b.callback_data.startswith('nav:cmd:')}
    assert available==set(menu.CATALOG)
    assert menu.reply_keyboard(42).is_persistent
    assert menu.reply_keyboard(42).resize_keyboard


def test_supervisor_commands_match_approved_blocks_and_hide_owner_actions(monkeypatch):
    uid=manager(monkeypatch)
    set_manager_blocks(uid,['monitoring','reports','meetings','accounting','handover'])
    assert menu.allowed(uid)=={'manager_report','spot_events','regulations'}
    for block in ['monitoring','reports','meetings','accounting','handover']:acknowledge_block(uid,block)
    from bot.manager_admin import _commands_for_manager
    from monitoring.managers import get_manager
    expected={c.command for c in _commands_for_manager(get_manager(uid))}-{'menu'}
    assert menu.allowed(uid)==expected
    assert not {'employees','manager_kpi_plan','founders_report','revenue'} & menu.allowed(uid)
    callbacks={b.callback_data for b in buttons(menu.inline_menu(uid))}
    assert callbacks=={'nav:cmd:'+c for c in expected}


def test_stale_button_denied_after_role_or_permission_change(monkeypatch):
    uid=manager(monkeypatch)
    handler=AsyncMock();ctx=SimpleNamespace(application=SimpleNamespace(bot_data={'menu_commands':{'manager_report':handler}}))
    set_manager_position(uid,'Бариста')
    u=update(uid,callback='nav:cmd:manager_report')
    asyncio.run(menu.on_menu_callback(u,ctx))
    handler.assert_not_awaited();u.callback_query.answer.assert_awaited_once()
    assert isinstance(menu.reply_keyboard(uid),ReplyKeyboardRemove)


def test_command_callback_uses_clicking_user_and_empty_args(monkeypatch):
    uid=manager(monkeypatch);handler=AsyncMock()
    ctx=SimpleNamespace(args=['stale-month'],user_data={'kept':True},application=SimpleNamespace(bot_data={'menu_commands':{'manager_report':handler}}))
    u=update(uid,callback='nav:cmd:manager_report')
    asyncio.run(menu.on_menu_callback(u,ctx))
    adapted,context=handler.call_args.args
    assert adapted.effective_user.id==uid
    assert adapted.effective_message is u.callback_query.message
    assert adapted.callback_query is None
    assert context.args==[] and context.user_data is ctx.user_data
    assert ctx.args==['stale-month']


def test_home_navigation_edits_same_menu_and_returns_to_root(monkeypatch):
    monkeypatch.setattr(menu.settings,'ROMAN_TELEGRAM_ID','42')
    u=update(42,callback='nav:cat:reports')
    asyncio.run(menu.on_menu_callback(u,SimpleNamespace()))
    assert 'Отчеты' in u.callback_query.edit_message_text.call_args.args[0]
    assert 'nav:home' in {b.callback_data for b in buttons(u.callback_query.edit_message_text.call_args.kwargs['reply_markup'])}


def test_persistent_keyboard_delivered_once_and_refreshed_when_permissions_change(monkeypatch):
    uid=manager(monkeypatch);bot=SimpleNamespace(send_message=AsyncMock())
    async def run():
        await menu.sync_keyboard(bot,uid)
        assert bot.send_message.await_count==1
        from monitoring.db import init_schema
        init_schema()
        await menu.sync_keyboard(bot,uid)
        assert bot.send_message.await_count==1
        set_manager_blocks(uid,['reports']);acknowledge_block(uid,'reports')
        await menu.sync_keyboard(bot,uid)
        assert bot.send_message.await_count==2
        remove_manager(uid)
        await menu.sync_keyboard(bot,uid)
        assert isinstance(bot.send_message.call_args.kwargs['reply_markup'],ReplyKeyboardRemove)
        await menu.sync_keyboard(bot,uid)
        assert bot.send_message.await_count==3
    asyncio.run(run())


def test_failed_keyboard_delivery_can_retry(monkeypatch):
    uid=manager(monkeypatch);bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError('offline')))
    async def run():
        await menu.sync_keyboard(bot,uid)
        bot.send_message.side_effect=None
        await menu.sync_keyboard(bot,uid)
        assert bot.send_message.await_count==2
    asyncio.run(run())


def test_keyboard_shortcut_precedes_report_answers(monkeypatch):
    from bot import private
    uid=manager(monkeypatch);handler=AsyncMock()
    context=SimpleNamespace(application=SimpleNamespace(bot_data={'menu_commands':{'manager_report':handler}}))
    answer=AsyncMock();monkeypatch.setattr(private,'on_manager_report_lifecycle_reply',answer)
    asyncio.run(private.on_private_text(update(uid,'📄 Отчет за месяц'),context))
    handler.assert_awaited_once();answer.assert_not_awaited()


def test_normal_text_still_reaches_active_report(monkeypatch):
    from bot import private
    uid=manager(monkeypatch)
    reply=AsyncMock(return_value=True);monkeypatch.setattr(private,'on_manager_report_lifecycle_reply',reply)
    asyncio.run(private.on_private_text(update(uid,'Причина отклонения от плана'),SimpleNamespace()))
    reply.assert_awaited_once()


def test_pending_unapproved_user_does_not_get_keyboard(monkeypatch):
    monkeypatch.setattr(menu.settings,'ROMAN_TELEGRAM_ID','42')
    register_manager(99,'Ожидающий','Управляющий',list_markets()[0]['id'])
    assert menu.allowed(99)==set()
    assert isinstance(menu.reply_keyboard(99),ReplyKeyboardRemove)


def test_help_documents_every_command_and_respects_telegram_message_limit(monkeypatch):
    from bot import menu_help
    monkeypatch.setattr(menu.settings,'ROMAN_TELEGRAM_ID','42')
    assert set(menu_help.DESCRIPTIONS)==set(menu.CATALOG)
    message=SimpleNamespace(reply_text=AsyncMock())
    asyncio.run(menu_help.show_help(message,42,'all'))
    messages=[c.args[0] for c in message.reply_text.call_args_list]
    text='\n'.join(messages)
    assert all(len(part)<=3500 for part in messages)
    assert all('/'+key+' —' in text for key in menu.CATALOG)
    assert '/menu' in text and '/help' in text and '/spravka' in text
    assert menu.CATEGORIES['settings']+' → '+menu.CATEGORIES['monitoring'] in text


def test_owner_help_available_by_button_word_and_help_command(monkeypatch):
    from bot import onboarding,menu_help
    monkeypatch.setattr(menu.settings,'ROMAN_TELEGRAM_ID','42')
    assert '❓ Справка' in [button.text for row in menu.reply_keyboard(42).keyboard for button in row]
    show=AsyncMock();monkeypatch.setattr(menu_help,'show_help',show)
    async def run():
        for text in ('❓ Справка','справка'):
            assert await menu.on_menu_text(update(42,text),SimpleNamespace())
        await onboarding.on_help(update(42),SimpleNamespace())
        assert show.await_count==3
        message=update(99,callback='nav:help:all')
        await menu.on_menu_callback(message,SimpleNamespace())
        assert show.await_count==3
    asyncio.run(run())
