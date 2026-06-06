"""Load and expose typed config from config.yaml, with optional account overrides from env."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).parent.parent


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    cfg_path = Path(os.environ.get("COMMODITY_CONFIG", _ROOT / "config.yaml"))
    with cfg_path.open() as fh:
        cfg = yaml.safe_load(fh)

    # Apply account overrides from ACCOUNT_OVERRIDES env var (JSON).
    # Format: {"CompanyName": {"relationship": "client", "priority": "high"}, ...}
    # Set this as a GitHub Secret to keep relationship/priority out of the public repo.
    raw = os.environ.get("ACCOUNT_OVERRIDES", "").strip()
    if raw:
        try:
            overrides: dict = json.loads(raw)
            for company in cfg.get("companies", []):
                name = company["name"]
                if name in overrides:
                    company.update(overrides[name])
        except Exception as exc:
            print(f"  [config] WARN: could not apply ACCOUNT_OVERRIDES: {exc}")

    return cfg


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)
