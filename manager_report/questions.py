"""Структурированные ответы управляющего, без догадок о фактах и сроках."""
import re
from datetime import date, datetime

from manager_report.model import number, deviations, with_answers


def full_date(value):
    try:
        if not re.fullmatch(r'\d{2}\.\d{2}\.\d{4}', value.strip()):
            raise ValueError
        return datetime.strptime(value.strip(), '%d.%m.%Y').date().isoformat()
    except ValueError:
        raise ValueError('Дата нужна полностью: ДД.ММ.ГГГГ.') from None


def positive_int(text):
    cleaned=re.sub(r'\s','',text)
    if not re.fullmatch(r'\d+',cleaned):
        raise ValueError('Нужно целое неотрицательное число.')
    value=int(cleaned)
    if value>1_000_000_000:
        raise ValueError('Проверьте число: оно слишком велико.')
    return value


def bounded(text, maximum, label):
    value=text.strip()
    if not value or len(value)>maximum:
        raise ValueError(f'{label}: от 1 до {maximum} символов.')
    return value


def lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_plan_value(kind,text):
    if kind=='count':return positive_int(text)
    value=number(text)
    if value is None or value<0 or (kind=='percent' and value>100):
        raise ValueError('Введите число от 0 до 100.' if kind=='percent' else 'Введите неотрицательное конечное число.')
    return value


def question_list(report, previous=None, planned_events=None):
    model=with_answers(report['model'],report['answers'])
    staff=model['staff']
    proposed='\n'.join(f'{name} | {count}' for name,count in staff['roles'].items())
    proposed+=f"\nТрудоустроено | {staff['official']}"
    events_note=''
    if planned_events:
        events_note='\nПлан маркетолога (проведение и участников нужно подтвердить):\n'+'\n'.join(
            f"{e['event_date']} — {e['title']}" for e in planned_events)
    questions=[
        dict(key='manager_name',kind='manager_name',label='ФИО управляющего',prompt='Пришли полное ФИО управляющего для титульной страницы отчета: фамилия, имя и отчество (если есть).'),
        dict(key='staff',kind='staff',label='Команда',prompt=(
            'Подтверди состав команды для отчетного месяца. НИМБ сейчас показывает:\n'+proposed+
            '\n\nЕсли все верно — «подтверждаю». Иначе пришли исправленный состав: должность | количество, '
            'по строке на должность. Последняя строка: Трудоустроено | число. '
            'Учитывай управляющего в численности. Бариста раздели по грейдам; стажеров проверь отдельно.')),
        dict(key='dishes',kind='count',label='Проданные блюда',prompt=(
            'Сколько всего блюд продано за отчетный месяц? Пришли общее количество проданных единиц. '
            'Это не число уникальных позиций меню. Чеки возьму из НИМБа, наполненность посчитаю сам.')),
        dict(key='events',kind='events',label='Мероприятия',prompt=(
            'Какие мероприятия, коллаборации и маркетинговые активности фактически проведены?\n'
            'По событию на строку:\nДД.ММ.ГГГГ | название | фактические участники | комментарий\n'
            'В комментарии к каждому: влияние на развитие спота, что сработало, что улучшить. '
            'До 220 символов на комментарий. Если событий не было — «нет мероприятий». Планы не считаются фактом.'+events_note)),
        dict(key='payouts',kind='payouts',label='График выплат',prompt=(
            'Планируемый график выплат начисленных дивидендов, по платежу на строку:\n'
            'ДД.ММ.ГГГГ | сумма | комментарий\n'
            'Если выплат пока не планируете: нет выплат | причина. Фактические прошлые платежи не заменяют план.')),
    ]
    if abs(model.get('payroll_difference',0))>1:
        questions.append(dict(key='payroll_note',kind='payroll_note',label='Сверка ФОТ',prompt=(
            f"Детализация начислений отличается от ФОТ P&L на {model['payroll_difference']:,.2f} руб. "
            'Сумму P&L не меняем. Поясни различие источников и что сверено (до 180 символов).')))
    if model['indirect']:
        groups={}
        for i,row in enumerate(model['indirect'],1):groups.setdefault(row['group'],[]).append(i)
        listing='\n'.join(f"{i+1}. {row['label']}" for i,row in enumerate(model['indirect']))
        grouping='\n'.join(f"{title} | {','.join(map(str,ids))}" for title,ids in groups.items())
        questions.append(dict(key='expense_groups',kind='expense_groups',label='Группировка косвенных расходов',
                              defaults=[{'title':title,'ids':ids} for title,ids in groups.items()],prompt=(
            'Косвенные расходы должны поместиться на одном листе. Статьи:\n'+listing+
            '\n\nПредлагаемые разделы НИМБа:\n'+grouping+
            '\n\nДля этих групп — «подтверждаю». Для собственной группировки: название | номера через запятую, '
            'по группе на строку. Каждый номер должен быть включен ровно один раз; суммы не теряются. '
            'Для показа отдельных статей — «по статьям».')))
    issues=deviations(model)
    if issues:
        listing=[]
        for row in issues:
            change='план 0, факт ненулевой' if row['delta'] is None else f"{row['delta']:+.2f}%"
            listing.append(f"{row['id']}. {row['label']}: {change}")
        questions.append(dict(key='deviations',kind='deviations',label='Объяснения отклонений',issues=issues,prompt=(
            'Объясни все отклонения от плана больше ±5%, включая благоприятные:\n'+'\n'.join(listing)+
            '\n\nФормат строки:\nномер | причина | действие | ожидаемый результат | ДД.ММ.ГГГГ\n'
            'При общей причине можно объединить несколько номеров: 1,2 | ... . '
            'Каждый номер должен встретиться ровно один раз. До 120 символов в каждом текстовом поле. '
            'Группируй только показатели с общей причиной, действием, результатом и сроком — без потери объяснений.')))
    questions.append(dict(key='summary',kind='summary',label='Общий комментарий',prompt=(
        'Общий комментарий управляющего (до 650 символов): вызовы месяца, как себя чувствуешь, '
        'что получилось, что доработать, в чем нужна помощь Романа.')))
    prior_tasks=(previous or {}).get('answers',{}).get('next_tasks',[]) if (previous or {}).get('status')=='completed' else []
    if len(prior_tasks)==5:
        listing='\n'.join(f"{i+1}. {t['title']} — срок {t['deadline']}" for i,t in enumerate(prior_tasks))
        questions.append(dict(key='past_tasks',kind='past_results',label='Результаты прошлых задач',tasks=prior_tasks,prompt=(
            'Пять задач из прошлого отчета, принятого Романом:\n'+listing+
            '\nПришли пять строк: номер | результат/статус. При выполнении укажи фактическую дату. '
            'До 160 символов на результат. Названия и согласованные сроки сохраняются.')))
    else:
        questions.append(dict(key='past_tasks',kind='past_tasks',label='Задачи прошлого месяца',prompt=(
            'В истории нет пяти задач прошлого месяца. Введи ровно пять строк:\n'
            'задача | закрепленный срок ДД.ММ.ГГГГ | результат/статус\n'
            'Название до 80, результат до 160 символов. При выполнении укажи фактическую дату в результате.')))
    questions.append(dict(key='next_tasks',kind='next_tasks',label='Задачи следующего месяца',prompt=(
        'Пять фокусных задач следующего месяца, ровно пять строк:\n'
        'задача | срок ДД.ММ.ГГГГ | комментарий | ожидаемый результат\n'
        'Название до 80, комментарий и ожидаемый результат до 120 символов. Срок обязателен для каждой задачи.')))
    return questions


def parse_answer(question,text,report):
    kind=question['kind']
    text=text.strip()
    if kind=='manager_name':
        value=bounded(text,120,'ФИО управляющего')
        if len(value.split())<2:raise ValueError('Укажи как минимум фамилию и имя управляющего.')
        return value
    if kind=='count':return positive_int(text)
    if kind=='summary':return bounded(text,650,'Комментарий')
    if kind=='payroll_note':return bounded(text,180,'Пояснение сверки ФОТ')
    if kind=='expense_groups':
        if text.lower()=='подтверждаю':return question['defaults']
        if text.lower()=='по статьям':return []
        wanted=set(range(1,len(report['model']['indirect'])+1));seen=set();result=[]
        for line in lines(text):
            parts=[v.strip() for v in line.split('|')]
            if len(parts)!=2 or not re.fullmatch(r'\d+(?:\s*,\s*\d+)*',parts[1]):
                raise ValueError('Группа: название | номера через запятую.')
            ids=[int(i.strip()) for i in parts[1].split(',')]
            if len(set(ids))!=len(ids) or any(i not in wanted or i in seen for i in ids):
                raise ValueError('Статья должна входить ровно в одну группу, без неизвестных номеров.')
            seen.update(ids);result.append(dict(title=bounded(parts[0],70,'Название группы'),ids=ids))
        if seen!=wanted:raise ValueError('Включите все статьи, пропущены: '+','.join(map(str,sorted(wanted-seen))))
        return result
    if kind=='staff':
        if text.lower() in ('подтверждаю','верно','все верно','всё верно'):
            return report['model']['staff']
        roles={};official=None
        for line in lines(text):
            parts=[p.strip() for p in line.split('|')]
            if len(parts)!=2:raise ValueError('Состав: должность | количество. Отдельно: Трудоустроено | количество.')
            role,count=bounded(parts[0],50,'Должность'),positive_int(parts[1])
            if role.lower()=='трудоустроено':
                if official is not None:raise ValueError('Трудоустроенных укажите один раз.')
                official=count
            else:
                if role.casefold() in {r.casefold() for r in roles}:raise ValueError('Должность повторяется.')
                roles[role]=count
        if not roles or sum(roles.values())==0 or official is None or official>sum(roles.values()):
            raise ValueError('Укажите ненулевой состав и трудоустроенных не больше общей численности.')
        return {'roles':roles,'official':official}
    if kind=='events':
        if text.lower()=='нет мероприятий':return []
        events=[];seen=set()
        for line in lines(text):
            parts=[p.strip() for p in line.split('|')]
            if len(parts)!=4:raise ValueError('Мероприятие: дата | название | участники | индивидуальный комментарий.')
            day=full_date(parts[0])
            if day[:7]!=report['month']:raise ValueError('Мероприятия должны относиться к отчетному месяцу.')
            if day not in report['model']['days']:raise ValueError('В НИМБе нет дневных данных на эту дату.')
            title=bounded(parts[1],80,'Название');identity=(day,title.casefold())
            if identity in seen:raise ValueError('Мероприятие повторяется.')
            seen.add(identity)
            events.append(dict(date=day,title=title,participants=positive_int(parts[2]),comment=bounded(parts[3],220,'Комментарий события')))
        if not events:raise ValueError('Пришлите список или «нет мероприятий».')
        return sorted(events,key=lambda e:(e['date'],e['title']))
    if kind=='payouts':
        if text.lower().startswith('нет выплат |'):
            return {'items':[],'note':bounded(text.split('|',1)[1],180,'Причина отсутствия выплат')}
        result=[]
        for line in lines(text):
            parts=[p.strip() for p in line.split('|')]
            if len(parts)!=3:raise ValueError('Платеж: дата | сумма | комментарий.')
            amount=number(parts[1])
            if amount is None or amount<=0:raise ValueError('Сумма платежа должна быть положительным конечным числом.')
            result.append(dict(date=full_date(parts[0]),amount=amount,comment=bounded(parts[2],100,'Комментарий платежа')))
        if not result:raise ValueError('Укажите выплаты или «нет выплат | причина».')
        return {'items':sorted(result,key=lambda x:x['date']),'note':''}
    if kind=='deviations':
        wanted={r['id'] for r in question['issues']};covered=set();result=[]
        for line in lines(text):
            parts=[p.strip() for p in line.split('|')]
            if len(parts)!=5:raise ValueError('Отклонение: номера | причина | действие | результат | срок ДД.ММ.ГГГГ.')
            if not re.fullmatch(r'\d+(?:\s*,\s*\d+)*',parts[0]):raise ValueError('Номера через запятую, например 1,2.')
            ids=[int(x.strip()) for x in parts[0].split(',')]
            if len(set(ids))!=len(ids) or any(i not in wanted or i in covered for i in ids):
                raise ValueError('Каждый номер отклонения должен встретиться ровно один раз, без неизвестных номеров.')
            covered.update(ids)
            result.append(dict(ids=ids,cause=bounded(parts[1],120,'Причина'),action=bounded(parts[2],120,'Действие'),
                               expected=bounded(parts[3],120,'Ожидаемый результат'),deadline=full_date(parts[4])))
        if covered!=wanted:raise ValueError('Не объяснены отклонения: '+', '.join(map(str,sorted(wanted-covered))))
        return result
    if kind in ('past_tasks','next_tasks','past_results'):
        entries=lines(text)
        if len(entries)!=5:raise ValueError('Нужно ровно пять строк — по одной на задачу.')
        result=[];seen=set()
        for line in entries:
            parts=[p.strip() for p in line.split('|')]
            if kind=='past_results':
                if len(parts)!=2 or parts[0] not in ('1','2','3','4','5'):raise ValueError('Результат: номер 1–5 | результат/статус.')
                index=int(parts[0])-1
                if index in seen:raise ValueError('Номер задачи повторяется.')
                seen.add(index)
                result.append({**question['tasks'][index],'result':bounded(parts[1],160,'Результат'),'index':index})
            elif kind=='past_tasks':
                if len(parts)!=3:raise ValueError('Прошлая задача: название | срок | результат.')
                result.append(dict(title=bounded(parts[0],80,'Задача'),deadline=full_date(parts[1]),result=bounded(parts[2],160,'Результат')))
            else:
                if len(parts)!=4:raise ValueError('Следующая задача: название | срок | комментарий | результат.')
                result.append(dict(title=bounded(parts[0],80,'Задача'),deadline=full_date(parts[1]),
                                   comment=bounded(parts[2],120,'Комментарий'),expected=bounded(parts[3],120,'Ожидаемый результат')))
        return sorted(result,key=lambda r:r.get('index',0))
    raise ValueError('Неизвестный вопрос')
