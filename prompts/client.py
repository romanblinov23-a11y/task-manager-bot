from anthropic import Anthropic

from config.settings import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def ask_claude(prompt: str, max_tokens: int = 4096) -> str:
    response = get_client().messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
