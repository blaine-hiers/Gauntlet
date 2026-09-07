from gauntlet.ablate import (
    build_ablation_report,
    remove_section,
    split_sections,
    variant_name,
)
from gauntlet.lint import SectionReport

DOC = """# Title

Intro.

## Alpha

alpha body

### Alpha child

child body

## Beta

beta body
"""


def test_a_section_spans_its_nested_children():
    # The one genuinely fiddly piece: lint's splitter is flat, so removing a ##
    # there would orphan its ### children and score a file nobody asked for.
    secs = {s.heading: s for s in split_sections(DOC, max_depth=2)}
    alpha = secs["Alpha"]
    assert alpha.level == 2
    out = remove_section(DOC, alpha)
    assert "alpha body" not in out
    assert "Alpha child" not in out and "child body" not in out
    # and nothing else moved
    assert "## Beta" in out and "beta body" in out and "Intro." in out


def test_max_depth_controls_what_is_offered():
    assert [s.slug for s in split_sections(DOC, max_depth=2)] == ["title", "alpha", "beta"]
    assert [s.slug for s in split_sections(DOC, max_depth=3)] == [
        "title", "alpha", "alpha-child", "beta",
    ]
    assert [s.slug for s in split_sections(DOC, max_depth=1)] == ["title"]


def test_a_top_level_section_takes_the_whole_document():
    (title,) = split_sections(DOC, max_depth=1)
    assert remove_section(DOC, title) == ""


def test_repeated_headings_get_distinct_slugs():
    doc = "## Notes\n\na\n\n## Other\n\nb\n\n## Notes\n\nc\n"
    assert [s.slug for s in split_sections(doc, max_depth=2)] == ["notes", "other", "notes-2"]


def test_variant_names_are_prefixed():
    (s,) = split_sections("## Data Handling\n\nx\n", max_depth=2)
    assert variant_name(s) == "minus-data-handling"


def _row(task_id, variant, passes, judge_score):
    return {
        "task_id": task_id,
        "category": "find-answer",
        "variant": variant,
        "is_error": False,
        "checks": [{"type": "file_exists", "path": "x", "passed": p} for p in passes],
        "judge": {"score": judge_score, "reasoning": "r"},
    }


def _rows_for(variant, scores):
    # Two repeats per task so the standard-error gate has spread to work with.
    out = []
    for task_id, judge in scores.items():
        for _ in range(2):
            out.append(_row(task_id, variant, [True], judge))
    return out


def test_a_section_whose_removal_changes_nothing_is_a_cut_candidate():
    sections = split_sections(DOC, max_depth=2)
    results = []
    results += _rows_for("current", {"t1": 6, "t2": 6})
    # minus-alpha does just as well; minus-beta collapses.
    results += _rows_for("minus-alpha", {"t1": 6, "t2": 6})
    results += _rows_for("minus-beta", {"t1": 1, "t2": 1})
    results += _rows_for("minus-title", {"t1": 6, "t2": 6})

    md = build_ablation_report(results, sections, run_label="L")

    assert "Baseline (`current`, the full file) scores" in md
    alpha_line = [l for l in md.splitlines() if l.startswith("| Alpha ")][0]
    beta_line = [l for l in md.splitlines() if l.startswith("| Beta ")][0]
    assert "cut candidate" in alpha_line
    assert "+0.00" in alpha_line
    assert "earns its tokens" in beta_line
    assert "-0." in beta_line  # removing Beta cost real score


def test_lint_columns_sit_beside_the_measured_delta():
    sections = split_sections(DOC, max_depth=2)
    results = _rows_for("current", {"t1": 6}) + _rows_for("minus-alpha", {"t1": 6})
    lint = [SectionReport(heading="Alpha", est_tokens=999, imperative_count=7, dead_paths=["a", "b"])]

    md = build_ablation_report(results, sections, lint, run_label="L")
    alpha_line = [l for l in md.splitlines() if l.startswith("| Alpha ")][0]
    assert "| 7 |" in alpha_line       # imperatives from lint
    assert "| 2 |" in alpha_line       # dead path count from lint


def test_a_section_never_run_is_reported_rather_than_dropped():
    sections = split_sections(DOC, max_depth=2)
    results = _rows_for("current", {"t1": 6})  # no minus-* rows at all
    md = build_ablation_report(results, sections, run_label="L")
    assert md.count("not run") == len(sections)


def test_single_sample_comparisons_are_called_out():
    sections = split_sections(DOC, max_depth=2)
    results = [_row("t1", "current", [True], 6), _row("t1", "minus-alpha", [True], 6)]
    md = build_ablation_report(results, sections, run_label="L")
    assert "single sample" in md and "--repeats" in md


def test_no_baseline_is_said_plainly():
    sections = split_sections(DOC, max_depth=2)
    md = build_ablation_report(_rows_for("minus-alpha", {"t1": 6}), sections)
    assert "No `current` rows" in md
