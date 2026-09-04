from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_CATEGORIES = {"find-answer", "file-organize", "draft-deliverable", "framework-upkeep"}
VALID_CHECKS = {"file_exists", "file_not_exists", "file_contains", "file_unchanged"}


@dataclass(frozen=True)
class GoldenTask:
    id: str
    category: str
    prompt: str
    setup: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    judge: dict | None = None


def load_tasks(tasks_dir: Path) -> list[GoldenTask]:
    tasks = []
    seen_ids: set[str] = set()
    for f in sorted(tasks_dir.glob("*.yaml")):
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
        for key in ("id", "category", "prompt"):
            if key not in raw:
                raise ValueError(f"{f.name}: missing '{key}'")
        if raw["category"] not in VALID_CATEGORIES:
            raise ValueError(f"{f.name}: invalid category '{raw['category']}'")
        for c in raw.get("checks", []):
            if c.get("type") not in VALID_CHECKS:
                raise ValueError(f"{f.name}: invalid check type '{c.get('type')}'")
            if not c.get("path"):
                raise ValueError(f"{f.name}: check missing non-empty 'path'")
        if not raw.get("checks") and not raw.get("judge"):
            raise ValueError(f"{f.name}: task needs checks or judge")
        if "judge" in raw and raw["judge"] is not None:
            if not raw["judge"].get("rubric"):
                raise ValueError(f"{f.name}: judge requires non-empty rubric")
        if raw["id"] in seen_ids:
            raise ValueError(f"{f.name}: duplicate task id '{raw['id']}'")
        seen_ids.add(raw["id"])
        tasks.append(
            GoldenTask(
                id=raw["id"],
                category=raw["category"],
                prompt=raw["prompt"],
                setup=raw.get("setup", []),
                checks=raw.get("checks", []),
                judge=raw.get("judge"),
            )
        )
    return tasks
