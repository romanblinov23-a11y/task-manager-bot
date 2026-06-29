from datetime import date

from config.projects import CATEGORIES, PROJECTS
from config.timeutil import today as tz_today
from prompts.client import ask_claude
from prompts.utils import parse_json_response

# Промпт 1 — PROJECT_SPEC.md, раздел 8.1
_PROMPT_TEMPLATE = """Ты — модуль анализа задач для бота-менеджера проектов Романа.
Твоя задача — найти в переданном тексте все рабочие договорённости
и задачи, которые нужно зафиксировать.

КОНТЕКСТ ВХОДНЫХ ДАННЫХ
Тебе передан один из трёх типов текста:
- Переписка из рабочего чата за определённый период
- Сообщение от Романа в личном чате с ботом
- Текст протокола встречи

Тебе известен проект, к которому относится этот текст: {project_name}
(один из: "Парк Горького", "Окко", "Аврора"; может быть null, если
определить нужно по содержанию — см. ниже)

ЧТО НУЖНО НАЙТИ
Найди ВСЕ задачи и договорённости в тексте — их может быть от 0 до
любого количества. Не ограничивайся одной задачей, даже если бот был
тегнут один раз — извлекай все договорённости из переданного контекста.

Задачей считается: явное поручение, обещание сделать что-либо,
договорённость о сроке выполнения чего-либо. Не считай задачей
обычное обсуждение без конкретного действия и исполнителя.

ДЛЯ КАЖДОЙ ЗАДАЧИ ОПРЕДЕЛИ
1. task_text — суть задачи кратко и ясно, в формате "что сделать"
2. assignee — имя исполнителя, как оно фигурирует в тексте
   (если неясно, кто исполнитель — пометь assignee_unclear: true)
3. category — выбери ОДНУ строго из этого списка, не придумывай новые:
   Техническое обеспечение, Сервис, Команда, Маркетинг, Бухгалтерия,
   Административные, Ноу-Хао, Управленческая отчётность
4. deadline — срок в формате YYYY-MM-DD, если упомянут явно или
   косвенно ("до пятницы", "на следующей неделе" — переведи в дату
   относительно сегодняшней даты: {current_date}). Если срок не
   упомянут — null.
5. project — если {project_name} известен заранее, используй его.
   Если null (личное сообщение/протокол без явной привязки к чату) —
   определи проект по исполнителю, если он уже встречается в одном из
   трёх проектов. Если определить невозможно — поставь project: null
   и project_unclear: true.
6. source_excerpt — точная цитата или фраза из текста, на основании
   которой ты сделал этот вывод (для проверки Романом)

ЕСЛИ НЕ УВЕРЕН
Если задача упомянута расплывчато (нет явного исполнителя или
непонятно, договорённость это или просто мысль вслух) — всё равно
включи её в список, но добавь поле confidence: "low" и кратко поясни
сомнение в source_excerpt.

ФОРМАТ ОТВЕТА
Ответь ТОЛЬКО валидным JSON, без преамбулы, без markdown-разметки.
Формат:

{
  "tasks": [
    {
      "task_text": "...",
      "assignee": "...",
      "assignee_unclear": false,
      "category": "...",
      "deadline": "2026-07-03",
      "project": "...",
      "project_unclear": false,
      "confidence": "high",
      "source_excerpt": "..."
    }
  ]
}

Если задач не найдено — верни {"tasks": []}"""


def extract_tasks(text: str, project_name: str | None = None, current_date: date | None = None) -> list[dict]:
    """Промпт 1: находит все задачи/договорённости в переданном тексте
    (буфер чата / личка Романа / протокол встречи)."""
    if current_date is None:
        current_date = tz_today()

    prompt = (
        _PROMPT_TEMPLATE
        .replace("{project_name}", project_name if project_name else "null")
        .replace("{current_date}", current_date.isoformat())
        + "\n\nТЕКСТ ДЛЯ АНАЛИЗА (формат строк — \"[время] Имя: сообщение\"; "
        "если автор сообщения говорит о себе в первом лице — \"я возьму\", "
        "\"беру на себя\", \"взял, делаю\" — без явного имени, assignee — это "
        "имя автора этой строки):\n" + text
    )

    data = parse_json_response(ask_claude(prompt))
    tasks = data.get("tasks", [])
    for task in tasks:
        _validate_task(task)
    return tasks


def _validate_task(task: dict) -> None:
    category = task.get("category")
    if category not in CATEGORIES:
        raise ValueError(f"Claude вернул категорию вне фиксированного списка: {category!r}")
    project = task.get("project")
    if project is not None and project not in PROJECTS:
        raise ValueError(f"Claude вернул проект вне фиксированного списка: {project!r}")
