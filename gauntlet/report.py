import math
import statistics
from collections import defaultdict
from dataclasses import dataclass


def _task_score(r: dict) -> float:
    """0-1 composite: mean of check pass fraction and judge score/10, using
    whichever components exist. A task with both checks and a judge blends
    them — checks alone previously masked judge scores (baseline defect)."""
    parts = []
    if r["checks"]:
        parts.append(sum(c["passed"] for c in r["checks"]) / len(r["checks"]))
    if r.get("judge") and r["judge"]["score"] is not None:
        parts.append(r["judge"]["score"] / 10)
    return sum(parts) / len(parts) if parts else 0.0


@dataclass(frozen=True)
class Cell:
    """One task × variant cell aggregated over its successful runs."""

    mean: float
    sd: float  # population sd of the composite; 0.0 for a single sample
    n: int


def _aggregate(scores: list[float]) -> Cell:
    sd = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    return Cell(mean=statistics.fmean(scores), sd=sd, n=len(scores))


def _fmt_cell(c: Cell) -> str:
    return f"{c.mean:.2f}" if c.n == 1 else f"{c.mean:.2f} ±{c.sd:.2f}"


def _cells(results: list[dict], key) -> tuple[dict, dict, dict]:
    """Returns (by_task, attempted, categories):
    by_task   task_id -> key(row) -> Cell, over successful rows only
    attempted task_id -> set of key(row) over every row, errors included
    Error rows are left out of cells: a timed-out run passes `file_unchanged`
    precisely because it did nothing, and would score the cell up while
    tightening its spread. `attempted` is what lets the verdict say a task was
    run but could not be compared, instead of dropping it silently."""
    samples = defaultdict(lambda: defaultdict(list))
    attempted = defaultdict(set)
    categories = {}
    for r in results:
        categories[r["task_id"]] = r["category"]
        attempted[r["task_id"]].add(key(r))
        if r.get("is_error"):
            continue
        samples[r["task_id"]][key(r)].append(_task_score(r))
    by_task = {t: {k: _aggregate(s) for k, s in ks.items()} for t, ks in samples.items()}
    return by_task, attempted, categories


def empty_beats_current(current: Cell, empty: Cell) -> bool:
    """The verdict gate. A bare `empty >= current` on single samples flagged a
    task on a 0.001 gap; with repeats, the gap has to clear the noise. The noise
    scale is the standard error of the difference of the two means, so more
    repeats make a real gap easier to confirm and a spurious one harder."""
    delta = empty.mean - current.mean
    noise = math.sqrt(current.sd**2 / current.n + empty.sd**2 / empty.n)
    if noise == 0.0:
        # No measured spread (single samples, or every repeat agreed): a tie is
        # real evidence that the file added nothing, so it flags.
        return delta >= 0.0
    return delta > noise


def is_single_sample(current: Cell, empty: Cell) -> bool:
    """One sample on either side means the gate had no spread to measure and
    fell back to tie-or-better — the report labels those verdicts unconfirmed."""
    return current.n == 1 or empty.n == 1


VERDICT_RULE = (
    "A task is flagged when the `empty` mean beats the `current` mean by more than the "
    "standard error of the difference (cells are mean ±sd over successful runs; errored "
    "runs are counted but not scored). With a single sample on either side there is no "
    "spread to measure, so a tie or better is listed as **unconfirmed** — re-run with "
    "`--repeats` to settle it. A task whose `current` or `empty` runs all errored is "
    "listed as not comparable, never as a pass."
)

UNCONFIRMED_NOTE = "single-sample; unconfirmed until re-run with --repeats"


def _split_verdicts(by_task: dict, attempted: dict, current_key, empty_key) -> tuple[list, list, list]:
    """Returns (flagged [(task, single_sample)], comparable [task], incomparable [task])."""
    flagged, comparable, incomparable = [], [], []
    for t in sorted(attempted):
        if not ({current_key, empty_key} <= attempted[t]):
            continue  # one variant was never run for this task: nothing to say
        cells = by_task.get(t, {})
        if current_key not in cells or empty_key not in cells:
            incomparable.append(t)
            continue
        comparable.append(t)
        if empty_beats_current(cells[current_key], cells[empty_key]):
            flagged.append((t, is_single_sample(cells[current_key], cells[empty_key])))
    return flagged, comparable, incomparable


def _verdict_lines(flagged: list, comparable: list, incomparable: list) -> list[str]:
    confirmed = [t for t, single in flagged if not single]
    unconfirmed = [t for t, single in flagged if single]
    blocks = []
    if confirmed:
        blocks.append(
            ["Tasks where running **without** the CLAUDE.md did as well or better — the instruction file is not earning its keep here:"]
            + [f"- {t}" for t in confirmed]
        )
    if unconfirmed:
        blocks.append(
            [f"Tasks where `empty` tied or beat `current` on a single sample ({UNCONFIRMED_NOTE}):"]
            + [f"- {t}" for t in unconfirmed]
        )
    if not flagged and comparable:
        blocks.append(["No task scored as well without the CLAUDE.md — the instruction file is pulling its weight."])
    if incomparable:
        blocks.append(
            ["Tasks **not comparable** — every `current` or every `empty` run errored, so there is no score to compare:"]
            + [f"- {t}" for t in incomparable]
        )
    if not comparable and not incomparable:
        blocks.append(["No task has both a `current` and an `empty` run to compare."])
    lines = []
    for i, block in enumerate(blocks):
        if i:
            lines.append("")
        lines += block
    return lines


def _summary_cells(rs: list[dict]) -> tuple[str, str, str, int, float]:
    ok = [r for r in rs if not r.get("is_error")]
    checks = [c for r in ok for c in r["checks"]]
    pass_rate = f"{100 * sum(c['passed'] for c in checks) / len(checks):.0f}%" if checks else "n/a"
    scores = [r["judge"]["score"] for r in ok if r.get("judge") and r["judge"]["score"] is not None]
    mean_judge = f"{sum(scores) / len(scores):.1f}" if scores else "n/a"
    errors = len(rs) - len(ok)
    cost = sum(r["cost_usd"] or 0 for r in rs)
    return pass_rate, mean_judge, str(len(rs)), errors, cost


def _lint_section(lint_reports: list) -> list[str]:
    if not lint_reports:
        return []
    lines = ["", "## Static Lint", "", "| Section | Est. tokens | Imperatives | Dead paths |", "|---|---|---|---|"]
    for s in lint_reports:
        dead = ", ".join(s.dead_paths) if s.dead_paths else "—"
        lines.append(f"| {s.heading} | {s.est_tokens} | {s.imperative_count} | {dead} |")
    return lines


def build_report(results: list[dict], lint_reports: list, run_label: str) -> str:
    models = {r["model"] for r in results if r.get("model")}
    if len(models) > 1:
        raise ValueError(
            f"build_report got rows from {len(models)} models ({', '.join(sorted(models))}); "
            "use build_compare, which keeps them apart"
        )

    lines = [f"# Gauntlet Report — {run_label}", ""]

    by_variant = defaultdict(list)
    for r in results:
        by_variant[r["variant"]].append(r)

    lines += ["## Variant Summary", "", "| Variant | Runs | Errors | Check pass rate | Mean judge score | Total cost (USD) |", "|---|---|---|---|---|---|"]
    for variant, rs in sorted(by_variant.items()):
        pass_rate, mean_judge, runs, errors, cost = _summary_cells(rs)
        lines.append(f"| {variant} | {runs} | {errors} | {pass_rate} | {mean_judge} | {cost:.2f} |")

    lines += ["", "## Per-Task Matrix", "", "| Task | Category | " + " | ".join(sorted(by_variant)) + " |", "|---" * (2 + len(by_variant)) + "|"]
    by_task, attempted, categories = _cells(results, key=lambda r: r["variant"])
    for task_id in sorted(categories):
        cells = by_task.get(task_id, {})
        row = " | ".join(_fmt_cell(cells[v]) if v in cells else "n/a" for v in sorted(by_variant))
        lines.append(f"| {task_id} | {categories[task_id]} | {row} |")

    lines += ["", "## Verdicts", "", VERDICT_RULE, ""]
    lines += _verdict_lines(*_split_verdicts(by_task, attempted, "current", "empty"))
    lines += _lint_section(lint_reports)

    return "\n".join(lines) + "\n"


def _short_model(model: str) -> str:
    return model.removeprefix("claude-")


def build_compare(results: list[dict], run_labels: list[str], lint_reports: list = ()) -> str:
    """Cross-model report: every result row must carry a 'model' key."""
    lines = [f"# Gauntlet Model Comparison — {', '.join(run_labels)}", ""]
    labels = sorted({r["label"] for r in results if r.get("label")}) or list(run_labels)
    if len(labels) > 1:
        # Rows from several labels share a cell. Say so where the numbers are:
        # the ±sd then spans labels, not only repeats within one.
        lines += [
            f"Cells pool every run for a task × model × variant across {len(labels)} labels "
            f"({', '.join(labels)}), so a cell's spread includes between-label differences.",
            "",
        ]

    models = sorted({r["model"] for r in results})
    by_cell = defaultdict(list)  # (model, variant) -> rows
    for r in results:
        by_cell[(r["model"], r["variant"])].append(r)
    pairs = sorted(by_cell)

    lines += [
        "## Summary (model × variant)",
        "",
        "| Model | Variant | Runs | Errors | Check pass rate | Mean judge score | Total cost (USD) |",
        "|---|---|---|---|---|---|---|",
    ]
    for model, variant in pairs:
        pass_rate, mean_judge, runs, errors, cost = _summary_cells(by_cell[(model, variant)])
        lines.append(
            f"| {_short_model(model)} | {variant} | {runs} | {errors} | {pass_rate} | {mean_judge} | {cost:.2f} |"
        )

    col_names = [f"{_short_model(m)}/{v}" for m, v in pairs]
    lines += [
        "",
        "## Per-Task Matrix (composite score 0–1)",
        "",
        "| Task | Category | " + " | ".join(col_names) + " |",
        "|---" * (2 + len(pairs)) + "|",
    ]
    by_task, attempted, categories = _cells(results, key=lambda r: (r["model"], r["variant"]))
    for task_id in sorted(categories):
        cells = by_task.get(task_id, {})
        row = " | ".join(_fmt_cell(cells[p]) if p in cells else "n/a" for p in pairs)
        lines.append(f"| {task_id} | {categories[task_id]} | {row} |")

    lines += ["", "## Verdicts (per model)", "", VERDICT_RULE, ""]
    for model in models:
        flagged, comparable, incomparable = _split_verdicts(
            by_task, attempted, (model, "current"), (model, "empty")
        )
        name = _short_model(model)
        if not comparable and not incomparable:
            lines.append(f"- **{name}**: no current/empty pairs to compare")
        elif flagged:
            names = ", ".join(f"{t} (unconfirmed)" if single else t for t, single in flagged)
            note = f" — {UNCONFIRMED_NOTE}" if any(single for _, single in flagged) else ""
            lines.append(
                f"- **{name}**: {len(flagged)}/{len(comparable)} tasks did as well "
                f"or better without the CLAUDE.md — {names}{note}"
            )
        elif comparable:
            lines.append(
                f"- **{name}**: CLAUDE.md pulled its weight on all "
                f"{len(comparable)} comparable tasks"
            )
        if incomparable:
            lines.append(
                f"- **{name}**: not comparable (every `current` or every `empty` run errored) — "
                f"{', '.join(incomparable)}"
            )
    lines += _lint_section(lint_reports)

    return "\n".join(lines) + "\n"
