import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import fp_report_flow as flow
from fp_report import session as storage


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'FP_REPORTS_DIR', tmp_path)
    monkeypatch.setattr(flow, 'has_fp_report_access', lambda uid: uid == 42)
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)
    return update, message


def test_change_stale_month_preserves_uploaded_files(chat):
    update, message = chat
    storage.save_session(42, {'month': '2026-09', 'stage': 'files'})
    template = storage.session_dir(42) / 'template.docx'
    template.write_bytes(b'template')
    inventory = storage.session_dir(42) / 'inventory.xlsx'
    inventory.write_bytes(b'inventory')
    asyncio.run(flow.on_fp_report_command(update, SimpleNamespace(args=['2026-10'])))
    assert storage.load_session(42)['month'] == '2026-10'
    assert template.read_bytes() == b'template'
    assert inventory.read_bytes() == b'inventory'
    assert flow._month_before(storage.load_session(42)['month']) == '2026-09'
    assert 'Октябрь 2026' in message.reply_text.call_args_list[0].args[0]


def test_resume_shows_saved_month_without_silently_changing_it(chat):
    update, message = chat
    storage.save_session(42, {'month': '2026-09', 'stage': 'files'})
    asyncio.run(flow.on_fp_report_command(update, SimpleNamespace(args=[])))
    assert storage.load_session(42)['month'] == '2026-09'
    assert 'Сентябрь 2026' in message.reply_text.call_args_list[0].args[0]


def test_new_report_defaults_to_last_calendar_month(chat, monkeypatch):
    update, _ = chat
    monkeypatch.setattr(flow, 'tz_now', lambda: datetime(2026, 11, 9))
    monkeypatch.setattr(flow, '_available_markets', lambda uid: [{'id': 1, 'name': 'Парк'}])
    asyncio.run(flow.on_fp_report_command(update, SimpleNamespace(args=[])))
    assert storage.load_session(42)['month'] == '2026-10'


@pytest.mark.parametrize('month', ['2026-13', '2026-0', '0000-01', 'октябрь'])
def test_invalid_period_does_not_change_session(chat, month):
    update, _ = chat
    asyncio.run(flow.on_fp_report_command(update, SimpleNamespace(args=[month])))
    assert storage.load_session(42) is None


def test_cannot_change_month_after_collection(chat):
    update, _ = chat
    storage.save_session(42, {'month': '2026-09', 'stage': 'questions'})
    asyncio.run(flow.on_fp_report_command(update, SimpleNamespace(args=['2026-10'])))
    assert storage.load_session(42)['month'] == '2026-09'


def test_incompatible_previous_report_blocks_change(chat, monkeypatch):
    update, _ = chat
    storage.save_session(42, {'month': '2026-09', 'stage': 'files'})
    (storage.session_dir(42) / 'previous.docx').write_bytes(b'previous')
    monkeypatch.setattr(flow, 'month_label_of', lambda path: 'Август 2026')
    asyncio.run(flow.on_fp_report_command(update, SimpleNamespace(args=['2026-10'])))
    assert storage.load_session(42)['month'] == '2026-09'


def test_january_previous_period():
    assert flow._month_before('2026-01') == '2025-12'


def test_non_owner_cannot_resume_saved_session(chat):
    _, message = chat
    storage.save_session(99, {'month': '2026-10', 'stage': 'files'})
    update = SimpleNamespace(effective_user=SimpleNamespace(id=99), effective_message=message)
    asyncio.run(flow.on_fp_report_command(update, SimpleNamespace(args=[])))
    assert 'только Роману' in message.reply_text.call_args.args[0]
    assert storage.load_session(99)['stage'] == 'files'


def test_old_manager_buttons_are_denied(chat):
    _, message = chat
    query = SimpleNamespace(from_user=SimpleNamespace(id=99), answer=AsyncMock(), edit_message_text=AsyncMock(), data='fpr_market:1:2026-10')
    asyncio.run(flow.on_fp_report_market_choice(SimpleNamespace(callback_query=query), SimpleNamespace()))
    query.answer.assert_awaited_once()
    assert query.answer.call_args.kwargs['show_alert'] is True
    query.edit_message_text.assert_not_awaited()
    assert storage.load_session(99) is None


def test_manager_files_and_answers_do_not_reach_old_session(chat):
    _, message = chat
    storage.save_session(99, {'month': '2026-10', 'stage': 'files'})
    update = SimpleNamespace(effective_user=SimpleNamespace(id=99), effective_message=message)
    assert asyncio.run(flow.on_fp_report_document(update, SimpleNamespace())) is False
    assert asyncio.run(flow.on_fp_report_reply(update, SimpleNamespace())) is False
    message.reply_text.assert_not_awaited()


def test_only_roman_has_founders_report_access(monkeypatch):
    from monitoring import managers
    monkeypatch.setattr(managers, 'ROMAN_TELEGRAM_ID', '42')
    monkeypatch.setattr(managers, 'OWNER_TELEGRAM_IDS', {'42', '99'})
    assert managers.has_fp_report_access(42)
    assert not managers.has_fp_report_access(99)  # Даже другой владелец.
    assert not managers.has_fp_report_access(100)


def test_migration_removes_old_staff_report_block():
    from monitoring.db import get_connection, init_schema
    from monitoring.managers import register_manager
    from monitoring.markets import create_market
    market = create_market('Отчёт учредителям')
    register_manager(99, 'Управляющий', 'Управляющий', market['id'])
    conn = get_connection()
    try:
        conn.execute("UPDATE manager SET blocks='monitoring,fp_report,accounting', blocks_ack='fp_report,monitoring' WHERE telegram_user_id=99")
        conn.commit()
    finally:
        conn.close()
    init_schema()
    conn = get_connection()
    try:
        row = conn.execute('SELECT blocks,blocks_ack FROM manager WHERE telegram_user_id=99').fetchone()
        assert row['blocks'] == 'monitoring,accounting'
        assert row['blocks_ack'] == 'monitoring'
    finally:
        conn.close()


def test_finished_report_goes_only_to_roman(chat, monkeypatch, tmp_path):
    _, message = chat
    report = tmp_path / 'report.docx'
    report.write_bytes(b'report')
    monkeypatch.setattr(flow, 'get_market', lambda mid: {'name': 'Парк', 'surf_spot_key': 'park_gorkogo'})
    monkeypatch.setattr(flow, 'load_inputs', lambda uid: {key: {} for key in ['pnl', 'manager', 'vozn', 'komanda', 'shtat', 'grafik']})
    monkeypatch.setattr(flow, 'load_save_positions', lambda path: [])
    monkeypatch.setattr(flow, '_previous_defaults', lambda uid: {})
    monkeypatch.setattr(flow, 'build_report', lambda **kwargs: str(report))
    monkeypatch.setattr(flow, 'count_open_fields', lambda path: 0)
    bot = SimpleNamespace(send_document=AsyncMock())
    asyncio.run(flow._finish(message, SimpleNamespace(bot=bot), 42, {'market_id': 1, 'month': '2026-10', 'answers': {}}))
    bot.send_document.assert_awaited_once()
    assert bot.send_document.call_args.kwargs['chat_id'] == 42
    assert 'учредителей' in bot.send_document.call_args.kwargs['caption']


def test_founders_command_is_hidden_from_other_owners(monkeypatch):
    import main
    monkeypatch.setattr(main, 'OWNER_TELEGRAM_IDS', {'42', '99'})
    monkeypatch.setattr(main, 'has_fp_report_access', lambda uid: uid == 42)
    monkeypatch.setattr(main, 'list_managers', lambda: [])
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    asyncio.run(main._set_bot_commands(SimpleNamespace(bot=bot)))
    menus = {call.kwargs['scope'].chat_id: [command.command for command in call.args[0]] for call in bot.set_my_commands.call_args_list if hasattr(call.kwargs['scope'], 'chat_id')}
    assert 'founders_report' in menus[42]
    assert 'founders_report' not in menus[99]
