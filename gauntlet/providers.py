"""The provider seam.

Gauntlet exists to compare CLAUDE.md variants against a fixed corpus, fixed
tasks, fixed checks and a fixed judge. The thing that *executes* a task is one
more axis of that comparison rather than a permanent dependency, so it belongs
behind an interface.

The interface is the point, not a repo boundary. Comparing a local model against
Claude Code is only worth anything when the corpus, the tasks, the checks and the
judge are identical, and that holds inside one harness and nowhere else.

A provider is given a prepared run directory and a task, and returns a result row
in the shape the rest of the harness already reads. Anything a provider cannot
honestly report is None rather than zero: a missing number is a gap, and a zero
sitting next to Claude's real spend is a lie that flatters whoever reported it.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Protocol, runtime_checkable

from gauntlet.runner import execute

# The implicit provider a config without a `providers` map describes. Rows
# written before this axis existed carry no provider field and were all produced
# by the Claude CLI, so readers default to this name and stay comparable.
DEFAULT_PROVIDER_NAME = "claude"
CLAUDE_CLI = "claude-cli"


@runtime_checkable
class AgentProvider(Protocol):
    """What the runner needs from anything that can work a task."""

    name: str  # provider id as it appears on rows and in the report
    kind: str  # implementation family; two names may share one kind
    model: str  # model identifier within that provider

    def run(self, task, variant, run_dir: Path) -> dict:
        """Execute one task in run_dir and return a result row."""
        ...


class ClaudeCliProvider:
    """Today's behaviour, unchanged, behind the interface.

    This is a pure refactor: it calls runner.execute with the same Config it
    always received, so the isolation flags, the timeout handling and the row
    shape are whatever runner.execute does. The existing runner tests cover it
    and were not touched.
    """

    kind = CLAUDE_CLI

    def __init__(self, name: str, model: str, cfg, execute_fn=None):
        self.name = name
        self.model = model
        # Config is frozen, so the per-provider model is applied by replacement
        # rather than by mutating the caller's config.
        self._cfg = dataclasses.replace(cfg, model=model)
        # The caller may hand in its own execute. That keeps the CLI's module
        # binding the single seam the tests already stub, instead of forcing
        # every existing test to learn where this class imports from.
        self._execute = execute_fn or execute

    def run(self, task, variant, run_dir: Path) -> dict:
        row = self._execute(task, variant, run_dir, self._cfg)
        row["provider"] = self.name
        row["provider_kind"] = self.kind
        return row


def resolve_providers(cfg, execute_fn=None) -> list[AgentProvider]:
    """Turn the config into the ordered provider list a run iterates.

    An absent or empty `providers` map means one implicit Claude provider on
    `cfg.model`. That is what every config written before this axis existed
    describes, and it is what `--model` keeps overriding, so neither has to
    change to keep working.
    """
    spec_map = getattr(cfg, "providers", None)
    if not spec_map:
        return [ClaudeCliProvider(DEFAULT_PROVIDER_NAME, cfg.model, cfg, execute_fn)]

    out: list[AgentProvider] = []
    for name, spec in spec_map.items():
        spec = spec or {}
        kind = spec.get("kind", CLAUDE_CLI)
        if kind != CLAUDE_CLI:
            # Deliberately a hard error rather than a skip. Silently dropping a
            # provider would produce a report that looks complete and is missing
            # an axis the config asked for.
            raise ValueError(
                f"provider {name!r}: unknown kind {kind!r} "
                f"(known kinds: {CLAUDE_CLI})"
            )
        out.append(ClaudeCliProvider(name, spec.get("model", cfg.model), cfg, execute_fn))
    return out
