"""Step 10 loader: content drops live in data/content_drops/<name>.yaml and
merge into the doc cache at world boot. DATA ONLY — this module reads rows,
never branches on drop ids. No code changes needed per drop."""
from __future__ import annotations


def drops_to_load():
    try:
        from omnisim.systems.production import _load as _ld
        drops = ((_ld("economy") or {}).get("content_drops", {}) or {}).get("load", [])
        return [str(d) for d in drops or []]
    except Exception:
        return []


def apply(village=None):
    """Merge every configured drop's *_append rows into the live docs."""
    import yaml
    from pathlib import Path
    try:
        from omnisim.systems.production import _load as _ld
        from omnisim.systems.production import reset_docs as _reset
        from omnisim.systems.production import set_doc as _set
    except Exception:
        return []
    try:  # labor.yaml is read straight from disk by schedule: merge
        # drop labor rows (needs/rates) into the live labor tables.
        import yaml as _ylab
        from pathlib import Path as _Pathb
        from omnisim.systems import schedule as _sched
        _ldoc = dict(_sched._labor_doc() or {})
        _base = _Pathb(__file__).resolve().parents[3] / "data" / "content_drops"
        for _name in drops_to_load():
            _pp = _base / ("%s.yaml" % _name)
            if not _pp.exists():
                continue
            _dd = _ylab.safe_load(_pp.read_text(encoding="utf-8")) or {}
            _lab = _dd.get("labor_append") or {}
            for _sec, _rows in _lab.items():
                _dst = _ldoc.setdefault(_sec, {})
                for _k, _v in (_rows or {}).items():
                    _dst[_k] = _v
        try:
            _sched._labor_doc = (lambda _d=dict(_ldoc): lambda: _d)()
            _sched.WORK_NEEDS.update(_sched._work_needs())
            _sched.RAW_OF.update(_sched._raw_of())
        except Exception:
            pass
    except Exception:
        pass
    loaded = []
    try:
        base = Path(__file__).resolve().parents[3] / "data" / "content_drops"
        for name in drops_to_load():
            p = base / ("%s.yaml" % name)
            if not p.exists():
                continue
            drop = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            _merge_drop(drop)
            loaded.append(name)
    except Exception:
        pass
    return loaded


def _merge_drop(drop):
    import copy
    try:
        from omnisim.entities.items import defs as _defs
    except Exception:
        _defs = None
    try:
        from omnisim.systems.production import _load as _ld
        from omnisim.systems.production import set_doc as _set
    except Exception:
        return
    # goods / production / trades / stations merge into production docs
    for key, doc_name in (("goods_append", "goods"),
                          ("recipes_append", "production"),
                          ("trades_append", "trades"),
                          ("stations_append", "stations"),
                          ("blueprints_append", "blueprints")):
        rows = drop.get(key) or []
        if not rows:
            continue
        doc = copy.deepcopy(_ld(doc_name))
        if doc_name == "stations" and isinstance(doc, list):
            doc = doc + list(rows)
            _set(doc_name, doc)
            continue
        plural = {"goods": "goods", "production": "recipes",
                  "trades": "trades", "stations": "stations",
                  "blueprints": "blueprints"}[doc_name]
        doc.setdefault(plural, []).extend(rows)
        _set(doc_name, doc)
    # buildings.blueprints() reads disk directly: append drop blueprints
    # into its memoized table so drops show up with no code changes
    try:
        import omnisim.systems.buildings as _bld
        bps = _bld.blueprints()
        for bp in (drop.get("blueprints_append") or []):
            if bp.get("id") and bp["id"] not in bps:
                bps[bp["id"]] = bp
    except Exception:
        pass
    # crops.yaml is read straight from disk by crops.crop_spec: merge into
    # BOTH the disk-reader cache and the production reference cache.
    crops = drop.get("crops_append") or {}
    if crops:
        try:
            import omnisim.systems.crops as _crops
            _crops.crop_spec().update(copy.deepcopy(crops))
        except Exception:
            pass
        try:
            import omnisim.systems.production as _prod
            if getattr(_prod, "_CROPS_CACHE", None) is None:
                _prod._crop_reference("__warm__", 1.0, ())
            _prod._CROPS_CACHE.update(copy.deepcopy(crops))
        except Exception:
            pass
    # stall assortments: founders stock the new goods so shelves can want them
    try:
        _defs_all = _defs()
        _defs_all.setdefault("stalls", {})
        for market, rows in (drop.get("stall_append") or {}).items():
            assort = _defs_all["stalls"].setdefault(market, {})
            for k, v in (rows or {}).items():
                if k not in assort:
                    assort[k] = list(v)
    except Exception:
        pass
    # live stalls: shelves already built this boot — stock the new goods now
    # (founders' stores, finite and declared) so mouths can want them
    try:
        if village is not None:
            for stall in (getattr(village, "stalls", []) or []):
                stock = stall.setdefault("stock", {})
                restock = stall.setdefault("restock", {})
                for market, rows in (drop.get("stall_append") or {}).items():
                    for k, v in (rows or {}).items():
                        if k not in stock:
                            stock[k] = list(v)
                        if k not in restock:
                            restock[k] = list(v)
    except Exception:
        pass
    # items.yaml defs: extend the loaded table in place (lists memoized: drop first)
    items = drop.get("items_append") or {}
    if items and _defs is not None:
        try:
            table = _defs().get("items", {})
            for k, v in items.items():
                if k not in table:
                    table[k] = v
        except Exception:
            pass
    try:
        from omnisim.entities.items import invalidate_lists as _inval
        _inval()
    except Exception:
        pass
    try:
        from omnisim.systems.production import _invalidate_tables as _invt
        _invt()
    except Exception:
        pass
    # derived tables snapshot at import: refresh SELLABLE-class lists so
    # new goods trade, sell, and seat at tables on arrival (no code per drop)
    try:
        from omnisim.entities.items import sellable as _se2
        import omnisim.core.actions as _acts
        _acts.SELLABLE = tuple(_se2())
    except Exception:
        pass
    try:  # TOOLS: rebuild import-time tables (SKILL_TOOL/FORGE_ORDER) so
        # drop tools (needle/Weaving) work however modules imported first.
        from omnisim.systems.tools import refresh_derived as _tref
        _tref()
    except Exception:
        pass
    try:
        from omnisim.entities.items import staples as _st2
        import omnisim.core.actions as _acts2
        _acts2.BUY_ORDER = tuple(_st2())
    except Exception:
        pass
