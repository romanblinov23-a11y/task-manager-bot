import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from telegram import MessageEntity
from bot import messaging as flow
from monitoring import broadcasts


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(flow,'is_owner',lambda uid:uid in (42,43))
    monkeypatch.setattr(flow,'_active_managers',lambda:[{'telegram_user_id':99,'name':'Управляющий А'},{'telegram_user_id':100,'name':'Управляющий Б'}])
    flow._awaiting_dm.clear();flow._awaiting_chat_message.clear()
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(),send_document=AsyncMock()))


def update(uid=42,text=None,caption=None,document=None,callback=''):
    m=SimpleNamespace(text=text,caption=caption,document=document,entities=[],caption_entities=[],media_group_id=None,reply_text=AsyncMock())
    q=SimpleNamespace(from_user=SimpleNamespace(id=uid),data=callback,answer=AsyncMock(),message=m,edit_message_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid),effective_message=m,callback_query=q)


def doc(file_id='file-pdf'):return SimpleNamespace(file_id=file_id,file_name='Образец.pdf')


def start():return broadcasts.start(42,[{'telegram_user_id':99,'name':'Управляющий А'}])


def confirm(uid=42,token=None):return update(uid,callback='bcast_confirm:'+(token or broadcasts.get(uid)['token']))


def calls_to(bot,uid):
    return [c for method in (bot.send_message,bot.send_document) for c in method.call_args_list if c.kwargs['chat_id']==uid]


def test_pdf_caption_preview_only_owner_then_explicit_confirm_sends(setup):
    start();u=update(caption='Новый отчет за ноябрь',document=doc())
    u.effective_message.caption_entities=[MessageEntity('bold',0,5)]
    async def run():
        assert await flow.on_broadcast_document(u,setup)
        assert len(calls_to(setup.bot,42))==2
        assert not calls_to(setup.bot,99)
        state=broadcasts.get(42)
        assert state['content']['text']=='Новый отчет за ноябрь'
        assert state['content']['entities'][0]['type']=='bold'
        await flow.on_broadcast_confirm(confirm(),setup)
        assert len(calls_to(setup.bot,99))==2
        text=next(c for c in calls_to(setup.bot,99) if 'text' in c.kwargs)
        assert text.kwargs['text']=='Новый отчет за ноябрь'
        assert text.kwargs['entities'][0].type=='bold'
        assert broadcasts.get(42)['state']=='completed'
        before=len(calls_to(setup.bot,99))
        await flow.on_broadcast_confirm(confirm(),setup)
        assert len(calls_to(setup.bot,99))==before
    asyncio.run(run())


def test_attachment_after_text_invalidates_old_send_button(setup):
    start()
    async def run():
        await flow.on_broadcast_text_reply(update(text='Текст'),setup)
        old=broadcasts.get(42)['token']
        await flow.on_broadcast_document(update(document=doc()),setup)
        assert broadcasts.get(42)['token']!=old
        await flow.on_broadcast_confirm(confirm(token=old),setup)
        assert not calls_to(setup.bot,99)
        await flow.on_broadcast_confirm(confirm(),setup)
        assert len(calls_to(setup.bot,99))==2
    asyncio.run(run())


def test_file_then_text_and_long_caption_remain_complete(setup):
    start();text='Обновили бота! '*200
    async def run():
        await flow.on_broadcast_document(update(document=doc()),setup)
        await flow.on_broadcast_text_reply(update(text=text),setup)
        assert broadcasts.get(42)['content']['text']==text
        assert len(broadcasts.get(42)['content']['documents'])==1
        await flow.on_broadcast_confirm(confirm(),setup)
        sent=next(c for c in calls_to(setup.bot,99) if 'text' in c.kwargs)
        assert sent.kwargs['text']==text
        assert setup.bot.send_document.call_args.kwargs['document']=='file-pdf'
    asyncio.run(run())


def test_document_caption_longer_than_telegram_document_limit_uses_separate_text(setup):
    start();text='Привет коллеги! '*150
    asyncio.run(flow.on_broadcast_document(update(document=doc(),caption=text),setup))
    assert setup.bot.send_message.call_args.kwargs['text']==text
    assert 'caption' not in setup.bot.send_document.call_args.kwargs
    assert not calls_to(setup.bot,99)


def test_draft_survives_restart_and_never_sends_on_recovery(setup):
    start()
    async def run():
        await flow.on_broadcast_document(update(document=doc(),caption='Итоги'),setup)
        token=broadcasts.get(42)['token']
        from monitoring.db import init_schema
        init_schema();broadcasts.recover()
        assert broadcasts.get(42)['token']==token
        assert broadcasts.get(42)['content']['documents'][0]['file_id']=='file-pdf'
        assert not calls_to(setup.bot,99)
        await flow.on_broadcast_command(update(),setup)
        assert not calls_to(setup.bot,99)
    asyncio.run(run())


def test_partial_delivery_retry_only_missing_parts(setup):
    broadcasts.start(42,flow._active_managers());fail=True
    async def send_doc(**kwargs):
        if kwargs['chat_id']==99 and fail:raise RuntimeError('offline')
    setup.bot.send_document.side_effect=send_doc
    async def run():
        nonlocal fail
        await flow.on_broadcast_document(update(document=doc(),caption='Текст'),setup)
        await flow.on_broadcast_confirm(confirm(),setup)
        assert broadcasts.get(42)['state']=='failed'
        token=broadcasts.get(42)['token']
        assert broadcasts.delivered(token)=={(99,0),(100,0),(100,1)}
        setup.bot.send_message.reset_mock();setup.bot.send_document.reset_mock();fail=False
        await flow.on_broadcast_confirm(confirm(),setup)
        setup.bot.send_message.assert_not_awaited()
        setup.bot.send_document.assert_awaited_once()
        assert setup.bot.send_document.call_args.kwargs['chat_id']==99
        assert broadcasts.get(42)['state']=='completed'
    asyncio.run(run())


def test_non_owner_and_stolen_preview_cannot_send(setup):
    start()
    async def run():
        await flow.on_broadcast_text_reply(update(text='Текст'),setup)
        token=broadcasts.get(42)['token']
        await flow.on_broadcast_confirm(confirm(uid=99,token=token),setup)
        await flow.on_broadcast_confirm(confirm(uid=43,token=token),setup)
        assert not calls_to(setup.bot,99)
        assert broadcasts.get(42)['state']=='review'
    asyncio.run(run())


def test_removed_recipient_requires_new_audience_confirmation(setup,monkeypatch):
    start()
    async def run():
        await flow.on_broadcast_text_reply(update(text='Текст'),setup)
        monkeypatch.setattr(flow,'_active_managers',lambda:[])
        await flow.on_broadcast_confirm(confirm(),setup)
        assert not calls_to(setup.bot,99)
        assert broadcasts.get(42)['state']=='review'
    asyncio.run(run())


def test_cancel_and_legacy_confirmation_never_send(setup):
    start()
    async def run():
        await flow.on_broadcast_text_reply(update(text='Текст'),setup)
        token=broadcasts.get(42)['token']
        await flow.on_broadcast_confirm(update(callback='bcast_confirm'),setup)
        assert broadcasts.get(42)['state']=='review'
        await flow.on_broadcast_cancel(update(callback='bcast_cancel:'+token),setup)
        await flow.on_broadcast_confirm(confirm(token=token),setup)
        assert broadcasts.get(42)['state']=='cancelled'
        assert not calls_to(setup.bot,99)
    asyncio.run(run())


def test_private_document_routes_to_broadcast_before_report(setup,monkeypatch):
    from bot import private
    start();report=AsyncMock(return_value=True)
    monkeypatch.setattr(private,'on_fp_report_document',report)
    async def run():
        await private.on_private_document(update(document=doc(),caption='Текст'),setup)
        report.assert_not_awaited()
        assert broadcasts.get(42)['content']['documents']
        assert not calls_to(setup.bot,99)
    asyncio.run(run())


def test_private_document_without_draft_has_correct_owner_hint(setup,monkeypatch):
    from bot import private
    monkeypatch.setattr(private,'is_owner',lambda uid:uid==42)
    monkeypatch.setattr(private,'on_fp_report_document',AsyncMock(return_value=False))
    u=update(document=doc(),caption='Текст')
    asyncio.run(private.on_private_document(u,setup))
    text=u.effective_message.reply_text.call_args.args[0]
    assert '/broadcast' in text and '/manager_report' not in text
    assert not calls_to(setup.bot,99)


def test_interrupted_delivery_resumes_only_after_confirmation(setup):
    start()
    async def run():
        await flow.on_broadcast_text_reply(update(text='Текст'),setup)
        d=broadcasts.get(42)
        assert broadcasts.claim(42,d['token'])
        broadcasts.recover()
        assert broadcasts.get(42)['state']=='failed'
        assert not calls_to(setup.bot,99)
        await flow.on_broadcast_confirm(confirm(),setup)
        assert len(calls_to(setup.bot,99))==1
    asyncio.run(run())


def test_navigation_pauses_broadcast_so_other_answers_are_not_consumed(setup):
    start()
    async def run():
        await flow.on_broadcast_text_reply(update(text='Текст объявления'),setup)
        old=broadcasts.get(42)['token']
        await flow.on_command_navigation(update(text='/manager_report'),setup)
        assert broadcasts.get(42)['state']=='paused'
        assert not await flow.on_broadcast_text_reply(update(text='Ответ на вопрос отчета'),setup)
        await flow.on_broadcast_confirm(confirm(token=old),setup)
        assert not calls_to(setup.bot,99)
        await flow.on_broadcast_command(update(),setup)
        assert broadcasts.get(42)['state']=='review'
        assert broadcasts.get(42)['content']['text']=='Текст объявления'
        assert broadcasts.get(42)['token']!=old
    asyncio.run(run())


def test_new_audience_selection_clears_old_direct_message_waiters(setup):
    flow._awaiting_dm['42']={'target_id':100}
    flow._awaiting_chat_message['42']={'chat_id':-100}
    asyncio.run(flow._start_broadcast_text(update().effective_message,'42',flow._active_managers()))
    assert '42' not in flow._awaiting_dm and '42' not in flow._awaiting_chat_message
    assert broadcasts.get(42)['state']=='composing'
