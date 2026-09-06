import dataclasses
import json
import tempfile
from pathlib import Path

from gauntlet import cli
from gauntlet.config import Config, Variant
from gauntlet.runner import ancestor_context_files as cli_real_ancestor_context_files


def fake_cfg(fake_framework):
    return Config(
        synced_root=fake_framework,
        model="claude-fable-5",
        judge_model="claude-fable-5",
        max_turns=30,
        timeout_s=900,
        exclude=["~$*"],
        variants=[Variant("current", None), Variant("empty", "")],
    )


def test_snapshot_lint_run_report_pipeline(fake_framework, tmp_path, monkeypatch, capsys):
    project_root = tmp_path / "gauntlet-project"
    tasks_dir = project_root / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "01-find.yaml").write_text(
        "id: find-alpha-status\n"
        "category: find-answer\n"
        'prompt: "What is the status of Alpha?"\n'
        "checks:\n"
        "  - type: file_unchanged\n"
        "    path: Projects/Alpha/status.md\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(cli, "load_project_config", lambda: fake_cfg(fake_framework))
    # Keep run-dir temp usage inside pytest's sandbox instead of the real system temp dir.
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path / "systemp"))
    # pytest's tmp root may itself sit under a directory holding a CLAUDE.md
    # (on Windows it is under the user's home); the preflight has its own test.
    monkeypatch.setattr(cli, "ancestor_context_files", lambda p: [])

    assert cli.main(["snapshot"]) == 0
    assert (project_root / "data" / "snapshot" / "CLAUDE.md").is_file()

    assert cli.main(["lint"]) == 0
    assert "Ghost Section" in capsys.readouterr().out

    def fake_execute(task, variant, run_dir, cfg):
        return {
            "task_id": task.id, "category": task.category, "variant": variant.name,
            "duration_s": 1.0, "cost_usd": 0.01, "output_text": "Alpha is on track",
            "exit_code": 0, "is_error": False,
        }

    monkeypatch.setattr(cli, "execute", fake_execute)
    assert cli.main(["run", "--label", "test-run"]) == 0
    jsonl = project_root / "data" / "runs" / "test-run" / "results.jsonl"
    rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2  # 1 task × 2 variants
    assert all(row["checks"][0]["passed"] for row in rows)

    assert cli.main(["report", "--label", "test-run"]) == 0
    report_md = project_root / "data" / "runs" / "test-run" / "report.md"
    assert "Gauntlet Report" in report_md.read_text(encoding="utf-8")

    # Test label reuse: re-run with same label and verify no duplicates
    assert cli.main(["run", "--label", "test-run"]) == 0
    rows_after_rerun = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
    assert len(rows_after_rerun) == 2  # Still 2 rows, not 4

    # Run dirs are scratch: they live under the system temp dir (never inside the
    # repo, so they don't inherit this repo's own CLAUDE.md) and are deleted after
    # each pair's checks are scored.
    import hashlib

    work_root = (
        Path(tempfile.gettempdir()) / "lit" / hashlib.sha1(b"test-run").hexdigest()[:8]
    )
    if work_root.is_dir():
        assert list(work_root.iterdir()) == []


def test_run_model_override_and_compare(fake_framework, tmp_path, monkeypatch):
    project_root = tmp_path / "gauntlet-project"
    tasks_dir = project_root / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "01-find.yaml").write_text(
        "id: find-alpha-status\n"
        "category: find-answer\n"
        'prompt: "What is the status of Alpha?"\n'
        "checks:\n"
        "  - type: file_unchanged\n"
        "    path: Projects/Alpha/status.md\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(cli, "load_project_config", lambda: fake_cfg(fake_framework))
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path / "systemp"))
    monkeypatch.setattr(cli, "ancestor_context_files", lambda p: [])

    seen_models = []

    def fake_execute(task, variant, run_dir, cfg):
        seen_models.append(cfg.model)
        return {
            "task_id": task.id, "category": task.category, "variant": variant.name,
            "model": cfg.model, "duration_s": 1.0, "cost_usd": 0.01,
            "output_text": "Alpha is on track", "exit_code": 0, "is_error": False,
        }

    monkeypatch.setattr(cli, "execute", fake_execute)

    assert cli.main(["snapshot"]) == 0
    assert cli.main(["run", "--label", "m-opus", "--model", "claude-opus-5"]) == 0
    assert set(seen_models) == {"claude-opus-5"}
    assert cli.main(["run", "--label", "m-base"]) == 0  # config default model

    assert cli.main(["compare", "--labels", "m-opus,m-base", "--out", "cmp"]) == 0
    cmp_md = (project_root / "data" / "runs" / "cmp.md").read_text(encoding="utf-8")
    assert "opus-5" in cmp_md and "fable-5" in cmp_md
    assert "## Verdicts (per model)" in cmp_md


def _project_with_one_task(tmp_path, monkeypatch, fake_framework):
    project_root = tmp_path / "gauntlet-project"
    tasks_dir = project_root / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "01-find.yaml").write_text(
        "id: find-alpha-status\n"
        "category: find-answer\n"
        'prompt: "What is the status of Alpha?"\n'
        "checks:\n"
        "  - type: file_unchanged\n"
        "    path: Projects/Alpha/status.md\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(cli, "load_project_config", lambda: fake_cfg(fake_framework))
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path / "systemp"))
    monkeypatch.setattr(cli, "ancestor_context_files", lambda p: [])

    def fake_execute(task, variant, run_dir, cfg):
        return {
            "task_id": task.id, "category": task.category, "variant": variant.name,
            "model": cfg.model, "duration_s": 1.0, "cost_usd": 0.01,
            "output_text": "Alpha is on track", "exit_code": 0, "is_error": False,
        }

    monkeypatch.setattr(cli, "execute", fake_execute)
    assert cli.main(["snapshot"]) == 0
    return project_root


def test_run_refuses_when_an_ancestor_holds_context(fake_framework, tmp_path, monkeypatch, capsys):
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    monkeypatch.setattr(cli, "ancestor_context_files", cli_real_ancestor_context_files)
    (tmp_path / "systemp" / ".claude").mkdir(parents=True)
    (tmp_path / "systemp" / ".claude" / "CLAUDE.md").write_text("leak", encoding="utf-8")

    assert cli.main(["run", "--label", "L"]) == 1
    out = capsys.readouterr().out
    assert "refusing to run" in out and "CLAUDE.md" in out
    assert not (project_root / "data" / "runs" / "L").exists()


def test_run_uses_configured_work_root(fake_framework, tmp_path, monkeypatch):
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    cfg = dataclasses.replace(fake_cfg(fake_framework), work_root=tmp_path / "elsewhere")
    monkeypatch.setattr(cli, "load_project_config", lambda: cfg)

    assert cli.main(["run", "--label", "W"]) == 0
    rows = _rows(project_root, "W")
    assert all(row["work_root"].startswith(str(tmp_path / "elsewhere")) for row in rows)


def _rows(project_root, label):
    jsonl = project_root / "data" / "runs" / label / "results.jsonl"
    return [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]


def test_run_resume_key_is_model_aware(fake_framework, tmp_path, monkeypatch):
    # Baseline defect: the key was (task, variant), so a label re-run under
    # --model skipped every cell and the "comparison" had one model in it.
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)

    assert cli.main(["run", "--label", "L"]) == 0
    assert len(_rows(project_root, "L")) == 2  # 1 task × 2 variants, default model

    assert cli.main(["run", "--label", "L", "--model", "claude-opus-5"]) == 0
    rows = _rows(project_root, "L")
    assert len(rows) == 4
    assert {r["model"] for r in rows} == {"claude-fable-5", "claude-opus-5"}

    # Same model again → nothing new.
    assert cli.main(["run", "--label", "L", "--model", "claude-opus-5"]) == 0
    assert len(_rows(project_root, "L")) == 4


def test_run_resume_reads_rows_without_model_or_repeat_fields(fake_framework, tmp_path, monkeypatch):
    # Rows from before either field existed: default model, single sample.
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    out_dir = project_root / "data" / "runs" / "old"
    out_dir.mkdir(parents=True)
    legacy = {
        "task_id": "find-alpha-status", "category": "find-answer", "variant": "current",
        "duration_s": 1.0, "cost_usd": 0.0, "output_text": "", "exit_code": 0,
        "is_error": False, "checks": [], "judge": None,
    }
    (out_dir / "results.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    assert cli.main(["run", "--label", "old"]) == 0
    rows = _rows(project_root, "old")
    assert len(rows) == 2  # legacy 'current' row kept, only 'empty' was run
    assert [r["variant"] for r in rows] == ["current", "empty"]

    # A legacy row was produced under the config's default model, so a --model
    # re-run must treat it as that model — not as the model now in effect — and
    # run every cell for the new model.
    assert cli.main(["run", "--label", "old", "--model", "claude-opus-5"]) == 0
    rows = _rows(project_root, "old")
    assert len(rows) == 4
    assert sum(1 for r in rows if r.get("model") == "claude-opus-5") == 2


def test_report_on_multi_model_label_writes_comparison(fake_framework, tmp_path, monkeypatch, capsys):
    # A label topped up under --model holds two models; blending them into one
    # cell would report between-model variance as run-to-run noise.
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    assert cli.main(["run", "--label", "mm"]) == 0
    assert cli.main(["run", "--label", "mm", "--model", "claude-opus-5"]) == 0
    assert cli.main(["report", "--label", "mm"]) == 0
    md = (project_root / "data" / "runs" / "mm" / "report.md").read_text(encoding="utf-8")
    assert "Model Comparison" in md
    assert "fable-5/current" in md and "opus-5/current" in md
    assert "## Static Lint" in md and "Ghost Section" in md  # lint survives the hand-off
    assert "2 models" in capsys.readouterr().out


def test_run_repeats(fake_framework, tmp_path, monkeypatch):
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)

    assert cli.main(["run", "--label", "R", "--repeats", "3"]) == 0
    rows = _rows(project_root, "R")
    assert len(rows) == 6  # 1 task × 2 variants × 3 repeats
    for variant in ("current", "empty"):
        assert sorted(r["repeat_idx"] for r in rows if r["variant"] == variant) == [0, 1, 2]

    # Re-running at the same repeat count adds nothing; raising it tops up.
    assert cli.main(["run", "--label", "R", "--repeats", "3"]) == 0
    assert len(_rows(project_root, "R")) == 6
    assert cli.main(["run", "--label", "R", "--repeats", "4"]) == 0
    assert len(_rows(project_root, "R")) == 8

    assert cli.main(["run", "--label", "R", "--repeats", "0"]) == 1


def test_compare_missing_label_fails(fake_framework, tmp_path, monkeypatch):
    project_root = tmp_path / "gauntlet-project"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(cli, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(cli, "load_project_config", lambda: fake_cfg(fake_framework))
    assert cli.main(["compare", "--labels", "does-not-exist"]) == 1


def test_run_rejects_unknown_variant(fake_framework, tmp_path, monkeypatch):
    project_root = tmp_path / "gauntlet-project"
    tasks_dir = project_root / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "01-find.yaml").write_text(
        "id: find-alpha-status\n"
        "category: find-answer\n"
        'prompt: "What is the status of Alpha?"\n'
        "checks:\n"
        "  - type: file_unchanged\n"
        "    path: Projects/Alpha/status.md\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(cli, "load_project_config", lambda: fake_cfg(fake_framework))
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path / "systemp"))

    assert cli.main(["snapshot"]) == 0
    assert cli.main(["run", "--label", "x", "--variants", "bogus"]) == 1
    results_path = project_root / "data" / "runs" / "x" / "results.jsonl"
    assert not results_path.exists()
