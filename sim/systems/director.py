"""Light-touch, logged condition injection from world-health metrics."""

from __future__ import annotations

from typing import Any


CONFLICT_EVENT_TYPES = {
    "ENCOUNTER_RESOLVED", "WAR_DECLARED", "TREATY_BREACHED", "FACTION_DISSOLVED",
}


class DirectorSystem:
    def __init__(self, config: dict[str, Any], *, last_checked_day: int = 0,
                 last_injection_day: int = 0, injections_by_year: dict[str, int] | None = None,
                 metrics: dict[str, Any] | None = None) -> None:
        self.settings = dict(config.get("director", {
            "enabled": False, "intensity_permille": 0, "check_interval_days": 30,
            "conflict_drought_days": 180, "injection_cooldown_days": 180,
            "max_injections_per_year": 2, "minimum_major_characters": 4,
        }))
        self.last_checked_day = last_checked_day
        self.last_injection_day = last_injection_day
        self.injections_by_year = dict(injections_by_year or {})
        self.metrics = dict(metrics or {})
        self.validate_state()

    @classmethod
    def create(cls, config: dict[str, Any]) -> "DirectorSystem":
        return cls(config)

    @classmethod
    def restore(cls, config: dict[str, Any], snapshot: dict[str, Any]) -> "DirectorSystem":
        required = {"last_checked_day", "last_injection_day", "injections_by_year", "metrics"}
        if not isinstance(snapshot, dict) or set(snapshot) != required:
            raise ValueError("Invalid director snapshot")
        return cls(config, **snapshot)

    def update_day(self, day: int, world) -> list[str]:
        interval = self.settings["check_interval_days"]
        if type(day) is not int or day != self.last_checked_day + 1:
            raise ValueError("Director must update on consecutive days")
        self.last_checked_day = day
        if day % interval:
            return []
        self.metrics = self._health_metrics(world, day)
        if not self.settings["enabled"] or self.settings["intensity_permille"] == 0:
            return []
        return self._inject_if_needed(world, day)

    def _health_metrics(self, world, day: int) -> dict[str, Any]:
        conflict_ticks = [event.tick for event in world.event_log
                          if event.type in CONFLICT_EVENT_TYPES]
        last_conflict_tick = max(conflict_ticks, default=0)
        major_count = sum(
            character.get("kind") == "character"
            and character.get("death_tick") is None
            and character.get("tier") in {"MAJOR", "LEGEND"}
            for character in world.entities.values()
        )
        faction_power = {
            faction_id: max(0, world.politics.factions.get(faction_id, {}).get("power", 0))
            for faction_id in sorted(world.politics.factions)
        }
        # Crew wealth is a stable available proxy for faction power in this milestone.
        if not any(faction_power.values()):
            faction_power = {
                faction_id: max(0, world.economy.crew_accounts.get(faction_id, {}).get(
                    "treasury_silver", 0))
                for faction_id in sorted(world.politics.factions)
            }
        power_total = sum(faction_power.values())
        top_share = (max(faction_power.values(), default=0) * 1000 // power_total
                     if power_total else 0)
        window_ticks = self.settings["check_interval_days"] * 3 * world.config["time"][
            "hours_per_day"] // world.config["time"]["hours_per_tick"]
        recent_trade = sum(
            event.data.get("quantity_milli", 0) for event in world.event_log
            if event.type == "MARKET_TRADE" and event.tick > world.clock.tick - window_ticks
        )
        return {
            "day": day,
            "days_since_notable_conflict": max(0, day - last_conflict_tick // max(
                1, world.config["time"]["hours_per_day"]
                // world.config["time"]["hours_per_tick"])),
            "last_notable_conflict_tick": last_conflict_tick,
            "major_plus_characters": major_count,
            "faction_top_power_share_permille": top_share,
            "recent_trade_quantity_milli": recent_trade,
            "player_region_pressure": None,
        }

    def _inject_if_needed(self, world, day: int) -> list[str]:
        settings = self.settings
        intensity = settings["intensity_permille"]
        drought_threshold = (settings["conflict_drought_days"] * 1000 + intensity - 1) // intensity
        year = (day - 1) // world.config["time"]["days_per_year"] + 1
        year_key = str(year)
        low_major_population = (self.metrics["major_plus_characters"]
                                < settings["minimum_major_characters"])
        conflict_stagnant = self.metrics["days_since_notable_conflict"] >= drought_threshold
        if (not conflict_stagnant and not low_major_population
                or self.last_injection_day > 0
                and day - self.last_injection_day < settings["injection_cooldown_days"]
                or self.injections_by_year.get(year_key, 0) >= settings[
                    "max_injections_per_year"]):
            return []
        living = sorted(
            entity_id for entity_id, person in world.entities.items()
            if person.get("kind") == "character" and person.get("death_tick") is None
        )
        targets = [entity_id for entity_id in living
                   if world.entities[entity_id].get("tier") in {"MAJOR", "LEGEND"}]
        if not targets:
            targets = living
        if not targets:
            return []
        rng = world.rng.stream(f"director:rookie:{year}:{day}")
        target_id = targets[rng.randbelow(len(targets))]
        target = world.entities[target_id]
        location = world.location_of(target_id)
        island_id = (location["id"] if location["kind"] == "island" else
                     world.geography.routes[location["id"]].origin)
        reason = "low_major_population" if low_major_population else "conflict_drought"
        event = world.event_log.append(
            tick=world.clock.tick, event_type="DIRECTOR_INJECTION", location=island_id,
            targets=[{"role": "condition_target", "entity_id": target_id}],
            data={"reason": reason, "condition": "rising_rookie_vendetta",
                  "days_since_notable_conflict": self.metrics["days_since_notable_conflict"],
                  "intensity_permille": intensity},
            causes_tags=[f"director:{reason}"], magnitude=0.6,
        )
        rookie_id = world.characters.create_character(
            world, tier="MAJOR", role="captain", cause_event_id=event.id,
            age_years=world.characters.data["rookie_age_years"], island_id=island_id,
        )
        rookie_created = world.entities[rookie_id]["last_event_id"]
        grudge_event = world.memories.add_memory(
            world, rookie_id, target_id, "betrayed", event_id=event.id,
            intensity_permille=min(1000, 500 + intensity // 2), valence_permille=-1000,
        )
        world.entities[rookie_id]["dream"] = {
            "kind": "defeat_character", "target_ref": target_id,
            "progress": 0, "stated_tick": world.clock.tick,
        }
        world.entities[rookie_id]["last_event_id"] = grudge_event
        self.last_injection_day = day
        self.injections_by_year[year_key] = self.injections_by_year.get(year_key, 0) + 1
        return [event.id, rookie_created, grudge_event]

    def validate_state(self) -> None:
        if (type(self.last_checked_day) is not int or self.last_checked_day < 0
                or type(self.last_injection_day) is not int or self.last_injection_day < 0
                or not isinstance(self.injections_by_year, dict)
                or any(not isinstance(year, str) or not year.isdigit()
                       or type(count) is not int or count < 0
                       for year, count in self.injections_by_year.items())
                or not isinstance(self.metrics, dict)):
            raise ValueError("Invalid director state")
        numeric_settings = ("intensity_permille", "check_interval_days", "conflict_drought_days",
                            "injection_cooldown_days", "max_injections_per_year",
                            "minimum_major_characters")
        if (any(type(self.settings.get(key)) is not int for key in numeric_settings)
                or not 0 <= self.settings["intensity_permille"] <= 1000
                or any(self.settings[key] <= 0 for key in numeric_settings[1:])):
            raise ValueError("Invalid director settings")
        if type(self.settings.get("enabled")) is not bool:
            raise ValueError("Invalid director enabled setting")
        if self.metrics:
            required_metrics = {"day", "days_since_notable_conflict", "last_notable_conflict_tick",
                                "major_plus_characters", "faction_top_power_share_permille",
                                "recent_trade_quantity_milli", "player_region_pressure"}
            if (set(self.metrics) != required_metrics
                    or any(type(self.metrics[key]) is not int for key in required_metrics -
                           {"player_region_pressure"})
                    or self.metrics["player_region_pressure"] is not None
                    and type(self.metrics["player_region_pressure"]) is not int):
                raise ValueError("Invalid director health metrics")

    def validate_world(self, world) -> None:
        self.validate_state()
        if self.last_checked_day != world.clock.tick // (
                world.config["time"]["hours_per_day"] // world.config["time"]["hours_per_tick"]):
            raise ValueError("Director snapshot day does not match world clock")
        for year, count in self.injections_by_year.items():
            if count > self.settings["max_injections_per_year"]:
                raise ValueError(f"Director injection cap exceeded for year {year}")
        ticks_per_day = (world.config["time"]["hours_per_day"]
                         // world.config["time"]["hours_per_tick"])
        days_per_year = world.config["time"]["days_per_year"]
        observed = {}
        for event in world.event_log.by_type("DIRECTOR_INJECTION"):
            if not event.causes_tags or not any(tag.startswith("director:") for tag in event.causes_tags):
                raise ValueError("Director injection lacks a visible reason tag")
            day = event.tick // ticks_per_day
            year = (day - 1) // days_per_year + 1
            key = str(year)
            observed[key] = observed.get(key, 0) + 1
        if observed != self.injections_by_year:
            raise ValueError("Director yearly injection counters do not match the event log")

    def snapshot(self) -> dict[str, Any]:
        return {"last_checked_day": self.last_checked_day,
                "last_injection_day": self.last_injection_day,
                "injections_by_year": dict(sorted(self.injections_by_year.items())),
                "metrics": dict(self.metrics)}
