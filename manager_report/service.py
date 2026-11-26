"""Допуск и последовательное чтение НИМБа одной сессией, без записей."""
from datetime import date, timedelta

from config.settings import SURF_EMAIL, SURF_PASSWORD
from manager_report.model import make_model, PLAN_FIELDS, number
from manager_report.storage import stamp
from revenue.surfcoffee_client import SurfCoffeeClient, SPOTS


class OpenMonthError(ValueError):
    pass


class MissingPlansError(ValueError):
    pass


def before(month):
    return (date.fromisoformat(month+'-01') - timedelta(days=1)).strftime('%Y-%m')


def collect(spot, month, plan):
    client = SurfCoffeeClient(SURF_EMAIL, SURF_PASSWORD)
    try:
        client.login()
        admission = client.get_month_admission(spot, month)
        if admission.get('closed') is not True:
            raise OpenMonthError('Месяц не закрыт в НИМБе. Закройте месяц и повторите проверку.')
        admission['checked_at'] = stamp()
        if not plan or any(key not in plan.get('values', {}) for key in PLAN_FIELDS):
            raise MissingPlansError('Роман еще не настроил планы гостевых KPI для этого проекта и месяца.')
        for key,(_,kind) in PLAN_FIELDS.items():
            value=number(plan['values'][key])
            if value is None or value<0 or (kind=='percent' and value>100) or (kind=='count' and value!=int(value)):
                raise MissingPlansError('Планы KPI заполнены некорректно.')
        previous = before(month)
        year = int(month[:4])
        data = {'pnl': {}, 'distribution': {}, 'warnings': []}
        # Два года покрывают прошлый год и скользящие 12 месяцев, включая январь.
        for y in sorted({year, year-1}):
            data['pnl'][str(y)] = client.get_pnl_year(spot, y)
            data['distribution'][str(y)] = client.get_distribution_year(spot, y)
        client.switch_spot(SPOTS[spot]['id'])
        data['dashboard'] = client.get_month_raw(month)
        data['manager'] = client.get_manager_report(spot, month)
        data['guests'] = client.get_guests_report(spot, month)
        data['team'] = client.get_team(spot)
        data['shifts'] = client.get_awards_shifts(spot, month)
        try:
            data['shifts_previous'] = client.get_awards_shifts(spot, previous)
        except Exception:
            data['shifts_previous'] = {}
            data['warnings'].append('Часы прошлого месяца недоступны; динамика SPMH скрыта.')
        for name in ('rating', 'payroll', 'bonuses', 'bep', 'contracts', 'other', 'holds', 'managers', 'expense_plan'):
            data[name] = client.get_monthly_source(name, spot, month)
        opening = min((r['spot_date_opening'][:10] for r in data['team'].get('employees', [])
                       if r.get('spot_date_opening')), default=None)
        last_year = f'{year-1}{month[4:]}'
        if opening and last_year >= opening[:7]:
            try:
                data['guests_year'] = client.get_guests_report(spot, last_year)
            except Exception:
                data['guests_year'] = {}
                data['warnings'].append('Прошлогодние гостевые показатели недоступны; сравнение скрыто.')
        model = make_model(data, month, plan)
        model['admission'] = admission
        return model
    finally:
        client.close()
