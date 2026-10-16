from datetime import timedelta

from bot.shift_reports import _manual_date_keyboard
from config.timeutil import today as tz_today


def test_manual_date_keyboard_offers_yesterday_and_today_with_correct_dates():
    today = tz_today()
    yesterday = today - timedelta(days=1)
    markup = _manual_date_keyboard(market_id=7)
    rows = markup.inline_keyboard

    assert len(rows) == 2
    yesterday_button, today_button = rows[0][0], rows[1][0]

    assert yesterday.strftime("%d.%m.%Y") in yesterday_button.text
    assert today.strftime("%d.%m.%Y") in today_button.text
    assert "Вчера" in yesterday_button.text
    assert "Сегодня" in today_button.text

    assert yesterday_button.callback_data == f"shrep_manualdate:7:{yesterday.isoformat()}"
    assert today_button.callback_data == f"shrep_manualdate:7:{today.isoformat()}"
