"""Управленческие метрики: сопоставимые периоды, динамичный ФОТ, без двойного учета."""
import calendar
from datetime import date,datetime,timedelta
from manager_report.model import number,delta


def ratio(a,b):return a/b*100 if a is not None and b is not None and b>0 else None


def sales_to_date(board,month,as_of):
    """Только завершенные календарные дни; неполный ряд не становится нулем."""
    if not board:return None
    start=date.fromisoformat(month+'-01')
    end=min(as_of-timedelta(days=1),date(start.year,start.month,calendar.monthrange(start.year,start.month)[1]))
    if end<start:return {'through':None,'fact':None,'plan':None,'checks':None,'average':None,'complete':False}
    rows=board.get('table',{}).get('revenue',{}).get('data')
    if not isinstance(rows,list):return None
    day_rows={}
    for row in rows:
        try:day=date.fromisoformat(str(row['date']).split(' ')[0])
        except (KeyError,ValueError):return None
        if day.strftime('%Y-%m')!=month:return None
        if day in day_rows:return None
        day_rows[day]=row
    wanted=[start+timedelta(days=i) for i in range((end-start).days+1)]
    if not all(d in day_rows for d in wanted):return {'through':end.isoformat(),'fact':None,'plan':None,'checks':None,'average':None,'complete':False}
    def total(key):
        values=[number(day_rows[d].get(key)) for d in wanted]
        return sum(values) if all(v is not None for v in values) else None
    dates=board.get('by_period',{}).get('period',[]);counts=board.get('by_period',{}).get('data',{}).get('count',[])
    by_date={}
    for index,value in enumerate(dates):
        try:d=datetime.strptime(str(value),'%d.%m.%Y').date() if not str(value).startswith(month) else date.fromisoformat(str(value)[:10])
        except ValueError:continue
        if index<len(counts):by_date[d]=number(counts[index])
    checks=sum(by_date[d] for d in wanted) if all(by_date.get(d) is not None for d in wanted) else None
    fact=total('fact_revenue');plan=total('planning_revenue')
    return {'through':end.isoformat(),'fact':fact,'plan':plan,'checks':checks,'average':fact/checks if fact is not None and checks is not None and checks>0 else None,'complete':fact is not None and plan is not None}


def labor_summary(p,payroll,shifts,employees,previous_shifts=None,previous_revenue=None):
    if not isinstance(payroll,dict) or not isinstance(shifts,dict):return None
    pay=payroll.get('awards');hours=shifts.get('awards')
    if not isinstance(pay,list) or not isinstance(hours,list):return None
    ids={e.get('id') for e in employees if e.get('main_manager') is True or str(e.get('job_title','')).strip().lower()=='управляющий'}-{None}
    def manager(r):return r.get('user_id') in ids or r.get('main_manager') is True or str(r.get('user_job_title') or r.get('employee_post') or '').strip().lower()=='управляющий'
    tax_values=[number(r.get('hold_tax')) for r in pay if manager(r)]
    if any(v is None or v<0 for v in tax_values):return None
    tax=sum(tax_values);salary=p('expenses_indirect_salary');ndfl=p('expenses_indirect_salary_taxes')
    if salary is None or ndfl is None or tax>ndfl+1 or tax>salary:return None
    fot=salary-tax
    def team_hours(rows):
        values=[number(r.get('duration')) for r in rows if not manager(r)]
        return sum(values) if all(v is not None and v>=0 for v in values) else None
    count=team_hours(hours)
    rev=p('income');plan_share=ratio(p('expenses_indirect_salary',col='plan'),p('income',col='plan'))
    budget=rev*plan_share/100 if rev is not None and plan_share is not None else None
    prev_hours=team_hours(previous_shifts['awards']) if isinstance(previous_shifts,dict) and isinstance(previous_shifts.get('awards'),list) else None
    return {'fot':fot,'share':ratio(fot,rev),'plan_share':plan_share,'budget':budget,
            'variance_rub':fot-budget if budget is not None else None,'hours':count,
            'spmh':rev/count if rev is not None and count is not None and count>0 else None,
            'previous_spmh':previous_revenue/prev_hours if previous_revenue is not None and prev_hours is not None and prev_hours>0 else None}


def operating_metrics(p,month,as_of,daily=None,previous_daily=None,labor=None):
    rev=p('income');profit=p('net_profit');plan_rev=p('income',col='plan');plan_profit=p('net_profit',col='plan')
    live=month==as_of.strftime('%Y-%m')
    sales=sales_to_date(daily,month,as_of) if live else None
    previous=None
    if live and sales and sales['through'] and previous_daily:
        first=date.fromisoformat(month+'-01');prev_end=first-timedelta(days=1);days=date.fromisoformat(sales['through']).day
        cutoff=date(prev_end.year,prev_end.month,min(days,prev_end.day))+timedelta(days=1)
        previous=sales_to_date(previous_daily,prev_end.strftime('%Y-%m'),cutoff)
    writeoff_codes=[('expenses_direct_writeoffs_expiration','Срок годности'),('expenses_direct_writeoffs_bad','Порча / брак / ошибка'),('expenses_direct_writeoffs_menu','Проработка / пролив / помол'),('expenses_indirect_marketing_thanks','Комплименты'),('expenses_indirect_team_eatingIn','Питание команды')]
    writeoffs=[{'key':key,'label':label,'fact':p(key),'plan':p(key,col='plan'),'share':ratio(p(key),rev)} for key,label in writeoff_codes]
    def total(column):return sum(r[column] for r in writeoffs) if all(r[column] is not None for r in writeoffs) else None
    sales_fact=sales.get('fact') if live and sales else None if live else rev
    sales_plan=sales.get('plan') if live and sales else None if live else plan_rev
    checks=sales.get('checks') if live and sales else None if live else p('araar_count_receipts')
    average=sales.get('average') if live and sales else None if live else p('araar_avg_receipts')
    return {'live':live,'sales':{'fact':sales_fact,'plan':sales_plan,'through':sales.get('through') if sales else None,'complete':sales.get('complete',False) if live and sales else not live},
            'margin':ratio(profit,rev),'plan_margin':ratio(plan_profit,plan_rev),'profit_variance':profit-plan_profit if not live and profit is not None and plan_profit is not None else None,
            'checks':checks,'checks_previous':previous.get('checks') if live and previous else None,
            'checks_plan':None if live else p('araar_count_receipts',col='plan'),
            'average':average,'average_previous':previous.get('average') if live and previous else None,
            'foodcost':p('food_cost'),'foodcost_plan':p('food_cost',col='plan'),
            'writeoffs':writeoffs,'writeoff_amount':total('fact'),'writeoff_share':ratio(total('fact'),rev),'writeoff_plan_share':ratio(total('plan'),plan_rev),'labor':labor}


def cost_attention(p,operating,indirect):
    """Непересекающиеся группы: фудкост целиком, ФОТ, прочие листовые статьи."""
    rows=[];rev=p('income')
    def add(key,label,fact,budget):
        if fact is not None and budget is not None and fact>budget:
            rows.append({'key':key,'label':label,'variance_rub':fact-budget,'fact':fact,'budget':budget})
    labor=operating.get('labor')
    if labor:add('fot','ФОТ выше динамичного бюджета',labor['fot'],labor['budget'])
    food=operating.get('foodcost');foodplan=operating.get('foodcost_plan')
    if rev is not None and food is not None and foodplan is not None:add('foodcost','Фудкост выше плановой доли',rev*food/100,rev*foodplan/100)
    if not operating['live']:
        for row in indirect:
            code=row['key']
            if code.startswith('expenses_indirect_salary'):continue
            if code.startswith('expenses_indirect_'):add(code,row['label'],row['fact'],row['plan'])
    return sorted(rows,key=lambda r:r['variance_rub'],reverse=True)[:3]
