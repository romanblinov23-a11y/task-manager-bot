"""Проверяем запуск оставшегося бота и сохранность доступов после удаления задач."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


def test_migration_removes_only_task_access():
    from monitoring.db import get_connection, init_schema
    from monitoring.managers import register_manager
    from monitoring.markets import create_market

    market = create_market('Проверка миграции')
    register_manager(88801, 'Менеджер', 'Менеджер', market['id'])
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE manager SET blocks = ?, blocks_ack = ? WHERE telegram_user_id = ?",
            ('tasks,monitoring,fp_report', 'monitoring,tasks', 88801),
        )
        conn.commit()
    finally:
        conn.close()
    init_schema()
    init_schema()  # Повторный запуск не меняет остальные разрешения.
    conn = get_connection()
    try:
        row = conn.execute('SELECT blocks, blocks_ack FROM manager WHERE telegram_user_id = 88801').fetchone()
        assert row['blocks'] == 'monitoring,fp_report'
        assert row['blocks_ack'] == 'monitoring'
    finally:
        conn.close()


def test_startup_keeps_reports_and_monitoring_without_task_handlers(monkeypatch):
    import main

    app = SimpleNamespace(add_handler=Mock(), job_queue=SimpleNamespace(run_daily=Mock(), run_repeating=Mock()), run_polling=Mock())
    builder = Mock()
    builder.token.return_value = builder
    builder.post_init.return_value = builder
    builder.build.return_value = app
    monkeypatch.setattr(main.Application, 'builder', lambda: builder)
    monkeypatch.setattr(main, 'init_personal_data_schema', Mock())
    monkeypatch.setattr(main, 'start_dashboard_server', Mock())
    main.main()

    commands = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands.update(getattr(handler, 'commands', set()))
    assert {'monitoring', 'fp_report', 'revenue', 'shift_report', 'employees', 'register_project'} <= commands
    assert not commands & {'task_work', 'mytasks', 'status', 'employee', 'weekly', 'stuck', 'needhelp', 'dashboard_tasks'}
    jobs = {call.args[0].__name__ for mock in (app.job_queue.run_daily, app.job_queue.run_repeating) for call in mock.call_args_list}
    assert {'_monitoring_reminder_job', '_shift_report_kickoff_job', '_revenue_weekly_job', '_meeting_confirmation_job'} <= jobs
    assert not jobs & {'_status_check_job', '_daily_report_job', '_weekly_report_job', '_task_retention_job'}
    app.run_polling.assert_called_once()


def test_group_mentions_still_monitor_without_task_extraction(monkeypatch):
    from bot import handlers

    monkeypatch.setattr(handlers, 'record_group_member', Mock())
    monkeypatch.setattr(handlers, 'ROMAN_TELEGRAM_ID', '9')
    monkeypatch.setattr(handlers, 'get_monitor_chat', lambda chat_id: {'title': 'Команда', 'trigger_list': ['авария']})
    message = SimpleNamespace(text='@bot авария', message_id=3, reply_text=AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock(), username='bot')
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=-100123, username=None, title='Команда'), effective_user=SimpleNamespace(id=8, full_name='Иван'))
    asyncio.run(handlers.on_group_message(update, SimpleNamespace(bot=bot)))
    bot.send_message.assert_awaited_once()
    assert 'авария' in bot.send_message.call_args.kwargs['text']
    message.reply_text.assert_not_awaited()


def test_old_task_regulation_cannot_restore_access():
    from monitoring.db import get_connection
    from monitoring.managers import acknowledge_block, register_manager, set_manager_blocks, get_manager_blocks
    from monitoring.markets import create_market

    market = create_market('Устаревшее меню')
    register_manager(88802, 'Менеджер', 'Менеджер', market['id'])
    set_manager_blocks(88802, ['tasks', 'monitoring'])
    acknowledge_block(88802, 'tasks')
    assert get_manager_blocks(88802) == ['monitoring']
    conn = get_connection()
    try:
        row = conn.execute('SELECT blocks_ack FROM manager WHERE telegram_user_id = 88802').fetchone()
        assert row['blocks_ack'] == ''
    finally:
        conn.close()
