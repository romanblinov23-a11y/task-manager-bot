import pytest

from monitoring.markets import create_market, list_market_names
from tasks.comments import append_comment
from tasks.log import append_log_entry, get_log_entries
from tasks.tasks import create_task, generate_task_id, get_all_tasks, get_task, update_task


def _project() -> str:
    return list_market_names()[0]


def test_generate_task_id_starts_at_0001():
    assert generate_task_id(_project()) == "TASK-0001"


def test_create_task_assigns_sequential_ids_per_project():
    project = _project()
    tid1 = create_task(project, source="chat", task_text="Первая", category="Сервис")
    tid2 = create_task(project, source="chat", task_text="Вторая", category="Сервис")
    assert tid1 == "TASK-0001"
    assert tid2 == "TASK-0002"


def test_task_ids_do_not_collide_across_projects():
    market2 = create_market("Второй проект")
    p1, p2 = _project(), market2["name"]
    tid1 = create_task(p1, source="chat", task_text="A", category="Сервис")
    tid2 = create_task(p2, source="chat", task_text="B", category="Сервис")
    assert tid1 == tid2 == "TASK-0001"
    assert get_task(p1, tid1)["task_text"] == "A"
    assert get_task(p2, tid2)["task_text"] == "B"


def test_get_all_tasks_isolated_per_project():
    market2 = create_market("Второй проект")
    p1, p2 = _project(), market2["name"]
    create_task(p1, source="chat", task_text="Только в p1", category="Сервис")
    assert len(get_all_tasks(p1)) == 1
    assert get_all_tasks(p2) == []


def test_create_task_rejects_unknown_project():
    with pytest.raises(ValueError):
        create_task("Не существует", source="manual", task_text="x", category="Сервис")


def test_create_task_rejects_unknown_category():
    with pytest.raises(ValueError):
        create_task(_project(), source="manual", task_text="x", category="Не существует")


def test_update_task_changes_fields():
    project = _project()
    tid = create_task(project, source="chat", task_text="Задача", category="Сервис")
    update_task(project, tid, status="в работе", last_comment="взял в работу")
    task = get_task(project, tid)
    assert task["status"] == "в работе"
    assert task["last_comment"] == "взял в работу"


def test_update_task_rejects_unknown_task_id():
    with pytest.raises(ValueError):
        update_task(_project(), "TASK-9999", status="выполнена")


def test_update_task_rejects_unknown_column():
    project = _project()
    tid = create_task(project, source="chat", task_text="Задача", category="Сервис")
    with pytest.raises(ValueError):
        update_task(project, tid, not_a_real_column="x")


def test_log_entries_scoped_to_project():
    market2 = create_market("Второй проект")
    p1, p2 = _project(), market2["name"]
    tid = create_task(p1, source="chat", task_text="Задача", category="Сервис")
    append_log_entry(p1, tid, "смена_статуса", old_value="новая", new_value="в работе")
    assert len(get_log_entries(p1)) == 1
    assert get_log_entries(p2) == []


def test_append_comment_rejects_unknown_author():
    project = _project()
    tid = create_task(project, source="chat", task_text="Задача", category="Сервис")
    with pytest.raises(ValueError):
        append_comment(project, tid, "неизвестно кто", "текст")
