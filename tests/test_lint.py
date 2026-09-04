from gauntlet.lint import lint_claude_md


def test_lint_reports_sections_imperatives_and_dead_paths(fake_framework):
    text = (fake_framework / "CLAUDE.md").read_text(encoding="utf-8")
    reports = lint_claude_md(text, fake_framework)

    by_heading = {r.heading: r for r in reports}
    assert set(by_heading) == {"Framework Rules", "Filing", "Ghost Section"}

    assert by_heading["Framework Rules"].imperative_count == 2  # MUST, ALWAYS
    assert by_heading["Framework Rules"].dead_paths == []

    assert by_heading["Filing"].dead_paths == []  # _intake/ and Projects/ exist

    ghost = by_heading["Ghost Section"]
    assert ghost.dead_paths == ["Old-Folder/legacy.md"]
    assert ghost.imperative_count == 1  # ALWAYS
    assert ghost.est_tokens > 0
