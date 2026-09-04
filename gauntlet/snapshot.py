import hashlib
import json
import os
import shutil
import stat
from fnmatch import fnmatch
from pathlib import Path


def _rmtree_force(path: Path) -> None:
    """rmtree that clears Windows read-only attributes blocking deletion."""

    def onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=onerror)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_path(dest: Path) -> Path:
    return dest.parent / f"{dest.name}.manifest.json"


def copy_tree_tolerant(src: Path, dest: Path, exclude: list[str]) -> list[str]:
    """Copy src to dest, skipping exclude globs and tolerating uncopyable files
    (sync-client locks, over-long Windows paths). Returns the skipped relative paths."""
    if dest.exists():
        _rmtree_force(dest)
    dest.mkdir(parents=True)

    def excluded(name: str) -> bool:
        return any(fnmatch(name, pat) for pat in exclude)

    skipped: list[str] = []
    for src_dir, dirnames, filenames in os.walk(src):
        rel = Path(src_dir).relative_to(src)
        dirnames[:] = [d for d in dirnames if not excluded(d)]
        for fn in filenames:
            if excluded(fn):
                continue
            target = dest / rel / fn
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(src_dir) / fn, target)
            except OSError:
                skipped.append((rel / fn).as_posix())
    return skipped


def make_snapshot(synced_root: Path, dest: Path, exclude: list[str]) -> dict[str, str]:
    """Snapshot synced_root to dest via copy_tree_tolerant; skipped files are
    reported, not fatal."""
    skipped = copy_tree_tolerant(synced_root, dest, exclude)
    if skipped:
        print(f"snapshot: skipped {len(skipped)} unreadable file(s):")
        for s in skipped:
            print(f"  ! {s}")

    manifest = {
        f.relative_to(dest).as_posix(): sha256_file(f)
        for f in sorted(dest.rglob("*"))
        if f.is_file()
    }
    _manifest_path(dest).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def load_manifest(dest: Path) -> dict[str, str]:
    return json.loads(_manifest_path(dest).read_text(encoding="utf-8"))
