"""Section ablation: which parts of a CLAUDE.md actually earn their tokens.

The whole-file A/B answers "is this context file worth having". It cannot answer
"which half of it is dead weight", and until this existed the README claimed an
answer the harness could not produce: `lint.py` scored sections statically, by
counting tokens and imperatives, which measures size and tone rather than effect.

Ablation closes that. Remove one section, run the same golden tasks against the
same frozen corpus, and read the difference. A section whose removal costs
nothing measurable is a section to delete.

The fiddly part is the hierarchy. `lint._split_sections` is flat: it strips the
leading hashes, so a `##` and a `###` are indistinguishable and removing a
parent would leave its orphaned children behind, changing the file in a way
nobody asked for and scoring the result as if the parent alone had gone. Here a
section runs from its heading to the next heading at the same or a shallower
level, so removing a `##` takes its `###` children with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import fmean

from gauntlet.report import Cell, _cells, empty_beats_current, is_single_sample

HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
BASELINE_VARIANT = "current"
VARIANT_PREFIX = "minus-"


@dataclass(frozen=True)
class Section:
    slug: str
    heading: str
    level: int
    start: int  # line index of the heading itself
    end: int  # exclusive; the next heading at this level or shallower
    body: str  # the span that removal deletes, heading included
    est_tokens: int


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s or "section"


def split_sections(text: str, max_depth: int | None = None) -> list[Section]:
    """Decompose a markdown document into removable, hierarchy-aware sections.

    `max_depth` caps the heading level offered for ablation. Nested sections are
    genuine candidates, but every level multiplies the grid, and a `####` is
    rarely a unit anyone would delete on its own.
    """
    lines = text.splitlines()
    heads: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))

    out: list[Section] = []
    seen: dict[str, int] = {}
    for n, (start, level, title) in enumerate(heads):
        if max_depth is not None and level > max_depth:
            continue
        end = len(lines)
        for j, lvl, _ in heads[n + 1 :]:
            if lvl <= level:
                end = j
                break
        slug = _slug(title)
        # Two sections can share a title ("Notes" under two parents). Suffix the
        # later ones rather than silently generating one variant for both.
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 1
        body = "\n".join(lines[start:end])
        out.append(Section(slug, title, level, start, end, body, len(body) // 4))
    return out


def remove_section(text: str, section: Section) -> str:
    """The document with one section's whole span removed, children included."""
    lines = text.splitlines()
    kept = lines[: section.start] + lines[section.end :]
    body = "\n".join(kept).rstrip()
    return body + "\n" if body else ""


def variant_name(section: Section) -> str:
    return f"{VARIANT_PREFIX}{section.slug}"


def _overall(by_task: dict, variant: str) -> Cell | None:
    """Mean of a variant's per-task cell means, across the tasks it has cells for."""
    means = [cells[variant].mean for cells in by_task.values() if variant in cells]
    if not means:
        return None
    ns = [cells[variant].n for cells in by_task.values() if variant in cells]
    return Cell(mean=fmean(means), sd=0.0, n=min(ns))


def build_ablation_report(
    results: list[dict],
    sections: list[Section],
    lint_reports: list = (),
    run_label: str = "",
) -> str:
    """Per-section delta against the full file, next to the static lint numbers.

    The static columns are kept deliberately adjacent to the measured one. A
    section that lints as large and turns out to change nothing is the finding
    worth acting on, and it is only visible when both numbers sit on one row.
    """
    title = f"# Gauntlet Section Ablation{f' - {run_label}' if run_label else ''}"
    lines = [title, ""]

    by_task, attempted, categories = _cells(results, key=lambda r: r["variant"])
    baseline = _overall(by_task, BASELINE_VARIANT)
    if baseline is None:
        return "\n".join(lines + ["No `current` rows to compare against.", ""]) + "\n"

    lint_by_heading = {r.heading: r for r in lint_reports}

    lines += [
        f"Baseline (`{BASELINE_VARIANT}`, the full file) scores "
        f"**{baseline.mean:.2f}** across {len(by_task)} tasks.",
        "",
        "`Delta` is the ablated score minus the baseline, so **positive means "
        "removing the section did not hurt**. `No harm` counts tasks where the "
        "ablated variant matched or beat the full file by more than the standard "
        "error of the difference, which is the same gate the whole-file verdicts "
        "use. A single sample cannot clear it, so run with `--repeats` before "
        "deleting anything.",
        "",
        "| Section | Lvl | Est. tokens | Imperatives | Dead paths | Score | Delta | No harm | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    unconfirmed = False
    for section in sections:
        v = variant_name(section)
        cell = _overall(by_task, v)
        lint = lint_by_heading.get(section.heading)
        imper = str(lint.imperative_count) if lint else "-"
        dead = str(len(lint.dead_paths)) if lint and lint.dead_paths else ("0" if lint else "-")

        if cell is None:
            lines.append(
                f"| {section.heading} | {section.level} | {section.est_tokens} | "
                f"{imper} | {dead} | n/a | n/a | n/a | not run |"
            )
            continue

        no_harm = 0
        comparable = 0
        for cells in by_task.values():
            if BASELINE_VARIANT not in cells or v not in cells:
                continue
            comparable += 1
            if empty_beats_current(cells[BASELINE_VARIANT], cells[v]):
                no_harm += 1
            if is_single_sample(cells[BASELINE_VARIANT], cells[v]):
                unconfirmed = True

        delta = cell.mean - baseline.mean
        if comparable and no_harm == comparable:
            verdict = f"**cut candidate** ({section.est_tokens} tok)"
        elif no_harm:
            verdict = "mixed"
        else:
            verdict = "earns its tokens"

        lines.append(
            f"| {section.heading} | {section.level} | {section.est_tokens} | "
            f"{imper} | {dead} | {cell.mean:.2f} | {delta:+.2f} | "
            f"{no_harm}/{comparable} | {verdict} |"
        )

    if unconfirmed:
        lines += [
            "",
            "At least one comparison rests on a single sample per cell, which carries "
            "no spread and therefore cannot be distinguished from noise. Re-run with "
            "`--repeats` before treating any row here as a decision.",
        ]

    cuts = [s for s in sections if _overall(by_task, variant_name(s)) is not None]
    reclaimable = sum(
        s.est_tokens
        for s in cuts
        if all(
            empty_beats_current(cells[BASELINE_VARIANT], cells[variant_name(s)])
            for cells in by_task.values()
            if BASELINE_VARIANT in cells and variant_name(s) in cells
        )
        and any(variant_name(s) in cells for cells in by_task.values())
    )
    lines += [
        "",
        f"Sections whose removal did no measurable harm account for roughly "
        f"**{reclaimable} tokens**. Overlapping parents and children both count "
        f"toward that figure, so treat it as an upper bound rather than a total.",
        "",
    ]
    return "\n".join(lines) + "\n"
