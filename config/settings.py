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

GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json")
# Альтернатива GOOGLE_SERVICE_ACCOUNT_FILE для хостов без локальной файловой
# системы (Railway) — содержимое JSON-ключа целиком, одной строкой
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

ROMAN_TELEGRAM_ID = os.getenv("ROMAN_TELEGRAM_ID")
# Имя, которым Романа называют в рабочих чатах — отображается в буфере сообщений
# и используется для сопоставления задач, назначенных на него самого.
# По умолчанию "Роман"; если команда обращается иначе (например "Рома") — задать здесь.
ROMAN_CHAT_NAME = os.getenv("ROMAN_CHAT_NAME", "Роман")

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
