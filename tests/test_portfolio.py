import asyncio,copy,json
from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient,TestServer
from portfolio import storage,service
from monitoring.markets import list_markets,set_market_surf_spot_key
from dashboard.server import _build_app,get_or_create_dashboard_token
from revenue.surfcoffee_client import SurfCoffeeClient
from test_monthly_manager_report import raw_inputs


def market():
    m=list_markets()[0];set_market_surf_spot_key(m['id'],'park_gorkogo')
    return {**m,'surf_spot_key':'park_gorkogo'}


def normalized():
    r=raw_inputs()
    return service.normalize(r['pnl'],r['distribution'],r['guests'],[{'id':'1','date':'2026-10-08','amount':200000},{'id':'2','date':None,'amount':100000}], '2026-10','2026-03',True)


def test_share_defaults_and_changes_apply_only_once_to_dividends(monkeypatch):
    m=market();assert storage.ownership(m)==50
    assert storage.ownership({**m,'id':list_markets()[1]['id'],'surf_spot_key':'okko'})==100
    monkeypatch.setattr(service,'_read',lambda *args:normalized())
    p=service.load_project(m,'2026-10')
    assert p['company_dividends']['accrued']==300000
    assert p['dividends']['accrued']==600000
    assert p['metrics'][0]['fact']==6000000
    assert p['payments'][0]['company_amount']==100000
    assert p['company_cash_known']==100000
    storage.set_ownership(m,25,42)
    p2=service.load_project(m,'2026-10')
    assert p2['company_dividends']['accrued']==150000
    assert p2['payments'][0]['company_amount']==50000
    assert p2['metrics'][0]['fact']==6000000
    assert service.weighted(1.01,50)==.51
    assert p2['company_dividends']['balance']==-25000


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,101,True])
def test_invalid_share_cannot_change_ownership(value):
    m=market()
    with pytest.raises(ValueError):storage.set_ownership(m,value,42)
    assert storage.ownership(m)==50


def test_payment_date_is_never_invented_and_monthly_sources_stay_separate():
    d=normalized()
    assert d['payments'][1]['date'] is None
    assert d['undated_count']==1
    assert d['cash_known']==200000
    assert d['dividends']['paid'] is None
    r=raw_inputs();r['distribution']['2026'].append({'code':'paided','months':{'oct':500000},'items':[{'code':'paided_488','months':{'oct':500000}}]})
    d=service.normalize(r['pnl'],r['distribution'],r['guests'],[{'id':'1','date':'2026-10-08','amount':200000}],'2026-10','2026-03',True)
    assert d['dividends']['paid']==500000 and d['cash_known']==200000
    assert any('отличается' in w for w in d['warnings'])
    assert not any(r['month']<'2026-03' for r in d['history'])
    assert d['metrics'][0]['last_year'] is None


def test_open_month_is_viewable_but_not_marked_closed():
    r=raw_inputs();d=service.normalize(r['pnl'],r['distribution'],{},[],'2026-10','2026-03',False)
    assert d['closed'] is False and d['available']
    assert d['cash_known']==0
    missing=service.normalize(r['pnl'],r['distribution'],{},None,'2026-10','2026-03',None)
    assert missing['cash_known'] is None
    before=service.normalize(r['pnl'],r['distribution'],{},[],'2026-02','2026-03',True)
    assert not before['available'] and all(m['fact'] is None for m in before['metrics'])


def test_stale_cache_is_explicit_and_failure_without_cache_is_not_zero(monkeypatch):
    m=market();key=f'portfolio-v2:{m["id"]}:park_gorkogo:2026-10'
    data=normalized();storage.cache_set(key,data)
    old=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    from monitoring.db import get_connection
    c=get_connection();c.execute('UPDATE portfolio_cache SET updated_at=?',(old,));c.commit();c.close()
    def failed(*a):raise RuntimeError('secret-access-details')
    monkeypatch.setattr(service,'_read',failed)
    p=service.load_project(m,'2026-10')
    assert p['stale'] and p['updated_at']==old and p['metrics'][0]['fact']==6000000
    with pytest.raises(RuntimeError):service.load_project(m,'2026-09')


def test_portfolio_total_is_not_silently_partial(monkeypatch):
    m=market();other=list_markets()[1];set_market_surf_spot_key(other['id'],'okko')
    def load(market,*a):
        if market['id']!=m['id']:raise RuntimeError('offline')
        return {'market_id':m['id'],'company_cash_known':100,'company_dividends':{'paid':200,'accrued':300,'balance':400,'plan':500}}
    monkeypatch.setattr(service,'load_project',load)
    value=service.portfolio('2026-10')
    assert not value['complete']
    assert value['cash_known_total'] is None and value['totals']['accrued'] is None
    assert any(p.get('error') for p in value['projects'])


def test_http_private_tokens_and_invalid_month_do_not_expose_data(monkeypatch):
    m=market();calls=[]
    monkeypatch.setattr(service,'request_project',lambda *args:calls.append(args) or {'name':'test'})
    async def run():
        async with TestClient(TestServer(_build_app())) as client:
            for path in ('/portfolio','/portfolio/api','/portfolio/meta'):
                assert (await client.get(path)).status==403
                assert (await client.get(path,params={'token':get_or_create_dashboard_token()})).status==403
            token=storage.token()
            for value in ('2026-13','2039-01','x'):
                r=await client.get('/portfolio/api',params={'token':token,'month':value})
                assert r.status==400
            assert not calls
            r=await client.get('/portfolio',params={'token':token})
            assert r.status==200 and r.headers['Cache-Control']=='no-store'
            assert 'no-referrer'==r.headers['Referrer-Policy']
            r=await client.get('/portfolio/api',params={'month':'2026-10','market_id':m['id']},headers={'Authorization':'Bearer '+token})
            assert r.status==200 and (await r.json())['name']=='test'
            r=await client.get('/portfolio/meta',headers={'Authorization':'Bearer '+token})
            assert (await r.json())['projects'][0]['share']==50
    asyncio.run(run())


def test_share_settings_are_roman_only_and_require_current_confirmation(monkeypatch):
    from bot import portfolio as flow
    monkeypatch.setattr(flow,'is_roman',lambda uid:uid==42)
    m=market()
    def update(uid,data='',text=''):
        msg=SimpleNamespace(reply_text=AsyncMock(),text=text)
        return SimpleNamespace(effective_user=SimpleNamespace(id=uid),effective_message=msg,callback_query=SimpleNamespace(from_user=SimpleNamespace(id=uid),data=data,message=msg,answer=AsyncMock()))
    async def run():
        await flow.on_portfolio_callback(update(99,f'pf:share:{m["id"]}'),SimpleNamespace())
        assert storage.dialog(99) is None
        await flow.on_portfolio_callback(update(42,f'pf:share:{m["id"]}'),SimpleNamespace())
        await flow.on_portfolio_reply(update(42,text='30%'),SimpleNamespace())
        state=storage.dialog(42);assert state['percent']==30 and storage.ownership(m)==50
        await flow.on_portfolio_callback(update(42,'pf:save:stale'),SimpleNamespace())
        assert storage.ownership(m)==50
        await flow.on_portfolio_callback(update(42,'pf:save:'+state['nonce']),SimpleNamespace())
        assert storage.ownership(m)==30
    asyncio.run(run())


def payment_client(rows,last=1):
    client=object.__new__(SurfCoffeeClient);client.switch_spot=lambda *args:None;client._with_retry=lambda fn:fn()
    def response(value):return SimpleNamespace(raise_for_status=lambda:None,json=lambda:value)
    client._client=SimpleNamespace(get=lambda *a,**k:response({'success':True,'data':[{'id':93,'code':'dividendy_partneram'}]}),post=lambda *a,**k:response({'success':True,'data':{'data':rows,'meta':{'last_page':last,'current_page':1}}}))
    return client


def raw_payment(**kwargs):
    return {'id':1,'spot_id':392,'status_id':2,'cash_flow_article_id':93,'pay_type_id':1,'amount':100,'date':'2026-10-08',**kwargs}


def test_real_payment_reader_keeps_missing_dates_and_drops_unapproved_records():
    client=payment_client([raw_payment(date=None,created_at='2026-01-03'),raw_payment(id=2),raw_payment(id=3,status_id=1)])
    rows=client.get_dividend_payments('park_gorkogo',2026)
    assert len(rows)==2 and rows[0]['date'] is None
    assert set(rows[0])=={'id','date','amount'}


@pytest.mark.parametrize('bad',[raw_payment(spot_id=97),raw_payment(date='invalid'),raw_payment(amount=float('nan')),raw_payment(cash_flow_article_id=1)])
def test_payment_scope_and_financial_validation_fail_closed(bad):
    with pytest.raises(RuntimeError):payment_client([bad]).get_dividend_payments('park_gorkogo',2026)


def test_unknown_pagination_is_not_treated_as_complete():
    with pytest.raises(RuntimeError):payment_client([raw_payment()],last=None).get_dividend_payments('park_gorkogo',2026)


def daily_board(month='2026-11',days=30):
    return {'table':{'revenue':{'data':[{'date':f'{month}-{i:02d} 00:00:00','planning_revenue':120,'fact_revenue':100} for i in range(1,days+1)]}},'by_period':{'period':[f'{i:02d}.{month[5:]}.{month[:4]}' for i in range(1,days+1)],'data':{'count':[10]*days}}}


def test_current_month_compares_only_completed_days():
    from portfolio.analytics import sales_to_date,operating_metrics
    board=daily_board();summary=sales_to_date(board,'2026-11',date(2026,11,15))
    assert summary['fact']==1400 and summary['plan']==1680 and summary['checks']==140
    assert summary['average']==10 and summary['through']=='2026-11-14'
    r=raw_inputs();p=lambda code,ym='2026-11',col='fact':service._field(r['pnl'],code,ym,col)
    o=operating_metrics(p,'2026-11',date(2026,11,15),board,daily_board('2026-10',31))
    assert o['sales']['fact']==1400 and o['checks_previous']==140
    assert o['checks_plan'] is None  # Не сравниваем полмесяца с полным месячным планом.
    assert sales_to_date(board,'2026-11',date(2026,11,1))['fact'] is None
    board['table']['revenue']['data'].pop(3)
    assert sales_to_date(board,'2026-11',date(2026,11,15))['fact'] is None


def test_team_efficiency_excludes_manager_pay_tax_and_hours():
    from portfolio.analytics import labor_summary
    r=raw_inputs();p=lambda code,ym='2026-10',col='fact':service._field(r['pnl'],code,ym,col)
    labor=labor_summary(p,r['payroll'],r['shifts'],r['team']['employees'],r['shifts_previous'],6000000)
    assert labor['fot']==1195000 and labor['hours']==1000
    assert labor['plan_share']==18 and labor['budget']==1080000
    assert labor['variance_rub']==115000 and labor['spmh']==6000
    assert labor['previous_spmh']==6000
    r['payroll']['awards'][0]['hold_tax']=200000
    assert labor_summary(p,r['payroll'],r['shifts'],r['team']['employees']) is None


def test_top_costs_do_not_add_direct_writeoffs_again_to_foodcost():
    from portfolio.analytics import cost_attention
    r=raw_inputs();p=lambda code,ym='2026-10',col='fact':service._field(r['pnl'],code,ym,col)
    o={'live':False,'foodcost':30,'foodcost_plan':27,'labor':{'fot':1195000,'budget':1080000}}
    rows=cost_attention(p,o,[{'key':'expenses_direct_writeoffs_expiration','label':'Срок годности','fact':200000,'plan':0},{'key':'expenses_indirect_salary_taxes','label':'НДФЛ','fact':130000,'plan':120000},{'key':'expenses_indirect_space_rent','label':'Аренда','fact':580000,'plan':480000}])
    assert {r['key'] for r in rows}=={'foodcost','fot','expenses_indirect_space_rent'}
    assert rows[0]['variance_rub']==180000


def test_thirty_day_plan_preserves_unknown_and_includes_only_horizon():
    project={'plan_report_month':'2026-10','planned':[{'date':'2026-11-26','company_amount':100},{'date':'2026-11-27','company_amount':200},{'date':'2026-12-26','company_amount':300},{'date':'2026-12-27','company_amount':400}]}
    assert service._upcoming(project,date(2026,11,27))==500
    project['plan_report_month']=None
    assert service._upcoming(project,date(2026,11,27)) is None


def test_business_margin_is_weighted_and_not_mean_of_project_margins(monkeypatch):
    m=market();other=list_markets()[1];set_market_surf_spot_key(other['id'],'okko')
    def load(market,*a):
        small=market['id']==m['id'];rev=100 if small else 900;profit=50 if small else 90
        return {'market_id':market['id'],'available':True,'company_cash_known':10,'company_dividends':{'paid':10,'accrued':20,'balance':30,'plan':40},'upcoming_30_days':50,'plan_report_month':'2026-10','metrics':[{'key':'income','fact':rev,'plan':rev},{'key':'net_profit','fact':profit,'plan':profit}]}
    monkeypatch.setattr(service,'load_project',load)
    data=service.portfolio('2026-10')
    assert data['business']['profit']==140
    assert data['business']['margin']==pytest.approx(14)  # Не (50% + 10%) / 2.
    assert data['business']['upcoming_30_days']==100


def test_only_latest_accepted_payout_schedule_is_used(monkeypatch,tmp_path):
    from manager_report import storage as reports,lifecycle
    from test_monthly_manager_report import model,full_answers
    m=market();monkeypatch.setattr(service,'_read',lambda market,month:normalized())
    def accept(month,amount):
        mod=model();mod['month']=month
        r=reports.create_report(m['id'],month,99,mod);answers=full_answers(mod)
        answers['payouts']={'items':[{'date':'2026-12-10','amount':amount,'comment':'Выплата'}],'note':''}
        reports.save_answers(r['id'],answers,1)
        reports.mark_review(r['id'],2,tmp_path/(month+'.pdf'));reports.claim_delivery(r['id'],2)
        lifecycle.submit(r['id'],2);reports.set_delivery_status(r['id'],'completed')
        assert lifecycle.decide(r['id'],2,'accept')
    accept('2026-09',100000);accept('2026-10',600000)
    p=service.load_project(m,'2026-10')
    assert len(p['planned'])==1
    assert p['planned'][0]['amount']==600000 and p['planned'][0]['company_amount']==300000
    assert p['plan_report_month']=='2026-10'


def test_background_reader_returns_before_nimba_and_does_not_duplicate_jobs(monkeypatch):
    import threading
    m=market();service._FAILED.clear();started=threading.Event();release=threading.Event();done=threading.Event();calls=[]
    def read(*args):
        calls.append(args);started.set();assert release.wait(5);return normalized()
    monkeypatch.setattr(service,'_read',read)
    first=service.request_project(m,'2026-10')
    assert first['loading']
    key=f'portfolio-v2:{m["id"]}:park_gorkogo:2026-10'
    future=service._JOBS[key];future.add_done_callback(lambda f:done.set())
    try:
        assert started.wait(3)
        assert service.request_project(m,'2026-10')['loading']
        assert len(calls)==1
    finally:release.set();assert done.wait(5)
    ready=service.request_project(m,'2026-10')
    assert not ready.get('loading') and ready['metrics'][0]['fact']==6000000


def test_cached_values_remain_available_while_background_refresh_is_blocked(monkeypatch):
    import threading
    m=market();service._FAILED.clear();key=f'portfolio-v2:{m["id"]}:park_gorkogo:2026-10';storage.cache_set(key,normalized())
    from monitoring.db import get_connection
    c=get_connection();c.execute('UPDATE portfolio_cache SET updated_at=?',((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),));c.commit();c.close()
    release=threading.Event();done=threading.Event()
    def read(*args):assert release.wait(5);return normalized()
    monkeypatch.setattr(service,'_read',read)
    try:
        value=service.request_project(m,'2026-10')
        assert value['stale'] and value['refreshing']
        assert value['metrics'][0]['fact']==6000000
        future=service._JOBS[key];future.add_done_callback(lambda f:done.set())
    finally:release.set();assert done.wait(5)


def test_background_failure_does_not_retry_on_every_poll(monkeypatch):
    import threading
    m=market();service._FAILED.clear();done=threading.Event();calls=[];release=threading.Event()
    def read(*args):calls.append(args);assert release.wait(5);raise RuntimeError('offline')
    monkeypatch.setattr(service,'_read',read)
    assert service.request_project(m,'2026-10')['loading']
    future=service._JOBS[f'portfolio-v2:{m["id"]}:park_gorkogo:2026-10'];future.add_done_callback(lambda f:done.set());release.set();assert done.wait(5)
    assert service.request_project(m,'2026-10').get('error')
    assert len(calls)==1
    service._FAILED.clear()
