"""Daily region weather driven by JSON seasonal distributions and named RNGs."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any


DEFAULT_WEATHER_PATH = Path(__file__).resolve().parents[1] / "data" / "weather.json"


class WeatherSystem:
    def __init__(self, geography, rng, states: dict[str, str] | None = None,
                 last_updated_day: int = 0, weather_data: dict[str, Any] | None = None,
                 days_per_year: int = 365) -> None:
        self.geography = geography
        self.rng = rng
        self.weather_data = weather_data or self._load_weather_data()
        self._validate_weather_data()
        if type(days_per_year) is not int or days_per_year <= 0:
            raise ValueError("Calendar year length must be a positive integer")
        self.days_per_year = days_per_year
        self.digest = hashlib.sha256(
            json.dumps(self.weather_data, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        self.states = (dict(states) if states is not None else {
            region_id: region.initial_weather
            for region_id, region in sorted(geography.regions.items())
        })
        self.last_updated_day = last_updated_day
        if set(self.states) != set(geography.regions):
            raise ValueError("Weather state must cover every region exactly once")
        if any(state not in self.weather_data["states"] for state in self.states.values()):
            raise ValueError("Unknown weather state in snapshot")
        if type(last_updated_day) is not int or last_updated_day < 0:
            raise ValueError("Weather day must be a nonnegative integer")

    @staticmethod
    def _load_weather_data() -> dict[str, Any]:
        try:
            return json.loads(DEFAULT_WEATHER_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot load weather data: {exc}") from exc

    def _validate_weather_data(self) -> None:
        data = self.weather_data
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Unsupported weather data schema")
        states, seasons = data.get("states"), data.get("seasonal_weights")
        if not isinstance(states, dict) or not states or not isinstance(seasons, dict) or not seasons:
            raise ValueError("Weather data requires states and seasonal weights")
        for state, row in states.items():
            if (not isinstance(row, dict)
                    or any(type(row.get(field)) is not int or row[field] < 0
                           for field in ("speed_permille", "visibility_permille", "hull_damage_per_million"))
                    or row["speed_permille"] <= 0):
                raise ValueError(f"Invalid weather effect row: {state}")
        for season, weights in seasons.items():
            if (not isinstance(weights, dict) or set(weights) != set(states)
                    or any(type(weight) is not int or weight < 0 for weight in weights.values())
                    or sum(weights.values()) <= 0):
                raise ValueError(f"Invalid weather weights for season: {season}")
        if type(data.get("persistence_bonus_weight")) is not int or data["persistence_bonus_weight"] < 0:
            raise ValueError("Weather persistence bonus must be a nonnegative integer")
        calendar = data.get("calendar_seasons")
        multipliers = data.get("calendar_weight_multipliers")
        if (not isinstance(calendar, list) or not calendar
                or not isinstance(multipliers, dict)):
            raise ValueError("Weather data requires a seasonal calendar and modifiers")
        calendar_ids = []
        for row in calendar:
            if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                    or type(row.get("days")) is not int or row["days"] <= 0):
                raise ValueError("Invalid weather calendar season")
            calendar_ids.append(row["id"])
        if (len(set(calendar_ids)) != len(calendar_ids)
                or set(multipliers) != set(calendar_ids)):
            raise ValueError("Weather calendar season IDs must be unique and fully configured")
        for season_id, row in multipliers.items():
            if (not isinstance(row, dict) or set(row) != set(states)
                    or any(type(value) is not int or value <= 0 for value in row.values())):
                raise ValueError(f"Invalid calendar weather multipliers: {season_id}")
        for region in self.geography.regions.values():
            if region.initial_weather not in states or region.season not in seasons:
                raise ValueError(f"Region {region.id} references unknown weather data")

    def state(self, region_id: str) -> str:
        try:
            return self.states[region_id]
        except KeyError as exc:
            raise ValueError(f"Unknown weather region: {region_id}") from exc

    def effects(self, region_id: str) -> dict[str, int]:
        return dict(self.weather_data["states"][self.state(region_id)])

    def speed_permille(self, region_id: str) -> int:
        return self.effects(region_id)["speed_permille"]

    def update_day(self, day: int) -> list[dict[str, str]]:
        if type(day) is not int or day != self.last_updated_day + 1:
            raise ValueError("Weather must update one consecutive day at a time")
        changed = []
        calendar_season = self.calendar_season(day)
        calendar_modifiers = self.weather_data["calendar_weight_multipliers"][calendar_season]
        for region_id in sorted(self.geography.regions):
            region = self.geography.regions[region_id]
            weights = {
                state: weight * calendar_modifiers[state] // 100
                for state, weight in self.weather_data["seasonal_weights"][region.season].items()
            }
            previous = self.states[region_id]
            weights[previous] += self.weather_data["persistence_bonus_weight"]
            draw = self.rng.stream(f"weather:{region_id}").randbelow(sum(weights.values()))
            selected = None
            for state, weight in sorted(weights.items()):
                if draw < weight:
                    selected = state
                    break
                draw -= weight
            if selected is None:
                raise AssertionError("Weather weighted selection failed")
            self.states[region_id] = selected
            if selected != previous:
                changed.append({"region_id": region_id, "from": previous, "to": selected})
        self.last_updated_day = day
        return changed

    def calendar_season(self, day: int) -> str:
        if type(day) is not int or day < 1:
            raise ValueError("Calendar day must be a positive integer")
        normalized_day = ((day - 1) % self.days_per_year) * 365 // self.days_per_year
        cursor = 0
        for season in self.weather_data["calendar_seasons"]:
            cursor += season["days"]
            if normalized_day < cursor:
                return season["id"]
        raise ValueError("Weather calendar does not cover its 365-day cycle")

    def snapshot(self) -> dict[str, Any]:
        return {"states": {key: self.states[key] for key in sorted(self.states)},
                "last_updated_day": self.last_updated_day}
