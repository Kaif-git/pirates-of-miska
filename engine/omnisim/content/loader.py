"""Content loader — YAML in data/ -> dicts. No game balance inside .py files."""
from __future__ import annotations
from pathlib import Path
import yaml


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_all(directory: Path) -> dict[str, dict]:
    out = {}
    for p in sorted(directory.glob("*.yaml")):
        out[p.stem] = load_yaml(p)
    return out
