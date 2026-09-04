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
