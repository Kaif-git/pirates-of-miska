"""Observed public habits with private diagnostic history kept out of guide views."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import Enum

from .ai import FullKey, PublicKey, TACTICAL_CATEGORIES


class PatternState(str, Enum):
    UNKNOWN = "UNKNOWN"
    TENTATIVE = "TENTATIVE"
    TENDENCY = "TENDENCY"
    HABIT = "HABIT"
    MIXED = "MIXED"


GROUNDED_ACTIONS = {"CRAWL", "RISE"}


@dataclass
class NotebookEntry:
    key: PublicKey
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    last_tick: int = 0
    state: PatternState = PatternState.UNKNOWN
    top_action: str | None = None
    full_keys: list[FullKey] = field(default_factory=list)
    hidden_diffs: list[str] = field(default_factory=list)
    habit_break_hint: str | None = None


@dataclass(frozen=True)
class PublicEntry:
    key: PublicKey
    counts: tuple[tuple[str, int], ...]
    total: int
    last_tick: int
    state: PatternState
    top_action: str | None
    habit_break_hint: str | None

    @property
    def ghost(self):
        return self.state in {PatternState.TENDENCY, PatternState.HABIT}

    @property
    def timeline_prediction(self):
        return self.state == PatternState.HABIT


def public_entry(entry):
    return PublicEntry(entry.key, tuple(sorted(entry.counts.items())), entry.total,
                       entry.last_tick, entry.state, entry.top_action, entry.habit_break_hint)


@dataclass(frozen=True)
class NotebookView:
    """Only this stripped, immutable type is accepted by the guide."""
    named: tuple[tuple[str, tuple[PublicEntry, ...]], ...]
    exchanges: tuple[tuple[str, int], ...]
    named_trait_summaries: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def entry(self, archetype, key, npc_id=None):
        if npc_id is None:
            return public_entry(NotebookEntry(key))
        for name, entries in self.named:
            if name == npc_id:
                for entry in entries:
                    if entry.key == key:
                        return entry
        return public_entry(NotebookEntry(key))

    def npc_traits(self, npc_id):
        return dict(self.named_trait_summaries).get(npc_id, ())


def state_of(entry, config):
    rule = config["notebook"]
    if entry.total == 0:
        return PatternState.UNKNOWN
    share = max(entry.counts.values()) / entry.total
    if entry.total < rule["tendency_min"]:
        return PatternState.TENTATIVE
    if entry.total >= rule["habit_min"] and share == 1:
        return PatternState.HABIT
    if share >= rule["tendency_share"]:
        return PatternState.TENDENCY
    if entry.total >= rule["mixed_min"]:
        return PatternState.MIXED
    return PatternState.TENTATIVE


class Notebook:
    def __init__(self, config):
        self.config = deepcopy(config)
        self.named = {}
        self.exchanges = {}

    def _record_entry(self, entries, key, action, tick, full_key, visible_dimensions):
        entry = entries.setdefault(key, NotebookEntry(key))
        broke = entry.state == PatternState.HABIT and entry.top_action != action
        if broke:
            entry.hidden_diffs = [name for name in ("blood_band", "fear_band", "pain_band")
                                  if any(getattr(previous, name) != getattr(full_key, name)
                                         for previous in entry.full_keys)]
            entry.habit_break_hint = "Something else changed."
            if entry.hidden_diffs and entry.hidden_diffs[0] in visible_dimensions:
                dimension = entry.hidden_diffs[0]
                description = {"blood_band": "he is badly hurt" if full_key.blood_band == "CRITICAL" else "his wounds changed",
                               "fear_band": "he looks afraid", "pain_band": "he looks in pain"}[dimension]
                entry.habit_break_hint = f"Something else changed: {description}."
        entry.counts[action] = entry.counts.get(action, 0) + 1
        entry.total += 1
        entry.last_tick = tick
        entry.top_action = max(sorted(entry.counts), key=lambda name: entry.counts[name])
        entry.state = PatternState.MIXED if broke else state_of(entry, self.config)
        entry.full_keys.append(full_key)
        entry.full_keys = entry.full_keys[-self.config["notebook"]["fullkey_history"]:]

    def record(self, archetype, npc_id, key, action, *, tick, full_key,
               observed=True, visible_dimensions=()):
        if not observed:
            return
        if not isinstance(key, PublicKey) or not isinstance(full_key, FullKey) or full_key.public != key:
            raise ValueError("Notebook public/full key mismatch")
        if (action not in self.config["ai"]["tie_order"]
                and action not in TACTICAL_CATEGORIES and action not in GROUNDED_ACTIONS):
            raise ValueError("Unknown notebook action")
        if not isinstance(tick, int) or tick < 0:
            raise ValueError("Invalid notebook tick")
        # The archetype argument is accepted for old callers only. Patterns
        # belong to the observed actor; aggregate profiles are not persisted.
        self._record_entry(self.named.setdefault(npc_id, {}), key, action, tick,
                           full_key, visible_dimensions)

    def entry(self, archetype, key, npc_id=None):
        if npc_id is None:
            return public_entry(NotebookEntry(key))
        return public_entry(self.named.get(npc_id, {}).get(key, NotebookEntry(key)))

    def _trait_summary(self, entries):
        rule = self.config["notebook"]
        entries = [entry for entry in entries
                   if entry.state in {PatternState.TENDENCY, PatternState.HABIT}
                   and entry.key.anger_band in {"ANGRY", "RAGE"}]
        total = sum(entry.total for entry in entries)
        guards = sum(entry.counts.get("GUARD", 0) for entry in entries)
        if len(entries) >= rule["trait_entries"] and total and guards / total < rule["trait_guard_share"]:
            return ("Aggressive: rarely guards when angry",)
        return ()

    def record_exchange(self, npc_id, observed=True):
        if observed:
            self.exchanges[npc_id] = self.exchanges.get(npc_id, 0) + 1

    def exact_stats_known(self, npc_id):
        return self.exchanges.get(npc_id, 0) >= self.config["notebook"]["reveal_exchanges"]

    def public_view(self):
        def groups_view(groups):
            return tuple((identity, tuple(public_entry(entry) for _, entry in
                                          sorted(entries.items(), key=lambda item: tuple(asdict(item[0]).values()))))
                         for identity, entries in sorted(groups.items()))
        return NotebookView(groups_view(self.named), tuple(sorted(self.exchanges.items())),
                            tuple((name, self._trait_summary(entries.values()))
                                  for name, entries in sorted(self.named.items())))

    def to_data(self):
        def groups_data(groups):
            return {identity: [asdict(entry) for _, entry in sorted(
                    entries.items(), key=lambda item: tuple(asdict(item[0]).values()))]
                    for identity, entries in sorted(groups.items())}
        return {"version": self.config["schema_version"],
                "named": groups_data(self.named), "exchanges": deepcopy(self.exchanges)}

    @classmethod
    def from_data(cls, data, config):
        data = deepcopy(data)
        if data.get("version") in set(range(1, 30)):
            data["version"] = config["schema_version"]
        if data.get("version") != config["schema_version"]:
            raise ValueError("Unsupported notebook snapshot version")
        result = cls(config)
        for identity, rows in data.get("named", {}).items():
            entries = {}
            for row in rows:
                values = deepcopy(row)
                values["key"] = PublicKey(**values["key"])
                values["state"] = PatternState(values["state"])
                values["full_keys"] = [FullKey(PublicKey(**item["public"]), item["blood_band"],
                                                item["fear_band"], item["pain_band"])
                                       for item in values["full_keys"]]
                entry = NotebookEntry(**values)
                if (sum(entry.counts.values()) != entry.total
                        or any(not isinstance(count, int) or count <= 0 for count in entry.counts.values())
                        or any(action not in config["ai"]["tie_order"]
                               and action not in TACTICAL_CATEGORIES and action not in GROUNDED_ACTIONS
                               for action in entry.counts)
                        or len(entry.full_keys) > config["notebook"]["fullkey_history"]
                        or any(key.public != entry.key for key in entry.full_keys)):
                    raise ValueError("Invalid notebook entry")
                entries[entry.key] = entry
            result.named[identity] = entries
        result.exchanges = deepcopy(data.get("exchanges", {}))
        return result
