from gauntlet.lint import SectionReport
from gauntlet.report import (
    Cell,
    _task_score,
    build_compare,
    build_report,
    empty_beats_current,
)


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


def test_verdict_gate_requires_margin_beyond_noise():
    # Plan's worked case: empty repeats 0.4 and 0.6 (mean 0.5, sd 0.1) against a
    # current of 0.5 — a coin flip, must not flag. Empty at 0.9/0.9 must.
    current = Cell(mean=0.5, sd=0.0, n=2)
    assert not empty_beats_current(current, Cell(mean=0.5, sd=0.1, n=2))
    assert empty_beats_current(current, Cell(mean=0.9, sd=0.0, n=2))
    # A gap that clears the standard error of the difference flags.
    assert empty_beats_current(Cell(0.5, 0.1, 4), Cell(0.7, 0.1, 4))
    assert not empty_beats_current(Cell(0.5, 0.1, 4), Cell(0.55, 0.1, 4))
    # Single samples carry no spread: the old "as well or better" rule stands.
    assert empty_beats_current(Cell(0.5, 0.0, 1), Cell(0.5, 0.0, 1))
    assert not empty_beats_current(Cell(0.5, 0.0, 1), Cell(0.49, 0.0, 1))


def test_aggregate_uses_sample_sd_so_gate_matches_documented_rule():
    from gauntlet.report import _aggregate

    # Review repro: with population sd the noise scale came out sqrt((n-1)/n)
    # too small and a 0.09 gap flagged against a documented threshold of 0.10.
    current = _aggregate([0.5, 0.5])
    empty = _aggregate([0.49, 0.69])
    assert abs(empty.sd - 0.1414) < 1e-3
    assert not empty_beats_current(current, empty)


def _repeat(task_id, variant, judge_score, idx):
    r = result(task_id, variant, [], judge_score)
    r["repeat_idx"] = idx
    return r


def test_build_report_aggregates_repeats():
    results = [
        _repeat("t1", "current", 5, 0), _repeat("t1", "current", 5, 1),
        _repeat("t1", "empty", 4, 0), _repeat("t1", "empty", 6, 1),   # noisy tie → no flag
        _repeat("t2", "current", 5, 0), _repeat("t2", "current", 5, 1),
        _repeat("t2", "empty", 9, 0), _repeat("t2", "empty", 9, 1),   # clear win → flag
    ]
    md = build_report(results, [], run_label="rep")
    summary = md[md.index("## Variant Summary") : md.index("## Per-Task Matrix")]
    assert "| current | 4 |" in summary and "| empty | 4 |" in summary
    matrix = md[md.index("## Per-Task Matrix") : md.index("## Verdicts")]
    assert "| t1 | find-answer | 0.50 ±0.00 | 0.50 ±0.14 |" in matrix  # sample sd of 0.4/0.6
    verdicts = md[md.index("## Verdicts") :]
    assert "- t2" in verdicts
    assert "- t1" not in verdicts


def test_build_report_single_sample_verdicts_are_unconfirmed():
    results = [
        result("t1", "current", [True, False], 6),
        result("t1", "empty", [True, True], 8),
    ]
    md = build_report(results, [], run_label="x")
    verdicts = md[md.index("## Verdicts") :]
    assert "- t1" in verdicts
    assert "unconfirmed" in verdicts
    assert "not earning its keep" not in verdicts  # that sentence is for confirmed flags only


def test_build_report_excludes_error_rows_from_cells():
    # A timed-out run passes file_unchanged because it did nothing; folding it
    # into the cell would raise the mean and shrink the spread.
    ok = _repeat("t1", "empty", 2, 0)
    err = _repeat("t1", "empty", None, 1)
    err["is_error"] = True
    err["checks"] = [{"type": "file_unchanged", "path": "x", "passed": True}]
    results = [_repeat("t1", "current", 8, 0), ok, err]
    md = build_report(results, [], run_label="e")
    summary = md[md.index("## Variant Summary") : md.index("## Per-Task Matrix")]
    assert "| empty | 2 | 1 |" in summary  # 2 runs, 1 error
    matrix = md[md.index("## Per-Task Matrix") : md.index("## Verdicts")]
    assert "| t1 | find-answer | 0.80 | 0.20 |" in matrix  # error row not averaged in

    all_err = [_repeat("t2", "current", 8, 0)]
    e = _repeat("t2", "empty", None, 0)
    e["is_error"] = True
    md = build_report(all_err + [e], [], run_label="e2")
    assert "| t2 | find-answer | 0.80 | n/a |" in md


def test_build_report_all_error_current_is_not_a_pass():
    # Regression from excluding error rows: a task whose `current` runs all
    # errored used to vanish from the verdict, and the report then printed
    # "pulling its weight" with nothing to base it on.
    rows = []
    for i in range(3):
        e = _repeat("t1", "current", None, i)
        e["is_error"] = True
        rows.append(e)
        rows.append(_repeat("t1", "empty", 10, i))
    md = build_report(rows, [], run_label="err")
    verdicts = md[md.index("## Verdicts") :]
    assert "pulling its weight" not in verdicts
    assert "not comparable" in verdicts and "- t1" in verdicts

    # The same shape per model in compare.
    for r in rows:
        r["model"] = "claude-opus-5"
    md = build_compare(rows, run_labels=["err"])
    assert "**opus-5**: not comparable" in md and "t1" in md
    assert "pulled its weight" not in md


def test_build_compare_notes_label_pooling_and_keeps_lint():
    from gauntlet.lint import SectionReport as SR

    a = result("t1", "current", [True], 8)
    a.update(model="claude-opus-5", label="run-a")
    b = result("t1", "current", [True], 6)
    b.update(model="claude-opus-5", label="run-b")
    md = build_compare([a, b], run_labels=["run-a", "run-b"], lint_reports=[SR("Ghost", 40, 1, [])])
    assert "across 2 labels (run-a, run-b)" in md
    assert "0.85 ±0.07" in md  # composites 0.9 and 0.8, pooled across the two labels
    assert "## Static Lint" in md and "Ghost" in md

    md_one = build_compare([a], run_labels=["run-a"])
    assert "across" not in md_one


def test_build_report_refuses_mixed_models():
    import pytest

    a = result("t1", "current", [True], 8)
    a["model"] = "claude-opus-5"
    b = result("t1", "empty", [True], 8)
    b["model"] = "claude-haiku-4-5"
    with pytest.raises(ValueError, match="build_compare"):
        build_report([a, b], [], run_label="x")


def test_build_compare_aggregates_repeats():
    def mrow(task_id, variant, model, judge_score, idx):
        r = _repeat(task_id, variant, judge_score, idx)
        r["model"] = model
        return r

    results = [
        mrow("t1", "current", "claude-opus-5", 5, 0), mrow("t1", "current", "claude-opus-5", 5, 1),
        mrow("t1", "empty", "claude-opus-5", 4, 0), mrow("t1", "empty", "claude-opus-5", 6, 1),
    ]
    md = build_compare(results, run_labels=["r"])
    assert "| opus-5 | current | 2 |" in md
    assert "0.50 ±0.14" in md
    assert "**opus-5**: CLAUDE.md pulled its weight" in md


def test_build_report_score_per_dollar_and_turn():
    r = result("t1", "current", [True, True], 8)  # composite 0.9
    r["cost_usd"] = 0.10
    r["num_turns"] = 5
    md = build_report([r], [], run_label="x")
    summary = md[md.index("## Variant Summary") : md.index("## Per-Task Matrix")]
    assert "9.00" in summary  # 0.9 / 0.10
    assert "0.180" in summary  # 0.9 / 5


def test_build_report_score_per_dollar_and_turn_are_scale_invariant():
    # Review finding: mean(composite) / sum(cost) carries a spurious 1/n factor —
    # doubling identical rows halved the reported efficiency instead of leaving
    # it unchanged. sum(composite) / sum(cost) must give the same figure either way.
    one = result("t1", "current", [True, True], 8)  # composite 0.9
    one["cost_usd"] = 0.10
    one["num_turns"] = 5
    md_one = build_report([one], [], run_label="one")
    summary_one = md_one[md_one.index("## Variant Summary") : md_one.index("## Per-Task Matrix")]
    assert "9.00" in summary_one and "0.180" in summary_one

    two = [result("t1", "current", [True, True], 8) for _ in range(2)]
    for r, idx in zip(two, range(2)):
        r["cost_usd"] = 0.10
        r["num_turns"] = 5
        r["repeat_idx"] = idx
    md_two = build_report(two, [], run_label="two")
    summary_two = md_two[md_two.index("## Variant Summary") : md_two.index("## Per-Task Matrix")]
    assert "9.00" in summary_two and "0.180" in summary_two


def test_build_report_score_per_dollar_excludes_errored_rows_from_both_sides():
    # An errored row has no composite (excluded from the numerator) and must
    # also be excluded from the cost/turn denominators, or the ratio would be
    # diluted by spend attributed to a run that produced no score.
    ok = result("t1", "current", [True, True], 8)  # composite 0.9
    ok["cost_usd"] = 0.10
    ok["num_turns"] = 5
    err = result("t1", "current", [True], None)
    err["is_error"] = True
    err["cost_usd"] = 5.00  # large errored spend that must not dilute the ratio
    err["num_turns"] = 30
    md = build_report([ok, err], [], run_label="e")
    summary = md[md.index("## Variant Summary") : md.index("## Per-Task Matrix")]
    assert "9.00" in summary and "0.180" in summary


def test_build_report_efficiency_skips_rows_missing_the_denominator():
    # PR review finding: a legacy row with no num_turns counted as 0 turns in
    # the denominator while its whole composite stayed in the numerator, so
    # mixing old and new rows inflated Score/turn. Same rule for cost.
    legacy = result("t1", "current", [True, True], 10)  # composite 1.0, no num_turns
    legacy.pop("cost_usd")
    new = result("t2", "current", [True, False], 5)  # composite 0.5
    new["cost_usd"] = 0.10
    new["num_turns"] = 5
    md = build_report([legacy, new], [], run_label="mix")
    summary = md[md.index("## Variant Summary") : md.index("## Per-Task Matrix")]
    assert "0.100" in summary and "0.300" not in summary  # 0.5 / 5, not 1.5 / 5
    assert "5.00" in summary and "15.00" not in summary  # 0.5 / 0.10, not 1.5 / 0.10


def test_build_report_no_cost_or_turns_is_na():
    r = result("t1", "current", [True], 8)
    r["cost_usd"] = None
    md = build_report([r], [], run_label="x")
    summary = md[md.index("## Variant Summary") : md.index("## Per-Task Matrix")]
    assert "n/a" in summary


def test_build_report_surfaces_degraded_judge_rows():
    ok = result("t1", "current", [True], 8)
    ok["judge"]["degraded"] = False
    bad = result("t1", "empty", [True], None)
    bad["judge"] = {"score": None, "reasoning": "unparseable judge reply: garbage", "degraded": True}
    md = build_report([ok, bad], [], run_label="x")
    assert "## Degraded Judge Rows" in md
    section = md[md.index("## Degraded Judge Rows") :]
    assert "t1" in section and "empty" in section and "unparseable" in section

    # A run with no degraded rows omits the section entirely.
    md_clean = build_report([ok], [], run_label="y")
    assert "## Degraded Judge Rows" not in md_clean


def test_build_report_judge_cost_kept_separate_from_total_cost():
    r = result("t1", "current", [True], 8)
    r["cost_usd"] = 1.00
    r["judge_cost_usd"] = 0.25
    md = build_report([r], [], run_label="x")
    summary = md[md.index("## Variant Summary") : md.index("## Per-Task Matrix")]
    assert "1.00" in summary and "0.25" in summary


def test_build_report_per_category_breakdown():
    a = result("t1", "current", [True], 8)
    a["category"] = "find-answer"
    b = result("t2", "current", [True, False], None)
    b["category"] = "file-organize"
    md = build_report([a, b], [], run_label="x")
    assert "## Per-Category Summary" in md
    section = md[md.index("## Per-Category Summary") :]
    assert "find-answer" in section and "file-organize" in section


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


def _prow(task_id, variant, model, provider, kind, passes, judge_score):
    r = result(task_id, variant, passes, judge_score)
    r["model"] = model
    r["provider"] = provider
    r["provider_kind"] = kind
    return r


def test_compare_without_a_provider_field_reads_as_the_implicit_provider():
    # Rows written before the provider axis existed must land in the cells they
    # always did, and the report must look exactly as it did.
    rows = []
    for variant, passes, score in (("current", [True], 9), ("empty", [False], 3)):
        r = result("t1", variant, passes, score)
        r["model"] = "claude-opus-5"
        rows.append(r)
    md = build_compare(rows, run_labels=["r"])

    assert "opus-5/current" in md            # no provider prefix on the columns
    assert "claude/opus-5/current" not in md
    assert "scaffold-confounded" not in md   # one kind, nothing to warn about
    assert "## Verdicts (per model)" in md
    assert "| claude |" in md                # but the column still names it


def test_compare_across_provider_kinds_labels_the_rows_scaffold_confounded():
    rows = [
        _prow("t1", "current", "claude-opus-5", "claude", "claude-cli", [True], 9),
        _prow("t1", "empty", "claude-opus-5", "claude", "claude-cli", [False], 3),
        _prow("t1", "current", "qwen3", "local", "openai-compatible", [True], 5),
        _prow("t1", "empty", "qwen3", "local", "openai-compatible", [True], 8),
    ]
    md = build_compare(rows, run_labels=["r"])

    assert "scaffold-confounded" in md
    assert "claude-cli" in md and "openai-compatible" in md
    # The columns and the verdicts both carry the provider once there are two.
    assert "local/qwen3/current" in md
    assert "claude/opus-5/current" in md
    assert "## Verdicts (per provider × model)" in md
    verdicts = md[md.index("## Verdicts") :]
    assert "**local/qwen3**" in verdicts and "**claude/opus-5**" in verdicts


def test_two_providers_of_one_kind_are_not_called_scaffold_confounded():
    # Same scaffold, two names. Comparing them is legitimate, so no warning.
    rows = [
        _prow("t1", "current", "claude-opus-5", "deep", "claude-cli", [True], 9),
        _prow("t1", "empty", "claude-opus-5", "deep", "claude-cli", [False], 3),
        _prow("t1", "current", "claude-haiku-4-5", "fast", "claude-cli", [True], 5),
        _prow("t1", "empty", "claude-haiku-4-5", "fast", "claude-cli", [True], 8),
    ]
    md = build_compare(rows, run_labels=["r"])
    assert "scaffold-confounded" not in md
    assert "fast/haiku-4-5/current" in md
