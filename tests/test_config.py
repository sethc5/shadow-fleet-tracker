"""Tests for configuration loader."""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.config import _deep_merge, load_config, reset_config, DEFAULTS


def test_load_default_config():
    """Config loads defaults when no file exists."""
    reset_config()
    config = load_config(Path("/nonexistent/config.yaml"))
    assert config["scoring"]["alert_threshold"] == 60
    assert config["scoring"]["weights"]["sanctioned"] == 100
    assert "CM" in config["scoring"]["high_risk_flags"]


def test_load_custom_config():
    """Config merges custom values with defaults."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({"scoring": {"alert_threshold": 75}}, f)
        f.flush()
        config = load_config(Path(f.name))

    assert config["scoring"]["alert_threshold"] == 75
    # Other defaults preserved
    assert config["scoring"]["weights"]["sanctioned"] == 100
    assert config["output"]["digest_max_vessels"] == 20

    Path(f.name).unlink()


def test_deep_merge():
    """Deep merge preserves nested defaults."""
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    override = {"a": {"b": 99}}
    result = _deep_merge(base, override)
    assert result["a"]["b"] == 99
    assert result["a"]["c"] == 2
    assert result["d"] == 3


def test_deep_merge_new_keys():
    """Deep merge adds new keys."""
    base = {"a": 1}
    override = {"b": 2}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": 2}


def test_config_override_weights():
    """Config overrides specific scoring weights."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({"scoring": {"weights": {"sanctioned": 50, "flag_high_risk": 30}}}, f)
        f.flush()
        config = load_config(Path(f.name))

    assert config["scoring"]["weights"]["sanctioned"] == 50
    assert config["scoring"]["weights"]["flag_high_risk"] == 30
    # Unchanged defaults
    assert config["scoring"]["weights"]["ais_dark_russia"] == 25

    Path(f.name).unlink()


def test_config_missing_file_falls_back():
    """Missing config file returns defaults without error."""
    config = load_config(Path("/tmp/does_not_exist_12345.yaml"))
    assert config == DEFAULTS


def test_config_output_settings():
    """Output settings load from config."""
    reset_config()
    config = load_config(Path("config.yaml"))
    assert config["output"]["digest_max_vessels"] == 20
    assert config["output"]["digest_max_alerts"] == 50