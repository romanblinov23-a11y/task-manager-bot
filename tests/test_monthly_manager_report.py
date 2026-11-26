import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from manager_report import storage
from manager_report.model import make_model, with_answers, deviations, delta
from manager_report.questions import parse_answer, question_list, parse_plan_value
from manager_report import service
from monitoring.managers import register_manager, approve_manager
from monitoring.markets import list_markets, set_market_surf_spot_key

MONTH='2026-10'


def raw_inputs():
    values={
        'income':('Доходы',6300000,6000000), 'net_profit':('Чистая прибыль',2505000,2220000),
        'araar_count_receipts':('Чеки',10500,10000), 'araar_avg_receipts':('Средний чек',600,600),
        'food_cost':('Фудкост',27,28),'expenses_direct_cost_percent':('Прямая себестоимость',23.5,24),
        'expenses_direct_cost_percent_drinks':('Напитки',10,10),
        'expenses_direct_writeoffs_expiration':('Истек срок годности',48000,60000),
        'expenses_direct_writeoffs_bad':('Порча',24000,30000),
        'expenses_direct_writeoffs_menu':('Проработка',12000,18000),
        'expenses_direct_writeoffs_realise':('В реализацию',0,0),
        'expenses_direct_delivery':('Доставка',63000,60000),
        'expenses_indirect_salary':('Оплата труда',1134000,1200000),
        'expenses_indirect_salary_taxes':('НДФЛ',120000,130000),
        'expenses_indirect_salary_social':('Взносы',180000,180000),
    }
    def field(code,label,plan,fact,year=2026):
        return dict(code=code,title=label,items=[],results=[dict(period=f'{year}-{i:02d}-01',plan=plan,fact=fact if year==2026 and i>=3 else 0) for i in range(1,13)])
    fields=[field(k,*v) for k,v in values.items()]
    fields.append(dict(code='expenses_indirect',title='Косвенные',results=[],items=[
        dict(code='expenses_indirect_space',title='Содержание подразделения',results=[],items=[
            field('expenses_indirect_space_rent','Аренда',480000,480000),
            field('expenses_indirect_space_utility','Коммунальные',78000,90000)]),
        dict(code='expenses_indirect_marketing',title='Маркетинг',results=[],items=[
            field('expenses_indirect_marketing_smm','SMM',90000,60000),
            field('expenses_indirect_marketing_thanks','Комплимент',24000,30000)]),
        dict(code='expenses_indirect_team',title='Команда',results=[],items=[
            field('expenses_indirect_team_eatingIn','Питание',40000,42000)]),
        dict(code='expenses_indirect_salary',title='ФОТ',results=[],items=[]),
    ]))
    previous_fields=[field(k,*v,year=2025) for k,v in values.items()]
    shifts=[dict(user_id=1,user_job_title='Управляющий',main_manager=True,duration=100,award=100000,night_bonus=0),
            dict(user_id=2,user_job_title='Бариста',duration=1000,award=750000,night_bonus=10000,grade_bonus=10000,holiday_bonus=10000)]
    raw=dict(pnl={'2026':{'fields':fields},'2025':{'fields':previous_fields}},
             distribution={'2026':[dict(code='accrued',months={k:600000 for k in ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']}),dict(code='debt',months={'oct':-100000})],'2025':[]},
             team={'employees':[dict(id=1,job_title='Управляющий',main_manager=True,spot_date_opening='2026-03-01',employee_type_at='2026-03-01'),
                                dict(id=2,job_title='Бариста',grade={'code':'1'},spot_date_opening='2026-03-01',employee_type_at='2026-03-01'),
                                dict(id=3,job_title='Стажер',spot_date_opening='2026-03-01',employee_type_at=None)]},
             manager={'fin_result':[dict(name='Индекс финансового состояния предприятия',oct={'value':'B'})]},
             rating={'period':MONTH,'kpi':{'value':.85,'is_preliminary':False},'rating':{'status':'approved'}},
             guests={'guests':{'table':[dict(date='2026-10-01',guest_count=10000,licenses=450,new_licenses_share=30,guest_share=62,avg_visits_per_guest=2.4)]}},
             shifts={'awards':shifts},shifts_previous={'awards':shifts},
             payroll={'awards':[dict(user_id=1,main_manager=True,hold_tax=5000),dict(user_id=2,hold_tax=125000,hold_other=10000)]},
             managers={'manager':[dict(user_id=1,main_manager=True)]},
             bonuses={'awards':[dict(user_id=2,award=50000)]},bep={'awards':[dict(user_id=2,total_award=20000)]},
             contracts={'contracts':[dict(user_id=2,award=60000)]},other={'other_awards':[dict(user_id=2,award=10000)]},holds={},expense_plan={},warnings=[])
    raw['dashboard']={'by_period':{'period':[f'{i:02d}.10.2026' for i in range(1,32)],'data':{
        'revenue':[190000]*30+[300000], 'count':[320]*30+[400], 'average':[593.75]*30+[750]}}}
    return raw


def plan():
    return {'effective_month':MONTH,'updated_at':'2026-10-01','values':dict(guest_count=10000,licenses=500,new_licenses_share=35,guest_share=60,avg_visits_per_guest=2.6,basket=2.5)}


def model():
    return make_model(raw_inputs(),MONTH,plan())


def report_stub():
    return dict(market_id=list_markets()[0]['id'],month=MONTH,model=model(),answers={})


def full_answers(m):
    issues=deviations(with_answers(m,{'dishes':24000}))
    groups=[dict(ids=[i['id'] for i in issues],cause='Сезонность и график',action='Проверить график и обучение',expected='Вернуться к плану',deadline='2026-11-15')]
    return dict(manager_name='Иванов Иван Иванович',staff=m['staff'],dishes=24000,events=[dict(date='2026-10-04',title='Каппинг',participants=18,comment='Помог развитию кофейного сообщества. Повторим формат.')],
                payouts=dict(items=[dict(date='2026-11-15',amount=600000,comment='Выплата')],note=''),
                deviations=groups,summary='Месяц был сложным. Нужна помощь с партнерством.',
                payroll_note='Сверены состав начислений и удержания. Различие отражено отдельно.',expense_groups=[],
                past_tasks=[dict(title=f'Задача {i}',deadline='2026-10-20',result='Выполнено 20.10.2026') for i in range(1,6)],
                next_tasks=[dict(title=f'Фокус {i}',deadline='2026-11-20',comment='Обучение команды',expected='Повысить качество') for i in range(1,6)])


def test_plan_versions_and_report_snapshots_survive_restart():
    market=list_markets()[0];mid=market['id']
    storage.save_plan(mid,MONTH,plan()['values'],42)
    first=storage.get_plan(mid,MONTH)
    report=storage.create_report(mid,MONTH,99,make_model(raw_inputs(),MONTH,first))
    storage.save_plan(mid,'2026-11',{**plan()['values'],'licenses':700},42)
    assert storage.get_plan(mid,'2026-12')['values']['licenses']==700
    assert storage.get_plan(mid,MONTH)['values']['licenses']==500
    assert storage.get_report(report['id'])['model']['plan_snapshot']==first
    storage.set_dialog(99,{'kind':'report','report_id':report['id']})
    from monitoring.db import init_schema
    init_schema()
    assert storage.get_dialog(99)['report_id']==report['id']
    assert storage.get_report(report['id'])['model']['plan_snapshot']['values']['licenses']==500


def test_model_has_no_preopening_history_and_dynamic_fot():
    m=model()
    assert not m['has_year']
    assert all(r['month']>='2026-03' for r in m['revenue_history'])
    assert len(m['revenue_history'])==8
    assert all(r['last_year'] is None for r in m['metrics'])
    assert m['fot']==1195000  # НДФЛ управляющего исключен; его взносов нет.
    assert m['hours']==1000
    assert m['fot_budget']==pytest.approx(1080000)
    assert m['spmh']['fact']==6000
    assert m['spmh']['plan'] is None
    assert m['debt']==-100000
    assert m['dividends']['plan']==2505000  # План ЧП, не вымышленный план дивидендов.
    assert not any(r['key'].startswith('expenses_indirect_salary') for r in m['indirect'])
    assert not any('thanks' in r['key'] or 'eatingIn' in r['key'] for r in m['indirect'])
    assert 'surname' not in str(m)


def test_deviation_threshold_handles_zero_both_directions_and_exact_boundary():
    metrics=[dict(key=str(i),label=str(i),fact=f,plan=p,direction='up') for i,(f,p) in enumerate([
        (105,100),(95,100),(105.01,100),(94.99,100),(1,0),(0,0),(3,None)])]
    issues=deviations({'metrics':metrics})
    assert [r['key'] for r in issues]==['2','3','4']
    assert issues[-1]['delta'] is None
    assert delta(5,0) is None
    assert delta(-80,-100)==20


@pytest.mark.parametrize('kind,value',[('number','nan'),('number','inf'),('percent','101'),('count','1.5'),('count','-1')])
def test_plan_parser_rejects_invalid_values(kind,value):
    with pytest.raises(ValueError):parse_plan_value(kind,value)


def test_missing_guest_period_and_nonfinite_inputs_are_not_silently_zero():
    raw=raw_inputs();raw['guests']['guests']['table'][0]['date']='2026-09-01'
    with pytest.raises(ValueError,match='месяц'):make_model(raw,MONTH,plan())
    raw=raw_inputs();raw['pnl']['2026']['fields'][0]['results'][9]['fact']=None
    with pytest.raises(ValueError,match='выручка'):make_model(raw,MONTH,plan())


def test_forms_require_each_deviation_once_and_five_dated_tasks():
    report=report_stub();report['answers']['dishes']=24000
    questions=question_list(report);q=next(q for q in questions if q['key']=='deviations')
    ids=','.join(str(i['id']) for i in q['issues'])
    good=f'{ids} | Причина | Действие | Результат | 15.11.2026'
    assert parse_answer(q,good,report)[0]['deadline']=='2026-11-15'
    with pytest.raises(ValueError):parse_answer(q,good+'\n'+good,report)
    with pytest.raises(ValueError):parse_answer(q,'1 | причина | действие | результат | 15.11.2026',report)
    q=next(q for q in questions if q['key']=='next_tasks')
    with pytest.raises(ValueError):parse_answer(q,'Задача | 15.11 | комментарий | результат',report)
    text='\n'.join(f'Задача {i} | 15.11.2026 | Комментарий | Результат' for i in range(5))
    assert len(parse_answer(q,text,report))==5


def test_previous_tasks_are_taken_only_from_completed_same_project_history():
    report=report_stub();previous={'status':'completed','answers':{'next_tasks':full_answers(model())['next_tasks']}}
    q=next(q for q in question_list(report,previous) if q['key']=='past_tasks')
    assert q['kind']=='past_results'
    parsed=parse_answer(q,'\n'.join(f'{i} | Выполнено 20.10.2026' for i in range(1,6)),report)
    assert parsed[0]['deadline']==previous['answers']['next_tasks'][0]['deadline']
    previous['status']='collecting'
    assert next(q for q in question_list(report,previous) if q['key']=='past_tasks')['kind']=='past_tasks'


def test_closed_gate_prevents_any_collection_or_questions(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,*args):pass
        def login(self):calls.append('login')
        def get_month_admission(self,*args):return dict(month=MONTH,closed=False)
        def get_pnl_year(self,*args):pytest.fail('No financial queries before closed admission')
        def close(self):calls.append('close')
    monkeypatch.setattr(service,'SurfCoffeeClient',Client)
    with pytest.raises(service.OpenMonthError):service.collect('park_gorkogo',MONTH,plan())
    assert calls==['login','close']


def test_pdf_is_eleven_landscape_pages_and_contains_required_sections(tmp_path):
    from manager_report.pdf import build_report
    # Parse with bundled pypdf only when installed; raw PDF checks still run.
    m=model();answers=full_answers(m)
    out=build_report(m,answers,{'name':'Тестовый спот'},tmp_path/'report.pdf',demo=True)
    assert out.stat().st_size>20000
    data=out.read_bytes()
    assert data.startswith(b'%PDF-')
    assert data.count(b'/Type /Page\n')==11
    assert b'/MediaBox [ 0 0 1120 630 ]' in data


def test_pdf_refuses_overflow_instead_of_truncating(tmp_path):
    from manager_report.pdf import build_report, LayoutError
    m=model();answers=full_answers(m)
    answers['events']=[dict(date='2026-10-04',title=f'Событие {i}',participants=10,comment='Очень длинный комментарий. '*8) for i in range(40)]
    with pytest.raises(LayoutError):build_report(m,answers,{'name':'Тест'},tmp_path/'overflow.pdf')


def test_storage_rejects_stale_revision_and_delivery_double_click():
    r=storage.create_report(list_markets()[0]['id'],MONTH,99,model())
    assert storage.save_answers(r['id'],{'dishes':1},1)
    assert not storage.save_answers(r['id'],{'dishes':2},1)
    assert storage.mark_review(r['id'],2,'file.pdf')
    assert storage.claim_delivery(r['id'],2)
    assert not storage.claim_delivery(r['id'],2)
    storage.record_delivery(r['id'],2,42)
    storage.recover_deliveries()
    assert storage.get_report(r['id'])['status']=='delivery_failed'
    assert storage.delivered_to(r['id'],2)=={42}


def setup_flow(monkeypatch):
    from bot import manager_report_flow as flow
    monkeypatch.setattr(flow.settings,'ROMAN_TELEGRAM_ID','42')
    market=list_markets()[0]
    set_market_surf_spot_key(market['id'],'park_gorkogo')
    market={**market,'surf_spot_key':'park_gorkogo'}
    register_manager(99,'Управляющий','Управляющий',market['id']);approve_manager(99)
    monkeypatch.setattr(flow,'tz_today',lambda:date_for_test())
    return flow,market


def date_for_test():
    from datetime import date
    return date(2026,11,26)


def update(uid,text='',callback=None):
    message=SimpleNamespace(text=text,reply_text=AsyncMock())
    query=SimpleNamespace(data=callback,from_user=SimpleNamespace(id=uid),message=message,answer=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid),effective_message=message,callback_query=query)


def test_flow_open_month_does_not_create_draft_or_ask_questions(monkeypatch):
    flow,market=setup_flow(monkeypatch)
    def closed(*args):raise service.OpenMonthError()
    monkeypatch.setattr(flow,'collect',closed)
    u=update(99)
    asyncio.run(flow.on_manager_report_command(u,SimpleNamespace(args=[MONTH])))
    assert storage.find_report(market['id'],MONTH) is None
    assert storage.get_dialog(99) is None
    assert any('не закрыт' in c.args[0] for c in u.effective_message.reply_text.call_args_list)


def test_kpi_settings_and_callbacks_are_roman_only_and_spot_specific(monkeypatch):
    flow,market=setup_flow(monkeypatch)
    u=update(99,callback=f"mr:plan:{market['id']}:{MONTH}")
    asyncio.run(flow.on_manager_report_callback(u,SimpleNamespace()))
    assert storage.get_dialog(99) is None
    u=update(42,callback=f"mr:plan:{market['id']}:{MONTH}")
    asyncio.run(flow.on_manager_report_callback(u,SimpleNamespace()))
    for key in flow.PLAN_FIELDS:
        asyncio.run(flow.on_manager_report_reply(update(42,str(plan()['values'][key])),SimpleNamespace()))
    asyncio.run(flow.on_manager_report_callback(update(42,callback='mr:saveplan'),SimpleNamespace()))
    assert storage.get_plan(market['id'],MONTH)['values']==plan()['values']
    assert storage.get_plan(list_markets()[1]['id'],MONTH) is None


def test_full_questionnaire_preview_approval_and_persistent_delivery(monkeypatch,tmp_path):
    flow,market=setup_flow(monkeypatch)
    monkeypatch.setattr(flow.settings,'MONITORING_DB_PATH',str(tmp_path/'db.sqlite'))
    collected=[]
    def collect_once(*args):
        collected.append(args)
        return {**model(),'admission':{'month':MONTH,'closed':True,'checked_at':'2026-11-26'}}
    monkeypatch.setattr(flow,'collect',collect_once)
    storage.save_plan(market['id'],MONTH,plan()['values'],42)
    documents=[]
    async def send_document(**kwargs):documents.append((kwargs['chat_id'],kwargs['document'].read(),kwargs.get('caption')))
    bot=SimpleNamespace(send_document=AsyncMock(side_effect=send_document))
    context=SimpleNamespace(args=[MONTH],bot=bot)
    async def scenario():
        await flow.on_manager_report_command(update(99),context)
        while True:
            state=storage.get_dialog(99)
            r=storage.get_report(state['report_id'])
            field=state.get('field')
            if field is None:break
            forms={
                'manager_name':'Иванов Иван Иванович','staff':'подтверждаю','dishes':'24000',
                'events':'04.10.2026 | Каппинг | 18 | Привлек новых гостей; формат стоит повторить.',
                'payouts':'15.11.2026 | 600000 | Выплата',
                'payroll_note':'Сверены начисления и удержания, разница показана отдельно.',
                'expense_groups':'подтверждаю','summary':'Месяц прошел хорошо, нужна помощь с партнерством.',
                'past_tasks':'\n'.join(f'Прошлая задача {i} | 20.10.2026 | Выполнено 20.10.2026' for i in range(5)),
                'next_tasks':'\n'.join(f'Новая задача {i} | 20.11.2026 | Обучение | Повысить качество' for i in range(5)),
            }
            if field=='deviations':
                q=next(q for q in flow._questions(r) if q['key']=='deviations')
                forms[field]=','.join(str(i['id']) for i in q['issues'])+' | Общая причина | Пересмотреть процессы | Вернуться к плану | 15.11.2026'
            await flow.on_manager_report_reply(update(99,forms[field]),context)
        r=storage.find_report(market['id'],MONTH)
        assert r['status']=='review'
        assert len(documents)==1 and documents[0][0]==99  # только предпросмотр
        # Другой сотрудник не может нажать кнопку отправки чужого отчета.
        await flow.on_manager_report_callback(update(100,callback=f"mr:send:{r['id']}:{r['revision']}"),context)
        assert len(documents)==1
        # Продолжение использует сохраненный допуск, без новой проверки закрытия.
        await flow.on_manager_report_command(update(99),context)
        assert len(collected)==1
        preview_count=len(documents)
        r=storage.get_report(r['id'])
        await flow.on_manager_report_callback(update(99,callback=f"mr:send:{r['id']}:{r['revision']}"),context)
        assert {d[0] for d in documents[preview_count:]}=={42,99}
        assert documents[-1][1]==documents[-2][1]
        assert storage.get_report(r['id'])['status']=='completed'
        sent=len(documents)
        await flow.on_manager_report_callback(update(99,callback=f"mr:send:{r['id']}:{r['revision']}"),context)
        assert len(documents)==sent
    asyncio.run(scenario())


def test_partial_delivery_retry_does_not_resend_successful_recipient(monkeypatch,tmp_path):
    flow,market=setup_flow(monkeypatch)
    r=storage.create_report(market['id'],MONTH,99,model())
    path=tmp_path/'test.pdf';path.write_bytes(b'%PDF-test')
    storage.mark_review(r['id'],1,path)
    sent=[];fail=True
    async def send(**kwargs):
        nonlocal fail
        uid=kwargs['chat_id']
        if uid==99 and fail:raise RuntimeError('Telegram unavailable')
        sent.append(uid)
    context=SimpleNamespace(bot=SimpleNamespace(send_document=AsyncMock(side_effect=send)))
    async def scenario():
        nonlocal fail
        u=update(99,callback=f"mr:send:{r['id']}:1")
        await flow.on_manager_report_callback(u,context)
        assert sent==[42]
        assert storage.get_report(r['id'])['status']=='delivery_failed'
        fail=False
        await flow.on_manager_report_callback(u,context)
        assert sent==[42,99]
        assert storage.get_report(r['id'])['status']=='completed'
    asyncio.run(scenario())


def test_editing_dishes_invalidates_all_previous_deviation_answers(monkeypatch):
    flow,market=setup_flow(monkeypatch)
    m=model();r=storage.create_report(market['id'],MONTH,99,m)
    storage.save_answers(r['id'],full_answers(m),1)
    storage.set_dialog(99,dict(kind='report',report_id=r['id'],field='dishes'))
    asyncio.run(flow.on_manager_report_reply(update(99,'25000'),SimpleNamespace()))
    assert 'deviations' not in storage.get_report(r['id'])['answers']
    assert storage.get_dialog(99)['field']=='deviations'


def test_lost_access_denies_existing_report_and_never_sends(monkeypatch):
    flow,market=setup_flow(monkeypatch)
    r=storage.create_report(market['id'],MONTH,99,model())
    storage.set_dialog(99,dict(kind='report',report_id=r['id'],field='dishes'))
    monkeypatch.setattr(flow,'available',lambda _:[])
    bot=SimpleNamespace(send_document=AsyncMock())
    asyncio.run(flow.on_manager_report_reply(update(99,'24000'),SimpleNamespace(bot=bot)))
    bot.send_document.assert_not_awaited()
    assert storage.get_dialog(99) is None


@pytest.mark.parametrize('rows', [[], [{'month':MONTH,'is_close':1}], [{'month':MONTH,'is_close':'true'}], [{'month':'2026-09','is_close':True}], [{'month':MONTH,'is_close':True}]*2])
def test_closed_month_source_rejects_missing_ambiguous_or_non_boolean_status(rows):
    from revenue.surfcoffee_client import SurfCoffeeClient
    client=object.__new__(SurfCoffeeClient)
    client.switch_spot=lambda *_:None
    client._client=SimpleNamespace(get=lambda *a,**k:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'success':True,'data':rows}))
    client._with_retry=lambda fn:fn()
    with pytest.raises(RuntimeError):client.get_month_admission('okko',MONTH)


def test_payroll_detail_has_no_plans_and_only_total_fot_has_budget():
    m=model()
    assert all(row['plan'] is None for row in m['payroll_rows'])
    assert m['fot_budget']>0
    assert m['hour_rate']['plan'] is None
    assert m['spmh']['plan'] is None


def test_cover_uses_reporting_month_and_supplied_metadata(monkeypatch,tmp_path):
    from manager_report import pdf
    centered=[];paragraphs=[];texts=[]
    original=pdf.canvas.Canvas.drawCentredString
    def draw(c,x,y,text,*args,**kwargs):
        centered.append(text);return original(c,x,y,text,*args,**kwargs)
    monkeypatch.setattr(pdf.canvas.Canvas,'drawCentredString',draw)
    para=pdf.Page.paragraph
    def paragraph(self,x,y,w,text,*args,**kwargs):
        paragraphs.append(text);return para(self,x,y,w,text,*args,**kwargs)
    monkeypatch.setattr(pdf.Page,'paragraph',paragraph)
    text=pdf.Page.text
    def record(self,x,y,value,*args,**kwargs):
        texts.append(str(value));return text(self,x,y,value,*args,**kwargs)
    monkeypatch.setattr(pdf.Page,'text',record)
    m=model();pdf.build_report(m,full_answers(m),{'name':'Тестовый спот'},tmp_path/'cover.pdf',provided_on='2026-11-26')
    assert centered[:2]==['October','2026']
    assert centered[-1]=='Thanks'
    assert paragraphs[:3]==['26.11.2026','Тестовый спот','Иванов Иван Иванович']
    assert '01 / 11' in texts and '11 / 11' in texts


def test_manager_cover_name_requires_surname_and_first_name():
    q={'kind':'manager_name'}
    with pytest.raises(ValueError):parse_answer(q,'Иван',{})
    assert parse_answer(q,'Иванов Иван Иванович',{})=='Иванов Иван Иванович'


def test_submission_on_next_day_refreshes_cover_before_confirmation(monkeypatch,tmp_path):
    from datetime import datetime,timedelta
    from manager_report import lifecycle
    flow,market=setup_flow(monkeypatch)
    r=storage.create_report(market['id'],MONTH,99,model())
    path=tmp_path/'yesterday.pdf';path.write_bytes(b'old-preview')
    storage.mark_review(r['id'],1,path);r=storage.get_report(r['id'])
    tomorrow=datetime.fromisoformat(r['updated_at']).astimezone(lifecycle.MOSCOW)+timedelta(days=1)
    monkeypatch.setattr(lifecycle,'now',lambda:tomorrow)
    preview=AsyncMock();monkeypatch.setattr(flow,'_preview',preview)
    context=SimpleNamespace(bot=SimpleNamespace(send_document=AsyncMock()))
    asyncio.run(flow._deliver(update(99).effective_message,context,99,r,1))
    preview.assert_awaited_once()
    context.bot.send_document.assert_not_awaited()
    assert lifecycle.workflow(r['id']) is None
    assert storage.get_report(r['id'])['status']=='review'
