from pathlib import Path

from gauntlet.snapshot import sha256_file


def run_checks(checks: list[dict], run_dir: Path, manifest: dict[str, str]) -> list[dict]:
    results = []
    for check in checks:
        kind = check["type"]
        rel = check.get("path", "")
        target = run_dir / rel
        if kind == "file_exists":
            passed = target.is_file()
        elif kind == "file_not_exists":
            passed = not target.exists()
        elif kind == "file_contains":
            passed = (
                target.is_file()
                and check["text"].lower()
                in target.read_text(encoding="utf-8", errors="replace").lower()
            )
        elif kind == "file_unchanged":
            passed = target.is_file() and sha256_file(target) == manifest.get(
                Path(rel).as_posix()
            )
        else:
            raise ValueError(f"unknown check type: {kind}")
        results.append({**check, "passed": passed})
    return results
