"""Load and expose typed config from config.yaml."""

from __future__ import annotations

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
    return cfg


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)
