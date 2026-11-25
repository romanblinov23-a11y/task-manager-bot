from bot.shift_reports import _finance_previous_line, _QUESTIONS, _FIELD_LABELS, render_finance_report


def test_daily_finance_format():
    text = render_finance_report({}, '2026-11-20', {
        'revenue_total': '1200', 'revenue_total_prev': '1000',
        'revenue_cash': '200', 'revenue_noncash': '1000',
        'cash_balance': '500', 'avg_check': '120', 'avg_check_prev': '100',
        'guests': '10', 'guests_prev': '10', 'writeoff_defects': '20',
        'writeoff_development': '30', 'shift_composition': '2 бариста',
    })
    assert text.startswith('20.11.2026 Пятница\n\n')
    assert '@бот_ФП' not in text
    assert 'выручка: 1 200,00\nналичные: 200,00\nбезнал: 1 000,00\n(1 000,00) ↗️ 20,00%' in text
    assert 'остаток наличных в кассе: 500,00' in text
    assert 'средний чек: 120,00\n(100,00) ↗️ 20,00%' in text
    assert 'гости: 10\n(10) ➡️ 0,00%' in text
    assert 'порча, брак, ошибка: 20,00\nпроработка меню, пролив, помол: 30,00' in text
    assert '4. Оборудование и технические\n' in text
    assert text.endswith('7. Сколько человек было на смене с разбивкой по должностям\n2 бариста')


def test_old_reports_and_zero_baselines():
    assert 'остаток наличных в кассе: —' in render_finance_report({}, '2026-11-20', {})
    assert _finance_previous_line({'guests': '10', 'guests_prev': '0'}, 'guests', count=True) == '(0) — —'
    assert _finance_previous_line({'guests': '5', 'guests_prev': '10'}, 'guests', count=True) == '(10) ↘️ 50,00%'


def test_new_fields_can_be_collected_and_edited():
    questions = {q['key']: q for q in _QUESTIONS}
    for key in ('cash_balance', 'writeoff_defects', 'writeoff_development'):
        assert questions[key]['kind'] == 'money'
        assert key in _FIELD_LABELS


def test_finance_service_time_normalizes_existing_reports():
    text = render_finance_report({}, '2026-11-25', {'avg_service_time': '0:00'})
    assert 'среднее время отдачи за день: 00:00' in text
    text = render_finance_report({}, '2026-11-25', {'avg_service_time': '5:35'})
    assert 'среднее время отдачи за день: 05:35' in text


def test_service_time_input_is_normalized_and_validated():
    from bot.shift_reports import _validate
    assert _validate('time', '0:00') == ('00:00', None)
    assert _validate('time', '05:35') == ('05:35', None)
    assert _validate('time', '5:99')[1] is not None
