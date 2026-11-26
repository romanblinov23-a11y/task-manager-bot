"""Нормализованные финансовые данные. Нет демонстрационных значений и ФИО."""
import copy
import math
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

PLAN_FIELDS = {
    'guest_count': ('Гостевой поток', 'count'),
    'licenses': ('Выданные лицензии', 'count'),
    'new_licenses_share': ('Выдача лицензий гостям без лицензии, %', 'percent'),
    'guest_share': ('Доля постоянных гостей, %', 'percent'),
    'avg_visits_per_guest': ('Визитов на лицензию', 'number'),
    'basket': ('Наполненность чека, блюд/чек', 'number'),
}
MONTH_KEYS = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            value = re.sub(r'\s', '', value).replace(',', '.').removesuffix('%')
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def delta(fact, base):
    if fact is None or base is None or base == 0:
        return None
    return float((Decimal(str(fact)) - Decimal(str(base))) / abs(Decimal(str(base))) * 100)


def month_before(month):
    return (date.fromisoformat(month+'-01')-timedelta(days=1)).strftime('%Y-%m')


def months_ending(month, count=12):
    result = []
    for _ in range(count):
        result.append(month)
        month = month_before(month)
    return list(reversed(result))


def flatten(fields):
    for row in fields:
        yield row
        yield from flatten(row.get('items') or [])


def required(value, label):
    if value is None:
        raise ValueError(f'В НИМБе отсутствует обязательный показатель: {label}')
    return value


def make_model(raw, month, plan):
    previous = month_before(month)
    year = int(month[:4])
    last_year = f'{year-1}{month[4:]}'
    pnl = {f['code']:f for f in flatten(raw['pnl'][str(year)].get('fields') or [])}

    def p(code, ym=month, col='fact'):
        data = raw['pnl'].get(ym[:4], {})
        field = next((f for f in flatten(data.get('fields') or []) if f.get('code') == code), {})
        row = next((r for r in field.get('results') or [] if str(r.get('period',''))[:7] == ym), {})
        return number(row.get(col))

    employees = raw['team'].get('employees') or []
    opening = min((str(e['spot_date_opening'])[:7] for e in employees if e.get('spot_date_opening')), default=None)
    # Без известной даты открытия нулевая годовая заготовка не становится историей.
    def available(ym):
        return ym <= month and ((opening is not None and ym >= opening) or
                                (opening is None and p('income',ym) not in (None,0)))
    has_year = available(last_year)
    revenue = required(p('income'), 'выручка')
    revenue_plan = required(p('income',col='plan'), 'план выручки')
    revenue_prev = p('income',previous) if available(previous) else None
    metrics = []

    def metric(key, label, fact, target=None, prev=None, ly=None, unit='money', direction='up', section=1):
        row = dict(key=key,label=label,fact=number(fact),plan=number(target),previous=number(prev),
                   last_year=number(ly),unit=unit,direction=direction,section=section)
        metrics.append(row)
        return row

    metric('revenue','Выручка',revenue,revenue_plan,revenue_prev,p('income',last_year) if has_year else None)
    metric('profit','Чистая прибыль',required(p('net_profit'),'чистая прибыль'),
           p('net_profit',col='plan'),p('net_profit',previous) if available(previous) else None,
           p('net_profit',last_year) if has_year else None)
    checks = required(p('araar_count_receipts'), 'чеки')
    metric('checks','Чеки',checks,p('araar_count_receipts',col='plan'),p('araar_count_receipts',previous),
           p('araar_count_receipts',last_year) if has_year else None,unit='count',section=2)
    metric('avg_check','Средний чек',p('araar_avg_receipts'),p('araar_avg_receipts',col='plan'),
           p('araar_avg_receipts',previous),unit='money',section=2)
    current_guests = next((r for r in raw['guests'].get('guests',{}).get('table',[]) if r.get('date') == month+'-01'), None)
    if current_guests is None:
        raise ValueError('Гостевой отчет НИМБа не содержит выбранный месяц')
    guest_year = next((r for r in raw.get('guests_year',{}).get('guests',{}).get('table',[])
                       if r.get('date') == last_year+'-01'), {}) if has_year else {}
    for key,(label,kind) in PLAN_FIELDS.items():
        metric(key,label,current_guests.get(key),plan['values'].get(key),ly=guest_year.get(key),
               unit='percent' if kind=='percent' else kind,section=2)
    metric('food_cost','Фудкост',p('food_cost'),p('food_cost',col='plan'),p('food_cost',previous),unit='percent',direction='down',section=3)
    category_rows = []
    for suffix,label in [('drinks','Напитки'),('food','FOOD'),('ctm','СТМ'),('merch','Мерч'),('joe','Joe')]:
        code='expenses_direct_cost_percent_'+suffix
        if code in pnl:
            category_rows.append(metric(code,label,p(code),p(code,col='plan'),p(code,previous),unit='percent',direction='down',section=3))
    metric('direct_share','Прямая себестоимость',p('expenses_direct_cost_percent'),
           p('expenses_direct_cost_percent',col='plan'),p('expenses_direct_cost_percent',previous),
           unit='percent',direction='down',section=3)
    writeoffs=[]
    writeoff_codes=[('expenses_direct_writeoffs_expiration','Срок годности'),('expenses_indirect_marketing_thanks','Комплимент'),
                    ('expenses_indirect_team_eatingIn','Питание'),('expenses_direct_writeoffs_bad','Порча / брак / ошибка'),
                    ('expenses_direct_writeoffs_menu','Проработка / пролив / помол'),('expenses_direct_writeoffs_realise','В реализацию'),
                    ('expenses_direct_cost_correction','Коррекция себестоимости'),('expenses_direct_inventory','Излишки инвентаризации'),
                    ('expenses_direct_shortage','Недостачи инвентаризации')]
    def share(value,base):
        return value/base*100 if value is not None and base not in (None,0) else None
    for code,label in writeoff_codes:
        if code in pnl:
            m=metric(code+'_share',label,share(p(code),revenue),share(p(code,col='plan'),revenue_plan),
                     share(p(code,previous),revenue_prev),unit='percent',direction='down',section=3)
            m.update(amount=p(code),amount_plan=p(code,col='plan'),amount_previous=p(code,previous))
            writeoffs.append(m)
    delivery=metric('delivery_share','Доставка до подразделения',share(p('expenses_direct_delivery'),revenue),
                    share(p('expenses_direct_delivery',col='plan'),revenue_plan),
                    share(p('expenses_direct_delivery',previous),revenue_prev),unit='percent',direction='down',section=3)
    delivery.update(amount=p('expenses_direct_delivery'),amount_plan=p('expenses_direct_delivery',col='plan'),
                    amount_previous=p('expenses_direct_delivery',previous))

    # Никакие паспортные/контактные сведения и ФИО в модель не попадают.
    manager_ids={r.get('user_id') for r in raw['managers'].get('manager',[])
                 if r.get('main_manager') or r.get('user_job_title','').strip().lower()=='управляющий'}
    manager_ids |= {e.get('id') for e in employees if e.get('main_manager') or e.get('job_title','').strip().lower()=='управляющий'}
    manager_ids.discard(None)
    def is_manager(row):
        return (row.get('user_id') in manager_ids or row.get('main_manager') is True or
                str(row.get('user_job_title','')).strip().lower()=='управляющий')
    def total(rows,key,exclude=True):
        return sum(number(row.get(key)) or 0 for row in rows if not exclude or not is_manager(row))
    shifts=raw['shifts'].get('awards')
    payroll=raw['payroll'].get('awards')
    if not isinstance(shifts,list) or not isinstance(payroll,list):
        raise ValueError('Не получена детализация ФОТ')
    manager_tax=sum(number(row.get('hold_tax')) or 0 for row in payroll if is_manager(row))
    ndfl_all=required(p('expenses_indirect_salary_taxes'),'НДФЛ')
    if manager_tax > ndfl_all+1:
        raise ValueError('НДФЛ управляющего превышает НДФЛ P&L. Требуется сверка источников.')
    salary_all=required(p('expenses_indirect_salary'),'ФОТ')
    fot=salary_all-manager_tax
    if fot < 0:
        raise ValueError('Не сходится ФОТ после исключения НДФЛ управляющего')
    target_share=share(p('expenses_indirect_salary',col='plan'),revenue_plan)
    budget=revenue*target_share/100 if target_share is not None else None
    fot_share=metric('fot_share','Доля ФОТ',share(fot,revenue),target_share,unit='percent',direction='down',section=4)
    # Денежный дубль доли не создает второй вопрос об одном и том же отклонении.
    hours=total(shifts,'duration')
    prev_shifts=raw.get('shifts_previous',{}).get('awards')
    prev_hours=total(prev_shifts,'duration') if isinstance(prev_shifts,list) else None
    spmh=metric('spmh','SPMH',revenue/hours if hours else None,prev=revenue_prev/prev_hours if revenue_prev is not None and prev_hours else None,unit='rate',section=4)
    hour_rate=metric('hour_rate','Средняя ставка',fot/hours if hours else None,unit='rate',direction='neutral',section=4)
    # Состав gross/net сверяется по источникам; НДФЛ вычтен из базы управляющего один раз.
    pay_rows=[('Часы, включая ночные',total(shifts,'award')+total(shifts,'night_bonus'),'expenses_indirect_salary_barista'),
              ('Премии',total(shifts,'grade_bonus')+total(shifts,'holiday_bonus')+
               total(raw['bonuses'].get('awards',[]),'award')+total(raw['bep'].get('awards',[]),'total_award'),None),
              ('Контракты',total(raw['contracts'].get('contracts',[]),'award'),None),
              ('Прочие начисления',total(raw['other'].get('other_awards',[]),'award'),None),
              ('Удержания',-total(payroll,'hold_inventory')-total(payroll,'hold_iiko')-total(payroll,'hold_other'),None),
              ('НДФЛ',ndfl_all-manager_tax,'expenses_indirect_salary_taxes'),
              ('Страховые взносы',p('expenses_indirect_salary_social'),'expenses_indirect_salary_social')]
    # Планы оплаты по должностям не выдаются за планы отдельных типов начислений.
    payroll_rows=[]
    for label,value,code in pay_rows:
        target=None  # Расшифровка начислений согласована без отдельных планов.
        payroll_rows.append(dict(label=label,amount=value,plan=target,share=share(value,fot),
                                 direction='neutral' if label in ('НДФЛ','Страховые взносы','Удержания') else 'down'))
        if target is not None:
            metric('payroll_'+code,label,value,target,unit='money',direction='neutral',section=4)

    role_counts={}
    for employee in employees:
        if (employee.get('dismissal') or {}).get('is_dismissed'):
            continue
        role=employee.get('job_title') or 'Прочие'
        grade=employee.get('grade') or {}
        if role.lower()=='бариста' and isinstance(grade,dict):
            role+=' · '+str(grade.get('title') or grade.get('code') or 'грейд не указан')
        role_counts[role]=role_counts.get(role,0)+1
    payroll_difference=round(fot-sum(r['amount'] for r in payroll_rows if r['amount'] is not None),2)
    staff={'roles':role_counts,'official':sum(bool(e.get('employee_type_at')) for e in employees
                                            if not (e.get('dismissal') or {}).get('is_dismissed'))}
    # Непересекающиеся разделы P&L; ФОТ исключен, комплимент/питание вынесены в фудкост.
    indirect=[]
    excluded={'expenses_indirect_salary','expenses_indirect_marketing_thanks','expenses_indirect_team_eatingIn'}
    def cost_rows(field):
        code=field['code']
        if code in excluded:
            return
        children=field.get('items') or []
        if children:
            for child in children:
                yield from cost_rows(child)
        elif p(code) not in (None,0) or p(code,col='plan') not in (None,0):
            yield field
    if 'expenses_indirect' not in pnl:
        raise ValueError('В P&L отсутствуют косвенные расходы')
    parent=pnl['expenses_indirect']
    for f in cost_rows(parent):
        code=f['code'];metric_row=metric(code,f.get('title') or code,p(code),p(code,col='plan'),
                                      unit='money',direction='down',section=5)
        group=next((c.get('title') or c['code'] for c in parent.get('items',[]) if code==c['code'] or code.startswith(c['code']+'_')), 'Прочие')
        metric_row.update(group=group,share=share(p(code),revenue),share_plan=share(p(code,col='plan'),revenue_plan))
        indirect.append(metric_row)

    indirect_fact=sum(r['fact'] for r in indirect) if all(r['fact'] is not None for r in indirect) else None
    indirect_plan=sum(r['plan'] for r in indirect) if all(r['plan'] is not None for r in indirect) else None
    metric('indirect_total','Остальные косвенные расходы, всего',indirect_fact,indirect_plan,unit='money',direction='down',section=5)

    def distribution(code,ym=month):
        rows=raw['distribution'].get(ym[:4],[])
        return number(next((r.get('months',{}).get(MONTH_KEYS[int(ym[5:])-1]) for r in rows if r.get('code')==code),None))
    dividends=metric('dividends','Начисленные дивиденды',required(distribution('accrued'),'дивиденды'),
                     p('net_profit',col='plan'),unit='money',section=6)
    debt=distribution('debt')
    if debt is None:
        raise ValueError('В распределении прибыли отсутствует накопленное сальдо ФП')
    current_key=MONTH_KEYS[int(month[5:])-1]
    index=next((r.get(current_key,{}).get('value') for r in raw['manager'].get('fin_result',[])
                if r.get('name')=='Индекс финансового состояния предприятия'),None)
    if index is None:
        raise ValueError('НИМБ не вернул индекс финансового состояния')
    rating=raw['rating']
    if rating.get('period') not in (month,month+'-01'):
        raise ValueError('Рейтинг относится к другому месяцу')
    days={}
    board=raw['dashboard']
    periods=board.get('by_period',{}).get('period',[])
    by_period=board.get('by_period',{}).get('data',{})
    for i,day in enumerate(periods):
        try:
            dd=date.fromisoformat(str(day)[:10]) if re.match(r'^\d{4}-',str(day)) else __import__('datetime').datetime.strptime(str(day),'%d.%m.%Y').date()
        except ValueError:
            raise ValueError('Неверная дата ежедневных продаж НИМБа')
        if dd.strftime('%Y-%m')!=month:
            raise ValueError('Ежедневные продажи относятся к другому месяцу')
        row={k:number(by_period.get(k,[])[i]) if i<len(by_period.get(k,[])) else None for k in ('revenue','count','average')}
        if row['revenue'] is None or row['count'] is None:
            raise ValueError('В ежедневных продажах отсутствуют выручка или число чеков')
        if row['average'] is None and row['count']:
            row['average']=row['revenue']/row['count']
        days[dd.isoformat()]=row
    if not days:
        raise ValueError('Отсутствуют дневные продажи')
    daily_revenue=sum(r['revenue'] for r in days.values() if r['revenue'] is not None)
    daily_checks=sum(r['count'] for r in days.values() if r['count'] is not None)
    warnings=list(raw.get('warnings',[]))
    if abs(daily_revenue-revenue)>1:
        warnings.append('Финансы взяты из P&L, дневные продажи — из анализа продаж НИМБа; их базы отличаются.')
    warnings.append('Планов часов, средней ставки и SPMH нет: показаны без плана.')
    if any(row['plan'] is None for row in payroll_rows[:5]):
        warnings.append('Расшифровка начислений показана без отдельных планов: факт и доля ФОТ.')
    return dict(month=month,opening=opening,has_year=has_year,plan_snapshot=copy.deepcopy(plan),metrics=metrics,
                categories=category_rows,writeoffs=writeoffs,delivery=delivery,payroll_rows=payroll_rows,
                payroll_difference=payroll_difference,fot=fot,fot_budget=budget,fot_share=fot_share,hours=hours,hour_rate=hour_rate,spmh=spmh,
                staff=staff,indirect=indirect,financial_index=str(index),
                rating={'value':number(rating.get('kpi',{}).get('value')),
                        'preliminary':bool(rating.get('kpi',{}).get('is_preliminary')),
                        'status':rating.get('rating',{}).get('status')},
                debt=debt,dividends=dividends,days=days,
                daily_averages={'revenue':daily_revenue/len(days),'count':daily_checks/len(days),
                                'average':daily_revenue/daily_checks if daily_checks else None},
                revenue_history=[{'month':ym,'value':p('income',ym)} for ym in months_ending(month) if available(ym) and p('income',ym) is not None],
                food_history=[{'month':ym,'value':p('food_cost',ym)} for ym in months_ending(month) if available(ym) and p('food_cost',ym) is not None],
                dividends_history=[{'month':ym,'value':distribution('accrued',ym)} for ym in months_ending(month) if available(ym) and distribution('accrued',ym) is not None],
                warnings=warnings)


def with_answers(model, answers):
    result=copy.deepcopy(model)
    dish_count=answers.get('dishes')
    checks=next(m['fact'] for m in result['metrics'] if m['key']=='checks')
    for m in result['metrics']:
        if m['key']=='basket':
            m['fact']=dish_count/checks if dish_count is not None and checks else None
    return result


def deviations(model):
    result=[]
    for m in model['metrics']:
        fact,plan=m['fact'],m['plan']
        if fact is None or plan is None:
            continue
        if plan==0:
            trigger=fact!=0
        else:
            # Decimal avoids floating point turning exactly 5% into 5.0000000001%.
            trigger=abs((Decimal(str(fact))-Decimal(str(plan)))/abs(Decimal(str(plan)))*100)>Decimal('5')
        if trigger:
            result.append({**m,'id':len(result)+1,'delta':delta(fact,plan)})
    return result
