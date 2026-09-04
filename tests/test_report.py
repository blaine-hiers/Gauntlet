from gauntlet.lint import SectionReport
from gauntlet.report import _task_score, build_compare, build_report


def result(task_id, variant, passes, judge_score):
    return {
        "task_id": task_id,
        "category": "find-answer",
        "variant": variant,
        "duration_s": 10.0,
        "cost_usd": 0.10,
        "output_text": "…",
        "exit_code": 0,
        "is_error": False,
        "checks": [{"type": "file_exists", "path": "x", "passed": p} for p in passes],
        "judge": {"score": judge_score, "reasoning": "r"} if judge_score is not None else None,
    }


def test_build_report_summary_and_verdicts():
    results = [
        result("t1", "current", [True, False], 6),
        result("t1", "empty", [True, True], 8),   # empty beats current → flag
        result("t2", "current", [True, True], 9),
        result("t2", "empty", [False, False], 3),
    ]
    lint = [SectionReport("Ghost Section", 40, 1, ["Old-Folder/legacy.md"])]
    md = build_report(results, lint, run_label="baseline-aug")

    assert "baseline-aug" in md
    assert "| current |" in md and "| empty |" in md
    assert "t1" in md and "t2" in md
    assert "Ghost Section" in md and "Old-Folder/legacy.md" in md
    # t1 must be flagged, t2 must not
    flagged = md[md.index("## Verdicts") :]
    assert "t1" in flagged
    assert "t2" not in flagged


def test_build_report_excludes_null_judge_from_mean():
    results = [
        result("t1", "current", [True], 8),
        result("t2", "current", [True], None),
    ]
    md = build_report(results, [], run_label="x")
    assert "8.0" in md  # mean of [8], not [8, 0]


def test_task_score_blends_checks_and_judge():
    # Baseline defect: judge was ignored whenever checks existed.
    both = result("t", "current", [True, True], 2)  # checks 1.0, judge 0.2
    assert _task_score(both) == 0.6
    checks_only = result("t", "current", [True, False], None)
    assert _task_score(checks_only) == 0.5
    judge_only = result("t", "current", [], 8)
    assert _task_score(judge_only) == 0.8
    neither = result("t", "current", [], None)
    assert _task_score(neither) == 0.0


def test_build_compare_cross_model():
    def mrow(task_id, variant, model, passes, judge_score):
        r = result(task_id, variant, passes, judge_score)
        r["model"] = model
        return r

    results = [
        mrow("t1", "current", "claude-opus-5", [True], 9),
        mrow("t1", "empty", "claude-opus-5", [False], 3),
        mrow("t1", "current", "claude-haiku-4-5", [True], 5),
        mrow("t1", "empty", "claude-haiku-4-5", [True], 8),  # haiku: empty wins → flag
    ]
    md = build_compare(results, run_labels=["run-opus", "run-haiku"])

    assert "run-opus" in md and "run-haiku" in md
    assert "opus-5/current" in md and "haiku-4-5/empty" in md
    verdicts = md[md.index("## Verdicts") :]
    assert "**haiku-4-5**: 1/1 tasks did as well or better" in verdicts
    assert "**opus-5**: CLAUDE.md pulled its weight" in verdicts


def test_build_report_missing_variant_shows_na():
    # Task with current but no empty variant should show n/a in matrix, not 0.00
    # and should NOT appear in Verdicts (which requires both variants)
    results = [
        result("t1", "current", [True], 8),
        # t1 has no "empty" result
        result("t2", "current", [True, True], 9),
        result("t2", "empty", [False, False], 3),
    ]
    md = build_report(results, [], run_label="x")

    # Verify matrix has n/a for t1 empty cell
    matrix = md[md.index("## Per-Task Matrix") : md.index("## Verdicts")]
    assert "| t1 |" in matrix
    # t1's row should contain n/a for the missing empty column
    t1_row_start = matrix.index("| t1 |")
    t1_row_end = matrix.index("\n", t1_row_start)
    t1_row = matrix[t1_row_start:t1_row_end]
    assert "n/a" in t1_row

    # Verify t1 does NOT appear in Verdicts (needs both variants to flag)
    verdicts = md[md.index("## Verdicts") :]
    assert "t1" not in verdicts
    # t2 should NOT appear either (empty < current)
    assert "t2" not in verdicts
