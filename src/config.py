"""Configuration loader — reads config.yaml with fallback defaults."""

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")

# Hardcoded defaults (used if config.yaml is missing or partial)
DEFAULTS = {
    "scoring": {
        "weights": {
            "sanctioned": 100,
            "ais_dark_russia": 25,
            "ais_dark_open": 15,
            "flag_high_risk": 20,
            "flag_change_90d": 20,
            "russian_port_30d": 25,
            "sts_activity": 25,
            "ownership_change_90d": 15,
            "age_over_20": 10,
        },
        "alert_threshold": 60,
        "high_risk_flags": ["CM", "SL", "KM", "PW", "CK", "TZ"],
    },
    "ingestion": {
        "max_retries": 3,
        "retry_delay_seconds": 5,
        "timeout_seconds": 30,
        "batch_size": 100,
        "rate_limit_delay": 1.5,
    },
    "output": {
        "digest_max_vessels": 20,
        "digest_max_alerts": 50,
    },
    "api": {
        "auth_key": "",
        "rate_limit_per_minute": 60,
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
        "alert_chat_id": "",
    },
    "osintukraine": {
        "enabled": False,
        "api_url": "",
        "api_key": "",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load configuration from YAML file, merging with defaults.

    Returns a dict with all config values (defaults + overrides).
    """
    config = DEFAULTS.copy()

    if path is None:
        path = DEFAULT_CONFIG_PATH

    if path.exists():
        try:
            with open(path, "r") as f:
                user_config = yaml.safe_load(f)
            if user_config and isinstance(user_config, dict):
                config = _deep_merge(DEFAULTS, user_config)
        except (yaml.YAMLError, OSError) as e:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to load config from %s: %s, using defaults", path, e
            )

    return config


# Module-level singleton
_config: dict[str, Any] | None = None


def get_config(path: Path | None = None) -> dict[str, Any]:
    """Get config (cached after first load)."""
    global _config
    if _config is None:
        _config = load_config(path)
    return _config


def reset_config():
    """Reset cached config (for testing)."""
    global _config
    _config = None