import dataclasses

import pytest

from gauntlet.config import Config, Variant
from gauntlet.providers import (
    CLAUDE_CLI,
    DEFAULT_PROVIDER_NAME,
    AgentProvider,
    ClaudeCliProvider,
    resolve_providers,
)


def cfg(**over):
    base = Config(
        synced_root=None,
        model="claude-fable-5",
        judge_model="claude-fable-5",
        max_turns=30,
        timeout_s=900,
        exclude=[],
        variants=[Variant("current", None), Variant("empty", "")],
    )
    return dataclasses.replace(base, **over)


def test_no_providers_key_is_one_implicit_claude_provider():
    # Every config written before this axis existed says exactly this, so the
    # absent key has to keep meaning what it always meant.
    (p,) = resolve_providers(cfg())
    assert p.name == DEFAULT_PROVIDER_NAME
    assert p.kind == CLAUDE_CLI
    assert p.model == "claude-fable-5"
    assert isinstance(p, AgentProvider)


def test_empty_providers_map_is_treated_as_absent():
    (p,) = resolve_providers(cfg(providers={}))
    assert p.name == DEFAULT_PROVIDER_NAME


def test_providers_map_resolves_in_order_and_inherits_the_default_model():
    providers = resolve_providers(cfg(providers={
        "fast": {"model": "claude-haiku-4-5"},
        "deep": {"kind": CLAUDE_CLI, "model": "claude-opus-5"},
        "inherits": {},
    }))
    assert [p.name for p in providers] == ["fast", "deep", "inherits"]
    assert [p.model for p in providers] == [
        "claude-haiku-4-5", "claude-opus-5", "claude-fable-5",
    ]


def test_unknown_kind_raises_rather_than_being_skipped():
    # Dropping it silently would produce a report that looks complete while
    # missing an axis the config asked for.
    with pytest.raises(ValueError, match="unknown kind"):
        resolve_providers(cfg(providers={"local": {"kind": "openai-compatible"}}))


def test_claude_provider_stamps_identity_and_applies_its_own_model():
    seen = {}

    def fake_execute(task, variant, run_dir, config):
        seen["model"] = config.model
        return {"task_id": "t", "variant": "current", "output_text": "", "is_error": False}

    p = ClaudeCliProvider("deep", "claude-opus-5", cfg(), fake_execute)
    row = p.run(object(), object(), None)
    assert seen["model"] == "claude-opus-5"      # not the config default
    assert row["provider"] == "deep"
    assert row["provider_kind"] == CLAUDE_CLI


def test_model_override_still_reaches_the_implicit_provider():
    # --model replaces cfg.model upstream; the implicit provider must follow it,
    # or the override would silently stop working the moment this seam landed.
    (p,) = resolve_providers(cfg(model="claude-opus-5"))
    assert p.model == "claude-opus-5"
