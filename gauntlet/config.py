import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Variant:
    name: str
    claude_md: str | None  # None = snapshot's own CLAUDE.md; "" = no CLAUDE.md; else path to replacement


@dataclass(frozen=True)
class Config:
    synced_root: Path
    model: str
    judge_model: str
    max_turns: int
    timeout_s: int
    exclude: list[str]
    variants: list[Variant]
    # Where snapshot + run data live. None = <project root>/data. Set this when the
    # harness itself lives inside a synced library, so its 300MB+ snapshot of that
    # library never lands back in the library (recursion + sync bloat).
    data_dir: Path | None = None
    # Where per-run scratch directories are created. None = the system temp dir.
    # Set this when the temp dir sits under a directory holding a CLAUDE.md —
    # on Windows it lives under the user's home, so ~/.claude/CLAUDE.md would be
    # inherited by every run; the runner refuses to start in that case.
    work_root: Path | None = None


def _optional_path(raw: dict, key: str) -> Path | None:
    return Path(os.path.expandvars(raw[key])).expanduser() if raw.get(key) else None


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in ("synced_root", "model", "judge_model", "variants") if k not in raw]
    if missing:
        raise ValueError(f"{path.name} missing keys: {missing}")
    variants = [Variant(name=n, claude_md=v) for n, v in raw["variants"].items()]
    return Config(
        synced_root=Path(raw["synced_root"]),
        model=raw["model"],
        judge_model=raw["judge_model"],
        max_turns=raw.get("max_turns", 30),
        timeout_s=raw.get("timeout_s", 900),
        exclude=raw.get("exclude", []),
        variants=variants,
        data_dir=_optional_path(raw, "data_dir"),
        work_root=_optional_path(raw, "work_root"),
    )
