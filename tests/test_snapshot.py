import json

from gauntlet.snapshot import load_manifest, load_snapshot_provenance, make_snapshot


def test_make_snapshot_copies_excludes_and_manifests(fake_framework, tmp_path):
    dest = tmp_path / "snap"
    manifest = make_snapshot(fake_framework, dest, exclude=["~$*", "*.tmp"])

    assert (dest / "CLAUDE.md").is_file()
    assert (dest / "Projects" / "Alpha" / "status.md").is_file()
    assert not (dest / "~$temp.docx").exists()

    assert "indexes/master-index.md" in manifest
    assert all(len(h) == 64 for h in manifest.values())
    assert load_manifest(dest) == manifest


def test_make_snapshot_stamps_provenance(fake_framework, tmp_path):
    dest = tmp_path / "snap"
    manifest = make_snapshot(fake_framework, dest, exclude=["~$*"])

    provenance = load_snapshot_provenance(dest)
    assert provenance["source_root"] == str(fake_framework)
    assert provenance["file_count"] == len(manifest)
    assert "T" in provenance["timestamp"]  # ISO 8601
    assert len(provenance["manifest_hash"]) == 64

    # Re-snapshotting an unchanged source reproduces the same manifest hash.
    provenance2 = load_snapshot_provenance(dest)
    make_snapshot(fake_framework, dest, exclude=["~$*"])
    assert load_snapshot_provenance(dest)["manifest_hash"] == provenance2["manifest_hash"]


def test_load_snapshot_provenance_missing_returns_none(tmp_path):
    assert load_snapshot_provenance(tmp_path / "nonexistent") is None


def test_make_snapshot_overwrites_previous(fake_framework, tmp_path):
    dest = tmp_path / "snap"
    make_snapshot(fake_framework, dest, exclude=[])
    (dest / "stale.txt").write_text("stale", encoding="utf-8")
    manifest = make_snapshot(fake_framework, dest, exclude=[])
    assert not (dest / "stale.txt").exists()
    assert "stale.txt" not in manifest


def test_make_snapshot_skips_unreadable_files(fake_framework, tmp_path, monkeypatch, capsys):
    import shutil as _shutil

    from gauntlet import snapshot as snap

    real_copy2 = _shutil.copy2

    def flaky_copy2(src, dst, **kwargs):
        if "status.md" in str(src):
            raise OSError(32, "file in use")
        return real_copy2(src, dst, **kwargs)

    monkeypatch.setattr(snap.shutil, "copy2", flaky_copy2)
    dest = tmp_path / "snap"
    manifest = make_snapshot(fake_framework, dest, exclude=[])

    assert "Projects/Alpha/status.md" not in manifest
    assert "indexes/master-index.md" in manifest
    assert "skipped 1 unreadable" in capsys.readouterr().out
