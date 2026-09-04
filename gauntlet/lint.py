import re
from dataclasses import dataclass
from pathlib import Path

IMPERATIVES = re.compile(r"\b(MUST|ALWAYS|NEVER|DO NOT)\b")
PATH_REF = re.compile(r"`([^`\n]*[/\\][^`\n]*)`")


@dataclass(frozen=True)
class SectionReport:
    heading: str
    est_tokens: int
    imperative_count: int
    dead_paths: list[str]


def _split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading, buf = "(preamble)", []
    for line in text.splitlines():
        if line.startswith("#"):
            sections.append((heading, "\n".join(buf)))
            heading, buf = line.lstrip("# ").strip(), []
        else:
            buf.append(line)
    sections.append((heading, "\n".join(buf)))
    return [(h, b) for h, b in sections if h != "(preamble)" or b.strip()]


def lint_claude_md(text: str, root: Path) -> list[SectionReport]:
    reports = []
    for heading, body in _split_sections(text):
        dead = []
        for m in PATH_REF.finditer(body):
            ref = m.group(1).strip().strip("/\\")
            if "*" in ref or ref.startswith("http"):
                continue
            if not (root / ref).exists():
                dead.append(ref)
        reports.append(
            SectionReport(
                heading=heading,
                est_tokens=len(body) // 4,
                imperative_count=len(IMPERATIVES.findall(body)),
                dead_paths=dead,
            )
        )
    return reports
