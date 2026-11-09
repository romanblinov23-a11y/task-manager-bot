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
