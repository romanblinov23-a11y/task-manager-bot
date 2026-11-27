"""Справочник меню Романа: пути, команды и назначение из единого каталога."""
from telegram import InlineKeyboardButton,InlineKeyboardMarkup
from bot.menu import CATALOG,CATEGORIES,HOME,is_roman

HELP='❓ Справка'
DESCRIPTIONS={
 'portfolio':'Получить личную ссылку на живые финансы проектов с выбором месяца и дивидендным потоком УК. Доли настраиваются в параметрах каждого проекта.',
 'manager_report':'Открыть или продолжить отчет управляющего за предыдущий месяц. Для другого периода: /manager_report ГГГГ-ММ. Сначала проверяется закрытие месяца в НИМБе.',
 'view_reports':'Найти отчет по смене за выбранную дату и открыть доступные действия с ним.',
 'shift_dashboard':'Посмотреть показатели смен, динамику и сравнение проектов на дашборде.',
 'dashboard_market':'Получить дашборд рынка или сводный дашборд по всем рынкам.',
 'founders_report':'Собрать отдельный отчет для учредителей по шаблону. Это другой сценарий, не PDF управляющего.',
 'revenue':'Посмотреть выручку за день, неделю или месяц, план/факт и выгрузку P&L.',
 'manager_kpi_plan':'Задать планы гостевых KPI конкретного спота. /manager_kpi_plan ГГГГ-ММ задает месяц начала действия; планы сохраняются до изменения.',
 'set_monthly_plan':'Загрузить план выручки и чеков для проекта без подключения к НИМБу. Для подключенных проектов планы берутся из НИМБа.',
 'set_writeoff_plan':'Установить план списаний для проекта.',
 'accounting':'Внести план и факт задач по работе с системой учета.',
 'employees':'Посмотреть сотрудников по проектам, подтвердить доступ, изменить роль, проект и блоки или отозвать доступ.',
 'spot_events':'Загрузить план мероприятий на неделю или добавить новые события; они появятся в утренних сообщениях команды.',
 'set_meeting_schedule':'Настроить периодичность и время собраний проекта.',
 'announce_meeting':'Запустить сбор повестки для ближайшего собрания.',
 'regulations':'Открыть регламенты работы с ботом по доступным направлениям.',
 'chats':'Посмотреть и настроить чаты проекта: рабочий чат и рассылки отчетов.',
 'monitor_chats':'Выбрать чаты для отслеживания триггерных слов и настроить триггеры.',
 'add_project':'Добавить проект и настроить его основные данные.',
 'market_settings':'Настроить проект: оператора персональных данных, согласия, связь с НИМБом, финпартнеров, долю УК Havana Standart, нормы списаний и другие параметры.',
 'report_chats':'Посмотреть зарегистрированные чаты для отправки отчетов.',
 'message':'Подготовить личное сообщение выбранному сотруднику.',
 'message_chat':'Подготовить сообщение в выбранный чат проекта.',
 'broadcast':'Подготовить рассылку выбранной группе сотрудников: текст и/или PDF. Бот покажет предпросмотр; отправка только после подтверждения.',
 'messaging':'Открыть общий выбор: сотруднику, в чат или группе.',
 'shift_report':'Вручную начать заполнение отчета по смене.',
 'set_shift_schedule':'Загрузить график смен на две недели.',
 'set_evening_report':'Настроить время запроса вечернего отчета и чек-лист.',
 'handover_report':'Вручную начать заполнение пересменки.',
 'set_handover_schedule':'Загрузить график пересменок на две недели.',
 'set_handover_report':'Настроить время запроса пересменки и ее чек-лист.',
 'reset_shift_report':'Начать сценарий сброса сегодняшнего отчета по смене. Удаление потребует подтверждения.',
 'reset_handover_report':'Начать сценарий сброса сегодняшней пересменки. Удаление потребует подтверждения.',
 'monitoring':'Начать сбор данных по конкурентам на рынке.',
 'add_competitor':'Добавить конкурента на выбранный рынок.',
 'close_competitor':'Отметить закрытие или возобновление работы конкурента.',
 'schedule':'Выбрать дни мониторинга для рынка.',
 'fix_reading':'Исправить дату снятия показателей у точки.',
 'set_own_point':'Выбрать свою точку на рынке для сравнений.',
 'reset_monitoring':'Начать сброс данных конкурентов выбранного рынка. Необратимое удаление потребует отдельного подтверждения.',
}


def path(category):
    if category in ('shifts','monitoring'):return CATEGORIES['settings']+' → '+CATEGORIES[category]
    return CATEGORIES[category]


def sections(category):
    if category=='settings':
        return 'Настройки открывают три группы:\n'+ '\n'.join(path(c) for c in ('shifts','monitoring','projects'))
    return path(category)+'\n\n'+'\n\n'.join(f'{label}\n/{command} — {DESCRIPTIONS[command]}' for command,(label,group) in CATALOG.items() if group==category)


def help_keyboard():
    keys=('reports','finance','team','projects','messages','settings','shifts','monitoring')
    return InlineKeyboardMarkup([[InlineKeyboardButton(CATEGORIES[key],callback_data='nav:help:'+key)] for key in keys]+[[InlineKeyboardButton('📖 Вся справка одним списком',callback_data='nav:help:all')],[InlineKeyboardButton('⬅️ Главное меню',callback_data='nav:home')]])


async def show_help(message,uid,category=None):
    if not is_roman(uid):return
    if category=='all':
        text='СПРАВКА ПО МЕНЮ\n/menu — главное меню с кнопками.\n/help или /spravka — эта справка.\n'+HOME+' — вернуться к разделам.\n\n'+'\n\n'.join(sections(key) for key in CATEGORIES)
        parts=[];current=''
        for paragraph in text.split('\n\n'):
            if len(current)+len(paragraph)>3400:parts.append(current);current=''
            current+=('\n\n' if current else '')+paragraph
        if current:parts.append(current)
        for i,part in enumerate(parts):await message.reply_text(part,reply_markup=help_keyboard() if i==len(parts)-1 else None)
    elif category in CATEGORIES:
        await message.reply_text(sections(category),reply_markup=help_keyboard())
    else:
        await message.reply_text('❓ Справка по меню\n\n/menu — открыть главное меню.\n/help или /spravka — вызвать справку. Можно также написать «справка».\n\nВыбери раздел: покажу путь к кнопке, команду и ее назначение. «Вся справка» выведет полный список.\n\nПосле выбора команды бот сам предложит следующие шаги. В активной анкете пиши обычные ответы; кнопка «Главное меню» открывает навигацию.',reply_markup=help_keyboard())
