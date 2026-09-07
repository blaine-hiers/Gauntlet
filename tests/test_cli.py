import dataclasses
import json
import tempfile
import threading
import time
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


def test_run_retry_errors_reruns_only_errored_cells(fake_framework, tmp_path, monkeypatch):
    # Review finding: an all-error cell was in the resume set but out of the
    # report, so the identical command skipped it forever.
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    calls = []

    def flaky_execute(task, variant, run_dir, cfg):
        calls.append(variant.name)
        err = variant.name == "empty" and len(calls) < 3  # first `empty` run times out
        return {
            "task_id": task.id, "category": task.category, "variant": variant.name,
            "model": cfg.model, "duration_s": 1.0, "cost_usd": None if err else 0.01,
            "output_text": "", "exit_code": None if err else 0, "is_error": err,
        }

    monkeypatch.setattr(cli, "execute", flaky_execute)
    assert cli.main(["run", "--label", "E"]) == 0
    assert [r["is_error"] for r in _rows(project_root, "E")] == [False, True]

    assert cli.main(["run", "--label", "E"]) == 0  # default: errored cell stays skipped
    assert len(_rows(project_root, "E")) == 2

    assert cli.main(["run", "--label", "E", "--retry-errors"]) == 0
    rows = _rows(project_root, "E")
    assert len(rows) == 3 and calls == ["current", "empty", "empty"]  # only `empty` re-ran
    assert [r["is_error"] for r in rows if r["variant"] == "empty"] == [True, False]


def test_compare_missing_label_fails(fake_framework, tmp_path, monkeypatch):
    project_root = tmp_path / "gauntlet-project"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(cli, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(cli, "load_project_config", lambda: fake_cfg(fake_framework))
    assert cli.main(["compare", "--labels", "does-not-exist"]) == 1


def test_run_stamps_snapshot_provenance_and_judge_cost(fake_framework, tmp_path, monkeypatch):
    project_root = tmp_path / "gauntlet-project"
    tasks_dir = project_root / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "01-find.yaml").write_text(
        "id: find-alpha-status\n"
        "category: find-answer\n"
        'prompt: "What is the status of Alpha?"\n'
        "judge:\n"
        '  rubric: "Must say on track."\n',
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

    seen_samples = []

    def fake_judge(output_text, judge_spec, judge_model, samples=1, **kwargs):
        seen_samples.append(samples)
        return {"score": 9, "reasoning": "good", "cost_usd": 0.03, "raw_reply": "{}", "degraded": False}

    monkeypatch.setattr(cli, "execute", fake_execute)
    monkeypatch.setattr(cli, "judge_output", fake_judge)
    assert cli.main(["snapshot"]) == 0
    assert cli.main(["run", "--label", "j", "--judge-samples", "3"]) == 0

    rows = _rows(project_root, "j")
    assert all(s == 3 for s in seen_samples)
    assert all(row["judge_cost_usd"] == 0.03 for row in rows)
    assert all(row["snapshot"]["file_count"] > 0 for row in rows)
    assert all("manifest_hash" in row["snapshot"] for row in rows)


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


def test_run_concurrency_lands_every_cell_exactly_once(fake_framework, tmp_path, monkeypatch):
    # The whole risk of --concurrency is a cell writing twice or not at all:
    # the grid fans out, the rows come back interleaved, and results.jsonl is a
    # shared append. Stub the executor so the grid is the only thing under test.
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)

    calls = []
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def fake_execute(task, variant, run_dir, cfg):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.02)  # long enough that the pool actually overlaps cells
        with lock:
            live["now"] -= 1
            calls.append((task.id, variant.name))
        return {
            "task_id": task.id, "category": task.category, "variant": variant.name,
            "model": cfg.model, "duration_s": 1.0, "cost_usd": 0.01,
            "output_text": "Alpha is on track", "exit_code": 0, "is_error": False,
        }

    monkeypatch.setattr(cli, "execute", fake_execute)

    assert cli.main(["run", "--label", "C", "--repeats", "3", "--concurrency", "4"]) == 0

    rows = _rows(project_root, "C")
    keys = [(r["task_id"], r["variant"], r["model"], r["repeat_idx"]) for r in rows]
    # 1 task x 2 variants x 3 repeats, each written exactly once.
    assert len(rows) == 6
    assert len(set(keys)) == 6
    assert sorted(k[3] for k in keys) == [0, 0, 1, 1, 2, 2]
    assert len(calls) == 6
    # If this is 1 the pool never overlapped and the test proved nothing.
    assert live["peak"] > 1

    # Every row is intact JSON: a torn line is what an unguarded concurrent
    # append produces, and it would still parse as "some" rows without this.
    jsonl = project_root / "data" / "runs" / "C" / "results.jsonl"
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6
    for line in lines:
        json.loads(line)


def test_run_concurrency_still_resumes(fake_framework, tmp_path, monkeypatch):
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    assert cli.main(["run", "--label", "R", "--repeats", "2", "--concurrency", "3"]) == 0
    assert len(_rows(project_root, "R")) == 4
    # Same grid again adds nothing: the resume key is unchanged by fan-out.
    assert cli.main(["run", "--label", "R", "--repeats", "2", "--concurrency", "3"]) == 0
    assert len(_rows(project_root, "R")) == 4


def test_run_rejects_concurrency_below_one(fake_framework, tmp_path, monkeypatch, capsys):
    _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    assert cli.main(["run", "--label", "Z", "--concurrency", "0"]) == 1
    assert "--concurrency must be at least 1" in capsys.readouterr().out


def test_run_fans_out_over_configured_providers(fake_framework, tmp_path, monkeypatch):
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    cfg = dataclasses.replace(fake_cfg(fake_framework), providers={
        "fast": {"model": "claude-haiku-4-5"},
        "deep": {"model": "claude-opus-5"},
    })
    monkeypatch.setattr(cli, "load_project_config", lambda: cfg)

    seen_dirs = []

    def fake_execute(task, variant, run_dir, config):
        seen_dirs.append(str(run_dir))
        return {
            "task_id": task.id, "category": task.category, "variant": variant.name,
            "model": config.model, "duration_s": 1.0, "cost_usd": 0.01,
            "output_text": "Alpha is on track", "exit_code": 0, "is_error": False,
        }

    monkeypatch.setattr(cli, "execute", fake_execute)

    assert cli.main(["run", "--label", "P"]) == 0
    rows = _rows(project_root, "P")
    # 2 providers x 1 task x 2 variants
    assert len(rows) == 4
    assert {r["provider"] for r in rows} == {"fast", "deep"}
    assert {r["model"] for r in rows} == {"claude-haiku-4-5", "claude-opus-5"}
    # Two providers must never share a run directory, or one would be scored
    # against the other's leftovers.
    assert len(set(seen_dirs)) == 4


def test_run_resume_key_is_provider_aware(fake_framework, tmp_path, monkeypatch):
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    assert cli.main(["run", "--label", "PR"]) == 0
    assert len(_rows(project_root, "PR")) == 2

    # Rows already on disk carry provider "claude" (the implicit name), so a
    # re-run under the same implicit provider adds nothing.
    assert cli.main(["run", "--label", "PR"]) == 0
    assert len(_rows(project_root, "PR")) == 2

    # A differently *named* provider on the same model is a genuinely new cell.
    cfg = dataclasses.replace(fake_cfg(fake_framework), providers={"deep": {}})
    monkeypatch.setattr(cli, "load_project_config", lambda: cfg)
    assert cli.main(["run", "--label", "PR"]) == 0
    rows = _rows(project_root, "PR")
    assert len(rows) == 4
    assert {r.get("provider") for r in rows} == {"claude", "deep"}


def test_run_rejects_an_unknown_provider_kind(fake_framework, tmp_path, monkeypatch, capsys):
    _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    cfg = dataclasses.replace(
        fake_cfg(fake_framework), providers={"local": {"kind": "openai-compatible"}}
    )
    monkeypatch.setattr(cli, "load_project_config", lambda: cfg)
    assert cli.main(["run", "--label", "U"]) == 1
    assert "unknown kind" in capsys.readouterr().out


def test_ablate_generates_a_variant_per_section_and_reports_deltas(
    fake_framework, tmp_path, monkeypatch
):
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)

    assert cli.main(["ablate", "--label", "A", "--yes"]) == 0

    out_dir = project_root / "data" / "runs" / "A"
    written = sorted(p.name for p in (out_dir / "ablation-variants").iterdir())
    assert written == [
        "minus-filing.md", "minus-framework-rules.md", "minus-ghost-section.md",
    ]
    # Removing a ## takes only its own span.
    filing = (out_dir / "ablation-variants" / "minus-filing.md").read_text(encoding="utf-8")
    assert "## Filing" not in filing and "## Ghost Section" in filing
    # The single top-level heading spans the file, so ablating it empties it.
    assert (out_dir / "ablation-variants" / "minus-framework-rules.md").read_text(
        encoding="utf-8"
    ) == ""

    rows = _rows(project_root, "A")
    # current + empty + 3 minus-variants, one task, one repeat
    assert len(rows) == 5
    assert {r["variant"] for r in rows} == {
        "current", "empty", "minus-filing", "minus-framework-rules", "minus-ghost-section",
    }

    report = (out_dir / "ablation.md").read_text(encoding="utf-8")
    assert "# Gauntlet Section Ablation" in report
    assert "Ghost Section" in report and "Filing" in report


def test_ablate_max_depth_limits_the_grid(fake_framework, tmp_path, monkeypatch):
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    assert cli.main(["ablate", "--label", "D", "--max-depth", "1", "--yes"]) == 0
    written = sorted(
        p.name for p in (project_root / "data" / "runs" / "D" / "ablation-variants").iterdir()
    )
    assert written == ["minus-framework-rules.md"]


def test_ablate_asks_before_a_large_grid_and_aborts_without_a_yes(
    fake_framework, tmp_path, monkeypatch, capsys
):
    # No stdin under pytest, so the prompt reads EOF, which must be a refusal
    # rather than an accident that spends money.
    project_root = _project_with_one_task(tmp_path, monkeypatch, fake_framework)
    assert cli.main(["ablate", "--label", "T", "--threshold", "1"]) == 1
    out = capsys.readouterr().out
    assert "5 cells" in out and "threshold" in out and "aborted" in out
    assert not (project_root / "data" / "runs" / "T" / "results.jsonl").exists()


def test_ablate_needs_a_snapshot(fake_framework, tmp_path, monkeypatch, capsys):
    project_root = tmp_path / "gauntlet-project"
    (project_root / "tasks").mkdir(parents=True)
    monkeypatch.setattr(cli, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(cli, "load_project_config", lambda: fake_cfg(fake_framework))
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path / "systemp"))
    assert cli.main(["ablate", "--label", "N"]) == 1
    assert "run `snapshot` first" in capsys.readouterr().out
