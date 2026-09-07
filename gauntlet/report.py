import math
import statistics
from collections import defaultdict

from gauntlet.providers import DEFAULT_PROVIDER_NAME
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
    sd: float  # sample sd of the composite; 0.0 for a single sample
    n: int


def _aggregate(scores: list[float]) -> Cell:
    # Sample sd, so sd**2 / n in the gate is the standard error the report
    # claims; the population estimator undershoots it by sqrt((n-1)/n).
    sd = statistics.stdev(scores) if len(scores) > 1 else 0.0
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


@dataclass(frozen=True)
class Summary:
    pass_rate: str
    mean_judge: str
    runs: int
    errors: int
    cost: float
    judge_cost: float
    degraded: int
    score_per_dollar: str
    score_per_turn: str


def _efficiency(ok: list[dict], field: str, places: int) -> str:
    rows = [r for r in ok if r.get(field) is not None]
    denominator = sum(r[field] for r in rows)
    if denominator <= 0:
        return "n/a"
    return f"{sum(_task_score(r) for r in rows) / denominator:.{places}f}"


def _summary_cells(rs: list[dict]) -> Summary:
    ok = [r for r in rs if not r.get("is_error")]
    checks = [c for r in ok for c in r["checks"]]
    pass_rate = f"{100 * sum(c['passed'] for c in checks) / len(checks):.0f}%" if checks else "n/a"
    scores = [r["judge"]["score"] for r in ok if r.get("judge") and r["judge"]["score"] is not None]
    mean_judge = f"{sum(scores) / len(scores):.1f}" if scores else "n/a"
    errors = len(rs) - len(ok)
    cost = sum(r.get("cost_usd") or 0 for r in rs)
    # Judge spend is harness overhead, tracked separately so it never inflates
    # the "Total cost (USD)" column, which is what a variant actually costs to run.
    judge_cost = sum(r.get("judge_cost_usd") or 0 for r in rs)
    degraded = sum(1 for r in ok if r.get("judge") and r["judge"].get("degraded"))
    # Score/$ and Score/turn are total score earned over total spend/turns among
    # scored (non-error) rows — sum/sum, not mean/sum, so two identical rows
    # report the same efficiency as one instead of halving it. A row that lacks
    # the denominator field (a pre-Phase-2 row with no num_turns, a provider
    # that reports no dollar cost) is left out of the numerator too; counting
    # its score against zero spend would inflate the ratio.
    score_per_dollar = _efficiency(ok, "cost_usd", 2)
    score_per_turn = _efficiency(ok, "num_turns", 3)
    return Summary(
        pass_rate=pass_rate,
        mean_judge=mean_judge,
        runs=len(rs),
        errors=errors,
        cost=cost,
        judge_cost=judge_cost,
        degraded=degraded,
        score_per_dollar=score_per_dollar,
        score_per_turn=score_per_turn,
    )


def _degraded_rows_section(results: list[dict]) -> list[str]:
    degraded = [
        r for r in results
        if not r.get("is_error") and r.get("judge") and r["judge"].get("degraded")
    ]
    if not degraded:
        return []
    lines = [
        "", "## Degraded Judge Rows", "",
        "Judge output was unparseable (even after the automatic retry) on at least one "
        "sample for these rows; their score, where present, rests on fewer usable "
        "samples than were attempted and should not be trusted the same as a clean row.",
        "",
        "| Task | Variant | Score | Reasoning |",
        "|---|---|---|---|",
    ]
    for r in degraded:
        score = r["judge"]["score"]
        score_s = f"{score:.1f}" if score is not None else "n/a"
        reasoning = (r["judge"].get("reasoning") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r['task_id']} | {r['variant']} | {score_s} | {reasoning[:200]} |")
    return lines


def _category_section(results: list[dict], key) -> list[str]:
    by_cat = defaultdict(list)
    for r in results:
        by_cat[(r["category"],) + key(r)].append(r)
    if not by_cat:
        return []
    extra_cols = len(next(iter(by_cat))) - 1
    header_extra = ["Variant"] if extra_cols == 1 else ["Model", "Variant"]
    lines = [
        "", "## Per-Category Summary", "",
        "| Category | " + " | ".join(header_extra)
        + " | Runs | Errors | Check pass rate | Mean judge score |",
        "|---" * (2 + extra_cols + 3) + "|",
    ]
    for group_key in sorted(by_cat):
        s = _summary_cells(by_cat[group_key])
        cols = " | ".join(group_key)
        lines.append(f"| {cols} | {s.runs} | {s.errors} | {s.pass_rate} | {s.mean_judge} |")
    return lines


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

    lines += [
        "## Variant Summary", "",
        "| Variant | Runs | Errors | Check pass rate | Mean judge score | Degraded judge | "
        "Total cost (USD) | Judge cost (USD) | Score/$ | Score/turn |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for variant, rs in sorted(by_variant.items()):
        s = _summary_cells(rs)
        lines.append(
            f"| {variant} | {s.runs} | {s.errors} | {s.pass_rate} | {s.mean_judge} | {s.degraded} | "
            f"{s.cost:.2f} | {s.judge_cost:.2f} | {s.score_per_dollar} | {s.score_per_turn} |"
        )

    lines += ["", "## Per-Task Matrix", "", "| Task | Category | " + " | ".join(sorted(by_variant)) + " |", "|---" * (2 + len(by_variant)) + "|"]
    by_task, attempted, categories = _cells(results, key=lambda r: r["variant"])
    for task_id in sorted(categories):
        cells = by_task.get(task_id, {})
        row = " | ".join(_fmt_cell(cells[v]) if v in cells else "n/a" for v in sorted(by_variant))
        lines.append(f"| {task_id} | {categories[task_id]} | {row} |")

    lines += _category_section(results, key=lambda r: (r["variant"],))

    lines += ["", "## Verdicts", "", VERDICT_RULE, ""]
    lines += _verdict_lines(*_split_verdicts(by_task, attempted, "current", "empty"))
    lines += _degraded_rows_section(results)
    lines += _lint_section(lint_reports)

    return "\n".join(lines) + "\n"


def _short_model(model: str) -> str:
    return model.removeprefix("claude-")


def build_compare(results: list[dict], run_labels: list[str], lint_reports: list = ()) -> str:
    """Cross-provider, cross-model report: every row must carry a 'model' key.

    Rows written before the provider axis existed carry no 'provider'. They were
    all produced by the Claude CLI, which is the implicit provider's name, so
    they land in the cells they always did instead of forming a second axis."""
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

    def _prov(r):
        return r.get("provider", DEFAULT_PROVIDER_NAME)

    providers_seen = sorted({_prov(r) for r in results})
    multi_provider = len(providers_seen) > 1
    kinds = sorted({r["provider_kind"] for r in results if r.get("provider_kind")})
    if len(kinds) > 1:
        # Put this where the numbers are rather than in a footnote. Our tool loop
        # and Claude Code are not the same agent, so an absolute gap between two
        # providers measures the scaffolds at least as much as the models.
        lines += [
            f"**Cross-provider rows are scaffold-confounded.** These results span "
            f"{len(kinds)} provider kinds ({', '.join(kinds)}). One tool loop is not "
            f"another, so a difference *between* providers is not a model comparison. "
            f"The defensible reading is within a single provider: its own CLAUDE.md "
            f"variants against each other.",
            "",
        ]

    combos = sorted({(_prov(r), r["model"]) for r in results})
    by_cell = defaultdict(list)  # (provider, model, variant) -> rows
    for r in results:
        by_cell[(_prov(r), r["model"], r["variant"])].append(r)
    pairs = sorted(by_cell)

    lines += [
        "## Summary (provider × model × variant)",
        "",
        "| Provider | Model | Variant | Runs | Errors | Check pass rate | Mean judge score | "
        "Degraded judge | Total cost (USD) | Judge cost (USD) | Score/$ | Score/turn |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for provider, model, variant in pairs:
        s = _summary_cells(by_cell[(provider, model, variant)])
        lines.append(
            f"| {provider} | {_short_model(model)} | {variant} | {s.runs} | {s.errors} | "
            f"{s.pass_rate} | {s.mean_judge} | "
            f"{s.degraded} | {s.cost:.2f} | {s.judge_cost:.2f} | {s.score_per_dollar} | {s.score_per_turn} |"
        )

    # The provider enters the column label only when there is more than one, so a
    # single-provider report reads exactly as it did before this axis existed.
    col_names = [
        (f"{p}/{_short_model(m)}/{v}" if multi_provider else f"{_short_model(m)}/{v}")
        for p, m, v in pairs
    ]
    lines += [
        "",
        "## Per-Task Matrix (composite score 0–1)",
        "",
        "| Task | Category | " + " | ".join(col_names) + " |",
        "|---" * (2 + len(pairs)) + "|",
    ]
    by_task, attempted, categories = _cells(
        results, key=lambda r: (_prov(r), r["model"], r["variant"])
    )
    for task_id in sorted(categories):
        cells = by_task.get(task_id, {})
        row = " | ".join(_fmt_cell(cells[p]) if p in cells else "n/a" for p in pairs)
        lines.append(f"| {task_id} | {categories[task_id]} | {row} |")

    lines += _category_section(
        results,
        key=lambda r: (
            f"{_prov(r)}/{_short_model(r['model'])}" if multi_provider else _short_model(r["model"]),
            r["variant"],
        ),
    )

    verdict_heading = (
        "## Verdicts (per provider × model)" if multi_provider else "## Verdicts (per model)"
    )
    lines += ["", verdict_heading, "", VERDICT_RULE, ""]
    for provider, model in combos:
        flagged, comparable, incomparable = _split_verdicts(
            by_task, attempted, (provider, model, "current"), (provider, model, "empty")
        )
        name = f"{provider}/{_short_model(model)}" if multi_provider else _short_model(model)
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
    lines += _degraded_rows_section(results)
    lines += _lint_section(lint_reports)

    return "\n".join(lines) + "\n"
