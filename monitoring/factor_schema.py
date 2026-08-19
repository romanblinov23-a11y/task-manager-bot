import json

# Каждое поле: (key, label, kind, options, note)
# kind: "buttons" (options — варианты) | "number" (свободный текст-число) | "text" (свободный текст)

PRODUCT_FIELDS = [
    ("grain", "Зерно", "buttons", ["смесь", "моносорт", "эксклюзив", "есть выбор"], None),
    ("milk", "Молоко", "buttons", ["эконом", "мидл", "премиум"], None),
    ("decaf", "Декаф", "buttons", ["есть", "нет"], None),
    ("stm", "СТМ", "buttons", ["есть", "нет"], None),
    ("signature_drinks", "Авторские напитки", "buttons", ["нет", "простые", "сложные"], None),
    ("food", "Еда", "buttons", ["нет", "эконом", "мидл", "премиум"], None),
    ("avg_check", "Средний чек", "number", None, "укажите сумму числом, например 350"),
]

ATMOSPHERE_FIELDS = [
    ("seating", "Посадочные места", "buttons", ["нет", "стандартная мебель", "авторская мебель"], None),
    ("interior", "Интерьер", "buttons", ["минимальный", "стандартный", "продуманный (авторский)"], None),
    ("music", "Музыка", "buttons", ["нет", "приятная", "раздражающий фактор"], None),
    ("light", "Свет", "buttons", ["сплошной", "уютный, продуманный"], None),
    ("facade", "Фасад", "buttons", ["незаметный", "заметный простой", "заметный продуманный"], None),
    ("navigation", "Указатели и навигация", "buttons", ["есть", "нет"], None),
    ("restroom", "Санузел", "buttons", ["нет", "простой", "уютный, продуманный"], None),
    ("cleanliness", "Чистота предприятия", "buttons", ["чисто", "грязно"], None),
]

SERVICE_FIELDS = [
    (
        "service",
        "Персонализация/сервис",
        "text",
        None,
        "встреча и прощание с гостем, коммуникация при заказе, опрятность, форма, слаженность команды, работа с отзывами, рейтинг",
    ),
]

BRAND_FIELDS = [
    (
        "mentions",
        "Сила бренда — число упоминаний",
        "number",
        None,
        "зайдите на wordstat.yandex.ru, введите название конкурента, посмотрите число запросов за последний месяц и пришлите это число",
    ),
]

LABOR_FIELDS = [
    ("hourly_rate", "Ставка в час", "number", None, None),
    ("bonuses", "Премии", "text", None, "опишите свободным текстом"),
    ("training", "Обучение", "buttons", ["есть", "нет"], None),
    ("work_norm_hours", "Норма труда (часов в день у бариста)", "number", None, None),
    ("fines", "Штрафы", "buttons", ["есть", "нет"], None),
    ("uniform", "Форма", "buttons", ["нет", "есть за свой счёт", "есть за счёт предприятия"], None),
    ("career_growth", "Карьерный рост", "buttons", ["есть", "нет"], None),
]

# (block_key, block_title, fields) — block_key совпадает с колонкой в competitor_factors
FACTOR_BLOCKS = [
    ("product", "Продукт", PRODUCT_FIELDS),
    ("atmosphere", "Атмосфера/интерьер", ATMOSPHERE_FIELDS),
    ("service", "Персонализация/сервис", SERVICE_FIELDS),
    ("brand_strength", "Сила бренда", BRAND_FIELDS),
    ("labor_market", "Рынок труда", LABOR_FIELDS),
]


def get_block(block_key: str) -> tuple[str, list]:
    for key, title, fields in FACTOR_BLOCKS:
        if key == block_key:
            return title, fields
    raise KeyError(block_key)


def all_block_keys() -> list[str]:
    return [key for key, _, _ in FACTOR_BLOCKS]


def parse_field_value(field: tuple, raw_text: str):
    """Валидирует и приводит текстовый ответ к значению поля. Бросает
    ValueError, если ответ не подходит (вызывающий код переспрашивает)."""
    _, _, kind, _, _ = field
    text = raw_text.strip()
    if kind == "number":
        try:
            value = float(text.replace(",", "."))
        except ValueError:
            raise ValueError("not a number") from None
        return int(value) if value == int(value) else value
    if kind == "text":
        if not text:
            raise ValueError("empty")
        return text
    raise ValueError("this field is button-only")


def factor_state_init(block_keys: list[str] | None = None) -> dict:
    """Состояние прохождения опросника факторов. block_keys=None — идём по
    всем блокам по порядку (/add_competitor); список из одного ключа —
    быстрый апдейт одного блока (/monitoring)."""
    return {
        "block_keys": list(block_keys) if block_keys is not None else all_block_keys(),
        "pos": 0,
        "field_idx": 0,
        "answers": {},
    }


def factor_progress_done(fstate: dict) -> bool:
    return fstate["pos"] >= len(fstate["block_keys"])


def current_field(fstate: dict) -> tuple[str, str, tuple]:
    block_key = fstate["block_keys"][fstate["pos"]]
    title, fields = get_block(block_key)
    return block_key, title, fields[fstate["field_idx"]]


def is_first_field_of_block(fstate: dict) -> bool:
    return fstate["field_idx"] == 0


def advance_factor_cursor(fstate: dict) -> None:
    block_key = fstate["block_keys"][fstate["pos"]]
    _, fields = get_block(block_key)
    fstate["field_idx"] += 1
    if fstate["field_idx"] >= len(fields):
        fstate["field_idx"] = 0
        fstate["pos"] += 1


def record_answer(fstate: dict, block_key: str, field_key: str, value) -> None:
    fstate["answers"].setdefault(block_key, {})[field_key] = value


def serialize_block(answers: dict) -> str:
    return json.dumps(answers, ensure_ascii=False)


def serialized_blocks(fstate: dict) -> dict[str, str]:
    """block_key -> JSON-строка, готово для save_factors(**this)."""
    return {block_key: serialize_block(values) for block_key, values in fstate["answers"].items()}


def parse_block_value(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"_raw": str(raw)}
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}


def render_block_lines(block_key: str, raw) -> list[tuple[str, str]]:
    """[(label, value), ...] для дашборда — незаполненные поля пропускаются.
    Данные до перехода на структурированный формат (обычный текст) отдаются
    одной строкой без разбивки на поля."""
    title, fields = get_block(block_key)
    data = parse_block_value(raw)
    if "_raw" in data:
        return [(title, data["_raw"])]
    lines = []
    for key, label, kind, options, note in fields:
        value = data.get(key)
        if value not in (None, ""):
            lines.append((label, str(value)))
    return lines
