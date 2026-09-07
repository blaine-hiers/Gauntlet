import json
from pathlib import Path

import pytest

from gauntlet.config import load_config


def write_config(tmp_path: Path, **overrides) -> Path:
    raw = {
        "synced_root": "C:/fake/root",
        "model": "claude-fable-5",
        "judge_model": "claude-fable-5",
        "variants": {"current": None, "empty": ""},
    }
    raw.update(overrides)
    p = tmp_path / "gauntlet.config.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def test_load_config_parses_fields_and_defaults(tmp_path):
    cfg = load_config(write_config(tmp_path))
    assert cfg.synced_root == Path("C:/fake/root")
    assert cfg.model == "claude-fable-5"
    assert cfg.max_turns == 30
    assert cfg.timeout_s == 900
    assert cfg.exclude == []
    names = {v.name: v.claude_md for v in cfg.variants}
    assert names == {"current": None, "empty": ""}


def test_load_config_data_dir_default_none(tmp_path):
    cfg = load_config(write_config(tmp_path))
    assert cfg.data_dir is None


def test_load_config_data_dir_expands_env_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("GAUNTLET_TEST_BASE", str(tmp_path))
    cfg = load_config(write_config(tmp_path, data_dir="%GAUNTLET_TEST_BASE%/gauntlet-data"))
    assert cfg.data_dir == tmp_path / "gauntlet-data"


def test_load_config_data_dir_expands_posix_env_vars(tmp_path, monkeypatch):
    # The %VAR% case above is what a Windows-authored config looks like. Both
    # syntaxes have to resolve on every platform, or a config travels badly.
    monkeypatch.setenv("GAUNTLET_TEST_BASE", str(tmp_path))
    cfg = load_config(write_config(tmp_path, data_dir="$GAUNTLET_TEST_BASE/gauntlet-data"))
    assert cfg.data_dir == tmp_path / "gauntlet-data"


def test_load_config_data_dir_leaves_undefined_env_var_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("GAUNTLET_TEST_UNSET", raising=False)
    cfg = load_config(write_config(tmp_path, data_dir="%GAUNTLET_TEST_UNSET%/gauntlet-data"))
    assert cfg.data_dir == Path("%GAUNTLET_TEST_UNSET%/gauntlet-data")


def test_load_config_missing_key_raises(tmp_path):
    p = tmp_path / "gauntlet.config.json"
    p.write_text(json.dumps({"model": "claude-fable-5"}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        load_config(p)
