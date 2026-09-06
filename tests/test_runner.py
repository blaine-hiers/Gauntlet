import subprocess

from gauntlet.config import Config, Variant
from gauntlet.runner import (
    ISOLATION_FLAGS,
    ancestor_context_files,
    execute,
    prepare_run_dir,
)
from gauntlet.snapshot import make_snapshot
from gauntlet.tasks import GoldenTask


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


def test_prepare_run_dir_separates_repeats(fake_framework, tmp_path):
    snap = tmp_path / "snap"
    make_snapshot(fake_framework, snap, exclude=[])
    work = tmp_path / "work"
    args = (snap, work, make_task(), Variant("current", None), tmp_path / "tasks", tmp_path)
    d0 = prepare_run_dir(*args, repeat_idx=0)
    d1 = prepare_run_dir(*args, repeat_idx=1)
    assert d0 != d1
    assert d0 == prepare_run_dir(*args)  # default repeat is 0


def test_ancestor_context_files(tmp_path):
    # The CLI checks every ancestor for CLAUDE.md and .claude/CLAUDE.md; the run
    # dir's own file is the variant under test and must not count.
    (tmp_path / "CLAUDE.md").write_text("top", encoding="utf-8")
    mid = tmp_path / "mid"
    (mid / ".claude").mkdir(parents=True)
    (mid / ".claude" / "CLAUDE.md").write_text("user-style memory", encoding="utf-8")
    run_dir = mid / "runs" / "abc"
    run_dir.mkdir(parents=True)
    (run_dir / "CLAUDE.md").write_text("variant", encoding="utf-8")

    found = ancestor_context_files(run_dir)
    assert (tmp_path / "CLAUDE.md").resolve() in found
    assert (mid / ".claude" / "CLAUDE.md").resolve() in found
    assert (run_dir / "CLAUDE.md").resolve() not in found
    # Only what lies under tmp_path is asserted: pytest's own tmp root may sit
    # under a directory holding a CLAUDE.md, which is the very leak this guards.


def test_execute_builds_command_and_parses_json(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            cmd, 0, stdout='{"result": "the answer", "total_cost_usd": 0.12}', stderr=""
        )

    monkeypatch.setattr("gauntlet.runner.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.runner.subprocess.run", fake_run)

    result = execute(make_task(), Variant("current", None), tmp_path, make_cfg(tmp_path))

    # Assert full command exactly. The trailing flags are the user-scope isolation:
    # no ~/.claude settings (plugins, skills, user MCP) and no MCP at all.
    assert captured["cmd"] == [
        "claude", "-p", "hello", "--output-format", "json",
        "--permission-mode", "acceptEdits", "--model", "claude-fable-5", "--max-turns", "30",
        "--setting-sources", "project,local", "--strict-mcp-config",
    ]
    assert result["isolation"] == ISOLATION_FLAGS

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

    monkeypatch.setattr("gauntlet.runner.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.runner.subprocess.run", fake_run)

    result = execute(make_task(), Variant("current", None), tmp_path, make_cfg(tmp_path))
    assert result["is_error"] is True
    assert result["output_text"] == "CRASH"


def test_execute_survives_timeout_with_partial_output(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 900, output="partial stdout so far")

    monkeypatch.setattr("gauntlet.runner.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.runner.subprocess.run", fake_run)

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

    monkeypatch.setattr("gauntlet.runner.find_claude", lambda: "claude")
    monkeypatch.setattr("gauntlet.runner.subprocess.run", fake_run)

    result = execute(make_task(), Variant("current", None), tmp_path, make_cfg(tmp_path))
    assert result["is_error"] is True
    assert result["exit_code"] is None
    assert result["cost_usd"] is None
    assert result["output_text"] == ""
    assert result["isolation"] == ISOLATION_FLAGS  # a timed-out row is still self-describing
