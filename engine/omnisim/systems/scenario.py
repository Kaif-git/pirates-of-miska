"""Scenario-local config: data/scenarios/<name>.yaml.

A scenario is a small override pack applied to the LIVE economy doc before
Game().setup(): flag flips plus which content drops load (worldgen merges the
drops at boot, reading economy.content_drops.load).  The canonical config ships
the sustenance legs off; only data/scenarios/sustenance.yaml turns them on.

Apply once, before setup.  Content drops mutate the shared item/stall tables in
place, so scenarios are not meant to be mixed inside one process: probe them in
a fresh interpreter (the tests and soaked runs do).
"""
from __future__ import annotations

from pathlib import Path

import yaml

SCENARIOS = Path(__file__).resolve().parents[3] / "data" / "scenarios"


def spec(name: str) -> dict:
    """The scenario's raw config, or {} when there is no such scenario."""
    path = SCENARIOS / ("%s.yaml" % name)
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def apply(name: str) -> dict:
    """Merge a scenario into the live economy doc; call before Game().setup().

    Returns the merged economy doc (handy for assertions and logging).
    """
    from omnisim.systems.production import _load as _ld
    from omnisim.systems.production import set_doc

    conf = spec(name)
    doc = {}
    doc.update(_ld("economy") or {})
    flags = dict(doc.get("flags") or {})
    flags.update(conf.get("flags") or {})
    doc["flags"] = flags
    drops = (conf.get("content_drops") or {}).get("load")
    if drops is not None:
        drops_doc = dict(doc.get("content_drops") or {})
        drops_doc["load"] = list(drops)
        doc["content_drops"] = drops_doc
    set_doc("economy", doc)
    return doc
