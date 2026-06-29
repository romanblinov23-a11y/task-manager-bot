import json
import re

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_json_response(text: str) -> dict:
    """Парсит ответ Claude как JSON. Промпты требуют 'ТОЛЬКО валидный JSON,
    без markdown-разметки', но на случай, если модель всё же оборачивает
    ответ в code fence, снимаем обёртку перед парсингом."""
    cleaned = text.strip()
    match = _CODE_FENCE_RE.match(cleaned)
    if match:
        cleaned = match.group(1)
    return json.loads(cleaned)
