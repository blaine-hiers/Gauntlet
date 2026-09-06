from pathlib import Path

import pytest

from gauntlet.tasks import load_tasks

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


def test_load_tasks_accepts_snapshot_unchanged_without_path(tmp_path: Path):
    text = """\
id: t
category: framework-upkeep
prompt: "Do nothing"
checks:
  - type: snapshot_unchanged
    allow: ["CLAUDE.md"]
"""
    (tmp_path / "ok.yaml").write_text(text, encoding="utf-8")
    tasks = load_tasks(tmp_path)
    assert tasks[0].checks[0]["type"] == "snapshot_unchanged"


def test_load_tasks_rejects_file_not_contains_without_text(tmp_path: Path):
    text = """\
id: t
category: find-answer
prompt: "Test"
checks:
  - type: file_not_contains
    path: "$response"
"""
    (tmp_path / "bad.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="text"):
        load_tasks(tmp_path)


@pytest.mark.parametrize("ctype", ["file_exists", "file_not_exists", "file_unchanged"])
def test_load_tasks_rejects_response_target_for_file_checks(tmp_path: Path, ctype):
    # $response has no backing file: file_unchanged would crash on sha256_file
    # at run time, and file_exists/file_not_exists are trivially always
    # True/False for it — reject all three at load time instead.
    text = f"""\
id: t
category: find-answer
prompt: "Test"
checks:
  - type: {ctype}
    path: "$response"
"""
    (tmp_path / "bad.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"\$response"):
        load_tasks(tmp_path)


def test_load_tasks_accepts_response_target_for_content_checks(tmp_path: Path):
    text = """\
id: t
category: find-answer
prompt: "Test"
checks:
  - type: file_not_contains
    path: "$response"
    text: "Jacksonville"
"""
    (tmp_path / "ok.yaml").write_text(text, encoding="utf-8")
    tasks = load_tasks(tmp_path)
    assert tasks[0].checks[0]["path"] == "$response"


def test_load_tasks_rejects_file_matches_without_pattern(tmp_path: Path):
    text = """\
id: t
category: find-answer
prompt: "Test"
checks:
  - type: file_matches
    path: "f.txt"
"""
    (tmp_path / "bad.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="pattern"):
        load_tasks(tmp_path)
