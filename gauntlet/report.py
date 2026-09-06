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
    """One task × variant cell aggregated over its repeats."""

    mean: float
    sd: float  # population sd of the composite; 0.0 for a single sample
    n: int


def _aggregate(scores: list[float]) -> Cell:
    sd = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    return Cell(mean=statistics.fmean(scores), sd=sd, n=len(scores))


def _fmt_cell(c: Cell) -> str:
    return f"{c.mean:.2f}" if c.n == 1 else f"{c.mean:.2f} ±{c.sd:.2f}"


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


VERDICT_RULE = (
    "A task is flagged when the `empty` mean beats the `current` mean by more than the "
    "standard error of the difference (cells are mean ±sd over repeats; with one sample "
    "per cell there is no spread to measure, so a tie or better flags)."
)


def build_report(results: list[dict], lint_reports: list, run_label: str) -> str:
    lines = [f"# Gauntlet Report — {run_label}", ""]

    by_variant = defaultdict(list)
    for r in results:
        by_variant[r["variant"]].append(r)

    lines += ["## Variant Summary", "", "| Variant | Runs | Check pass rate | Mean judge score | Total cost (USD) |", "|---|---|---|---|---|"]
    for variant, rs in sorted(by_variant.items()):
        checks = [c for r in rs for c in r["checks"]]
        pass_rate = f"{100 * sum(c['passed'] for c in checks) / len(checks):.0f}%" if checks else "n/a"
        scores = [r["judge"]["score"] for r in rs if r.get("judge") and r["judge"]["score"] is not None]
        mean_judge = f"{sum(scores) / len(scores):.1f}" if scores else "n/a"
        cost = sum(r["cost_usd"] or 0 for r in rs)
        lines.append(f"| {variant} | {len(rs)} | {pass_rate} | {mean_judge} | {cost:.2f} |")

    lines += ["", "## Per-Task Matrix", "", "| Task | Category | " + " | ".join(sorted(by_variant)) + " |", "|---" * (2 + len(by_variant)) + "|"]
    samples = defaultdict(lambda: defaultdict(list))  # task_id -> variant -> [score per repeat]
    categories = {}
    for r in results:
        samples[r["task_id"]][r["variant"]].append(_task_score(r))
        categories[r["task_id"]] = r["category"]
    by_task = {t: {v: _aggregate(s) for v, s in vs.items()} for t, vs in samples.items()}
    for task_id, cells in sorted(by_task.items()):
        row = " | ".join(_fmt_cell(cells[v]) if v in cells else "n/a" for v in sorted(by_variant))
        lines.append(f"| {task_id} | {categories[task_id]} | {row} |")

    lines += ["", "## Verdicts", "", VERDICT_RULE, ""]
    flagged = [
        t for t, cells in sorted(by_task.items())
        if "current" in cells and "empty" in cells
        and empty_beats_current(cells["current"], cells["empty"])
    ]
    if flagged:
        lines.append("Tasks where running **without** the CLAUDE.md did as well or better — the instruction file is not earning its keep here:")
        lines += [f"- {t}" for t in flagged]
    else:
        lines.append("No task scored as well without the CLAUDE.md — the instruction file is pulling its weight.")

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
        "| Model | Variant | Runs | Check pass rate | Mean judge score | Errors | Total cost (USD) |",
        "|---|---|---|---|---|---|---|",
    ]
    for model, variant in pairs:
        rs = by_cell[(model, variant)]
        checks = [c for r in rs for c in r["checks"]]
        pass_rate = f"{100 * sum(c['passed'] for c in checks) / len(checks):.0f}%" if checks else "n/a"
        scores = [r["judge"]["score"] for r in rs if r.get("judge") and r["judge"]["score"] is not None]
        mean_judge = f"{sum(scores) / len(scores):.1f}" if scores else "n/a"
        errors = sum(1 for r in rs if r["is_error"])
        cost = sum(r["cost_usd"] or 0 for r in rs)
        lines.append(
            f"| {_short_model(model)} | {variant} | {len(rs)} | {pass_rate} | {mean_judge} | {errors} | {cost:.2f} |"
        )

    col_names = [f"{_short_model(m)}/{v}" for m, v in pairs]
    lines += [
        "",
        "## Per-Task Matrix (composite score 0–1)",
        "",
        "| Task | Category | " + " | ".join(col_names) + " |",
        "|---" * (2 + len(pairs)) + "|",
    ]
    samples = defaultdict(lambda: defaultdict(list))  # task_id -> (model, variant) -> [score]
    categories = {}
    for r in results:
        samples[r["task_id"]][(r["model"], r["variant"])].append(_task_score(r))
        categories[r["task_id"]] = r["category"]
    by_task = {t: {p: _aggregate(s) for p, s in ps.items()} for t, ps in samples.items()}
    for task_id, cells in sorted(by_task.items()):
        row = " | ".join(_fmt_cell(cells[p]) if p in cells else "n/a" for p in pairs)
        lines.append(f"| {task_id} | {categories[task_id]} | {row} |")

    lines += ["", "## Verdicts (per model)", "", VERDICT_RULE, ""]
    for model in models:
        comparable = [
            t for t, cells in sorted(by_task.items())
            if (model, "current") in cells and (model, "empty") in cells
        ]
        flagged = [
            t for t in comparable
            if empty_beats_current(by_task[t][(model, "current")], by_task[t][(model, "empty")])
        ]
        if not comparable:
            lines.append(f"- **{_short_model(model)}**: no current/empty pairs to compare")
        elif flagged:
            lines.append(
                f"- **{_short_model(model)}**: {len(flagged)}/{len(comparable)} tasks did as well "
                f"or better without the CLAUDE.md — {', '.join(flagged)}"
            )
        else:
            lines.append(
                f"- **{_short_model(model)}**: CLAUDE.md pulled its weight on all "
                f"{len(comparable)} comparable tasks"
            )

    return "\n".join(lines) + "\n"
