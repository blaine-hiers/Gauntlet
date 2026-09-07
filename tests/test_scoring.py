from gauntlet.scoring import run_checks
from gauntlet.snapshot import make_snapshot


def test_run_checks_all_types(fake_framework, tmp_path):
    snap = tmp_path / "snap"
    manifest = make_snapshot(fake_framework, snap, exclude=[])

    # simulate a model run: filed a doc, updated the index, left status.md alone
    (snap / "Projects" / "Alpha" / "invoice.md").write_text("filed\n", encoding="utf-8")
    index = snap / "indexes" / "master-index.md"
    index.write_text(index.read_text(encoding="utf-8") + "- Invoice\n", encoding="utf-8")

    checks = [
        {"type": "file_exists", "path": "Projects/Alpha/invoice.md"},
        {"type": "file_not_exists", "path": "_intake/invoice.md"},
        {"type": "file_contains", "path": "indexes/master-index.md", "text": "invoice"},
        {"type": "file_unchanged", "path": "Projects/Alpha/status.md"},
        {"type": "file_unchanged", "path": "indexes/master-index.md"},  # was modified
    ]
    results = run_checks(checks, snap, manifest)
    assert [r["passed"] for r in results] == [True, True, True, True, False]


def test_file_not_contains(fake_framework, tmp_path):
    snap = tmp_path / "snap"
    manifest = make_snapshot(fake_framework, snap, exclude=[])
    checks = [
        {"type": "file_not_contains", "path": "indexes/master-index.md", "text": "invoice"},
        {"type": "file_not_contains", "path": "does/not/exist.md", "text": "anything"},
    ]
    results = run_checks(checks, snap, manifest)
    assert [r["passed"] for r in results] == [True, True]  # absent text, and absent file both pass

    index = snap / "indexes" / "master-index.md"
    index.write_text(index.read_text(encoding="utf-8") + "Invoice filed\n", encoding="utf-8")
    results = run_checks(checks, snap, manifest)
    assert results[0]["passed"] is False  # case-insensitive match now present


def test_file_matches_regex(fake_framework, tmp_path):
    snap = tmp_path / "snap"
    manifest = make_snapshot(fake_framework, snap, exclude=[])
    checks = [{"type": "file_matches", "path": "Projects/Alpha/status.md", "pattern": r"on\s+track"}]
    assert run_checks(checks, snap, manifest)[0]["passed"] is True

    checks = [{"type": "file_matches", "path": "Projects/Alpha/status.md", "pattern": r"off\s+track"}]
    assert run_checks(checks, snap, manifest)[0]["passed"] is False


def test_check_against_model_response_text(fake_framework, tmp_path):
    # $response targets the model's own answer text rather than a file on disk —
    # how a deterministic trap check (e.g. "must not say Jacksonville") is expressed.
    snap = tmp_path / "snap"
    manifest = make_snapshot(fake_framework, snap, exclude=[])
    checks = [{"type": "file_not_contains", "path": "$response", "text": "Jacksonville"}]
    assert run_checks(checks, snap, manifest, output_text="Three DCs: Ashgrove, Baytown, Cordell.")[
        0
    ]["passed"] is True
    assert run_checks(checks, snap, manifest, output_text="There is a Jacksonville DC.")[0][
        "passed"
    ] is False


def test_snapshot_unchanged_reports_drift(fake_framework, tmp_path):
    snap = tmp_path / "snap"
    manifest = make_snapshot(fake_framework, snap, exclude=[])

    checks = [{"type": "snapshot_unchanged"}]
    clean = run_checks(checks, snap, manifest)[0]
    assert clean["passed"] is True
    assert clean["drifted_count"] == 0

    # Model trashes an unrelated file and adds a new one — blast radius the
    # single-path file_unchanged check on a different file would never see.
    (snap / "indexes" / "master-index.md").write_text("wiped\n", encoding="utf-8")
    (snap / "Projects" / "Alpha" / "extra.md").write_text("surprise\n", encoding="utf-8")
    dirty = run_checks(checks, snap, manifest)[0]
    assert dirty["passed"] is False
    assert dirty["drifted_count"] == 2
    assert "indexes/master-index.md" in dirty["drifted"]
    assert "Projects/Alpha/extra.md" in dirty["drifted"]


def test_snapshot_unchanged_allow_list_excludes_known_changes(fake_framework, tmp_path):
    # The CLAUDE.md variant swap and task setup files are expected, intentional
    # differences from the snapshot — an `allow` glob keeps them out of the diff.
    snap = tmp_path / "snap"
    manifest = make_snapshot(fake_framework, snap, exclude=[])
    (snap / "CLAUDE.md").unlink()  # simulates the `empty` variant

    checks = [{"type": "snapshot_unchanged", "allow": ["CLAUDE.md"]}]
    result = run_checks(checks, snap, manifest)[0]
    assert result["passed"] is True
    assert result["drifted_count"] == 0
