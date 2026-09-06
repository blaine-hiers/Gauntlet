from dataclasses import dataclass, field
from pathlib import Path

import yaml

from gauntlet.scoring import RESPONSE_TARGET

VALID_CATEGORIES = {"find-answer", "file-organize", "draft-deliverable", "framework-upkeep"}
VALID_CHECKS = {
    "file_exists",
    "file_not_exists",
    "file_contains",
    "file_unchanged",
    "file_not_contains",
    "file_matches",
    "snapshot_unchanged",
}
# snapshot_unchanged diffs the whole run dir against the manifest instead of one
# path, so it takes an optional 'allow' glob list instead of a required 'path'.
CHECKS_WITHOUT_PATH = {"snapshot_unchanged"}
# $response reads text content (the model's answer), so it is only meaningful
# for the content checks. file_exists/file_not_exists always see it as present
# (nothing to check), and file_unchanged would hash a file that never exists,
# crashing at run time — reject all three at load time instead.
RESPONSE_TARGET_CHECKS = {"file_contains", "file_not_contains", "file_matches"}


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
            ctype = c.get("type")
            if ctype not in VALID_CHECKS:
                raise ValueError(f"{f.name}: invalid check type '{c.get('type')}'")
            if ctype not in CHECKS_WITHOUT_PATH and not c.get("path"):
                raise ValueError(f"{f.name}: check missing non-empty 'path'")
            if c.get("path") == RESPONSE_TARGET and ctype not in RESPONSE_TARGET_CHECKS:
                raise ValueError(
                    f"{f.name}: '{RESPONSE_TARGET}' path is only valid for "
                    f"{sorted(RESPONSE_TARGET_CHECKS)}, not '{ctype}'"
                )
            if ctype in ("file_contains", "file_not_contains") and not c.get("text"):
                raise ValueError(f"{f.name}: {ctype} check missing non-empty 'text'")
            if ctype == "file_matches" and not c.get("pattern"):
                raise ValueError(f"{f.name}: file_matches check missing non-empty 'pattern'")
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
