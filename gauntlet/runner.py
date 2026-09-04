import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from gauntlet.config import Config, Variant
from gauntlet.snapshot import copy_tree_tolerant
from gauntlet.tasks import GoldenTask


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
) -> Path:
    # Short hashed dir name: Windows caps paths at 260 chars, and deep framework
    # trees only fit if the run-dir prefix stays shorter than the snapshot's own.
    name = hashlib.sha1(f"{task.id}--{variant.name}".encode()).hexdigest()[:10]
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
    }
