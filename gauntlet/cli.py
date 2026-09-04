import argparse
import dataclasses
import hashlib
import json
import sys
import tempfile
from pathlib import Path

# Windows consoles default to cp1252, which cannot print the framework's unicode
# headings; force utf-8 so lint/run output never crashes on a glyph.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gauntlet.config import Config, load_config
from gauntlet.judge import judge_output
from gauntlet.lint import lint_claude_md
from gauntlet.report import build_compare, build_report
from gauntlet.runner import execute, prepare_run_dir
from gauntlet.scoring import run_checks
from gauntlet.snapshot import _rmtree_force, load_manifest, make_snapshot
from gauntlet.tasks import load_tasks

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_config() -> Config:
    return load_config(PROJECT_ROOT / "gauntlet.config.json")


def data_root(cfg: Config) -> Path:
    return cfg.data_dir if cfg.data_dir else PROJECT_ROOT / "data"


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


def cmd_run(cfg: Config, label: str, variants_filter: str | None, tasks_dir: Path) -> int:
    snap = data_root(cfg) / "snapshot"
    if not snap.is_dir():
        print("no snapshot — run `snapshot` first")
        return 1
    if not tasks_dir.is_dir():
        print(f"tasks directory not found: {tasks_dir}")
        return 1
    manifest = load_manifest(snap)
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
    out_dir = data_root(cfg) / "runs" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    # "lit/<8-char hash>" keeps the run-dir prefix shorter than the snapshot's own
    # path, so deep framework trees stay under Windows' 260-char path limit.
    work_root = (
        Path(tempfile.gettempdir()) / "lit" / hashlib.sha1(label.encode()).hexdigest()[:8]
    )
    results_path = out_dir / "results.jsonl"

    # Read existing results to avoid duplicates
    recorded_pairs = set()
    if results_path.is_file():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                recorded_pairs.add((row["task_id"], row["variant"]))
            except (json.JSONDecodeError, KeyError):
                pass

    with results_path.open("a", encoding="utf-8") as out:
        for task in tasks:
            for variant in variants:
                if (task.id, variant.name) in recorded_pairs:
                    print(f"{task.id} × {variant.name}: skipped (already recorded)")
                    continue
                run_dir = prepare_run_dir(snap, work_root, task, variant, tasks_dir, PROJECT_ROOT)
                result = execute(task, variant, run_dir, cfg)
                result["checks"] = run_checks(task.checks, run_dir, manifest)
                try:
                    _rmtree_force(run_dir)
                except OSError:
                    print(f"warning: could not delete run dir {run_dir}")
                result["judge"] = (
                    judge_output(result["output_text"], task.judge, cfg.judge_model)
                    if task.judge and not result["is_error"]
                    else None
                )
                out.write(json.dumps(result) + "\n")
                out.flush()
                print(f"{task.id} × {variant.name}: "
                      f"checks {sum(c['passed'] for c in result['checks'])}/{len(result['checks'])}"
                      + (f", judge {result['judge']['score']}" if result["judge"] else ""))
    print(f"results -> {results_path}")
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
    snap = data_root(cfg) / "snapshot"
    claude_md = snap / "CLAUDE.md"
    lint_reports = (
        lint_claude_md(claude_md.read_text(encoding="utf-8"), snap) if claude_md.is_file() else []
    )
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
        if args.model:
            cfg = dataclasses.replace(cfg, model=args.model)
        return cmd_run(cfg, args.label, args.variants, tasks_dir)
    if args.command == "report":
        return cmd_report(cfg, args.label)
    if args.command == "compare":
        labels = [x.strip() for x in args.labels.split(",") if x.strip()]
        return cmd_compare(cfg, labels, args.out)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
