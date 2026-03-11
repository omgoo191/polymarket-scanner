"""
config/loader.py — Loads and validates config.yaml
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).parent / "config.yaml"
EXAMPLE_PATH = Path(__file__).parent / "config.example.yaml"


class ConfigError(Exception):
    pass


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    path = CONFIG_PATH
    if not path.exists():
        raise ConfigError(
            f"config.yaml not found at {path}. "
            f"Copy config.example.yaml → config.yaml and fill in your credentials."
        )
    with open(path) as f:
        cfg = yaml.safe_load(f)

    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    required = [
        ("telegram", "bot_token"),
        ("telegram", "chat_id"),
        ("polygonscan", "api_key"),
        ("database", "url"),
    ]
    for section, key in required:
        val = cfg.get(section, {}).get(key, "")
        if not val or "YOUR_" in str(val):
            raise ConfigError(
                f"Missing required config: {section}.{key}  "
                f"(edit config/config.yaml)"
            )


def get(section: str, key: str, default: Any = None) -> Any:
    cfg = load_config()
    return cfg.get(section, {}).get(key, default)
