from pathlib import Path

import pytest

from assay.tasks import load_tasks

VALID_TASK = """\
id: find-alpha-status
category: find-answer
prompt: "What is the current status of project Alpha?"
checks:
  - type: file_unchanged
    path: Projects/Alpha/status.md
judge:
  rubric: "Answer must state that Alpha is on track."
  answer_key: "Alpha status: on track"
"""


def test_load_tasks_parses_valid_yaml(tmp_path: Path):
    (tmp_path / "01-find.yaml").write_text(VALID_TASK, encoding="utf-8")
    tasks = load_tasks(tmp_path)
    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "find-alpha-status"
    assert t.category == "find-answer"
    assert t.checks[0]["type"] == "file_unchanged"
    assert t.judge["answer_key"] == "Alpha status: on track"
    assert t.setup == []


def test_load_tasks_rejects_bad_category(tmp_path: Path):
    (tmp_path / "bad.yaml").write_text(
        VALID_TASK.replace("category: find-answer", "category: bogus"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="category"):
        load_tasks(tmp_path)


def test_load_tasks_rejects_no_scoring(tmp_path: Path):
    text = "id: t\ncategory: find-answer\nprompt: hi\n"
    (tmp_path / "bad.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="checks or judge"):
        load_tasks(tmp_path)


def test_load_tasks_ignores_subdirectories(tmp_path: Path):
    # Top-level task should load
    (tmp_path / "01-top.yaml").write_text(VALID_TASK, encoding="utf-8")
    # Subdirectory task should be ignored (non-recursive glob)
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "skip-me.yaml").write_text(VALID_TASK, encoding="utf-8")
    tasks = load_tasks(tmp_path)
    assert len(tasks) == 1
    assert tasks[0].id == "find-alpha-status"


def test_load_tasks_rejects_missing_required_field(tmp_path: Path):
    # Missing 'prompt' key
    text = "id: t\ncategory: find-answer\nchecks:\n  - type: file_exists\n    path: f.txt\n"
    (tmp_path / "bad.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="prompt"):
        load_tasks(tmp_path)


def test_load_tasks_rejects_empty_judge(tmp_path: Path):
    text = """\
id: empty-judge
category: find-answer
prompt: "Test"
checks:
  - type: file_exists
    path: f.txt
judge: {}
"""
    (tmp_path / "bad.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="rubric"):
        load_tasks(tmp_path)


def test_load_tasks_rejects_check_without_path(tmp_path: Path):
    text = """\
id: no-path
category: find-answer
prompt: "Test"
checks:
  - type: file_exists
"""
    (tmp_path / "bad.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="path"):
        load_tasks(tmp_path)


def test_load_tasks_rejects_check_with_empty_path(tmp_path: Path):
    text = """\
id: empty-path
category: find-answer
prompt: "Test"
checks:
  - type: file_exists
    path: ""
"""
    (tmp_path / "bad.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="path"):
        load_tasks(tmp_path)


def test_load_tasks_rejects_duplicate_ids(tmp_path: Path):
    (tmp_path / "01-a.yaml").write_text(VALID_TASK, encoding="utf-8")
    (tmp_path / "02-b.yaml").write_text(VALID_TASK, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_tasks(tmp_path)
