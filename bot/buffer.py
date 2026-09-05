from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from config.settings import MESSAGE_BUFFER_HOURS, TZ


@dataclass
class BufferedMessage:
    message_id: int
    timestamp: datetime  # должен быть timezone-aware (UTC) — см. Telegram message.date
    sender_name: str
    sender_id: int  # Telegram user_id — нужен для однозначной идентификации при совпадении имён
    text: str
    link: str


class MessageBuffer:
    """Скользящий буфер сообщений группового чата (раздел 2.1, 9.3
    PROJECT_SPEC.md). Хранит последние MESSAGE_BUFFER_HOURS часов в
    памяти, старые сообщения вытесняются при каждом добавлении.

    Ключ — (chat_id, message_thread_id): в форуме с топиками разные ветки
    одного чата могут относиться к разным проектам, поэтому буферизуются
    отдельно, а не смешиваются в одну переписку (message_thread_id=None
    для обычного чата без топиков — как и раньше)."""

    def __init__(self, retention_hours: int = MESSAGE_BUFFER_HOURS) -> None:
        self._retention = timedelta(hours=retention_hours)
        self._chats: dict[tuple[int, int | None], deque[BufferedMessage]] = defaultdict(deque)

    def add(self, buffer_key: tuple[int, int | None], message: BufferedMessage) -> None:
        chat_buffer = self._chats[buffer_key]
        chat_buffer.append(message)
        self._evict_old(chat_buffer)

    def get_context(self, buffer_key: tuple[int, int | None], hours: int) -> list[BufferedMessage]:
        """Сообщения ветки/чата за последние `hours` часов, в порядке получения."""
        chat_buffer = self._chats[buffer_key]
        self._evict_old(chat_buffer)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return [m for m in chat_buffer if m.timestamp >= cutoff]

    def _evict_old(self, chat_buffer: deque[BufferedMessage]) -> None:
        cutoff = datetime.now(timezone.utc) - self._retention
        while chat_buffer and chat_buffer[0].timestamp < cutoff:
            chat_buffer.popleft()


def format_context(messages: list[BufferedMessage]) -> str:
    """Сериализует сообщения буфера в текст для передачи в Промпт 1."""
    lines = []
    for m in messages:
        ts = m.timestamp.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
        lines.append(f"[{ts}] {m.sender_name}: {m.text}")
    return "\n".join(lines)
