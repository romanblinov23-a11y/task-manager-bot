from datetime import date

from config.timeutil import today as tz_today
from prompts.client import ask_claude
from prompts.utils import parse_json_response

# Промпт 2 — PROJECT_SPEC.md, раздел 8.2
_PROMPT_TEMPLATE = """Ты — модуль анализа ответов сотрудников для бота-менеджера проектов
Романа. Бот недавно спросил сотрудника о статусе конкретной задачи,
и сотрудник ответил. Твоя задача — разобрать этот ответ.

КОНТЕКСТ ЗАДАЧИ
Задача: {task_text}
Текущий срок: {deadline_current}
Текущий статус: {status}

ВОПРОС БОТА СОТРУДНИКУ
{bot_question}

ОТВЕТ СОТРУДНИКА
{employee_reply}

ЧТО НУЖНО ОПРЕДЕЛИТЬ

1. status_clear — true/false: можно ли из ответа однозначно понять
   текущий статус задачи. Если ответ расплывчатый, уклончивый или
   не содержит ясного указания на статус ("разбираюсь", "работаю
   над этим", без уточнения готовности/сроков) — поставь false.

2. new_status — один из: "в работе", "выполнена", "просрочена".
   Заполняется ТОЛЬКО если status_clear=true. Если status_clear=false
   — оставь null, статус не меняется автоматически.

3. clarifying_question — если status_clear=false, сформулируй прямой
   уточняющий вопрос для сотрудника (например: "Уточни, пожалуйста —
   задача уже выполнена, или ещё в процессе и нужен новый срок?").
   Если status_clear=true — null.

4. deadline_changed — true/false: упомянул ли сотрудник перенос срока

5. new_deadline — если deadline_changed=true, новая дата в формате
   YYYY-MM-DD (переведи относительные указания типа "на следующей
   неделе" относительно сегодняшней даты: {current_date}).
   Если перенос упомянут, но без конкретной даты — null, и в этом
   случае также верни status_clear=false с уточняющим вопросом про
   точный срок.
   ВАЖНО: новый дедлайн всегда в будущем. Если расчётная дата уже
   прошла — бери следующее вхождение этого дня (через 7 дней).

6. reason — если был перенос или есть сложности, кратко суть причины
   своими словами (1 фраза)

7. needs_help — true/false: просит ли сотрудник помощи Романа явно
   или косвенно

8. comment_summary — краткое резюме ответа сотрудника (1-2 фразы)
   для истории комментариев по задаче (заполняется всегда, даже
   если статус неясен — сам факт ответа стоит зафиксировать)

9. signal_type — определи, нужно ли мгновенно уведомить Романа.
   Один из: "завершение", "перенос_срока", "запрос_помощи", "нет"
   (если по ответу одновременно есть несколько признаков — приоритет:
   запрос_помощи > перенос_срока > завершение. Если status_clear=false
   и needs_help не true — signal_type обычно "нет")

ФОРМАТ ОТВЕТА
Ответь ТОЛЬКО валидным JSON, без преамбулы и markdown:

{
  "status_clear": true,
  "new_status": "...",
  "clarifying_question": null,
  "deadline_changed": false,
  "new_deadline": null,
  "reason": "...",
  "needs_help": false,
  "comment_summary": "...",
  "signal_type": "нет"
}"""

_VALID_NEW_STATUSES = {"в работе", "выполнена", "просрочена"}
_VALID_SIGNAL_TYPES = {"завершение", "перенос_срока", "запрос_помощи", "нет"}


def parse_status_reply(
    *,
    task_text: str,
    deadline_current: str,
    status: str,
    bot_question: str,
    employee_reply: str,
    current_date: date | None = None,
) -> dict:
    """Промпт 2: разбирает ответ сотрудника на вопрос бота о статусе задачи."""
    if current_date is None:
        current_date = tz_today()

    prompt = (
        _PROMPT_TEMPLATE
        .replace("{task_text}", task_text)
        .replace("{deadline_current}", deadline_current)
        .replace("{status}", status)
        .replace("{bot_question}", bot_question)
        .replace("{employee_reply}", employee_reply)
        .replace("{current_date}", current_date.isoformat())
    )

    data = parse_json_response(ask_claude(prompt))
    _validate_reply(data)
    return data


def _validate_reply(data: dict) -> None:
    if data.get("status_clear") and data.get("new_status") not in _VALID_NEW_STATUSES:
        raise ValueError(f"Claude вернул new_status вне фиксированного списка: {data.get('new_status')!r}")
    if data.get("signal_type") not in _VALID_SIGNAL_TYPES:
        raise ValueError(f"Claude вернул signal_type вне фиксированного списка: {data.get('signal_type')!r}")
