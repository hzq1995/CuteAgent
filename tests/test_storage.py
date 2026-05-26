from app.storage import TaskStore


def test_task_store_appends_reasoning_and_answer(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    task = store.create_task("hello")

    store.append_reasoning(task["id"], "think ")
    store.append_answer(task["id"], "answer")
    updated = store.get_task(task["id"])

    assert updated["reasoning_content"] == "think "
    assert updated["answer"] == "answer"
    assert updated["status"] == "queued"
