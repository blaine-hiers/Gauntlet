from pathlib import Path

import pytest


@pytest.fixture
def fake_framework(tmp_path: Path) -> Path:
    root = tmp_path / "framework"
    (root / "Projects" / "Alpha").mkdir(parents=True)
    (root / "_intake").mkdir()
    (root / "indexes").mkdir()
    (root / "CLAUDE.md").write_text(
        "# Framework Rules\n"
        "\n"
        "You MUST ALWAYS update `indexes/master-index.md` after filing.\n"
        "\n"
        "## Filing\n"
        "\n"
        "NEVER leave files in `_intake/`. Destinations live under `Projects/`.\n"
        "\n"
        "## Ghost Section\n"
        "\n"
        "ALWAYS consult `Old-Folder/legacy.md` before every task.\n",
        encoding="utf-8",
    )
    (root / "indexes" / "master-index.md").write_text("# Master Index\n", encoding="utf-8")
    (root / "Projects" / "Alpha" / "status.md").write_text(
        "Alpha status: on track\n", encoding="utf-8"
    )
    (root / "~$temp.docx").write_text("junk", encoding="utf-8")
    return root
