"""Чтение НИМБа для портфеля. Доля УК применяется один раз, только к дивидендам."""
import copy
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timezone
from decimal import Decimal,ROUND_HALF_UP

from config import settings
from config.timeutil import today
from manager_report.model import number,delta,flatten,MONTH_KEYS,month_before
from monitoring.db import get_connection
from monitoring.markets import list_markets,get_market
from portfolio import storage
from portfolio.analytics import operating_metrics,labor_summary,cost_attention,ratio
from revenue.surfcoffee_client import SurfCoffeeClient,SPOTS

_LOCK=threading.RLock()
_JOBS_LOCK=threading.RLock()
_EXECUTOR=ThreadPoolExecutor(max_workers=1,thread_name_prefix='portfolio-reader')
_JOBS={}
_FAILED={}


def weighted(value,percent):
    if value is None:return None
    return float((Decimal(str(value))*Decimal(str(percent))/100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))


def valid_month(month):return bool(re.fullmatch(r'(?:20\d{2})-(?:0[1-9]|1[0-2])',month))


def _field(pnl,code,month,column='fact'):
    field=next((f for f in flatten(pnl.get(month[:4],{}).get('fields') or []) if f.get('code')==code),{})
    row=next((r for r in field.get('results') or [] if str(r.get('period',''))[:7]==month),{})
    return number(row.get(column))


def accepted_report(market_id,month=None):
    c=get_connection()
    try:
        condition=' AND r.month=?' if month else ''
        args=(market_id,month) if month else (market_id,)
        row=c.execute("SELECT v.snapshot,w.accepted_at FROM monthly_manager_report r JOIN manager_report_workflow w ON w.report_id=r.id JOIN manager_report_version v ON v.report_id=r.id AND v.revision=w.revision WHERE r.market_id=? AND w.state='accepted'"+condition+' ORDER BY r.month DESC LIMIT 1',args).fetchone()
        if not row:return None
        value=json.loads(row['snapshot'])
        comments={}
        if value.get('model',{}).get('metrics'):
            from manager_report.model import with_answers,deviations
            issues=deviations(with_answers(value['model'],value['answers']))
            for group in value['answers'].get('deviations',[]):
                for item in issues:
                    if item['id'] in group.get('ids',[]):comments[item['key']]={k:group.get(k) for k in ('cause','action','expected','deadline')}
        return {'month':value['month'],'accepted_at':row['accepted_at'],'answers':value['answers'],'id':value['id'],'deviation_comments':comments}
    finally:c.close()


def _read(market,month):
    spot=market['surf_spot_key'];year=int(month[:4]);pnl={};pdr={};warnings=[];team={};daily=None;previous_daily=None;labor=None
    c=SurfCoffeeClient(settings.SURF_EMAIL,settings.SURF_PASSWORD)
    try:
        c.login()
        for y in (year,year-1):
            try:
                pnl[str(y)]=c.get_pnl_year(spot,y)
                pdr[str(y)]=c.get_distribution_year(spot,y)
            except Exception:
                if y==year:raise
                warnings.append('Данные предыдущего года временно недоступны.')
        try:closure=c.get_month_admission(spot,month)['closed']
        except Exception:closure=None;warnings.append('Статус закрытия месяца временно недоступен.')
        try:
            team=c.get_team(spot)
            opening=min((str(e['spot_date_opening'])[:7] for e in team.get('employees',[]) if e.get('spot_date_opening')),default=None)
        except Exception:opening=None
        try:guests=c.get_guests_report(spot,month)
        except Exception:guests={};warnings.append('Гостевые показатели временно недоступны.')
        if month==today().strftime('%Y-%m'):
            try:
                c.switch_spot(SPOTS[spot]['id'])
                daily=c.get_month_raw(month)
                previous_daily=c.get_month_raw(month_before(month))
            except Exception:warnings.append('Продажи за завершенные дни временно недоступны; сравнение с полным планом месяца не подставляется.')
        try:
            payroll=c.get_monthly_source('payroll',spot,month)
            shifts=c.get_awards_shifts(spot,month)
            try:previous_shifts=c.get_awards_shifts(spot,month_before(month))
            except Exception:previous_shifts=None
            p=lambda code,ym=month,col='fact':_field(pnl,code,ym,col)
            labor=labor_summary(p,payroll,shifts,team.get('employees',[]),previous_shifts,p('income',month_before(month)))
            if labor is None:warnings.append('ФОТ и часы команды требуют сверки источников. Расчет эффективности не подменяется общим ФОТ.')
        except Exception:warnings.append('Данные эффективности команды временно недоступны.')
        try:payments=c.get_dividend_payments(spot,year)
        except Exception:payments=None;warnings.append('Реестр дат выплат временно недоступен; месячные итоги показаны из распределения прибыли.')
        return normalize(pnl,pdr,guests,payments,month,opening,closure,warnings,daily,previous_daily,labor)
    finally:c.close()


def normalize(pnl,pdr,guests,payments,month,opening,closed,warnings=(),daily=None,previous_daily=None,labor=None):
    result={'month':month,'opening':opening,'closed':closed,'warnings':list(warnings)}
    p=lambda code,ym=month,col='fact':_field(pnl,code,ym,col)
    def available(ym):return ym<=today().strftime('%Y-%m') and ((opening and ym>=opening) or (not opening and p('income',ym) not in (None,0)))
    def dividend(code,ym=month):
        row=next((r for r in pdr.get(ym[:4],[]) if r.get('code')==code),{})
        return number(row.get('months',{}).get(MONTH_KEYS[int(ym[5:])-1]))
    result['available']=bool(available(month))
    prev=month_before(month);ly=f'{int(month[:4])-1}{month[4:]}'
    metrics=[]
    for code,label,unit,direction in [('income','Выручка','money','up'),('net_profit','Чистая прибыль','money','up'),('araar_count_receipts','Чеки','count','up'),('araar_avg_receipts','Средний чек','money','up'),('food_cost','Фудкост','percent','down'),('expenses_indirect_salary','ФОТ по P&L','money','down')]:
        metrics.append({'key':code,'label':label,'unit':unit,'direction':direction,'fact':p(code) if available(month) else None,'plan':p(code,col='plan') if available(month) else None,'previous':p(code,prev) if available(prev) else None,'last_year':p(code,ly) if available(ly) else None})
    result['metrics']=metrics
    result['dividends']={key:dividend(code) if available(month) else None for key,code in [('accrued','accrued'),('paid','paided'),('balance','debt')]}
    result['dividends']['plan']=p('net_profit',col='plan') if available(month) else None
    result['guests']=next((r for r in guests.get('guests',{}).get('table',[]) if r.get('date')==month+'-01'),{})
    result['guests']={key:number(result['guests'].get(key)) for key in ('guest_count','licenses','new_licenses_share','guest_share','avg_visits_per_guest')}
    result['history']=[]
    for offset in range(11,-1,-1):
        n=int(month[:4])*12+int(month[5:])-1-offset;ym=f'{n//12:04d}-{n%12+1:02d}'
        if available(ym):result['history'].append({'month':ym,'revenue':p('income',ym),'profit':p('net_profit',ym),'margin':ratio(p('net_profit',ym),p('income',ym)),'checks':p('araar_count_receipts',ym),'accrued':dividend('accrued',ym),'paid':dividend('paided',ym)})
    result['months']=sorted({str(r.get('period',''))[:7] for f in flatten(pnl.get(month[:4],{}).get('fields') or []) if f.get('code')=='income' for r in f.get('results',[]) if re.fullmatch(r'20\d{2}-\d{2}',str(r.get('period',''))[:7]) and available(str(r['period'])[:7])})
    # Только листовые статьи: итоговые строки не складываются с дочерними повторно.
    result['expenses']=[]
    all_fields=list(flatten(pnl.get(month[:4],{}).get('fields') or []))
    parents={f.get('code') for f in all_fields if f.get('items')}
    seen=set()
    for field in all_fields:
        code=field.get('code','')
        if not code.startswith('expenses_') or code in parents or code in seen or '_percent' in code:continue
        seen.add(code)
        fact=p(code);plan=p(code,col='plan')
        if fact not in (None,0) or plan not in (None,0):result['expenses'].append({'key':code,'label':field.get('title') or code,'fact':fact,'plan':plan})
    result['operating']=operating_metrics(p,month,today(),daily,previous_daily,labor)
    result['cost_attention']=cost_attention(p,result['operating'],result['expenses'])
    if payments is not None and any(r.get('date') and r['date']>today().isoformat() for r in payments):
        result['warnings'].append('Утвержденные операции с будущими датами не включены в прошедшие выплаты.')
        payments=[r for r in payments if not r.get('date') or r['date']<=today().isoformat()]
    result['payments']=payments
    result['cash_known']=sum(r['amount'] for r in payments if r['date'] and r['date'].startswith(month)) if payments is not None else None
    result['undated_count']=sum(r['date'] is None for r in payments) if payments is not None else None
    if payments is not None and result['cash_known'] is not None and result['dividends']['paid'] is not None and abs(result['cash_known']-result['dividends']['paid'])>1:
        result['warnings'].append('Сумма по датам операций отличается от выплат в распределении прибыли. Это разные источники и периоды учета; суммы не складываются.')
    return result


def load_project(market,month,refresh=False,cached_only=False):
    if not valid_month(month) or month>today().strftime('%Y-%m'):raise ValueError('Некорректный отчетный месяц')
    key=f'portfolio-v2:{market["id"]}:{market.get("surf_spot_key")}:{month}'
    if cached_only:
        cached=storage.cache_get(key)
        if not cached:raise ValueError('Сохраненные данные еще не получены')
        value=copy.deepcopy(cached['data']);value.update(updated_at=cached['updated_at'],stale=(datetime.now(timezone.utc)-datetime.fromisoformat(cached['updated_at'])).total_seconds()>=300)
        return present(value,market,month)
    with _LOCK:
        cached=storage.cache_get(key)
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(cached['updated_at'])).total_seconds() if cached else None
        if cached and age<(30 if refresh else 300):
            value=copy.deepcopy(cached['data']);value.update(updated_at=cached['updated_at'],stale=False)
        else:
            try:
                data=_read(market,month);storage.cache_set(key,data);cached=storage.cache_get(key)
                value=copy.deepcopy(data);value.update(updated_at=cached['updated_at'],stale=False)
            except Exception:
                if not cached:raise
                value=copy.deepcopy(cached['data']);value.update(updated_at=cached['updated_at'],stale=True)
                value['warnings'].append('Обновление НИМБа не удалось. Показаны последние успешно полученные данные.')
    return present(value,market,month)


def present(value,market,month):
    percent=storage.ownership(market)
    value.update(market_id=market['id'],name=market['name'],share=percent)
    value['company_cash_known']=weighted(value['cash_known'],percent)
    value['company_dividends']={key:weighted(amount,percent) for key,amount in value['dividends'].items()}
    for row in value['history']:
        row['company_accrued']=weighted(row['accrued'],percent);row['company_paid']=weighted(row['paid'],percent)
    payments=value['payments']
    if payments is not None:
        for row in payments:row['company_amount']=weighted(row['amount'],percent)
    report=accepted_report(market['id'],month)
    value['report']=report
    if report:
        checks=next((m['fact'] for m in value['metrics'] if m['key']=='araar_count_receipts'),None)
        dishes=number(report['answers'].get('dishes'))
        value['operating']['basket']=dishes/checks if dishes is not None and checks is not None and checks>0 else None
    else:value['operating']['basket']=None
    prior=accepted_report(market['id'],month_before(month))
    value['focus_tasks']=(prior or report or {}).get('answers',{}).get('next_tasks',[])
    value['focus_source_month']=(prior or report or {}).get('month')
    value['focus_for_month']=month if prior else None
    for cost in value['cost_attention']:
        key={'fot':'fot_share'}.get(cost['key'],cost['key'])
        cost['comment']=(report or {}).get('deviation_comments',{}).get(key)
    value['attention']=[]
    sales=value['operating']['sales'];d=delta(sales['fact'],sales['plan'])
    if d is not None and d < -5:value['attention'].append('Выручка ниже сопоставимого плана на '+str(round(abs(d),1))+'%')
    value['attention'].extend(row['label'] for row in value['cost_attention'])
    if not report:value['attention'].append('Нет принятого управленческого отчета за месяц')
    schedule=accepted_report(market['id'])
    value['planned']=[];value['plan_report_month']=schedule['month'] if schedule else None
    if schedule:
        for index,p in enumerate(schedule['answers'].get('payouts',{}).get('items',[])):
            value['planned'].append({'id':f'report:{schedule["id"]}:{index}','date':p['date'],'amount':p['amount'],'company_amount':weighted(p['amount'],percent),'comment':p.get('comment',''),'source_month':schedule['month']})
    if not schedule:value['attention'].append('Нет принятого графика выплат')
    value['upcoming_30_days']=_upcoming(value,today())
    return value


def _upcoming(project,as_of):
    from datetime import timedelta
    if not project.get('plan_report_month'):return None
    end=(as_of+timedelta(days=29)).isoformat();start=as_of.isoformat()
    return sum(p['company_amount'] for p in project['planned'] if start<=p['date']<=end)


def portfolio(month,refresh=False):
    projects=[]
    for market in list_markets():
        if not market.get('surf_spot_key'):continue
        try:projects.append(load_project(market,month,refresh))
        except Exception:projects.append({'market_id':market['id'],'name':market['name'],'share':storage.ownership(market),'error':'Не удалось получить данные проекта. Попробуй обновить позже.'})
    return aggregate(month,projects)


def aggregate(month,projects):
    good=[p for p in projects if not p.get('error') and not p.get('loading')]
    def total(group,key):
        values=[p[group].get(key) for p in good]
        return sum(values) if len(good)==len(projects) and values and all(v is not None for v in values) else None
    cash_values=[p['company_cash_known'] for p in good]
    cash_total=sum(cash_values) if len(good)==len(projects) and cash_values and all(v is not None for v in cash_values) else None
    operating=[p for p in good if p.get('available') is not False]
    metrics=lambda key:[next((m['fact'] for m in p.get('metrics',[]) if m['key']==key),None) for p in operating]
    def strict_sum(values):return sum(values) if len(good)==len(projects) and values and all(v is not None for v in values) else None
    revenue=strict_sum(metrics('income'));profit=strict_sum(metrics('net_profit'))
    profit_plans=[next((m['plan'] for m in p.get('metrics',[]) if m['key']=='net_profit'),None) for p in operating]
    upcoming=strict_sum([p.get('upcoming_30_days') for p in good])
    return {'month':month,'as_of':today().isoformat(),'business':{'revenue':revenue,'profit':profit,'profit_plan':strict_sum(profit_plans),'margin':ratio(profit,revenue),'upcoming_30_days':upcoming,'plan_coverage':sum(bool(p.get('plan_report_month')) for p in good),'operating_count':len(operating)},'cash_known_total':cash_total,'projects':projects,'totals':{k:total('company_dividends',k) for k in ('accrued','paid','balance','plan')},'complete':len(good)==len(projects)}


def _job_done(key,future):
    with _JOBS_LOCK:
        _JOBS.pop(key,None)
        try:
            if future.result().get('stale'):_FAILED[key]=time.monotonic()
            else:_FAILED.pop(key,None)
        except Exception:_FAILED[key]=time.monotonic()


def request_project(market,month,refresh=False):
    if not valid_month(month) or month>today().strftime('%Y-%m'):raise ValueError('Некорректный отчетный месяц')
    key=f'portfolio-v2:{market["id"]}:{market.get("surf_spot_key")}:{month}'
    cached=storage.cache_get(key)
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(cached['updated_at'])).total_seconds() if cached else None
    with _JOBS_LOCK:
        failed=key in _FAILED and time.monotonic()-_FAILED[key]<60
        if (not cached or age>=(30 if refresh else 300)) and key not in _JOBS and not failed:
            if len(_JOBS)<8:
                future=_EXECUTOR.submit(load_project,market,month,refresh)
                _JOBS[key]=future
                future.add_done_callback(lambda f:_job_done(key,f))
        active=key in _JOBS
    if cached:
        result=load_project(market,month,cached_only=True)
        result['refreshing']=active
        if failed:result['warnings'].append('Не удалось обновить НИМБ. Сохраненные значения остаются доступны; повтор возможен позже.')
        return result
    if failed:return {'market_id':market['id'],'name':market['name'],'share':storage.ownership(market),'error':'НИМБ временно недоступен. Попробуй обновить позже.'}
    return {'market_id':market['id'],'name':market['name'],'share':storage.ownership(market),'loading':True}


def request_portfolio(month,refresh=False):
    return aggregate(month,[request_project(m,month,refresh) for m in list_markets() if m.get('surf_spot_key')])
