from monitoring.factor_schema import describe_current_values, describe_schema, validate_proposed_changes
from prompts.client import ask_claude
from prompts.utils import parse_json_response

_PROMPT_TEMPLATE = """Ты — модуль обновления факторов формирования конкурентов для бота
мониторинга конкурентов Surf Coffee. Оператор мониторинга описал свободным
текстом, что изменилось у конкурента "{competitor_name}". Твоя задача —
определить, какие именно поля факторов формирования нужно обновить и на
какие значения, строго в рамках схемы ниже.

СХЕМА ПОЛЕЙ
{schema_description}

ТЕКУЩИЕ ЗНАЧЕНИЯ ФАКТОРОВ ЭТОГО КОНКУРЕНТА
{current_values}

ОПИСАНИЕ ОПЕРАТОРА
{operator_text}

ЧТО НУЖНО СДЕЛАТЬ
Определи, какие поля обновились по описанию оператора. Для поля с
фиксированными вариантами новое значение должно быть СТРОГО одним из
перечисленных вариантов, дословно, без изменений формулировки. Для
числового поля — обычное число. Для текстового — краткая фраза своими
словами по сути описания.

Не выдумывай изменения, которых нет в описании оператора. Если описание
не про факторы формирования вовсе, или непонятно, к какому полю оно
относится — верни changes: [].

ФОРМАТ ОТВЕТА
Ответь ТОЛЬКО валидным JSON, без преамбулы и markdown:

{
  "changes": [
    {"block": "atmosphere", "field": "music", "new_value": "приятная", "reason": "оператор упомянул новую фоновую музыку"}
  ]
}"""


def propose_factor_changes(competitor_name: str, factors_row: dict | None, operator_text: str) -> list[dict]:
    """Прогоняет свободный текст оператора через Claude и возвращает
    провалидированный список изменений факторов (может быть пустым, если
    Claude не нашёл ничего по описанию или предложил что-то вне схемы —
    такие варианты отбрасываются validate_proposed_changes)."""
    prompt = (
        _PROMPT_TEMPLATE.replace("{competitor_name}", competitor_name)
        .replace("{schema_description}", describe_schema())
        .replace("{current_values}", describe_current_values(factors_row))
        .replace("{operator_text}", operator_text)
    )
    data = parse_json_response(ask_claude(prompt))
    return validate_proposed_changes(data.get("changes", []), factors_row)
