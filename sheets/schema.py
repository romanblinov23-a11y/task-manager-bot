# Названия листов и колонок — зеркало структуры из PROJECT_SPEC.md, раздел 1.2

TASKS_SHEET = "Задачи"
LOG_SHEET = "Лог переносов и изменений"
COMMENTS_SHEET = "Комментарии"

TASKS_COLUMNS = [
    "task_id",
    "created_at",
    "source",
    "source_chat",
    "source_link",
    "project",
    "category",
    "task_text",
    "assignee",
    "assignee_telegram_id",
    "deadline_original",
    "deadline_current",
    "status",
    "last_comment",
    "needs_help",
    "last_status_check",
    "closed_at",
]

LOG_COLUMNS = [
    "task_id",
    "timestamp",
    "event_type",
    "old_value",
    "new_value",
    "reason_comment",
]

COMMENTS_COLUMNS = [
    "task_id",
    "timestamp",
    "author",
    "comment_text",
    "related_status",
]
