import os
from zoneinfo import ZoneInfo

# Часовой пояс, в котором бот считает "сегодня", расписание отчётов и
# таймстемпы в таблицах — не зависит от часового пояса хоста (важно для
# деплоя на Railway, где система обычно в UTC, а Роман и сотрудники — в MSK).
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
TZ = ZoneInfo(TIMEZONE)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

ROMAN_TELEGRAM_ID = os.getenv("ROMAN_TELEGRAM_ID")
# Имя, которым Романа называют в рабочих чатах — отображается в буфере сообщений
# и используется для сопоставления задач, назначенных на него самого.
# По умолчанию "Роман"; если команда обращается иначе (например "Рома") — задать здесь.
ROMAN_CHAT_NAME = os.getenv("ROMAN_CHAT_NAME", "Роман")

# Telegram ID владельцев модуля мониторинга конкурентов (через запятую) —
# видят все рынки, добавляют новые проекты/рынки. Роман входит в список по
# умолчанию, если явно не переопределён через OWNER_TELEGRAM_IDS.
OWNER_TELEGRAM_IDS = {
    uid.strip()
    for uid in os.getenv("OWNER_TELEGRAM_IDS", ROMAN_TELEGRAM_ID or "").split(",")
    if uid.strip()
}

# Путь к SQLite-базе модуля мониторинга конкурентов (рынки, конкуренты, снятия)
MONITORING_DB_PATH = os.getenv("MONITORING_DB_PATH", "data/monitoring.db")
# Путь к SQLite-базе трекера задач — отдельная база от мониторинга, общие
# только пользователи (monitoring.manager, по telegram_user_id)
TASKS_DB_PATH = os.getenv("TASKS_DB_PATH", "data/tasks.db")
# Путь к базе персональных данных стажёров (согласие, паспорт/СНИЛС/реквизиты) —
# физически отдельный файл/путь от остальных баз, чтобы можно было позже
# перенести именно его на сервер в РФ (152-ФЗ, ст.18 ч.5) без переписывания
# остального кода — достаточно поменять этот путь на РФ-хостинг (Postgres DSN и т.п.)
PERSONAL_DATA_DB_PATH = os.getenv("PERSONAL_DATA_DB_PATH", "data/personal_data.db")
# Раз в неделю бот шлёт напоминание-задание по рынкам, у которых сегодня день мониторинга
MONITORING_REMINDER_TIME = os.getenv("MONITORING_REMINDER_TIME", "09:30")

DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "20:00")
WEEKLY_REPORT_DAY = os.getenv("WEEKLY_REPORT_DAY", "fri")
WEEKLY_REPORT_TIME = os.getenv("WEEKLY_REPORT_TIME", "22:00")
# Сколько дней без обновления статуса считается "застрявшей" задачей (раздел 8.3)
STALE_DAYS = int(os.getenv("STALE_DAYS", "5"))

# Когда раз в день проверять задачи с подошедшим/просроченным deadline_current (раздел 4)
STATUS_CHECK_TIME = os.getenv("STATUS_CHECK_TIME", "10:00")
# После скольких неясных ответов сотрудника подряд эскалировать Роману, а не уточнять дальше
MAX_CLARIFYING_ROUNDS = int(os.getenv("MAX_CLARIFYING_ROUNDS", "2"))

# Сколько часов сообщений хранить в скользящем буфере группового чата
MESSAGE_BUFFER_HOURS = int(os.getenv("MESSAGE_BUFFER_HOURS", "3"))
# Сколько часов контекста брать для экстракции задач при срабатывании триггера
EXTRACTION_CONTEXT_HOURS = int(os.getenv("EXTRACTION_CONTEXT_HOURS", "2"))

# Ежедневный отчёт по смене (раздел "shift report"): когда просить у
# управляющего график смен (14-е и последний день месяца), когда начинать
# собирать отчёт у ответственного менеджера, когда эскалировать управляющему,
# если отчёт не собран, и когда рассылать готовый отчёт дальше.
SHIFT_SCHEDULE_REMINDER_TIME = os.getenv("SHIFT_SCHEDULE_REMINDER_TIME", "10:00")
# Точки закрываются в разное время — управляющий задаёт своё время сбора
# отчёта для точки через /set_evening_report (см. monitoring.markets.
# get_effective_shift_report_time); это значение — только дефолт для точек,
# которые ещё не настроили своё.
SHIFT_REPORT_START_TIME = os.getenv("SHIFT_REPORT_START_TIME", "22:00")
# Эскалация управляющему — не абсолютное время, а сколько минут ждать после
# СВОЕГО времени сбора отчёта точки, прежде чем подключать управляющего.
SHIFT_REPORT_ESCALATE_OFFSET_MINUTES = int(os.getenv("SHIFT_REPORT_ESCALATE_OFFSET_MINUTES", "90"))
# Если к этому времени вчерашний отчёт так и не согласован Романом — бот
# сообщает ему напрямую, независимо от того, сработали ли предыдущие
# эскалации управляющему.
SHIFT_REPORT_OWNER_ESCALATE_TIME = os.getenv("SHIFT_REPORT_OWNER_ESCALATE_TIME", "09:00")
SHIFT_REPORT_DISPATCH_TIME = os.getenv("SHIFT_REPORT_DISPATCH_TIME", "10:00")

# Когда просить у управляющего план по выручке/чекам на следующий месяц (25-е
# число) — только для рынков БЕЗ привязки к Surf Coffee (см. /add_project);
# для подключённых план бот забирает сам. Ручной ввод через /set_monthly_plan
# доступен в любой день.
MONTHLY_PLAN_REMINDER_TIME = os.getenv("MONTHLY_PLAN_REMINDER_TIME", "10:00")
# Утреннее напоминание команде точки — план на день + старт/стоп-листы со вчера
TEAM_MORNING_REPORT_TIME = os.getenv("TEAM_MORNING_REPORT_TIME", "07:00")

# Собрания: во сколько ежедневно проверять, нет ли завтра собрания по
# настроенному Управляющим ритму, и просить подтверждения
MEETING_CONFIRM_TIME = os.getenv("MEETING_CONFIRM_TIME", "10:00")

# Модуль "Выручка" (revenue/) — перенесён из отдельного бота "Аналитик Иван".
# Логин в закрытое API Surf Coffee (surfis.surfcoffee.ru) для ежедневного/
# недельного/месячного отчёта и план/факт/P&L. Только для владельца.
SURF_EMAIL = os.getenv("SURF_EMAIL")
SURF_PASSWORD = os.getenv("SURF_PASSWORD")
# JSON сервисного аккаунта Google (как строка) — для экспорта P&L в Google Sheets.
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
# Ежедневный/недельный/месячный отчёт по выручке — все три в одно и то же
# время, как было у "Аналитика Ивана".
REVENUE_REPORT_TIME = os.getenv("REVENUE_REPORT_TIME", "10:00")
