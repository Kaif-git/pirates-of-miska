"""Finite port-based source of voluntary player-crew candidates."""
from copy import deepcopy


# These are authored starting contacts, not actors manufactured during hourly
# simulation. Their skills/potential are recruitment profiles; ShipLife owns a
# recruit's active Sailor state after an explicit hire transition.
CANDIDATES = {
    "pippin_ash": {
        "name": "Pip Ash", "port": "verdant", "age": 16,
        "background": "Fisher's apprentice", "asking_price": 35,
        "summary": "An earnest, disciplined novice who learns quickly, but has little sea experience.",
        "skills": {"repair": 0, "bail": 1, "cook": 1, "clean": 1, "rig": 0, "load": 0},
        "personality": {"diligence": 82, "sociability": 56, "resilience": 64, "curiosity": 76},
        "mental": {"intelligence": 70, "discipline": 90, "leadership": 42, "tactical_awareness": 45},
        "traits": ["obedient", "quick_learner"], "learning_potential": 92,
        "preference": "lookout", "specialty": "rig",
        "role": "Deckhand — rigging", "wage_per_day": 1,
    },
    "elira_voss": {
        "name": "Elira Voss", "port": "verdant", "age": 24,
        "background": "Coastal pilot", "asking_price": 95,
        "summary": "A cautious, capable pilot who values a steady berth and fair treatment.",
        "skills": {"repair": 0, "bail": 1, "cook": 1, "clean": 1, "rig": 5, "load": 0},
        "personality": {"diligence": 74, "sociability": 42, "resilience": 78, "curiosity": 62},
        "mental": {"intelligence": 86, "discipline": 82, "leadership": 72, "tactical_awareness": 80},
        "traits": ["patient", "light_sleeper"], "learning_potential": 68,
        "preference": "lookout", "specialty": "rig",
        "role": "Pilot — rigging", "wage_per_day": 3,
    },
    "brannoc_reed": {
        "name": "Brannoc Reed", "port": "cinder", "age": 31,
        "background": "Former privateer", "asking_price": 120,
        "summary": "A seasoned deckhand with a reputation for nerve and a short temper.",
        "skills": {"repair": 2, "bail": 3, "cook": 1, "clean": 1, "rig": 3, "load": 4},
        "personality": {"diligence": 66, "sociability": 36, "resilience": 72, "curiosity": 48},
        "mental": {"intelligence": 62, "discipline": 61, "leadership": 68, "tactical_awareness": 83},
        "traits": ["hard_worker", "restless"], "learning_potential": 52,
        "preference": "craft", "specialty": "load",
        "role": "Deckhand — cargo and guns", "wage_per_day": 4,
    },
    "maelin_orr": {
        "name": "Maelin Orr", "port": "verdant", "age": 22,
        "background": "Harbor copyist", "asking_price": 60,
        "summary": ("A careful clerk who needs passage to Cinder before the quarterly assize. "
                    "She will work the deck on the way and asks for honest daily wages."),
        "skills": {"repair": 1, "bail": 1, "cook": 1, "clean": 3, "rig": 1, "load": 2},
        "personality": {"diligence": 82, "sociability": 64, "resilience": 58, "curiosity": 74},
        "mental": {"intelligence": 84, "discipline": 79, "leadership": 46, "tactical_awareness": 38},
        "traits": ["patient", "quick_learner"], "learning_potential": 80,
        "preference": "craft", "specialty": "clean",
        "role": "Deckhand — records and rigging", "wage_per_day": 2,
        "undertaking": {
            "destination": "cinder", "deadline_days": 3,
            "reason": ("She must present sealed harbor copies at Cinder's assay office "
                       "before the quarterly assize."),
        },
    },
}


def sailor_from_candidate(candidate_id, x, y, z, bunk, age_origin_day=1):
    """Build the canonical recruit identity used to validate active-crew saves."""
    from pirates.ship import LABORS, Sailor

    candidate = CANDIDATES.get(candidate_id)
    if candidate is None:
        raise ValueError("Unknown recruit identity")
    specialty = candidate["specialty"]
    labors = {kind: (1 if kind == specialty else 4) for kind in LABORS}
    skill_xp = {kind: 0.0 for kind in LABORS}
    return Sailor(
        f"hire:{candidate_id}", candidate["name"], candidate["name"][0].upper(),
        x, y, (185, 198, 178), candidate["background"], candidate["preference"],
        bunk, labors, deepcopy(candidate["skills"]), z=z,
        personality=deepcopy(candidate["personality"]), mental=deepcopy(candidate["mental"]),
        traits=list(candidate["traits"]),
        skill_xp=skill_xp, age=candidate["age"], background=candidate["background"],
        learning_potential=candidate["learning_potential"],
        age_origin=candidate["age"], age_origin_day=age_origin_day,
    )


class RecruitmentPool:
    """Persist candidate availability; hired people move to the ship roster."""

    def __init__(self, statuses=None):
        supplied = dict(statuses or {})
        unknown = set(supplied) - set(CANDIDATES)
        if unknown or any(value not in ("available", "recruited", "declined")
                          for value in supplied.values()):
            raise ValueError("Invalid recruitment pool")
        # A candidate added after a save was written is simply available there;
        # existing statuses are never rewritten or retired by migration.
        self.statuses = {candidate_id: supplied.get(candidate_id, "available")
                         for candidate_id in CANDIDATES}

    def at_port(self, port):
        return [{"id": candidate_id, **deepcopy(candidate)}
                for candidate_id, candidate in CANDIDATES.items()
                if candidate["port"] == port and self.statuses[candidate_id] == "available"]

    def candidate(self, candidate_id):
        candidate = CANDIDATES.get(candidate_id)
        if candidate is None or self.statuses.get(candidate_id) != "available":
            return None
        return {"id": candidate_id, **deepcopy(candidate)}

    def mark_recruited(self, candidate_id, port):
        candidate = self.candidate(candidate_id)
        if candidate is None or candidate["port"] != port:
            raise ValueError("That candidate is no longer available at this port")
        self.statuses[candidate_id] = "recruited"
        return candidate

    def decline(self, candidate_id, port):
        candidate = self.candidate(candidate_id)
        if candidate is None or candidate["port"] != port:
            raise ValueError("That candidate is no longer available at this port")
        self.statuses[candidate_id] = "declined"
        return candidate

    def snapshot(self):
        return {"version": 1, "statuses": dict(self.statuses)}

    @classmethod
    def restore(cls, data):
        if data is None:
            return cls()
        if (not isinstance(data, dict) or set(data) != {"version", "statuses"}
                or type(data["version"]) is not int or data["version"] != 1
                or not isinstance(data["statuses"], dict)
                or any(not isinstance(key, str) for key in data["statuses"])
                or not set(data["statuses"]) <= set(CANDIDATES)
                or any(value not in ("available", "recruited", "declined")
                       for value in data["statuses"].values())):
            raise ValueError("Invalid recruitment pool")
        # Missing known candidates are old-save content, not corruption.
        return cls(data["statuses"])
