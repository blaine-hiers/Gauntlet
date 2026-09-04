from collections import defaultdict


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


def build_report(results: list[dict], lint_reports: list, run_label: str) -> str:
    lines = [f"# Assay Report — {run_label}", ""]

    by_variant = defaultdict(list)
    for r in results:
        by_variant[r["variant"]].append(r)

    lines += ["## Variant Summary", "", "| Variant | Check pass rate | Mean judge score | Total cost (USD) |", "|---|---|---|---|"]
    for variant, rs in sorted(by_variant.items()):
        checks = [c for r in rs for c in r["checks"]]
        pass_rate = f"{100 * sum(c['passed'] for c in checks) / len(checks):.0f}%" if checks else "n/a"
        scores = [r["judge"]["score"] for r in rs if r.get("judge") and r["judge"]["score"] is not None]
        mean_judge = f"{sum(scores) / len(scores):.1f}" if scores else "n/a"
        cost = sum(r["cost_usd"] or 0 for r in rs)
        lines.append(f"| {variant} | {pass_rate} | {mean_judge} | {cost:.2f} |")

    lines += ["", "## Per-Task Matrix", "", "| Task | Category | " + " | ".join(sorted(by_variant)) + " |", "|---" * (2 + len(by_variant)) + "|"]
    by_task = defaultdict(dict)
    categories = {}
    for r in results:
        by_task[r["task_id"]][r["variant"]] = _task_score(r)
        categories[r["task_id"]] = r["category"]
    for task_id, scores in sorted(by_task.items()):
        cells = " | ".join(f"{scores[v]:.2f}" if v in scores else "n/a" for v in sorted(by_variant))
        lines.append(f"| {task_id} | {categories[task_id]} | {cells} |")

    lines += ["", "## Verdicts", ""]
    flagged = [
        t for t, s in sorted(by_task.items())
        if "current" in s and "empty" in s and s["empty"] >= s["current"]
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
    lines = [f"# Assay Model Comparison — {', '.join(run_labels)}", ""]

    models = sorted({r["model"] for r in results})
    by_cell = defaultdict(list)  # (model, variant) -> rows
    for r in results:
        by_cell[(r["model"], r["variant"])].append(r)
    pairs = sorted(by_cell)

    lines += [
        "## Summary (model × variant)",
        "",
        "| Model | Variant | Check pass rate | Mean judge score | Errors | Total cost (USD) |",
        "|---|---|---|---|---|---|",
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
            f"| {_short_model(model)} | {variant} | {pass_rate} | {mean_judge} | {errors} | {cost:.2f} |"
        )

    col_names = [f"{_short_model(m)}/{v}" for m, v in pairs]
    lines += [
        "",
        "## Per-Task Matrix (composite score 0–1)",
        "",
        "| Task | Category | " + " | ".join(col_names) + " |",
        "|---" * (2 + len(pairs)) + "|",
    ]
    by_task = defaultdict(dict)  # task_id -> (model, variant) -> score
    categories = {}
    for r in results:
        by_task[r["task_id"]][(r["model"], r["variant"])] = _task_score(r)
        categories[r["task_id"]] = r["category"]
    for task_id, scores in sorted(by_task.items()):
        cells = " | ".join(f"{scores[p]:.2f}" if p in scores else "n/a" for p in pairs)
        lines.append(f"| {task_id} | {categories[task_id]} | {cells} |")

    lines += ["", "## Verdicts (per model)", ""]
    for model in models:
        flagged = [
            t for t, s in sorted(by_task.items())
            if (model, "current") in s and (model, "empty") in s
            and s[(model, "empty")] >= s[(model, "current")]
        ]
        comparable = [
            t for t, s in sorted(by_task.items())
            if (model, "current") in s and (model, "empty") in s
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
