import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from gauntlet.config import Config, Variant
from gauntlet.snapshot import copy_tree_tolerant
from gauntlet.tasks import GoldenTask


# The run dir under the temp root keeps *project*-side context out of a run, but
# the CLI also loads the invoking user's ~/.claude settings (plugins, enabled
# skills, MCP servers). Dropping the `user` setting source and refusing every MCP
# server not passed explicitly closes that channel, so the `empty` variant is
# actually empty. Stamped onto every result row so old runs are self-describing.
ISOLATION_FLAGS = ["--setting-sources", "project,local", "--strict-mcp-config"]

# What the CLI looks for at every level from cwd up to the filesystem root.
CONTEXT_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")


def ancestor_context_files(path: Path) -> list[Path]:
    """Context files the CLI would auto-load into a run at `path` from its
    ancestors. The setting-source flags do not touch this walk, so a run dir
    under any directory holding one inherits it. On Windows the system temp
    dir sits under the user's home, which puts ~/.claude/CLAUDE.md on the walk
    as if it were project context — verified against claude 2.1.261."""
    found = []
    for ancestor in Path(path).resolve().parents:
        for name in CONTEXT_FILES:
            f = ancestor / name
            if f.is_file():
                found.append(f)
    return found


def find_claude() -> str:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found on PATH")
    return exe


def prepare_run_dir(
    snapshot_dir: Path,
    work_root: Path,
    task: GoldenTask,
    variant: Variant,
    tasks_dir: Path,
    project_root: Path,
    repeat_idx: int = 0,
    model: str = "",
) -> Path:
    # Short hashed dir name: Windows caps paths at 260 chars, and deep framework
    # trees only fit if the run-dir prefix stays shorter than the snapshot's own.
    # Every axis of the result key (model, repeat) is in the hash so no cell ever
    # reuses a path whose previous occupant failed to delete.
    name = hashlib.sha1(
        f"{task.id}--{variant.name}--{model}--{repeat_idx}".encode()
    ).hexdigest()[:10]
    run_dir = work_root / name
    skipped = copy_tree_tolerant(snapshot_dir, run_dir, exclude=[])
    if skipped:
        print(f"{task.id} × {variant.name}: run copy skipped {len(skipped)} file(s)")

    claude_md = run_dir / "CLAUDE.md"
    if variant.claude_md == "":
        claude_md.unlink(missing_ok=True)
    elif variant.claude_md is not None:
        src = project_root / variant.claude_md
        claude_md.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    for step in task.setup:
        dst = run_dir / step["to"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tasks_dir / step["copy"], dst)
    return run_dir


def execute(task: GoldenTask, variant: Variant, run_dir: Path, cfg: Config) -> dict:
    cmd = [
        find_claude(),
        "-p",
        task.prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "--model",
        cfg.model,
        "--max-turns",
        str(cfg.max_turns),
        *ISOLATION_FLAGS,
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=run_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cfg.timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        duration = time.monotonic() - start
        stdout = e.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {
            "task_id": task.id,
            "category": task.category,
            "variant": variant.name,
            "model": cfg.model,
            "duration_s": round(duration, 1),
            "cost_usd": None,
            "output_text": stdout,
            "exit_code": None,
            "is_error": True,
            "isolation": list(ISOLATION_FLAGS),
        }
    duration = time.monotonic() - start
    try:
        payload = json.loads(proc.stdout)
        is_error = bool(payload.get("is_error", False)) or proc.returncode != 0
    except json.JSONDecodeError:
        payload = {"result": proc.stdout, "total_cost_usd": None}
        is_error = True
    return {
        "task_id": task.id,
        "category": task.category,
        "variant": variant.name,
        "model": cfg.model,
        "duration_s": round(duration, 1),
        "cost_usd": payload.get("total_cost_usd"),
        "output_text": payload.get("result", ""),
        "exit_code": proc.returncode,
        "is_error": is_error,
        "isolation": list(ISOLATION_FLAGS),
    }
