"""Config loading and validation."""

from __future__ import annotations

import json

import pytest

from nexus import ConfigError, load_config


def _write(tmp_path, cfg):
    p = tmp_path / "agent.json"
    p.write_text(json.dumps(cfg))
    return p


def test_loads_the_real_config():
    cfg = load_config()
    assert cfg["identity"]["name"] == "NEXUS"
    assert cfg["model"]["planner_model"] == "claude-opus-5"
    # sampling params must NOT be present — they 400 on this model
    assert "temperature" not in cfg["model"]
    assert cfg["model"]["effort"] == "high"


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.json")


def test_malformed_json_is_a_clear_error(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(p)


def test_missing_required_key(tmp_path):
    p = _write(tmp_path, {"identity": {"name": "X"}})
    with pytest.raises(ConfigError, match="missing required key"):
        load_config(p)


def test_bad_default_tier_rejected(tmp_path, config):
    config["permissions"]["default_tier"] = "yolo"
    with pytest.raises(ConfigError, match="default_tier"):
        load_config(_write(tmp_path, config))


def test_bad_tool_tier_rejected(tmp_path, config):
    config["permissions"]["tool_tiers"]["web_search"] = "sometimes"
    with pytest.raises(ConfigError, match="tool_tiers"):
        load_config(_write(tmp_path, config))


def test_empty_enabled_tools_rejected(tmp_path, config):
    config["enabled_tools"] = []
    with pytest.raises(ConfigError, match="enabled_tools"):
        load_config(_write(tmp_path, config))


def test_non_positive_limit_rejected(tmp_path, config):
    config["limits"]["max_steps"] = 0
    with pytest.raises(ConfigError, match="max_steps"):
        load_config(_write(tmp_path, config))
