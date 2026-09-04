import subprocess

from assay.config import Config, Variant
from assay.runner import execute, prepare_run_dir
from assay.snapshot import make_snapshot
from assay.tasks import GoldenTask


def make_task(**overrides):
    base = dict(id="t1", category="find-answer", prompt="hello", setup=[], checks=[], judge=None)
    base.update(overrides)
    return GoldenTask(**base)


def make_cfg(tmp_path):
    return Config(
        synced_root=tmp_path,
        model="claude-fable-5",
        judge_model="claude-fable-5",
        max_turns=30,
        timeout_s=900,
        exclude=[],
        variants=[],
    )


def test_prepare_run_dir_variants(fake_framework, tmp_path):
    snap = tmp_path / "snap"
    make_snapshot(fake_framework, snap, exclude=[])
    work = tmp_path / "work"
    project_root = tmp_path / "proj"
    (project_root / "variants").mkdir(parents=True)
    (project_root / "variants" / "trimmed.md").write_text("# Trimmed\n", encoding="utf-8")
    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "seed").mkdir(parents=True)
    (tasks_dir / "seed" / "doc.txt").write_text("seed doc", encoding="utf-8")

    task = make_task(setup=[{"copy": "seed/doc.txt", "to": "_intake/doc.txt"}])

    d1 = prepare_run_dir(snap, work, task, Variant("current", None), tasks_dir, project_root)
    assert (d1 / "CLAUDE.md").read_text(encoding="utf-8").startswith("# Framework Rules")
    assert (d1 / "_intake" / "doc.txt").read_text(encoding="utf-8") == "seed doc"

    d2 = prepare_run_dir(snap, work, task, Variant("empty", ""), tasks_dir, project_root)
    assert not (d2 / "CLAUDE.md").exists()

    d3 = prepare_run_dir(
        snap, work, task, Variant("trimmed", "variants/trimmed.md"), tasks_dir, project_root
    )
    assert (d3 / "CLAUDE.md").read_text(encoding="utf-8") == "# Trimmed\n"


def test_execute_builds_command_and_parses_json(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            cmd, 0, stdout='{"result": "the answer", "total_cost_usd": 0.12}', stderr=""
        )

    monkeypatch.setattr("assay.runner.find_claude", lambda: "claude")
    monkeypatch.setattr("assay.runner.subprocess.run", fake_run)

    result = execute(make_task(), Variant("current", None), tmp_path, make_cfg(tmp_path))

    # Assert full command exactly
    assert captured["cmd"] == [
        "claude", "-p", "hello", "--output-format", "json",
        "--permission-mode", "acceptEdits", "--model", "claude-fable-5", "--max-turns", "30"
    ]

    # Assert subprocess.run kwargs
    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["timeout"] == 900

    # Assert result parsing
    assert result["output_text"] == "the answer"
    assert result["cost_usd"] == 0.12
    assert result["is_error"] is False
    assert result["model"] == "claude-fable-5"


def test_execute_survives_non_json_output(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="CRASH", stderr="boom")

    monkeypatch.setattr("assay.runner.find_claude", lambda: "claude")
    monkeypatch.setattr("assay.runner.subprocess.run", fake_run)

    result = execute(make_task(), Variant("current", None), tmp_path, make_cfg(tmp_path))
    assert result["is_error"] is True
    assert result["output_text"] == "CRASH"


def test_execute_survives_timeout_with_partial_output(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 900, output="partial stdout so far")

    monkeypatch.setattr("assay.runner.find_claude", lambda: "claude")
    monkeypatch.setattr("assay.runner.subprocess.run", fake_run)

    result = execute(make_task(), Variant("current", None), tmp_path, make_cfg(tmp_path))
    assert result["is_error"] is True
    assert result["exit_code"] is None
    assert result["cost_usd"] is None
    assert result["output_text"] == "partial stdout so far"
    assert result["task_id"] == "t1"
    assert result["variant"] == "current"
    assert result["model"] == "claude-fable-5"


def test_execute_survives_timeout_with_no_output(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 900)

    monkeypatch.setattr("assay.runner.find_claude", lambda: "claude")
    monkeypatch.setattr("assay.runner.subprocess.run", fake_run)

    result = execute(make_task(), Variant("current", None), tmp_path, make_cfg(tmp_path))
    assert result["is_error"] is True
    assert result["exit_code"] is None
    assert result["cost_usd"] is None
    assert result["output_text"] == ""
