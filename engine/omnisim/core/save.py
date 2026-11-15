"""Save/load stub. Rule: sim state must be plain data (dicts/lists) so JSON saves just work."""
from __future__ import annotations
import json
from pathlib import Path


def save_game(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_game(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
