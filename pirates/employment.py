"""Bounded player-crew employment agreements owned by the economy ledger.

Candidate profiles are authored content; an agreement is the accepted, persisted
version of those terms. Starter sailors and recruits from older saves have no
record and keep an explicitly labeled standing arrangement rather than invented
back pay. `crew_history` remains a short departure ledger, not a wage ledger.
"""
from copy import deepcopy

from pirates.crew_recruitment import CANDIDATES

EMPLOYMENT_VERSION = 2
UNDERTAKING_STATUSES = ("open", "satisfied", "late", "failed", "withdrawn")
REVIEW_STATUSES = ("offered", "accepted", "declined")
MAX_ROLE_LENGTH = 80
MAX_CAUSE_LENGTH = 200
REVIEW_OFFERS = {
    "maelin_orr": {
        "role": "Ship's clerk — records and rigging",
        "wage_per_day": 3,
        "summary": ("She asks to be rated as ship's clerk after keeping her passage promise; "
                    "the work is the same, the responsibility is not."),
    },
}


def marks(count):
    """Player-facing Mark count with a correct singular."""
    return f"{count} Mark" + ("" if count == 1 else "s")


def candidate_terms(candidate_id):
    """Return the authored release-one terms for a known candidate."""
    candidate = CANDIDATES.get(candidate_id)
    if candidate is None:
        return None
    undertaking = candidate.get("undertaking")
    return {
        "role": candidate["role"],
        "wage_per_day": candidate["wage_per_day"],
        "signing_payment": candidate["asking_price"],
        "undertaking": deepcopy(undertaking),
    }


def _integer(value, lo, hi, label):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f"Invalid employment {label}")
    return value


def _text(value, limit, label):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"Invalid employment {label}")
    return value


def _validate_undertaking(row, accepted_day, day):
    if row is None:
        return None
    if not isinstance(row, dict) or set(row) != {
            "destination", "deadline_day", "status", "closed_day", "reason"}:
        raise ValueError("Invalid employment undertaking")
    from pirates.economy import PORTS
    if row["destination"] not in PORTS:
        raise ValueError("Invalid undertaking destination")
    _text(row["reason"], 240, "undertaking reason")
    deadline = _integer(row["deadline_day"], accepted_day, accepted_day + 3650,
                        "undertaking deadline")
    if row["status"] not in UNDERTAKING_STATUSES:
        raise ValueError("Invalid undertaking status")
    if row["status"] == "open":
        if row["closed_day"] is not None:
            raise ValueError("Open undertaking cannot have a closing day")
    else:
        _integer(row["closed_day"], accepted_day, day, "undertaking closing day")
    if deadline < accepted_day:
        raise ValueError("Invalid undertaking deadline")
    return row


def _validate_record(record, day, label="record"):
    required = {"candidate_id", "role", "wage_per_day", "signing_payment",
                "accepted_day", "status", "closed_day", "close_cause",
                "arrears", "paid_total", "unpaid_since_day", "undertaking", "review"}
    if not isinstance(record, dict) or set(record) != required:
        raise ValueError(f"Invalid employment {label}")
    candidate_id = record["candidate_id"]
    if not isinstance(candidate_id, str) or candidate_id not in CANDIDATES:
        raise ValueError("Invalid employment candidate identity")
    _text(record["role"], MAX_ROLE_LENGTH, "role")
    _integer(record["wage_per_day"], 1, 1000, "daily wage")
    _integer(record["signing_payment"], 0, 10**9, "signing payment")
    accepted = _integer(record["accepted_day"], 1, day, "acceptance day")
    _integer(record["arrears"], 0, 10**12, "arrears")
    _integer(record["paid_total"], 0, 10**12, "paid total")
    if record["unpaid_since_day"] is not None:
        _integer(record["unpaid_since_day"], accepted, day, "unpaid notice day")
    if record["status"] == "active":
        if record["closed_day"] is not None or record["close_cause"] is not None:
            raise ValueError("Active employment already has a closing cause")
    elif record["status"] == "closed":
        _integer(record["closed_day"], accepted, day, "closing day")
        _text(record["close_cause"], MAX_CAUSE_LENGTH, "closing cause")
    else:
        raise ValueError("Invalid employment status")
    review = record["review"]
    if review is not None:
        if (not isinstance(review, dict) or set(review) != {"status", "day"}
                or review["status"] not in REVIEW_STATUSES):
            raise ValueError("Invalid employment review")
        _integer(review["day"], accepted, day, "review day")
    undertaking = _validate_undertaking(record["undertaking"], accepted, day)
    record["undertaking"] = undertaking
    return record


class EmploymentLedger:
    """One active-or-closed agreement per canonical sailor identity."""

    def __init__(self, settled_day=1, records=None):
        self.settled_day = settled_day
        self.records = {sailor: deepcopy(row) for sailor, row in (records or {}).items()}

    def record(self, sailor_id):
        return self.records.get(sailor_id)

    def prepare(self, candidate, day):
        """Build and validate an agreement without mutating ledger state."""
        if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str):
            raise ValueError("Choose a known crew candidate")
        candidate_id = candidate["id"]
        terms = candidate_terms(candidate_id)
        if terms is None:
            raise ValueError("Unknown recruit identity")
        _integer(day, 1, 10**12, "acceptance day")
        sailor_id = f"hire:{candidate_id}"
        if sailor_id in self.records:
            raise ValueError("That person already has an employment record")
        undertaking = terms["undertaking"]
        if undertaking is not None:
            undertaking = {
                "destination": undertaking["destination"],
                "deadline_day": day + undertaking["deadline_days"],
                "status": "open",
                "closed_day": None,
                "reason": undertaking["reason"],
            }
        record = {
            "candidate_id": candidate_id,
            "role": terms["role"],
            "wage_per_day": terms["wage_per_day"],
            "signing_payment": terms["signing_payment"],
            "accepted_day": day,
            "status": "active",
            "closed_day": None,
            "close_cause": None,
            "arrears": 0,
            "paid_total": 0,
            "unpaid_since_day": None,
            "undertaking": undertaking,
            "review": None,
        }
        return _validate_record(record, day)

    def commit(self, record):
        """Insert a prepared agreement. Validation happened in `prepare`."""
        sailor_id = f"hire:{record['candidate_id']}"
        if sailor_id in self.records:
            raise ValueError("That person already has an employment record")
        self.records[sailor_id] = deepcopy(record)

    def remove(self, sailor_id):
        self.records.pop(sailor_id, None)

    @staticmethod
    def _sailor_name(world, sailor_id):
        sailor = world.ship.sailor(sailor_id)
        if sailor is not None:
            return sailor.name
        row = next((row for row in world.ship.crew_history if row["id"] == sailor_id), None)
        return row["name"] if row else sailor_id

    @staticmethod
    def _announce(world, text):
        """Record a visible commitment outcome without inventing a new ledger."""
        world.economy.record(text)
        world.message(text)
        voyage = getattr(getattr(world, "voyages", None), "voyage", None)
        if voyage and voyage.get("status") in ("underway", "encounter", "storm", "arrived"):
            voyage["outcomes"] = (list(voyage.get("outcomes") or []) + [text])[-30:]

    def _close_undertaking(self, world, sailor_id, status, day, text):
        record = self.records[sailor_id]
        undertaking = record["undertaking"]
        undertaking["status"] = status
        undertaking["closed_day"] = day
        if (status == "satisfied" and record["review"] is None
                and record["candidate_id"] in REVIEW_OFFERS):
            record["review"] = {"status": "offered", "day": day}
        self._announce(world, text)

    def settle_undertakings(self, world, day=None):
        """Close each open promise once it is kept, missed or outlives the sailor.

        The check is a post-arrival/post-movement authority: normal movement and
        every voyage tick flow through `World.advance`, and the day settlement
        catches a deadline that passes while the captain is elsewhere.
        """
        day = world.clock.day if day is None else _integer(day, 1, 10**12, "undertaking day")
        changed = []
        ashore = (not getattr(world, "at_sea", False) and world.player.z == 0
                  and world.player.x < 45)
        for sailor_id in sorted(self.records):
            record = self.records[sailor_id]
            undertaking = record.get("undertaking")
            if not undertaking or undertaking["status"] != "open":
                continue
            sailor = world.ship.sailor(sailor_id) if record["status"] == "active" else None
            if sailor is None:
                self._close_undertaking(
                    world, sailor_id, "failed", day,
                    f"{self._sailor_name(world, sailor_id)}'s undertaking ended without "
                    f"completion ({record.get('close_cause') or 'no longer aboard'}).")
                changed.append(sailor_id)
                continue
            from pirates.economy import PORTS
            where = PORTS[undertaking["destination"]]["name"]
            if ashore and world.economy.port == undertaking["destination"]:
                if day <= undertaking["deadline_day"]:
                    self._close_undertaking(
                        world, sailor_id, "satisfied", day,
                        f"{sailor.name} kept the undertaking: ashore at {where} on day {day}, "
                        f"before the day {undertaking['deadline_day']} deadline.")
                else:
                    self._close_undertaking(
                        world, sailor_id, "late", day,
                        f"{sailor.name} reached {where} on day {day}, after the "
                        f"day {undertaking['deadline_day']} deadline.")
                changed.append(sailor_id)
            elif day > undertaking["deadline_day"]:
                self._close_undertaking(
                    world, sailor_id, "late", day,
                    f"{sailor.name}'s undertaking lapsed: {where} was required by "
                    f"day {undertaking['deadline_day']} and the deadline has passed.")
                changed.append(sailor_id)
        return changed

    def announce_undertaking(self, world, sailor_id):
        """Announce a departure-closed promise once; arrival closures already spoke."""
        record = self.records.get(sailor_id)
        undertaking = record.get("undertaking") if record else None
        if not undertaking or undertaking["status"] in ("open", "satisfied", "late"):
            return
        from pirates.economy import PORTS
        where = PORTS[undertaking["destination"]]["name"]
        self._announce(
            world, f"{self._sailor_name(world, sailor_id)}'s undertaking to reach {where} "
                   f"was not completed: {record['close_cause']}.")

    def review_offer(self, sailor_id):
        """Return the authored post-undertaking terms, if the captain may review them."""
        record = self.records.get(sailor_id)
        if (record is None or record["status"] != "active"
                or record["review"] is None or record["review"]["status"] != "offered"):
            return None
        offer = REVIEW_OFFERS.get(record["candidate_id"])
        return deepcopy(offer) if offer else None

    def accept_review(self, world, sailor_id):
        """Apply an authored amendment only after explicit consent at a port."""
        offer = self.review_offer(sailor_id)
        if offer is None:
            raise ValueError("No employment review is waiting for that sailor")
        if (getattr(world, "at_sea", False) or world.player.z != 0
                or world.player.x >= 45):
            raise ValueError("Employment reviews require the captain ashore at a port")
        record = self.records[sailor_id]
        record["role"] = offer["role"]
        record["wage_per_day"] = offer["wage_per_day"]
        record["review"] = {"status": "accepted", "day": world.clock.day}
        _validate_record(record, world.clock.day, "reviewed record")
        self._announce(
            world, f"{self._sailor_name(world, sailor_id)} accepted amended terms: "
                   f"{offer['role']}, {marks(offer['wage_per_day'])} per completed day "
                   f"from day {world.clock.day}.")
        return record

    def decline_review(self, world, sailor_id):
        """Keep the current terms and stop offering the same authored review."""
        if self.review_offer(sailor_id) is None:
            raise ValueError("No employment review is waiting for that sailor")
        if (getattr(world, "at_sea", False) or world.player.z != 0
                or world.player.x >= 45):
            raise ValueError("Employment reviews require the captain ashore at a port")
        record = self.records[sailor_id]
        record["review"] = {"status": "declined", "day": world.clock.day}
        self._announce(
            world, f"{self._sailor_name(world, sailor_id)}'s review closed with the "
                   f"current terms unchanged ({marks(record['wage_per_day'])} per completed day).")
        return record

    def _pay_due(self, world, due):
        """Pay integer wages proportionally; leave any shortfall as explicit arrears."""
        total = sum(due.values())
        if total <= 0:
            return 0
        available = max(0, world.economy.money)
        paid = {}
        if available >= total:
            paid = dict(due)
        else:
            paid = {sailor_id: available * amount // total for sailor_id, amount in due.items()}
            leftover = available - sum(paid.values())
            order = sorted(due, key=lambda sailor_id: (
                -((available * due[sailor_id]) % total), sailor_id))
            for sailor_id in order:
                if leftover <= 0:
                    break
                if paid[sailor_id] < due[sailor_id]:
                    paid[sailor_id] += 1
                    leftover -= 1
        transferred = sum(paid.values())
        if transferred > available:
            raise ValueError("Payroll cannot pay more than available captain funds")
        if transferred <= 0:
            return 0
        world.economy.money -= transferred
        for sailor_id, amount in paid.items():
            if amount <= 0:
                continue
            record = self.records[sailor_id]
            record["paid_total"] += amount
            record["arrears"] -= amount
            if record["arrears"] < 0:
                raise ValueError("Payroll overpaid an employment agreement")
            if record["arrears"] == 0:
                record["unpaid_since_day"] = None
        return transferred

    def settle_day(self, world, day):
        """Accrue and pay each crossed completed day exactly once, in order."""
        _integer(day, 1, 10**12, "settlement day")
        if day <= self.settled_day:
            return {"days": 0, "accrued": 0, "paid": 0}
        crossed = day - self.settled_day
        accrued = paid = 0
        for current in range(self.settled_day + 1, day + 1):
            due = {}
            notices = []
            for sailor_id in sorted(self.records):
                record = self.records[sailor_id]
                if record["accepted_day"] >= current:
                    continue
                if record["status"] == "closed" and current > record["closed_day"]:
                    continue
                wage = record["wage_per_day"]
                record["arrears"] += wage
                accrued += wage
                if record["unpaid_since_day"] is None:
                    record["unpaid_since_day"] = current
                    notices.append(sailor_id)
                due[sailor_id] = record["arrears"]
            transferred = self._pay_due(world, due)
            paid += transferred
            for sailor_id in notices:
                record = self.records[sailor_id]
                if record["arrears"] > 0:
                    name = self._sailor_name(world, sailor_id)
                    text = (f"{name} is owed {marks(record['arrears'])} in unpaid wages "
                            f"from day {record['unpaid_since_day']}.")
                    world.economy.record(text)
                    world.message(text)
        self.settled_day = day
        self.settle_undertakings(world, day)
        return {"days": crossed, "accrued": accrued, "paid": paid}

    def pay_arrears(self, world, sailor_id=None):
        """Pay outstanding earned wages explicitly from captain funds."""
        if sailor_id is None:
            selected = {key: row for key, row in self.records.items() if row["arrears"] > 0}
        else:
            row = self.records.get(sailor_id)
            selected = {sailor_id: row} if row is not None and row["arrears"] > 0 else {}
        total = sum(row["arrears"] for row in selected.values())
        if total <= 0:
            return 0
        if world.economy.money < total:
            raise ValueError(f"Not enough Marks to settle {marks(total)} of outstanding wages")
        world.economy.money -= total
        for row in selected.values():
            row["paid_total"] += row["arrears"]
            row["arrears"] = 0
            row["unpaid_since_day"] = None
        names = ", ".join(self._sailor_name(world, sailor_id) for sailor_id in sorted(selected))
        unit = marks(total)
        world.economy.record(f"Paid {unit} in outstanding crew wages: {names}.")
        world.message(f"Paid {unit} in outstanding wages.")
        return total

    def close(self, sailor_id, day, cause, undertaking_status="withdrawn"):
        """Close one agreement and any open undertaking without touching money."""
        record = self.records.get(sailor_id)
        if record is None or record["status"] != "active":
            return None
        _integer(day, record["accepted_day"], 10**12, "closing day")
        _text(cause, MAX_CAUSE_LENGTH, "closing cause")
        if undertaking_status not in UNDERTAKING_STATUSES:
            raise ValueError("Invalid undertaking status")
        record["status"] = "closed"
        record["closed_day"] = day
        record["close_cause"] = cause
        undertaking = record.get("undertaking")
        if undertaking is not None and undertaking["status"] == "open":
            undertaking["status"] = undertaking_status
            undertaking["closed_day"] = day
        return record

    def snapshot(self):
        return {"version": EMPLOYMENT_VERSION, "settled_day": self.settled_day,
                "records": deepcopy(self.records)}

    @classmethod
    def restore(cls, data, day):
        """Load an optional ledger; old saves start with no invented wages."""
        if data is None:
            return cls(day)
        if (not isinstance(data, dict) or set(data) != {"version", "settled_day", "records"}
                or type(data["version"]) is not int
                or data["version"] not in (1, EMPLOYMENT_VERSION)
                or not isinstance(data["records"], dict)):
            raise ValueError("Invalid employment ledger")
        version = data["version"]
        settled_day = _integer(data["settled_day"], 1, day, "settled day")
        records = {}
        for sailor_id, row in data["records"].items():
            if not isinstance(sailor_id, str) or not sailor_id.startswith("hire:"):
                raise ValueError("Invalid employment sailor identity")
            if not isinstance(row, dict) or sailor_id[5:] != row.get("candidate_id"):
                raise ValueError("Employment identity disagrees with candidate")
            row = deepcopy(row)
            if version == 1:
                # Version 1 had no review state; no promise or wage is inferred.
                row["review"] = None
            records[sailor_id] = _validate_record(row, day)
        return cls(settled_day, records)
