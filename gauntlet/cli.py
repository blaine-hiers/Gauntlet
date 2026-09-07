import argparse
import dataclasses
import hashlib
import json
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Windows consoles default to cp1252, which cannot print the framework's unicode
# headings; force utf-8 so lint/run output never crashes on a glyph.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gauntlet.ablate import (
    build_ablation_report,
    remove_section,
    split_sections,
    variant_name,
)
from gauntlet.config import Config, Variant, load_config
from gauntlet.judge import judge_output
from gauntlet.lint import lint_claude_md
from gauntlet.providers import DEFAULT_PROVIDER_NAME, resolve_providers
from gauntlet.report import build_compare, build_report
from gauntlet.runner import ancestor_context_files, execute, prepare_run_dir
from gauntlet.scoring import run_checks
from gauntlet.snapshot import (
    _rmtree_force,
    load_manifest,
    load_snapshot_provenance,
    make_snapshot,
)
from gauntlet.tasks import load_tasks

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_config() -> Config:
    return load_config(PROJECT_ROOT / "gauntlet.config.json")


def data_root(cfg: Config) -> Path:
    return cfg.data_dir if cfg.data_dir else PROJECT_ROOT / "data"


def work_root_for(cfg: Config, label: str) -> Path:
    # "lit/<8-char hash>" keeps the run-dir prefix shorter than the snapshot's own
    # path, so deep framework trees stay under Windows' 260-char path limit.
    base = cfg.work_root if cfg.work_root else Path(tempfile.gettempdir())
    return base / "lit" / hashlib.sha1(label.encode()).hexdigest()[:8]


def cmd_snapshot(cfg: Config) -> int:
    dest = data_root(cfg) / "snapshot"
    dest.parent.mkdir(parents=True, exist_ok=True)
    manifest = make_snapshot(cfg.synced_root, dest, cfg.exclude)
    print(f"snapshot: {len(manifest)} files -> {dest}")
    return 0


def cmd_lint(cfg: Config) -> int:
    snap = data_root(cfg) / "snapshot"
    claude_md = snap / "CLAUDE.md"
    if not claude_md.is_file():
        print("no data/snapshot/CLAUDE.md — run `snapshot` first")
        return 1
    for s in lint_claude_md(claude_md.read_text(encoding="utf-8"), snap):
        dead = ", ".join(s.dead_paths) if s.dead_paths else "-"
        print(f"{s.heading:40s} ~{s.est_tokens:5d} tok  imperatives={s.imperative_count}  dead={dead}")
    return 0


def _run_cell(
    provider,
    task,
    variant,
    repeat_idx: int,
    cell: str,
    *,
    snap: Path,
    work_root: Path,
    tasks_dir: Path,
    cfg: Config,
    manifest,
    snapshot_provenance,
    judge_samples: int,
):
    """Run one task x variant x repeat cell and return (result, log_lines).

    Nothing here prints. Under --concurrency the cells finish interleaved, and
    half of one cell's output landing inside another's is how a console log
    stops being readable at exactly the moment you need it. The caller emits
    each buffer in one piece, under the same lock that appends the row."""
    lines: list[str] = []
    run_dir = prepare_run_dir(
        snap, work_root, task, variant, tasks_dir, PROJECT_ROOT,
        repeat_idx=repeat_idx, model=provider.model, provider=provider.name,
        log=lines.append,
    )
    result = provider.run(task, variant, run_dir)
    result.setdefault("model", provider.model)
    result.setdefault("provider", provider.name)
    result.setdefault("provider_kind", provider.kind)
    result["repeat_idx"] = repeat_idx
    result["work_root"] = str(work_root)
    result["snapshot"] = snapshot_provenance
    result["checks"] = run_checks(
        task.checks, run_dir, manifest, result.get("output_text", "")
    )
    try:
        _rmtree_force(run_dir)
    except OSError:
        lines.append(f"warning: could not delete run dir {run_dir}")
    result["judge"] = (
        judge_output(
            result["output_text"], task.judge, cfg.judge_model, samples=judge_samples
        )
        if task.judge and not result["is_error"]
        else None
    )
    result["judge_cost_usd"] = result["judge"]["cost_usd"] if result["judge"] else None
    lines.append(
        f"{cell}: "
        f"checks {sum(c['passed'] for c in result['checks'])}/{len(result['checks'])}"
        + (f", judge {result['judge']['score']}" if result["judge"] else "")
    )
    return result, lines


def cmd_run(
    cfg: Config,
    label: str,
    variants_filter: str | None,
    tasks_dir: Path,
    repeats: int = 1,
    legacy_model: str | None = None,
    retry_errors: bool = False,
    judge_samples: int = 1,
    concurrency: int = 1,
) -> int:
    """`legacy_model` is the config's default model *before* any --model override:
    rows written before the model field existed were produced under it, so that
    is the identity they resume with — not whatever model this run happens to use.
    `retry_errors` leaves errored rows out of the resume set so their cells run
    again; the old rows stay (append-only) and the report already excludes them."""
    if repeats < 1:
        print("--repeats must be at least 1")
        return 1
    if concurrency < 1:
        print("--concurrency must be at least 1")
        return 1
    legacy_model = legacy_model or cfg.model
    snap = data_root(cfg) / "snapshot"
    if not snap.is_dir():
        print("no snapshot — run `snapshot` first")
        return 1
    if not tasks_dir.is_dir():
        print(f"tasks directory not found: {tasks_dir}")
        return 1
    manifest = load_manifest(snap)
    snapshot_provenance = load_snapshot_provenance(snap)
    tasks = load_tasks(tasks_dir)
    if not tasks:
        print(f"no tasks found in {tasks_dir}")
        return 1
    variants = cfg.variants
    if variants_filter:
        wanted = set(v.strip() for v in variants_filter.split(","))
        known = {v.name for v in cfg.variants}
        unknown = sorted(wanted - known)
        if unknown:
            print(f"unknown variant(s): {', '.join(unknown)}")
            return 1
        variants = [v for v in variants if v.name in wanted]
    work_root = work_root_for(cfg, label)
    # The CLI auto-loads CLAUDE.md from every ancestor of the run dir, and the
    # isolation flags do not stop that walk. A run under such a directory would
    # inherit context in every variant, including `empty`, so refuse to start.
    inherited = ancestor_context_files(work_root)
    if inherited:
        print(f"refusing to run: {work_root} would inherit context from")
        for f in inherited:
            print(f"  {f}")
        print("set work_root in gauntlet.config.json to a directory with no CLAUDE.md "
              "in any ancestor (on Windows the system temp dir sits under your home)")
        return 1
    out_dir = data_root(cfg) / "runs" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"

    # Read existing results to avoid duplicates. The key carries the model and
    # the repeat index: a label re-run under --model must not be skipped as
    # "already recorded", and repeat k of a cell is distinct from repeat k+1.
    # Rows written before either field existed used the config's default model
    # and were single-sample, hence the defaults. An errored row still counts as
    # recorded by default — a re-run must not silently re-spend on a cell that
    # timed out — so an all-error cell stays "not comparable" in the report
    # until the run is repeated with --retry-errors.
    recorded = set()
    if results_path.is_file():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if retry_errors and row.get("is_error"):
                    continue
                recorded.add((
                    row["task_id"],
                    row["variant"],
                    row.get("model", legacy_model),
                    row.get("repeat_idx", 0),
                    # Rows predating the provider axis were all produced by the
                    # Claude CLI, which is the implicit provider's name, so they
                    # resume against it rather than looking like a new cell.
                    row.get("provider", DEFAULT_PROVIDER_NAME),
                ))
            except (json.JSONDecodeError, KeyError):
                pass

    # `execute` is passed by name so the module binding stays the one seam a
    # caller (or a test) can stub, rather than each provider reaching past it.
    try:
        providers = resolve_providers(cfg, execute_fn=execute)
    except ValueError as e:
        print(str(e))
        return 1

    # Resolve the whole grid first so the skip lines print in grid order rather
    # than arriving interleaved with completed cells.
    pending = []
    for provider in providers:
        for task in tasks:
            for variant in variants:
                for repeat_idx in range(repeats):
                    cell = f"{task.id} × {variant.name}"
                    if len(providers) > 1:
                        cell = f"{provider.name}/{cell}"
                    if repeats > 1:
                        cell += f" #{repeat_idx}"
                    key = (task.id, variant.name, provider.model, repeat_idx, provider.name)
                    if key in recorded:
                        print(f"{cell}: skipped (already recorded)")
                        continue
                    pending.append((provider, task, variant, repeat_idx, cell))

    cell_kwargs = dict(
        snap=snap, work_root=work_root, tasks_dir=tasks_dir, cfg=cfg,
        manifest=manifest, snapshot_provenance=snapshot_provenance,
        judge_samples=judge_samples,
    )
    write_lock = threading.Lock()

    with results_path.open("a", encoding="utf-8") as out:
        def commit(result, lines):
            # One lock covers the append, the flush and the cell's log together.
            # The flush stays per row so an interrupted sweep is still resumable
            # from what reached disk, and holding the lock across the print keeps
            # a row on disk before the line claiming it appears.
            with write_lock:
                out.write(json.dumps(result) + "\n")
                out.flush()
                for line in lines:
                    print(line)

        if concurrency == 1:
            # Kept deliberately separate from the pool path: serial runs stop at
            # the first failure instead of paying for every cell already queued
            # behind it, and that is the default.
            for provider, task, variant, repeat_idx, cell in pending:
                commit(*_run_cell(provider, task, variant, repeat_idx, cell, **cell_kwargs))
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {
                    pool.submit(
                        _run_cell, provider, task, variant, repeat_idx, cell, **cell_kwargs
                    ): cell
                    for provider, task, variant, repeat_idx, cell in pending
                }
                for fut in as_completed(futures):
                    commit(*fut.result())
    print(f"results -> {results_path}")
    return 0


def cmd_ablate(
    cfg: Config,
    label: str,
    tasks_dir: Path,
    repeats: int = 1,
    concurrency: int = 1,
    max_depth: int = 2,
    threshold: int = 60,
    assume_yes: bool = False,
    legacy_model: str | None = None,
    retry_errors: bool = False,
    judge_samples: int = 1,
) -> int:
    """Generate one variant per removable section, run the grid, report the deltas."""
    snap = data_root(cfg) / "snapshot"
    claude_md = snap / "CLAUDE.md"
    if not claude_md.is_file():
        print(f"no CLAUDE.md in the snapshot at {snap} — run `snapshot` first")
        return 1
    if not tasks_dir.is_dir():
        print(f"tasks directory not found: {tasks_dir}")
        return 1

    text = claude_md.read_text(encoding="utf-8")
    sections = split_sections(text, max_depth=max_depth)
    if not sections:
        print(f"no headings at depth <= {max_depth} in the snapshot's CLAUDE.md")
        return 1
    tasks = load_tasks(tasks_dir)
    if not tasks:
        print(f"no tasks found in {tasks_dir}")
        return 1

    out_dir = data_root(cfg) / "runs" / label
    variant_dir = out_dir / "ablation-variants"
    variant_dir.mkdir(parents=True, exist_ok=True)

    # `current` is the baseline every delta is measured against; `empty` is kept
    # because the floor is the single most informative comparison in the report
    # and it costs one variant among many.
    variants = [Variant("current", None), Variant("empty", "")]
    for section in sections:
        path = variant_dir / f"{variant_name(section)}.md"
        path.write_text(remove_section(text, section), encoding="utf-8")
        # Absolute, because these are generated under data/ rather than checked
        # in beside the project's own variants.
        variants.append(Variant(variant_name(section), str(path)))

    planned = len(variants) * len(tasks) * repeats
    print(
        f"{len(sections)} section(s) at depth <= {max_depth}: "
        f"{len(variants)} variants × {len(tasks)} tasks × {repeats} repeat(s) "
        f"= {planned} cells"
    )
    # sections × variants × tasks × repeats grows fast, and every cell is a paid
    # model call. Make the size a decision rather than a surprise.
    if planned > threshold and not assume_yes:
        try:
            answer = input(f"that is over the {threshold}-cell threshold. continue? [y/N] ")
        except (EOFError, OSError):
            # No usable stdin: a cron job, a CI runner, a detached process. The
            # only safe reading of "nobody can answer" is no, because the
            # alternative spends money on a grid nobody sized.
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("aborted")
            return 1

    rc = cmd_run(
        dataclasses.replace(cfg, variants=variants),
        label, None, tasks_dir,
        repeats=repeats, legacy_model=legacy_model, retry_errors=retry_errors,
        judge_samples=judge_samples, concurrency=concurrency,
    )
    if rc:
        return rc

    results_path = out_dir / "results.jsonl"
    results = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]
    for row in results:
        row.setdefault("model", cfg.model)
    md = build_ablation_report(
        results, sections, lint_claude_md(text, snap), run_label=label
    )
    report_path = out_dir / "ablation.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"ablation -> {report_path}")
    return 0


def cmd_compare(cfg: Config, labels: list[str], out_name: str) -> int:
    rows = []
    for label in labels:
        results_path = data_root(cfg) / "runs" / label / "results.jsonl"
        if not results_path.is_file():
            print(f"no results at {results_path}")
            return 1
        for line in results_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            # Rows from runs before the model axis existed carry no model field;
            # those runs always used the config's default model.
            row.setdefault("model", cfg.model)
            row["label"] = label  # lets the report say when cells pool labels
            rows.append(row)
    md = build_compare(rows, run_labels=labels)
    out_path = data_root(cfg) / "runs" / f"{out_name}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"compare -> {out_path}")
    return 0


def cmd_report(cfg: Config, label: str) -> int:
    out_dir = data_root(cfg) / "runs" / label
    results_path = out_dir / "results.jsonl"
    if not results_path.is_file():
        print(f"no results at {results_path}")
        return 1
    results = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]
    for row in results:
        row.setdefault("model", cfg.model)
    snap = data_root(cfg) / "snapshot"
    claude_md = snap / "CLAUDE.md"
    lint_reports = (
        lint_claude_md(claude_md.read_text(encoding="utf-8"), snap) if claude_md.is_file() else []
    )
    models = sorted({r["model"] for r in results})
    if len(models) > 1:
        # One label can now hold several models (a --model re-run tops it up).
        # Blending them into one cell would report between-model variance as
        # run-to-run noise, so hand off to the report that keeps them apart.
        print(f"label holds {len(models)} models ({', '.join(models)}); writing a model comparison")
        md = build_compare(results, run_labels=[label], lint_reports=lint_reports)
    else:
        md = build_report(results, lint_reports, run_label=label)
    report_path = out_dir / "report.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"report -> {report_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gauntlet")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("lint")
    p_run = sub.add_parser("run")
    p_run.add_argument("--label", required=True)
    p_run.add_argument("--variants", default=None)
    p_run.add_argument("--tasks-dir", default=None)
    p_run.add_argument("--model", default=None, help="override config model for this run")
    p_run.add_argument(
        "--repeats", type=int, default=1,
        help="samples per task × variant cell; the report gates verdicts on their spread",
    )
    p_run.add_argument(
        "--retry-errors", action="store_true",
        help="re-run cells whose recorded row errored (timeout, crash) instead of skipping them",
    )
    p_run.add_argument(
        "--judge-samples", type=int, default=1,
        help="judge calls per row, taking the median score (for a genuinely borderline rubric)",
    )
    p_run.add_argument(
        "--concurrency", type=int, default=1,
        help="cells to run at once across the task × variant × repeat grid (default 1, serial)",
    )
    p_ablate = sub.add_parser("ablate")
    p_ablate.add_argument("--label", required=True)
    p_ablate.add_argument("--tasks-dir")
    p_ablate.add_argument("--model")
    p_ablate.add_argument("--repeats", type=int, default=1)
    p_ablate.add_argument("--concurrency", type=int, default=1)
    p_ablate.add_argument(
        "--max-depth", type=int, default=2,
        help="deepest heading level offered for ablation (default 2, so # and ##)",
    )
    p_ablate.add_argument(
        "--threshold", type=int, default=60,
        help="planned cell count above which the run asks for confirmation",
    )
    p_ablate.add_argument(
        "--yes", action="store_true", help="skip the cost confirmation",
    )
    p_ablate.add_argument("--retry-errors", action="store_true")
    p_ablate.add_argument("--judge-samples", type=int, default=1)
    p_report = sub.add_parser("report")
    p_report.add_argument("--label", required=True)
    p_compare = sub.add_parser("compare")
    p_compare.add_argument("--labels", required=True, help="comma-separated run labels")
    p_compare.add_argument("--out", default="compare", help="output name (data/runs/<out>.md)")
    args = parser.parse_args(argv)

    cfg = load_project_config()
    if args.command == "snapshot":
        return cmd_snapshot(cfg)
    if args.command == "lint":
        return cmd_lint(cfg)
    if args.command == "run":
        tasks_dir = Path(args.tasks_dir) if args.tasks_dir else PROJECT_ROOT / "tasks"
        default_model = cfg.model
        if args.model:
            cfg = dataclasses.replace(cfg, model=args.model)
        return cmd_run(
            cfg, args.label, args.variants, tasks_dir,
            repeats=args.repeats, legacy_model=default_model, retry_errors=args.retry_errors,
            judge_samples=args.judge_samples, concurrency=args.concurrency,
        )
    if args.command == "ablate":
        tasks_dir = Path(args.tasks_dir) if args.tasks_dir else PROJECT_ROOT / "tasks"
        default_model = cfg.model
        if args.model:
            cfg = dataclasses.replace(cfg, model=args.model)
        return cmd_ablate(
            cfg, args.label, tasks_dir,
            repeats=args.repeats, concurrency=args.concurrency,
            max_depth=args.max_depth, threshold=args.threshold, assume_yes=args.yes,
            legacy_model=default_model, retry_errors=args.retry_errors,
            judge_samples=args.judge_samples,
        )
    if args.command == "report":
        return cmd_report(cfg, args.label)
    if args.command == "compare":
        labels = [x.strip() for x in args.labels.split(",") if x.strip()]
        return cmd_compare(cfg, labels, args.out)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
