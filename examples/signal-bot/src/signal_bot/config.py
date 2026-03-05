"""Configuration loading — YAML defaults + environment variable overrides."""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def load_config(config_path: str | None = None) -> dict:
    """Load config from YAML file, then override with environment variables."""
    if config_path:
        path = Path(config_path)
    else:
        path = Path(__file__).resolve().parents[2] / "config" / "defaults.yaml"

    if path.exists():
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    # Ensure nested dicts exist
    cfg.setdefault("signal", {})
    cfg.setdefault("agdel", {})

    # Environment variable overrides
    if os.environ.get("SIGNAL_COIN"):
        cfg["signal"]["coin"] = os.environ["SIGNAL_COIN"]
    if os.environ.get("SIGNAL_INTERVAL"):
        cfg["signal"]["interval_seconds"] = int(os.environ["SIGNAL_INTERVAL"])
    if os.environ.get("SIGNAL_CANDLE_COUNT"):
        cfg["signal"]["candle_count"] = int(os.environ["SIGNAL_CANDLE_COUNT"])
    if os.environ.get("SIGNAL_HORIZON"):
        cfg["signal"]["horizon"] = os.environ["SIGNAL_HORIZON"]

    if os.environ.get("AGDEL_API_URL"):
        cfg["agdel"]["api_url"] = os.environ["AGDEL_API_URL"]
    if os.environ.get("AGDEL_DRY_RUN"):
        cfg["agdel"]["dry_run"] = os.environ["AGDEL_DRY_RUN"].lower() in ("true", "1", "yes")

    return cfg
