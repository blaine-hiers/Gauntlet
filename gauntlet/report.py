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
    """One task × variant cell aggregated over its successful repeats."""

    mean: float
    sd: float  # population sd of the composite; 0.0 for a single sample
    n: int


def _aggregate(scores: list[float]) -> Cell:
    sd = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    return Cell(mean=statistics.fmean(scores), sd=sd, n=len(scores))


def _fmt_cell(c: Cell) -> str:
    return f"{c.mean:.2f}" if c.n == 1 else f"{c.mean:.2f} ±{c.sd:.2f}"


def _cells(results: list[dict], key) -> tuple[dict, dict]:
    """task_id -> key(row) -> Cell, plus task_id -> category. Error rows are
    left out: a timed-out run passes `file_unchanged` precisely because it did
    nothing, and would score the cell up while tightening its spread."""
    samples = defaultdict(lambda: defaultdict(list))
    categories = {}
    for r in results:
        categories[r["task_id"]] = r["category"]
        if r.get("is_error"):
            continue
        samples[r["task_id"]][key(r)].append(_task_score(r))
    by_task = {t: {k: _aggregate(s) for k, s in ks.items()} for t, ks in samples.items()}
    return by_task, categories


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
    "standard error of the difference (cells are mean ±sd over successful repeats). "
    "With a single sample on either side there is no spread to measure, so a tie or "
    "better is listed as **unconfirmed** — re-run with `--repeats` to settle it."
)

UNCONFIRMED_NOTE = "single-sample; unconfirmed until re-run with --repeats"


def _verdict_lines(flagged: list[tuple[str, bool]]) -> list[str]:
    """`flagged` is [(task_id, single_sample)]."""
    confirmed = [t for t, single in flagged if not single]
    unconfirmed = [t for t, single in flagged if single]
    lines = []
    if confirmed:
        lines.append("Tasks where running **without** the CLAUDE.md did as well or better — the instruction file is not earning its keep here:")
        lines += [f"- {t}" for t in confirmed]
    if unconfirmed:
        if confirmed:
            lines.append("")
        lines.append(f"Tasks where `empty` tied or beat `current` on a single sample ({UNCONFIRMED_NOTE}):")
        lines += [f"- {t}" for t in unconfirmed]
    if not flagged:
        lines.append("No task scored as well without the CLAUDE.md — the instruction file is pulling its weight.")
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
    by_task, categories = _cells(results, key=lambda r: r["variant"])
    for task_id in sorted(categories):
        cells = by_task.get(task_id, {})
        row = " | ".join(_fmt_cell(cells[v]) if v in cells else "n/a" for v in sorted(by_variant))
        lines.append(f"| {task_id} | {categories[task_id]} | {row} |")

    lines += ["", "## Verdicts", "", VERDICT_RULE, ""]
    flagged = [
        (t, is_single_sample(cells["current"], cells["empty"]))
        for t, cells in sorted(by_task.items())
        if "current" in cells and "empty" in cells
        and empty_beats_current(cells["current"], cells["empty"])
    ]
    lines += _verdict_lines(flagged)

    if lint_reports:
        lines += ["", "## Static Lint", "", "| Section | Est. tokens | Imperatives | Dead paths |", "|---|---|---|---|"]
        for s in lint_reports:
            dead = ", ".join(s.dead_paths) if s.dead_paths else "—"
            lines.append(f"| {s.heading} | {s.est_tokens} | {s.imperative_count} | {dead} |")

    return "\n".join(lines) + "\n"


def _short_model(model: str) -> str:
    return model.removeprefix("claude-")


def build_compare(results: list[dict], run_labels: list[str]) -> str:
    """Cross-model report: every result row must carry a 'model' key."""
    lines = [f"# Gauntlet Model Comparison — {', '.join(run_labels)}", ""]

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
    by_task, categories = _cells(results, key=lambda r: (r["model"], r["variant"]))
    for task_id in sorted(categories):
        cells = by_task.get(task_id, {})
        row = " | ".join(_fmt_cell(cells[p]) if p in cells else "n/a" for p in pairs)
        lines.append(f"| {task_id} | {categories[task_id]} | {row} |")

    lines += ["", "## Verdicts (per model)", "", VERDICT_RULE, ""]
    for model in models:
        comparable = [
            t for t, cells in sorted(by_task.items())
            if (model, "current") in cells and (model, "empty") in cells
        ]
        flagged = [
            (t, is_single_sample(by_task[t][(model, "current")], by_task[t][(model, "empty")]))
            for t in comparable
            if empty_beats_current(by_task[t][(model, "current")], by_task[t][(model, "empty")])
        ]
        if not comparable:
            lines.append(f"- **{_short_model(model)}**: no current/empty pairs to compare")
        elif flagged:
            names = ", ".join(f"{t} (unconfirmed)" if single else t for t, single in flagged)
            note = f" — {UNCONFIRMED_NOTE}" if any(single for _, single in flagged) else ""
            lines.append(
                f"- **{_short_model(model)}**: {len(flagged)}/{len(comparable)} tasks did as well "
                f"or better without the CLAUDE.md — {names}{note}"
            )
        else:
            lines.append(
                f"- **{_short_model(model)}**: CLAUDE.md pulled its weight on all "
                f"{len(comparable)} comparable tasks"
            )

    return "\n".join(lines) + "\n"
