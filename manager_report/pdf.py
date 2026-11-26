"""Обложка и 10 горизонтальных страниц. Переполнение возвращается в анкету, не обрезается."""
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph

from manager_report.model import delta, deviations, with_answers

W,H,M=1120,630,44
CW=W-2*M
INK='#171917';GRAY='#687069';LIGHT='#EDEFE9';BG='#FAFAF7';LIME='#E6FA46';WHITE='#FFFFFF';GREEN='#176140';RED='#B32925'
FONT_DIR=Path(__file__).parent/'fonts'


class LayoutError(ValueError):
    def __init__(self,section,message):
        self.section=section
        super().__init__(f'Раздел {section}: {message}')


def money(value):
    return '—' if value is None else f'{value:,.0f}'.replace(',',' ')+' руб.'


def fmt(value,unit='number'):
    if value is None:return '—'
    if unit=='money':return money(value)
    if unit=='count':return f'{value:,.0f}'.replace(',',' ')
    return f'{value:,.2f}'.replace(',',' ').replace('.',',')+('%' if unit=='percent' else '')


def change(fact,base,direction='up'):
    d=delta(fact,base)
    if d is None:
        return ('база 0' if base==0 else '—',GRAY)
    color=GRAY if d==0 or direction=='neutral' else GREEN if ((d>0)==(direction=='up')) else RED
    return (f'{d:+.1f}%'.replace('.',','),color)


def date_label(day):
    return '.'.join(reversed(day.split('-')))


class Page:
    def __init__(self,path,model,market,demo=False):
        for name,file in [('MR','LiberationSans-Regular.ttf'),('MRB','LiberationSans-Bold.ttf')]:
            if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(FONT_DIR/file)))
        self.c=canvas.Canvas(str(path),pagesize=(W,H));self.section=0
        self.c.setTitle(f"Отчет управляющего · {market['name']} · {model['month']}")
        self.model=model;self.market=market;self.demo=demo

    def box(self,x,y,w,h,fill=LIGHT,r=12):
        self.c.setFillColor(HexColor(fill));self.c.roundRect(x,H-y-h,w,h,r,fill=1,stroke=0)

    def text(self,x,y,s,size=11,bold=False,color=INK):
        if x<0 or y<0 or y+size>H-18:raise LayoutError(self.section,'Текст выходит за границы страницы.')
        self.c.setFillColor(HexColor(color));self.c.setFont('MRB' if bold else 'MR',size)
        self.c.drawString(x,H-y-size*.83,str(s))

    def paragraph(self,x,y,w,s,size=11,maxh=50,color=INK,bold=False):
        p=Paragraph(escape(str(s)).replace('\n','<br/>'),ParagraphStyle('m',fontName='MRB' if bold else 'MR',fontSize=size,leading=size*1.2,textColor=HexColor(color)))
        _,h=p.wrap(w,maxh)
        if h>maxh+.1:raise LayoutError(self.section,'Слишком длинный текст. Сократите формулировки или объедините общие причины.')
        p.drawOn(self.c,x,H-y-h)
        return h

    def start(self,num,title,dark=False):
        self.section=num;self.c.setFillColor(HexColor(INK if dark else BG));self.c.rect(0,0,W,H,fill=1,stroke=0)
        self.text(M,24,'SURF COFFEE / ОТЧЕТ УПРАВЛЯЮЩЕГО',10,True,WHITE if dark else INK)
        self.text(W-M-43,24,f'{num+1:02d} / 11',10,color=LIME if dark else INK)
        if not dark:
            for x,label,col in [(641,'благоприятно',GREEN),(769,'неблагоприятно',RED),(916,'без оценки',GRAY)]:
                self.c.setFillColor(HexColor(col));self.c.circle(x,H-28,2.5,fill=1,stroke=0);self.text(x+8,24,label,8.7,color=col)
        if title:
            self.text(M,56,title,36,True,WHITE if dark else INK)
            self.text(M,103,f"{self.market['name'].upper()} / {self.model['month']}",10,color=GRAY)
        self.c.setStrokeColor(HexColor('#D6DAD1' if not dark else '#3C413B'));self.c.setLineWidth(.5)
        self.c.line(M,H-35,W-M,H-35);self.c.line(M,H-593,W-M,H-593)
        footer='ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ / ТЕСТ СБОРКИ' if self.demo else f"Отчетный месяц {self.model['month']} · НИМБ и подтвержденные ответы управляющего"
        self.text(M,601,footer,9,color=GRAY);self.text(W-M-16,601,f'{num+1:02d}',9,color=GRAY)

    def end(self):self.c.showPage()

    def card(self,x,y,w,label,value,lines=(),fill=LIGHT,h=108):
        self.box(x,y,w,h,fill)
        self.text(x+16,y+13,label,10,True)
        size=min(30,30*(w-32)/max(1,pdfmetrics.stringWidth(str(value),'MRB',30)))
        self.text(x+16,y+34,value,size,True)
        for i,line in enumerate(lines):
            string,color=line if isinstance(line,tuple) else (line,GRAY)
            self.paragraph(x+16,y+73+15*i,w-32,string,10,maxh=14,color=color)

    def table(self,x,y,w,headers,rows,weights=None,maxh=300,size=11,minsize=9.3):
        widths=[w*a/sum(weights or [1]*len(headers)) for a in (weights or [1]*len(headers))]
        def cell(v):return v if isinstance(v,tuple) else (v,INK)
        def paragraph(v,ww,fs,header=False):
            value,col=cell(v)
            return Paragraph(escape(str(value)).replace('\n','<br/>'),ParagraphStyle('t',fontName='MRB' if header else 'MR',fontSize=fs,leading=fs*1.18,textColor=HexColor(WHITE if header else col)))
        chosen=None
        sizes=[max(minsize,size-i*.25) for i in range(int((size-minsize)/.25)+2)]
        for fs in sizes:
            grid=[]
            for ri,row in enumerate([headers]+list(rows)):
                pars=[paragraph(v,ww-16,fs if ri else min(10,fs),ri==0) for v,ww in zip(row,widths)]
                height=max([p.wrap(ww-16,1000)[1] for p,ww in zip(pars,widths)]+[0])+7
                grid.append((pars,height))
            if sum(h for _,h in grid)<=maxh:
                chosen=grid;break
        if chosen is None:raise LayoutError(self.section,'Таблица не помещается на один лист. Сгруппируйте статьи/общие причины или сократите комментарии; ничего не было обрезано.')
        yy=y
        for ri,(pars,height) in enumerate(chosen):
            if ri==0:self.box(x,yy,w,height,INK,r=6)
            elif ri%2:self.box(x,yy,w,height,LIGHT,r=0)
            xx=x
            for p,ww in zip(pars,widths):
                _,ph=p.wrap(ww-16,height)
                p.drawOn(self.c,xx+8,H-yy-3-ph);xx+=ww
            yy+=height
        return yy

    def chart(self,x,y,w,h,series,title,dark=False,percent=False,money_scale=False):
        self.text(x,y,title,11,True,WHITE if dark else INK)
        if not series:
            self.text(x,y+35,'История не получена',11,color=GRAY);return
        vals=[r['value']/(1_000_000 if money_scale else 1) for r in series]
        lo=min(vals);hi=max(vals)
        spread=hi-lo or max(abs(hi)*.2,1)
        lo-=spread*.15;hi+=spread*.15
        left=x+35;right=x+w-10;top=y+32;bottom=y+h-26
        if bottom<=top:raise LayoutError(self.section,'Недостаточно места для графика.')
        for j in range(3):
            v=lo+(hi-lo)*j/2;yy=bottom-(bottom-top)*j/2
            self.c.setStrokeColor(HexColor('#3C413B' if dark else '#D6DAD1'));self.c.line(left,H-yy,right,H-yy)
            self.text(x,yy-5,f'{v:.1f}'.replace('.',','),8,color=GRAY)
        points=[]
        for i,(row,v) in enumerate(zip(series,vals)):
            px=(left+right)/2 if len(vals)==1 else left+(right-left)*i/(len(vals)-1)
            py=bottom-(v-lo)/(hi-lo)*(bottom-top);points.append((px,py))
            self.text(px-14,bottom+9,row['month'][2:].replace('-','/'),8,color=GRAY)
        self.c.setStrokeColor(HexColor(LIME if dark else INK));self.c.setLineWidth(2)
        path=self.c.beginPath();path.moveTo(points[0][0],H-points[0][1])
        for xx,yy in points[1:]:path.lineTo(xx,H-yy)
        self.c.drawPath(path)
        for (xx,yy),value in zip(points,vals):
            self.c.setFillColor(HexColor(LIME if dark else INK));self.c.circle(xx,H-yy,2.5,fill=1,stroke=0)
        self.c.setLineWidth(.5)


def build_report(model,answers,market,path,demo=False,provided_on=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    m=with_answers(model,answers);p=Page(path,m,market,demo);metrics={r['key']:r for r in m['metrics']}
    # Обложка отделена от десяти содержательных разделов.
    from config.timeutil import today
    provided_on=provided_on or today().isoformat()
    months=('January','February','March','April','May','June','July','August','September','October','November','December')
    month_name=months[int(m['month'][5:7])-1]
    p.start(0,'',dark=True)
    p.c.setFillColor(HexColor(LIME));p.c.setFont('MR',134)
    baseline=H/2-(pdfmetrics.getAscent('MR')+pdfmetrics.getDescent('MR'))/1000*134/2
    p.c.drawCentredString(W/2,baseline+48,month_name)
    p.c.setFont('MR',62);p.c.drawCentredString(W/2,baseline-40,m['month'][:4])
    for x,w,label,value in [
        (M,240,'ДАТА ПРЕДОСТАВЛЕНИЯ',date_label(provided_on)),
        (M+270,320,'СПОТ',market['name']),
        (M+620,CW-620,'УПРАВЛЯЮЩИЙ',answers.get('manager_name') or 'ФИО не указано')]:
        p.text(x,513,label,9,True,GRAY)
        p.paragraph(x,535,w,value,16,maxh=44,color=WHITE)
    p.end()
    def comp(row,key,label):
        base=row.get(key)
        if base is None:return None
        return (f"{label}: {fmt(base,row['unit'])} / {change(row['fact'],base,row['direction'])[0]}",change(row['fact'],base,row['direction'])[1])
    def comps(row,year=False,prev=False):
        out=[comp(row,'plan','план')]
        if year:out.append(comp(row,'last_year','год назад'))
        if prev:out.append(comp(row,'previous','прошлый месяц'))
        return [r for r in out if r is not None]
    ww=(CW-24)/3
    # 1 — титульная. План дивидендов — план чистой прибыли.
    p.start(1,'Итоги месяца')
    for i,key in enumerate(('revenue','profit','dividends')):
        row=metrics[key];p.card(M+i*(ww+12),130,ww,row['label'].upper(),fmt(row['fact'],row['unit']),comps(row,year=key!='dividends'),LIME if i==0 else LIGHT,h=112)
    p.box(M,256,CW,64,WHITE)
    p.text(M+16,268,'ИНДЕКС ФИНАНСОВОГО СОСТОЯНИЯ',10,True);p.text(M+16,287,m['financial_index'],20)
    p.text(M+CW/2,268,'РЕЙТИНГ УПРАВЛЯЮЩЕГО',10,True)
    score=fmt(m['rating']['value'])+(' · предварительный' if m['rating']['preliminary'] else '')
    p.text(M+CW/2,287,score,18)
    p.box(M,334,CW,242,INK);p.chart(M+20,350,CW-40,206,m['revenue_history'],'Выручка / доступная история, до 12 месяцев / млн руб.',True,money_scale=True)
    p.end()
    # 2 — четыре KPI, гостевая таблица без пустой прошлогодней колонки.
    p.start(2,'Выручка, чеки и гости');sw=(CW-36)/4
    for i,key in enumerate(('revenue','checks','avg_check','basket')):
        row=metrics[key];ls=comps(row,year=key in ('revenue','checks','basket'),prev=key=='avg_check')
        if key=='basket':ls=[f"{answers['dishes']} блюд / {fmt(metrics['checks']['fact'],'count')} чеков"]+ls
        p.card(M+i*(sw+12),130,sw,row['label'].upper(),fmt(row['fact'],row['unit']),ls,LIME if i==0 else LIGHT,h=122)
    p.text(M,266,'ЛОЯЛЬНОСТЬ И ГОСТЕВОЙ ПОТОК',11,True)
    guest_rows=[metrics[k] for k in ('guest_count','licenses','new_licenses_share','guest_share','avg_visits_per_guest')]
    show_year=any(r['last_year'] is not None for r in guest_rows)
    headers=['Показатель','План','Факт','Δ к плану']+(['Год назад','Δ к году'] if show_year else [])
    rows=[]
    for r in guest_rows:
        row=[r['label'],fmt(r['plan'],r['unit']),fmt(r['fact'],r['unit']),change(r['fact'],r['plan'],r['direction'])]
        if show_year:row += [fmt(r['last_year'],r['unit']),change(r['fact'],r['last_year'],r['direction'])]
        rows.append(row)
    p.table(M,288,CW,headers,rows,weights=[2.6,1,1,1]+([1,1] if show_year else []),maxh=235,size=12)
    p.paragraph(M,548,CW,'Наполненность = проданные блюда / чеки. Гости и чеки — разные показатели. Планы гостевых KPI заданы Романом для этого спота.',11,maxh=32,color=GRAY);p.end()
    # 3 — фудкост на одном листе.
    p.start(3,'Фудкост')
    for i,key in enumerate(('food_cost','direct_share')):
        r=metrics[key];p.card(M+i*(ww+12),130,ww,r['label'].upper(),fmt(r['fact'],'percent'),comps(r,prev=True),LIME if i==0 else LIGHT,h=112)
    total=sum(r['amount'] or 0 for r in m['writeoffs'])
    p.card(M+2*(ww+12),130,ww,'СПИСАНИЯ И КОРРЕКТИРОВКИ',money(total),['по категориям ниже'],h=112)
    half=(CW-20)/2
    for x,title,rows in [(M,'СЕБЕСТОИМОСТЬ / % ВЫРУЧКИ',m['categories']), (M+half+20,'СПИСАНИЯ / % ВЫРУЧКИ',m['writeoffs'])]:
        p.text(x,258,title,10,True)
        p.table(x,272,half,['Категория','План','Факт','Пред. мес.'],[
            [r['label'],fmt(r['plan'],'percent'),(fmt(r['fact'],'percent'),change(r['fact'],r['plan'],'down')[1]),fmt(r['previous'],'percent')] for r in rows],weights=[2.6,1,1,1.1],maxh=180,size=10.3,minsize=9.3)
    d=m['delivery'];p.box(M,456,310,106,LIME)
    p.text(M+14,468,'ДОСТАВКА ДО ПОДРАЗДЕЛЕНИЯ',9.7,True);p.text(M+14,489,money(d['amount']),23,True)
    p.text(M+14,520,f"доля {fmt(d['fact'],'percent')} / план {fmt(d['plan'],'percent')}",10)
    p.text(M+14,538,'прошлый месяц '+money(d['amount_previous']),10)
    p.chart(M+335,452,CW-335,112,m['food_history'],'Фудкост НИМБа / доступная история / %',percent=True)
    p.paragraph(M,574,CW,'Комплименты и питание показаны здесь отдельно; в косвенных расходах не повторяются. Фудкост сохранен по классификации НИМБа.',9.4,maxh=17,color=GRAY);p.end()
    # 4 — подтвержденная команда и ФОТ без управляющего.
    p.start(4,'ФОТ и команда');staff=answers['staff'];roles=list(staff['roles'].items())
    if len(roles)>10:raise LayoutError(4,'Сгруппируйте прочие должности в составе команды, сохранив общую численность.')
    p.text(M,130,f"КОМАНДА: {sum(staff['roles'].values())} ЧЕЛ. / ТРУДОУСТРОЕНО: {staff['official']}",14,True)
    rw=(CW-(len(roles)-1)*8)/len(roles)
    for i,(label,count) in enumerate(roles):
        x=M+i*(rw+8);p.box(x,154,rw,58,LIME if label.lower()=='управляющий' else LIGHT)
        p.text(x+10,162,count,22,True);p.paragraph(x+10,190,rw-20,label,9,maxh=21)
    left=620;rx=M+left+26;rightw=CW-left-26
    p.text(M,230,'НАЧИСЛЕНИЯ И НАЛОГИ',11,True)
    rows=[]
    for r in m['payroll_rows']:
        rows.append([r['label'],money(r['amount']),fmt(r['share'],'percent')])
    if m.get('payroll_difference') not in (None,0):rows.append(['Разница источников',money(m['payroll_difference']),('сверить',GRAY)])
    p.table(M,254,left,['Статья','Факт','Доля ФОТ'],rows,weights=[2.5,1.4,1.1],maxh=268,size=10.3)
    p.card(rx,230,rightw,'ФОТ С НАЛОГАМИ',money(m['fot']),[
        (f"доля {fmt(m['fot_share']['fact'],'percent')} / план {fmt(m['fot_share']['plan'],'percent')}",change(m['fot_share']['fact'],m['fot_share']['plan'],'down')[1]),
        'динамичный бюджет '+money(m['fot_budget']),change(m['fot'],m['fot_budget'],'down')],LIME,h=126)
    p.table(rx,375,rightw,['Показатель','Факт'],[['Часы команды',fmt(m['hours'])],['Средняя ставка, руб./ч',fmt(m['hour_rate']['fact'])],['SPMH, руб./чел.-ч',fmt(m['spmh']['fact'])]],weights=[2,1],maxh=123,size=11)
    if m['spmh']['previous'] is not None:
        label,col=change(m['spmh']['fact'],m['spmh']['previous'],'up')
        p.text(rx+8,518,'SPMH к прошлому месяцу: '+label,10,True,col)
    note=answers.get('payroll_note') or 'Планы часов, средней ставки и SPMH пока не ведутся. Расшифровка начислений — факт и доля ФОТ.'
    p.paragraph(M,545,CW,note,10,maxh=28,color=GRAY)
    p.paragraph(M,575,CW,'Управляющий входит в численность. Его оплата, НДФЛ и часы исключены. Отдельно страховые взносы управляющего не вычитались: их нет.',9.4,maxh=17,color=GRAY);p.end()
    # 5 — все статьи или явно подтвержденные группы, полное покрытие.
    p.start(5,'Остальные косвенные расходы')
    expense_rows=m['indirect'];groups=answers.get('expense_groups')
    if groups:
        mapped={i+1:r for i,r in enumerate(expense_rows)};expense_rows=[]
        for group in groups:
            rs=[mapped[i] for i in group['ids']]
            def summed(key):return sum(r[key] for r in rs) if all(r[key] is not None for r in rs) else None
            expense_rows.append(dict(label=group['title'],fact=summed('fact'),plan=summed('plan'),share=summed('share'),share_plan=summed('share_plan')))
    fact=sum(r['fact'] for r in expense_rows) if all(r['fact'] is not None for r in expense_rows) else None
    plan=sum(r['plan'] for r in expense_rows) if all(r['plan'] is not None for r in expense_rows) else None
    p.card(M,130,ww,'ФАКТИЧЕСКИЕ РАСХОДЫ',money(fact),[],LIME,h=95)
    p.card(M+ww+12,130,ww,'ПЛАН',money(plan),[],h=95)
    p.card(M+2*(ww+12),130,ww,'ОТКЛОНЕНИЕ',money(fact-plan if fact is not None and plan is not None else None),[change(fact,plan,'down')],h=95)
    p.table(M,247,CW,['Статья / раздел','План','Факт','Доля план','Доля факт','Δ к плану'],[
        [r['label'],money(r['plan']),money(r['fact']),fmt(r['share_plan'],'percent'),fmt(r['share'],'percent'),change(r['fact'],r['plan'],'down')] for r in expense_rows],
        weights=[3,1.25,1.25,1,1,1],maxh=323,size=11,minsize=9.3)
    p.text(M,578,'Без ФОТ, комплиментов и питания, уже представленных отдельно. Группировка, если есть, подтверждена управляющим.',9.4,color=GRAY);p.end()
    # 6 — только факт начислений и сальдо PDR, будущий график отдельно.
    p.start(6,'Обязательства перед финпартнером');dw=(CW-12)/2
    p.card(M,130,dw,'НАКОПЛЕННОЕ САЛЬДО ПЕРЕД ФП',money(m['debt']),['Нераспределенная прибыль; знак сохранен по НИМБу'],LIME,h=100)
    p.card(M+dw+12,130,dw,'НАЧИСЛЕННЫЕ ДИВИДЕНДЫ МЕСЯЦА',money(m['dividends']['fact']),['Источник: Распределение прибыли НИМБа'],h=100)
    p.box(M,250,580,325,INK);p.chart(M+20,270,540,280,m['dividends_history'],'Начисленные дивиденды / млн руб.',True,money_scale=True)
    rx=M+606;rw=CW-606;p.text(rx,253,'ПЛАН ВЫПЛАТ',18,True)
    payouts=answers['payouts']
    if payouts['items']:
        p.table(rx,292,rw,['Дата','Сумма','Комментарий'],[[date_label(r['date']),money(r['amount']),r['comment']] for r in payouts['items']],weights=[1.2,1.3,1.6],maxh=210,size=10.5)
    else:p.paragraph(rx,300,rw,'Выплаты не запланированы. '+payouts['note'],13,maxh=180)
    p.box(rx,522,rw,53,LIME);p.text(rx+12,531,'ПО ГРАФИКУ: '+money(sum(r['amount'] for r in payouts['items'])),18,True);p.end()
    # 7 — каждое событие с индивидуальным комментарием и дневными показателями.
    p.start(7,'Маркетинг и мероприятия');avg=m['daily_averages']
    p.box(M,130,CW,55,LIME)
    p.text(M+16,148,money(avg['revenue'])+' / день',20,True);p.text(M+385,148,fmt(avg['count'])+' чека / день',20,True)
    p.text(M+760,148,money(avg['average'])+' / чек',20,True)
    rows=[]
    for event in answers['events']:
        d=m['days'][event['date']]
        vals=[]
        for key in ('revenue','count','average'):
            val=d[key];unit='count' if key=='count' else 'money';label,col=change(val,avg[key],'up')
            vals.append((fmt(val,unit)+' / '+label,col))
        rows.append([date_label(event['date'])+' · '+event['title'],event['participants'],*vals,event['comment']])
    if rows:
        p.table(M,210,CW,['Дата / активность','Участ.','Выручка / Δ','Чеки / Δ','Ср. чек / Δ','Влияние на спот / вывод'],rows,
                weights=[2,0.6,1.35,1.0,1.35,3.2],maxh=345,size=10.7,minsize=9.3)
    else:p.text(M,250,'Управляющий подтвердил: мероприятий не было.',20)
    p.paragraph(M,566,CW,'Финансовые показатели относятся ко всему дню, включая дни с несколькими активностями. Сравнение со средним днем месяца не доказывает дополнительную выручку именно от события.',9.6,maxh=24,color=GRAY);p.end()
    # 8 — компактно, без потери обязательных объяснений.
    p.start(8,'Отклонения и план действий')
    p.text(M,133,'Отклонения от плана строго больше ±5%, включая благоприятные. План 0 и ненулевой факт — тоже с объяснением.',11,True)
    issues={r['id']:r for r in deviations(m)};rows=[]
    covered=set()
    for group in answers.get('deviations',[]):
        labels=[];colors=[]
        for idx in group['ids']:
            r=issues[idx];d,col=change(r['fact'],r['plan'],r['direction']);labels.append(r['label']+' / '+d);colors.append(col);covered.add(idx)
        color=colors[0] if len(set(colors))==1 else GRAY
        rows.append([('\n'.join(labels),color),group['cause'],group['action'],group['expected'],date_label(group['deadline'])])
    if covered!=set(issues):raise ValueError('Не все отклонения имеют объяснения после редактирования данных.')
    if rows:p.table(M,174,CW,['Показатель / Δ','Причина','Действие','Ожидаемый результат','Срок'],rows,weights=[2,2,2.5,2.5,1.15],maxh=405,size=10.2,minsize=9.3)
    else:p.text(M,245,'Отклонений от плана больше ±5% нет.',21)
    p.end()
    # 9 — общий комментарий и десять задач с отдельными полями сроков.
    p.start(9,'Фокус управляющего');p.paragraph(M,132,CW,answers['summary'],12,maxh=65)
    col=(CW-24)/2;rx=M+col+24
    p.text(M,214,'5 ЗАДАЧ ПРОШЛОГО МЕСЯЦА',13,True);p.text(rx,214,'5 ЗАДАЧ СЛЕДУЮЩЕГО МЕСЯЦА',13,True)
    for i,(past,nxt) in enumerate(zip(answers['past_tasks'],answers['next_tasks'])):
        y=239+i*69
        for x,task,body,fill in [(M,past,past['result'],LIGHT),(rx,nxt,nxt['comment']+'\nРезультат: '+nxt['expected'],LIME if i==0 else LIGHT)]:
            p.box(x,y,col,65,fill,r=7)
            p.paragraph(x+10,y+6,col-150,f"{i+1:02d}  {task['title']}",10.3,maxh=25,bold=True)
            p.box(x+col-130,y+4,120,20,WHITE,r=5);p.text(x+col-124,y+10,'Срок: '+date_label(task['deadline']),8.6,True)
            p.paragraph(x+10,y+32,col-20,body,9.5,maxh=30)
    p.end()
    p.start(10,'',dark=True);p.c.setFillColor(HexColor(LIME));p.c.setFont('MR',134)
    baseline=H/2-(pdfmetrics.getAscent('MR')+pdfmetrics.getDescent('MR'))/1000*134/2
    p.c.drawCentredString(W/2,baseline,'Thanks');p.end();p.c.save()
    return path
