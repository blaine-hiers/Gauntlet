import re
from fnmatch import fnmatch
from pathlib import Path

from gauntlet.snapshot import sha256_file

# A check's 'path' can name this sentinel instead of a file to target the
# model's own answer text rather than something on disk (e.g. asserting a
# wrong-answer trap never appears in the response).
RESPONSE_TARGET = "$response"


def _snapshot_diff(run_dir: Path, manifest: dict[str, str], allow: list[str]) -> list[str]:
    """Every path that differs between run_dir and the manifest it was copied
    from: added, removed, or modified. Excludes anything matching an `allow`
    glob (e.g. the CLAUDE.md variant swap and task setup files, which are
    expected, intentional differences from the frozen snapshot)."""

    def allowed(rel: str) -> bool:
        return any(fnmatch(rel, pat) for pat in allow)

    current = {
        f.relative_to(run_dir).as_posix(): f
        for f in run_dir.rglob("*")
        if f.is_file()
    }
    drifted = []
    for rel, expected_hash in manifest.items():
        if allowed(rel):
            continue
        f = current.get(rel)
        if f is None or sha256_file(f) != expected_hash:
            drifted.append(rel)
    for rel in current:
        if rel not in manifest and not allowed(rel):
            drifted.append(rel)
    return sorted(set(drifted))


def run_checks(
    checks: list[dict], run_dir: Path, manifest: dict[str, str], output_text: str = ""
) -> list[dict]:
    results = []
    for check in checks:
        kind = check["type"]
        rel = check.get("path", "")
        if kind == "snapshot_unchanged":
            drifted = _snapshot_diff(run_dir, manifest, check.get("allow", []))
            results.append(
                {**check, "passed": not drifted, "drifted_count": len(drifted), "drifted": drifted}
            )
            continue

        if rel == RESPONSE_TARGET:
            exists = True
            content = output_text
        else:
            target = run_dir / rel
            exists = target.is_file()
            content = target.read_text(encoding="utf-8", errors="replace") if exists else ""

        if kind == "file_exists":
            passed = exists
        elif kind == "file_not_exists":
            passed = not exists
        elif kind == "file_contains":
            passed = exists and check["text"].lower() in content.lower()
        elif kind == "file_not_contains":
            passed = not (exists and check["text"].lower() in content.lower())
        elif kind == "file_matches":
            passed = exists and re.search(check["pattern"], content) is not None
        elif kind == "file_unchanged":
            passed = exists and sha256_file(run_dir / rel) == manifest.get(Path(rel).as_posix())
        else:
            raise ValueError(f"unknown check type: {kind}")
        results.append({**check, "passed": passed})
    return results
