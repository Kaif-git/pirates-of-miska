"""Encounter-local contact state for a sighted vessel.

This module owns only the nonviolent decisions of a sea sighting: ranged
signals, approach, evasion, close parley and the captain's crossing intent.
It never moves a person, fires a gun, damages a hull or resolves a clash.

Two hard boundaries follow the plan:

* Combat is owned by the separate combat system. New cannon and refused/unclear
  crossing intents stop at a durable ``combat_pending`` request. Already-active
  legacy artillery saves keep their compatibility path in ``VoyageSystem``.
  Nothing here consumes ammunition or writes a new combat result.
* Factions, alliances and cooperation are owned by the parallel faction
  system. This module only reads an organization's identity and public
  reputation. It never invents a stance, treaty, shared objective or fleet.

Range is encounter-local and deliberately coarse: ``distant`` then ``close``.
Lookout observations may include chart-scale relative position/motion, but
approach and evasion remain deterministic encounter checks with visible time
costs, not tactical sailing physics.
"""
from __future__ import annotations

from hashlib import blake2b
from copy import deepcopy
import json
import math

from pirates import sea_trade

SIGNAL_INTENTS = ("identify", "intentions", "warn_off")
PARLEY_TOPICS = ("identity", "intentions", "warn_off", "stand_down", "transfer_permission")
MANIFEST_MODES = ("alone", "escorts", "everyone")
THREATS = ("merchant", "pirates", "marines")
MAX_SIGNALS = 6
MAX_PARLEY_TOPICS = len(PARLEY_TOPICS)
MAX_CROSSING_PARTICIPANTS = 6


def new_contact():
    """A fresh distant contact with no invented signals or permissions."""
    return {
        "range": "distant",
        "approach_attempts": 0,
        "evade_attempts": 0,
        "signals": [],
        "parley": None,
        "crossing": None,
        "combat_pending": None,
        "crossing_completed": False,
        "trade_offer": None,
        "trade_pending": None,
        "trade_sequence": 0,
        "trade_receipts": [],
    }


def contact_of(encounter, *, create=True):
    """Return the encounter contact, lazily defaulting legacy sightings."""
    if not isinstance(encounter, dict):
        raise ValueError("Encounter contact requires a sighting record")
    contact = encounter.get("contact")
    if contact is None:
        contact = new_contact()
        if create:
            encounter["contact"] = contact
    return contact


def threat_of(encounter):
    threat = encounter.get("threat")
    return threat if threat in THREATS else "merchant"


def _unit(encounter, salt):
    payload = f"{encounter.get('id', 'unknown')}:{salt}".encode()
    return int.from_bytes(blake2b(payload, digest_size=8).digest(), "big") / 2**64


def _contact_unit(encounter, salt):
    identity = encounter.get("contact_instance_id", encounter.get("id", "unknown"))
    payload = f"{identity}:{salt}".encode()
    return int.from_bytes(blake2b(payload, digest_size=8).digest(), "big") / 2**64


def _clamp(value, low, high):
    return max(low, min(high, value))


def pirate_risk_assessment(world, encounter):
    """Estimate local warning response from visible vessel condition/crew/guns.

    This is a bounded encounter estimate, not NPC hidden intent or legal
    authority. Loaded shot, private cargo, faction goals and remote state are
    intentionally excluded.
    """
    if threat_of(encounter) != "pirates":
        raise ValueError("Pirate risk assessment requires an estimated pirate contact")
    living_crew = sum(1 for sailor in world.ship.crew if sailor.health > 0)
    layout = getattr(world.ship, "layout", None)
    visible_guns = sum(1 for gun in ("port_cannon", "starboard_cannon")
                       if layout is not None and layout.available(gun))
    own_condition = _clamp((float(world.ship.hull) + float(world.ship.rigging)) / 200.0,
                           0.15, 1.0)
    other_crew = max(1, int(encounter.get("crew", 0)))
    other_condition = _clamp(int(encounter.get("condition", 100)) / 100.0, 0.15, 1.0)
    own_strength = max(1.0, living_crew + 2.0 * visible_guns) * own_condition
    other_strength = other_crew * other_condition
    ratio = own_strength / max(0.5, other_strength)
    chance = _clamp(0.05 + (ratio - 0.75) * 0.42, 0.05, 0.80)
    band = "favourable" if ratio >= 1.5 else "uncertain" if ratio >= 0.8 else "poor"
    return {"band": band, "ratio": round(ratio, 3),
            "warn_off_chance": round(chance, 4),
            "warn_off_percent": round(chance * 100),
            "own_visible_crew": living_crew, "own_visible_guns": visible_guns,
            "other_observed_crew": other_crew,
            "other_observed_condition": int(encounter.get("condition", 100))}


def hostile_commitment_authorized(world, encounter):
    """Return whether a scoped witnessed event or warrant supports commitment."""
    return hostile_commitment_basis(world, encounter) is not None


def hostile_commitment_basis(world, encounter):
    """Return the local evidence row, never treating appearance as authority."""
    voyages = getattr(world, "voyages", None)
    resolver = getattr(voyages, "hostile_commitment_basis", None)
    return resolver(world, encounter) if callable(resolver) else None


def _mission_statement(encounter):
    """A bounded public statement; never echo an NPC's private mission field."""
    threat = threat_of(encounter)
    if threat == "pirates":
        return "We ask no questions of ships that keep their distance."
    if threat == "marines":
        return "We maintain order on these waters; keep a respectful distance."
    return "We are passing through and seek no quarrel."


def sighting_rows(world, encounter):
    """Read-only lookout information; no hidden state or private intent."""
    rows = [
        f"Observed vessel: {encounter.get('name', 'Unknown vessel')}",
        "Lookout confidence: vessel contact is consistently tracked; the name and claimed "
        "colours are observations, not verified legal identity.",
        f"Claimed colours: {encounter.get('faction', 'unmarked')}",
        f"Lookout assessment: {threat_of(encounter)} vessel (estimate, not verified intent)",
        f"Observed condition {int(encounter.get('condition', 100))}/100 | "
        f"crew estimate {int(encounter.get('crew', 0))}",
    ]
    if threat_of(encounter) == "pirates":
        risk = pirate_risk_assessment(world, encounter)
        rows.append(
            f"Local risk: {risk['band']} readiness; a close warning has an estimated "
            f"{risk['warn_off_percent']}% chance to make this crew back down. "
            "Estimate uses observed crew/condition and visible crew/cannon fit; intent and hidden stores remain unknown.")
    basis = hostile_commitment_basis(world, encounter)
    if basis is None:
        rows.append("Hostility: prohibited; no witnessed attack or verified warrant is recorded for this contact.")
    else:
        rows.append(f"Hostility: evidence recorded ({basis['cause']}); combat still requires the separate M5 authority.")
    if threat_of(encounter) == "marines":
        rows.append("Marine patrol: neutral by default; claimed colours do not establish a warrant or authorize attack.")
    org_id = encounter.get("organization_id")
    society = getattr(world, "society", None)
    organization = (society.organizations.get(org_id)
                    if society is not None and isinstance(org_id, str) else None)
    if organization is not None:
        rows.append(
            f"Registry match: {organization['name']} ({organization['kind']}); "
            f"public reputation {organization['reputation']}/100.")
    else:
        rows.append("Registry: no verified organization match; colours are a claim only.")
    report = getattr(getattr(world, "npcs", None), "sighting_reports", {}).get(encounter.get("id"))
    if report is not None:
        current_hour = world.clock.total_ticks // 360000
        age = max(0, current_hour - report["observed_hour"])
        rows.append(f"Source: lookout observation at sea-hour {report['observed_hour']} "
                    f"({age}h old); registry read from the current society record.")
    else:
        rows.append("Source: current lookout report; age unavailable in this legacy sighting.")
    rows.append("No captain-to-faction stance exists in the shared record; willingness is unknown.")
    return rows


def contact_rows(world, encounter):
    """Current contact state and honest estimates, without leaking rolls."""
    contact = contact_of(encounter, create=False)
    rows = [f"Encounter range: {contact['range']}"]
    instance_id = encounter.get("contact_instance_id")
    if isinstance(instance_id, str):
        rows.append(f"Lookout contact: {instance_id} (permissions apply to this meeting only).")
    observation = encounter.get("relative_observation")
    if isinstance(observation, dict):
        bearing = observation["bearing_deg"]
        bearing_text = "bearing unavailable" if bearing is None else f"bearing {bearing:.1f}°"
        rows.append(f"Lookout: {observation['range_units']:.2f} chart units, {bearing_text}.")
        motion_text = {
            "unknown": "relative course not established",
            "closing": "closing on the sampled course",
            "overtaking": "same-course traffic is overtaking",
            "opening": "opening on the sampled course",
            "crossing": "crossing the sampled course",
            "following": "following at similar speed",
            "steady": "little relative movement observed",
        }[observation["relative_motion"]]
        rows.append(f"Observed motion: {motion_text}; chart-scale estimate, not tactical range.")
    if contact["signals"]:
        rows.append(f"Signal exchanges: {len(contact['signals'])}")
    if contact["approach_attempts"]:
        rows.append(f"Approach attempts: {contact['approach_attempts']}")
    if contact["evade_attempts"]:
        rows.append(f"Evasion attempts: {contact['evade_attempts']} (estimate: "
                    f"{evasion_label(world, encounter)})")
    parley = contact.get("parley")
    if parley:
        granted = []
        if parley["stand_down"]:
            granted.append("mutual stand-down")
        if parley["transfer_permission"]:
            granted.append("personnel transfer")
        if parley["warned_off"]:
            granted.append("warn-off acknowledged")
        rows.append("Parley: " + (", ".join(granted) if granted else "no concession granted"))
    crossing = contact.get("crossing")
    if crossing:
        rows.append(f"Peaceful crossing request pending: {crossing['mode']}; "
                    "no person has moved and no clash is resolved here.")
    handoff = contact.get("combat_pending")
    if handoff:
        rows.append(f"Combat handoff pending: {handoff['cause']} | request {handoff['request_id']}; "
                    "the combat system owns resolution.")
    return rows


def validate_contact(contact, encounter_id=None, valid_nodes=None, *,
                     valid_people=None, living_people=None, captain_id="captain",
                     contact_instance_id=None, current_tick=None):
    """Strict validation for a persisted contact; raises ValueError."""
    if not isinstance(contact, dict) or set(contact) != set(new_contact()):
        raise ValueError("Invalid saved encounter contact")
    if contact["range"] not in ("distant", "close"):
        raise ValueError("Invalid saved encounter range")
    if type(contact["crossing_completed"]) is not bool:
        raise ValueError("Invalid saved crossing completion state")
    sea_trade.validate_contact_trade(
        contact, contact_instance_id or encounter_id or "unknown",
        current_tick if current_tick is not None else 10**18)
    for key in ("approach_attempts", "evade_attempts"):
        if type(contact[key]) is not int or not 0 <= contact[key] <= 100:
            raise ValueError("Invalid saved encounter attempt count")
    signals = contact["signals"]
    if not isinstance(signals, list) or len(signals) > MAX_SIGNALS:
        raise ValueError("Invalid saved encounter signal log")
    seen_intents = set()
    for row in signals:
        if (not isinstance(row, dict)
                or set(row) != {"tick", "intent", "outcome", "reply"}
                or not isinstance(row["intent"], str)
                or row["intent"] not in SIGNAL_INTENTS
                or row["intent"] in seen_intents
                or type(row["tick"]) is not int or row["tick"] < 0
                or row["outcome"] not in ("answered", "refused", "no_answer")
                or not isinstance(row["reply"], str) or not row["reply"]
                or len(row["reply"]) > 240):
            raise ValueError("Invalid saved encounter signal log")
        seen_intents.add(row["intent"])
    parley = contact["parley"]
    if parley is not None:
        if (not isinstance(parley, dict)
                or set(parley) != {"topics", "stand_down", "transfer_permission", "warned_off"}
                or not isinstance(parley["topics"], list)
                or len(parley["topics"]) > MAX_PARLEY_TOPICS
                or any(not isinstance(topic, str) or topic not in PARLEY_TOPICS
                       for topic in parley["topics"])
                or len(set(parley["topics"])) != len(parley["topics"])
                or any(type(parley[key]) is not bool
                       for key in ("stand_down", "transfer_permission", "warned_off"))
                or (parley["stand_down"] and "stand_down" not in parley["topics"])
                or (parley["transfer_permission"]
                    and "transfer_permission" not in parley["topics"])
                or (parley["warned_off"] and "warn_off" not in parley["topics"])):
            raise ValueError("Invalid saved encounter parley state")
        if contact["range"] != "close":
            raise ValueError("Saved parley requires close range")
    crossing = contact["crossing"]
    if crossing is not None:
        if (not isinstance(crossing, dict)
                or set(crossing) != {"request_id", "mode", "participants", "tick", "status"}
                or not isinstance(crossing["request_id"], str) or not crossing["request_id"]
                or crossing["mode"] not in MANIFEST_MODES
                or not isinstance(crossing["participants"], list)
                or not crossing["participants"]
                or len(crossing["participants"]) > MAX_CROSSING_PARTICIPANTS
                or any(not isinstance(sid, str) or not sid for sid in crossing["participants"])
                or len(set(crossing["participants"])) != len(crossing["participants"])
                or type(crossing["tick"]) is not int or crossing["tick"] < 0
                or crossing["status"] != "pending"):
            raise ValueError("Invalid saved crossing request")
        if contact["crossing_completed"]:
            raise ValueError("A completed crossing cannot also be pending")
        if contact["range"] != "close":
            raise ValueError("Saved crossing request requires close range")
        if captain_id not in crossing["participants"]:
            raise ValueError("A crossing manifest must include the captain")
        if crossing["mode"] == "alone" and crossing["participants"] != [captain_id]:
            raise ValueError("A solo crossing can only name the captain")
        if crossing["mode"] == "escorts" and len(crossing["participants"]) < 2:
            raise ValueError("An escorted crossing must name at least one escort")
        if valid_people is not None and any(sid not in valid_people for sid in crossing["participants"]):
            raise ValueError("Crossing manifest contains an unknown or non-living participant")
        if (crossing["mode"] == "everyone" and living_people is not None
                and crossing["participants"] != [captain_id, *living_people]):
            raise ValueError("Whole-party crossing does not match the living crew manifest")
        if not (parley and parley["transfer_permission"]):
            raise ValueError("A peaceful crossing requires recorded permission")
        if contact["trade_pending"] is not None:
            raise ValueError("A crossing and a sea-trade quote cannot both be pending")
    handoff = contact["combat_pending"]
    if handoff is not None:
        validate_handoff(handoff, valid_nodes=valid_nodes, valid_people=valid_people,
                         living_people=living_people, captain_id=captain_id)
        if encounter_id is not None and handoff["encounter_id"] != encounter_id:
            raise ValueError("Combat handoff belongs to a different encounter")
        if contact["trade_pending"] is not None or crossing is not None:
            raise ValueError("A combat handoff cannot overlap another pending contact request")
        if contact["crossing"] is not None:
            raise ValueError("A contact cannot hold both a peaceful crossing and combat handoff")
        if handoff["cause"] != "cannon_fire" and contact["range"] != "close":
            raise ValueError("A boarding handoff requires close range")
    return contact


def validate_sighting(encounter, valid_nodes=None, *, valid_people=None,
                      living_people=None, captain_id="captain", current_tick=None):
    """Validate the sighting fields later actions depend on, plus contact."""
    if not isinstance(encounter, dict):
        raise ValueError("Invalid saved ship sighting")
    for key in ("id", "name", "faction"):
        if not isinstance(encounter.get(key), str) or not encounter[key]:
            raise ValueError("Invalid saved ship sighting identity")
    if encounter.get("threat") not in THREATS:
        raise ValueError("Invalid saved ship sighting threat class")
    for key in ("crew", "condition"):
        if type(encounter.get(key)) is not int:
            raise ValueError("Invalid saved ship sighting observation")
    if not 0 <= encounter["condition"] <= 100 or encounter["crew"] < 0:
        raise ValueError("Invalid saved ship sighting observation")
    instance_id = encounter.get("contact_instance_id")
    if instance_id is not None:
        prefix = f"{encounter['id']}:"
        suffix = instance_id[len(prefix):] if isinstance(instance_id, str) and instance_id.startswith(prefix) else ""
        if (not suffix.isdigit() or str(int(suffix)) != suffix
                or not 1 <= int(suffix) <= 1_000_000):
            raise ValueError("Invalid lookout contact-instance identity")
    chart_position = encounter.get("chart_position")
    if chart_position is not None and (
            not isinstance(chart_position, list) or len(chart_position) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   or not -16 <= value <= 16 for value in chart_position)):
        raise ValueError("Invalid saved lookout chart position")
    observation = encounter.get("relative_observation")
    if observation is not None and (
            not isinstance(observation, dict)
            or set(observation) != {"range_units", "bearing_deg", "relative_motion"}
            or type(observation["range_units"]) not in (int, float)
            or not math.isfinite(observation["range_units"])
            or not 0 <= observation["range_units"] <= 32
            or observation["bearing_deg"] is not None
            and (type(observation["bearing_deg"]) not in (int, float)
                 or not math.isfinite(observation["bearing_deg"])
                 or not 0 <= observation["bearing_deg"] < 360)
            or observation["relative_motion"] not in
            ("unknown", "closing", "overtaking", "opening", "crossing", "following", "steady")):
        raise ValueError("Invalid saved relative lookout observation")
    if "contact" in encounter:
        contact = encounter["contact"]
        legacy_contact_fields = {
            "range", "approach_attempts", "evade_attempts", "signals",
            "parley", "crossing", "combat_pending"}
        if isinstance(contact, dict) and set(contact) in (
                legacy_contact_fields,
                legacy_contact_fields | {"crossing_completed"},
                set(new_contact()) - {"trade_offer", "trade_pending", "trade_sequence", "trade_receipts"}):
            defaults = new_contact()
            for field, value in defaults.items():
                contact.setdefault(field, deepcopy(value))
        validate_contact(encounter["contact"], encounter["id"], valid_nodes,
                         valid_people=valid_people, living_people=living_people,
                         captain_id=captain_id,
                         contact_instance_id=instance_id or encounter["id"],
                         current_tick=current_tick)
        crossing = encounter["contact"].get("crossing")
        if crossing:
            action = {"kind": "cross_with_manifest", "mode": crossing["mode"],
                      "personnel_manifest": crossing["participants"]}
            expected = _stable_request_id(encounter, "peaceful_transfer", crossing["tick"],
                                          crossing["participants"], action)
            if crossing["request_id"] != expected:
                raise ValueError("Peaceful crossing request identity does not match its contents")
    else:
        encounter["contact"] = new_contact()
    return encounter


def request_pending(voyage):
    encounter = voyage.get("encounter") if isinstance(voyage, dict) else None
    contact = encounter.get("contact") if isinstance(encounter, dict) else None
    return bool(contact and (contact.get("crossing") or contact.get("combat_pending")))


def signal(encounter, tick, intent):
    """Exchange one bounded signal subject; deterministic and non-binding."""
    if intent not in SIGNAL_INTENTS:
        raise ValueError("Choose a recognised signal subject")
    contact = contact_of(encounter)
    if contact["range"] != "distant":
        raise ValueError("Ranged signals require a distant contact")
    if request_pending({"encounter": encounter}):
        raise ValueError("A pending request blocks further signals")
    if any(row["intent"] == intent for row in contact["signals"]):
        raise ValueError("Signals on that subject have already been exchanged")
    if type(tick) is not int or tick < 0:
        raise ValueError("Signal time must be a non-negative tick count")
    threat = threat_of(encounter)
    response_chance = {"merchant": 0.72, "marines": 0.82, "pirates": 0.62}[threat]
    if _unit(encounter, f"signal:{intent}") >= response_chance:
        outcome = "no_answer"
        reply = f"No reply from {encounter['name']}; your signal remains unanswered."
    elif intent == "warn_off" and threat == "pirates":
        outcome = "refused"
        reply = f"{encounter['name']} refuses the warning: \"Sheer off or be taken.\""
    else:
        outcome = "answered"
        if intent == "identify":
            reply = (f"{encounter['name']} claims '{encounter.get('faction', 'unmarked')}' "
                     "colours; no registry proof is offered.")
        elif intent == "intentions":
            reply = f"{encounter['name']} signals: \"{_mission_statement(encounter)}\""
        elif threat == "marines":
            reply = f"{encounter['name']} answers: \"Keep clear of government waters.\""
        else:
            reply = f"{encounter['name']} answers: \"We will keep our distance; no trouble sought.\""
    row = {"tick": tick, "intent": intent, "outcome": outcome,
           "reply": reply[:240]}
    contact["signals"].append(row)
    return row


def approach(encounter, tick):
    """Move distant contact to close when the other captain permits it."""
    contact = contact_of(encounter)
    if request_pending({"encounter": encounter}):
        raise ValueError("A pending request blocks approach")
    if contact["range"] == "close":
        raise ValueError("The vessels are already at close range")
    if contact["crossing"] is not None:
        raise ValueError("Withdraw the pending crossing request before approaching again")
    attempt = contact["approach_attempts"]
    if attempt >= 100:
        raise ValueError("No further approach attempts are recorded for this contact")
    contact["approach_attempts"] = attempt + 1
    threat = threat_of(encounter)
    # Pirates want the prey within reach; others may hold their distance.
    refused = (threat != "pirates"
               and _unit(encounter, f"approach:{attempt}") < (0.20 if threat == "marines" else 0.12))
    if refused:
        return {"accepted": False,
                "text": (f"{encounter['name']} holds its distance and refuses a close approach. "
                         "The attempt still costs time; the contact remains distant.")}
    contact["range"] = "close"
    return {"accepted": True,
            "text": (f"{encounter['name']} permits you to close. The vessels are now at close range; "
                     "closeness is not consent to board or to transfer anyone.")}


def evasion_chance(world, encounter):
    ship = world.ship
    rigging = _clamp(ship.rigging / 100, 0.0, 1.0)
    hull = _clamp(ship.hull / 100, 0.0, 1.0)
    condition = _clamp(int(encounter.get("condition", 100)) / 100, 0.0, 1.0)
    crew = max(0, int(encounter.get("crew", 0)))
    chance = (0.50 + 0.20 * (rigging - 0.5) + 0.10 * (hull - 0.5)
              - 0.15 * (condition - 0.5) - 0.10 * _clamp((crew - 6) / 10, 0.0, 1.0))
    return _clamp(chance, 0.15, 0.85)


def evasion_label(world, encounter):
    chance = evasion_chance(world, encounter)
    if chance >= 0.65:
        return "favourable"
    if chance >= 0.45:
        return "uncertain"
    return "poor"


def evade(world, encounter, tick):
    """Attempt separation; a failure brings a distant contact to close range."""
    contact = contact_of(encounter)
    if request_pending({"encounter": encounter}):
        raise ValueError("A pending request blocks evasion")
    if type(tick) is not int or tick < 0:
        raise ValueError("Evasion time must be a non-negative tick count")
    attempt = contact["evade_attempts"]
    if attempt >= 100:
        raise ValueError("No further evasion attempts are recorded for this contact")
    contact["evade_attempts"] = attempt + 1
    chance = evasion_chance(world, encounter)
    escaped = _unit(encounter, f"evade:{attempt}") < chance
    if escaped:
        return {"escaped": True, "chance": chance,
                "text": (f"You use the weather and a ragged turn to break contact with "
                         f"{encounter['name']}. The lookout loses the vessel.")}
    if contact["range"] == "distant":
        contact["range"] = "close"
    return {"escaped": False, "chance": chance,
            "text": (f"{encounter['name']} matches your turn and closes the distance. "
                     "The contact remains and is now at close range.")}


def _consent_chance(encounter, contact, topic):
    threat = threat_of(encounter)
    if threat == "pirates":
        return 0.0
    base = {"warn_off": 0.65, "stand_down": 0.55, "transfer_permission": 0.45}[topic]
    if topic == "transfer_permission" and (contact.get("parley") or {}).get("stand_down"):
        base += 0.20
    if threat == "marines":
        base -= 0.20
    base += (_clamp(int(encounter.get("condition", 100)), 0, 100) - 50) / 500
    return _clamp(base, 0.05, 0.90)


def parley(encounter, topic, *, world=None):
    """One consent-based close-range subject; never a lasting agreement."""
    if topic not in PARLEY_TOPICS:
        raise ValueError("Choose a recognised parley subject")
    contact = contact_of(encounter)
    if request_pending({"encounter": encounter}):
        raise ValueError("A pending request blocks further parley")
    if contact["range"] != "close":
        raise ValueError("Parley requires close range; approach the vessel first")
    if contact["crossing"] is not None:
        raise ValueError("Withdraw the pending crossing request before opening a parley")
    threat = threat_of(encounter)
    if threat == "pirates" and topic not in ("warn_off", "stand_down"):
        raise ValueError("The pirate vessel refuses parley and answers only with threats")
    parley_state = contact.get("parley")
    topics = parley_state["topics"] if parley_state else []
    if topic in topics:
        raise ValueError("That parley subject has already been raised")
    if topic == "transfer_permission" and "identity" not in topics:
        raise ValueError("Exchange identities before requesting a personnel transfer")
    if parley_state is None:
        parley_state = {"topics": [], "stand_down": False,
                        "transfer_permission": False, "warned_off": False}
        contact["parley"] = parley_state
    agreed = None
    if threat == "pirates":
        assessment = pirate_risk_assessment(world, encounter) if world is not None else None
        chance = assessment["warn_off_chance"] if assessment is not None else 0.0
        agreed = _contact_unit(encounter, f"pirate_fear:{topic}") < chance
        if topic == "warn_off":
            parley_state["warned_off"] = agreed
            text = (f"{encounter['name']} backs down from your close warning for this encounter."
                    if agreed else
                    f"{encounter['name']} refuses the warning and holds its ground.")
        else:
            parley_state["stand_down"] = agreed
            text = (f"{encounter['name']} accepts a mutual stand-down for this encounter only."
                    if agreed else
                    f"{encounter['name']} refuses to stand down.")
    elif topic in ("identity", "intentions"):
        agreed = True
        text = (f"Identity challenge answered: {encounter['name']}, claiming "
                f"{encounter.get('faction', 'unmarked')} colours."
                if topic == "identity" else
                f"{encounter['name']} states: \"{_mission_statement(encounter)}\"")
    else:
        agreed = _unit(encounter, f"parley:{topic}") < _consent_chance(encounter, contact, topic)
        if topic == "warn_off":
            parley_state["warned_off"] = agreed
            text = (f"{encounter['name']} acknowledges the warning and will keep its distance."
                    if agreed else
                    f"{encounter['name']} rejects the warning and holds its ground.")
        elif topic == "stand_down":
            parley_state["stand_down"] = agreed
            text = (f"{encounter['name']} accepts a mutual stand-down for this encounter only."
                    if agreed else
                    f"{encounter['name']} declines a stand-down.")
        else:
            parley_state["transfer_permission"] = agreed
            text = (f"{encounter['name']} grants permission for a personnel transfer during this contact."
                    if agreed else
                    f"{encounter['name']} refuses any personnel transfer.")
    parley_state["topics"].append(topic)
    return {"topic": topic, "agreed": agreed, "text": text}


def manifest(world, mode, escorts=()):
    """Resolve a crossing manifest against real living crew identities."""
    if mode not in MANIFEST_MODES:
        raise ValueError("Choose who crosses: alone, named escorts, or everyone")
    living = [sailor.id for sailor in world.ship.crew if sailor.health > 0]
    if mode == "alone":
        if escorts:
            raise ValueError("A solo crossing cannot name escorts")
        return [world.player.id]
    if mode == "everyone":
        if escorts:
            raise ValueError("A whole-party crossing does not name individual escorts")
        return [world.player.id, *living]
    if not isinstance(escorts, (list, tuple)) or not escorts:
        raise ValueError("Name at least one escort or choose another crossing mode")
    if any(not isinstance(sid, str) or not sid for sid in escorts):
        raise ValueError("Escort identities must be non-empty strings")
    if len(set(escorts)) != len(escorts):
        raise ValueError("An escort cannot be named twice")
    if any(sid not in living for sid in escorts):
        raise ValueError("Every escort must be a living crew member")
    return [world.player.id, *escorts]


def _stable_request_id(encounter, cause, tick, participants, requested_action):
    body = json.dumps([encounter.get("id"), cause, tick, participants,
                       requested_action], separators=(",", ":"), sort_keys=True)
    return blake2b(body.encode(), digest_size=12).hexdigest()


def _stable_handoff_id(encounter, cause, tick, participants, requested_action,
                       lawful_basis_id=None):
    if lawful_basis_id is None:
        return _stable_request_id(encounter, cause, tick, participants, requested_action)
    body = json.dumps([encounter.get("id"), cause, tick, participants,
                       requested_action, lawful_basis_id],
                      separators=(",", ":"), sort_keys=True)
    return blake2b(body.encode(), digest_size=12).hexdigest()


def _position_snapshot(world, encounter):
    player_xy = world.voyages.map_coordinate()
    return {"player_chart": [round(float(player_xy[0]), 4), round(float(player_xy[1]), 4)],
            "target_node": encounter.get("node", world.voyages.position)}


def build_handoff(world, encounter, cause, tick, participants, requested_action,
                  lawful_basis_id=None):
    if cause not in ("cannon_fire", "forced_boarding"):
        raise ValueError("Unknown combat handoff cause")
    if (not isinstance(participants, list) or not participants
            or len(set(participants)) != len(participants)
            or any(not isinstance(sid, str) or not sid for sid in participants)
            or world.player.id not in participants):
        raise ValueError("Combat handoff requires unique stable participant ids including the captain")
    if lawful_basis_id is not None and (not isinstance(lawful_basis_id, str)
                                        or not lawful_basis_id):
        raise ValueError("A lawful combat handoff requires a stable evidence identity")
    request_id = _stable_handoff_id(encounter, cause, tick, participants,
                                    requested_action, lawful_basis_id)
    handoff = {
        "schema_version": 1,
        "request_id": request_id,
        "encounter_id": encounter["id"],
        "cause": cause,
        "participants": list(participants),
        "initiator": world.player.id,
        "target_id": encounter["id"],
        "position": _position_snapshot(world, encounter),
        "range": contact_of(encounter)["range"],
        "requested_action": requested_action,
        "tick": tick,
        "status": "pending",
    }
    if lawful_basis_id is not None:
        handoff["lawful_basis_id"] = lawful_basis_id
    return handoff


def validate_handoff(handoff, valid_nodes=None, *, valid_people=None,
                     living_people=None, captain_id="captain"):
    required = {"schema_version", "request_id", "encounter_id", "cause", "participants",
                "initiator", "target_id", "position", "range", "requested_action", "tick", "status"}
    if (not isinstance(handoff, dict) or set(handoff) not in
            (required, required | {"lawful_basis_id"})
            or type(handoff["schema_version"]) is not int or handoff["schema_version"] != 1
            or not isinstance(handoff["request_id"], str) or not handoff["request_id"]
            or not isinstance(handoff["encounter_id"], str) or not handoff["encounter_id"]
            or handoff["cause"] not in ("cannon_fire", "forced_boarding")
            or not isinstance(handoff["participants"], list) or not handoff["participants"]
            or any(not isinstance(sid, str) or not sid for sid in handoff["participants"])
            or len(set(handoff["participants"])) != len(handoff["participants"])
            or handoff["initiator"] != captain_id or handoff["initiator"] not in handoff["participants"]
            or handoff["target_id"] != handoff["encounter_id"]
            or not isinstance(handoff["position"], dict)
            or set(handoff["position"]) != {"player_chart", "target_node"}
            or not isinstance(handoff["position"]["player_chart"], list)
            or len(handoff["position"]["player_chart"]) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in handoff["position"]["player_chart"])
            or not isinstance(handoff["position"]["target_node"], str)
            or valid_nodes is not None and handoff["position"]["target_node"] not in valid_nodes
            or handoff["range"] not in ("distant", "close")
            or not isinstance(handoff["requested_action"], dict)
            or type(handoff["tick"]) is not int or handoff["tick"] < 0
            or "lawful_basis_id" in handoff and
            (not isinstance(handoff["lawful_basis_id"], str)
             or not handoff["lawful_basis_id"])
            or handoff["status"] != "pending"):
        raise ValueError("Invalid saved combat handoff request")
    if valid_people is not None and any(sid not in valid_people for sid in handoff["participants"]):
        raise ValueError("Combat handoff contains an unknown or non-living participant")
    action = handoff["requested_action"]
    if handoff["cause"] == "cannon_fire":
        if (set(action) != {"kind", "cannon"} or action["kind"] != "fire_cannon"
                or action["cannon"] not in ("port_cannon", "starboard_cannon")):
            raise ValueError("Invalid saved cannon handoff action")
    else:
        if (set(action) != {"kind", "mode", "personnel_manifest"}
                or action["kind"] != "cross_with_manifest"
                or action["mode"] not in MANIFEST_MODES
                or action["personnel_manifest"] != handoff["participants"]):
            raise ValueError("Invalid saved boarding handoff action")
        if (action["mode"] == "alone" and handoff["participants"] != [captain_id]
                or action["mode"] == "escorts" and len(handoff["participants"]) < 2
                or action["mode"] == "everyone" and living_people is not None
                and handoff["participants"] != [captain_id, *living_people]):
            raise ValueError("Boarding handoff does not match its declared crossing manifest")
        if handoff["range"] != "close":
            raise ValueError("A boarding handoff requires close range")
    expected_id = _stable_handoff_id(
        {"id": handoff["encounter_id"]}, handoff["cause"], handoff["tick"],
        handoff["participants"], action, handoff.get("lawful_basis_id"))
    if handoff["request_id"] != expected_id:
        raise ValueError("Combat handoff request identity does not match its contents")
    return handoff


def request_crossing(world, encounter, tick, mode, participants):
    """Record a peaceful transfer or create one durable boarding handoff."""
    contact = contact_of(encounter, create=False)
    if contact is None:
        contact = new_contact()
    if contact["range"] != "close":
        raise ValueError("A crossing request requires close range")
    if contact["crossing_completed"]:
        raise ValueError("This contact has already completed its one permitted crossing")
    if type(tick) is not int or tick < 0:
        raise ValueError("Crossing time must be a non-negative tick count")
    if mode not in MANIFEST_MODES:
        raise ValueError("Choose who crosses: alone, named escorts, or everyone")
    if (not isinstance(participants, list) or not participants
            or any(not isinstance(sid, str) or not sid for sid in participants)
            or len(set(participants)) != len(participants)
            or world.player.id not in participants):
        raise ValueError("Crossing requires a unique manifest including the captain")
    if mode == "alone" and participants != [world.player.id]:
        raise ValueError("A solo crossing can only name the captain")
    if mode == "escorts" and len(participants) < 2:
        raise ValueError("An escorted crossing must name at least one escort")
    living = [sailor.id for sailor in world.ship.crew if sailor.health > 0]
    if any(sid != world.player.id and sid not in living for sid in participants):
        raise ValueError("Crossing manifest contains an unknown or non-living participant")
    if mode == "everyone" and participants != [world.player.id, *living]:
        raise ValueError("Whole-party crossing must include every living crew member")
    requested_action = {"kind": "cross_with_manifest", "mode": mode,
                        "personnel_manifest": list(participants)}
    if contact["combat_pending"] is not None:
        existing = contact["combat_pending"]
        if existing["cause"] == "forced_boarding" and existing["requested_action"] == requested_action:
            return existing
        raise ValueError("A different combat handoff request is already pending")
    if contact["crossing"] is not None:
        existing = contact["crossing"]
        if existing["mode"] == mode and existing["participants"] == list(participants):
            return existing
        raise ValueError("A different peaceful crossing request is already pending")
    if contact["trade_pending"] is not None:
        raise ValueError("Commit or cancel the pending sea-trade quote before requesting a crossing")
    parley_state = contact.get("parley")
    if parley_state and parley_state["warned_off"]:
        raise ValueError("The other captain accepted your warn-off; no crossing is available")
    permitted = bool(parley_state and parley_state["transfer_permission"])
    if not permitted:
        basis = hostile_commitment_basis(world, encounter)
        if basis is None:
            raise ValueError(
                "Hostile boarding is prohibited: no witnessed attack or verified warrant supports this contact.")
        handoff = build_handoff(world, encounter, "forced_boarding", tick,
                                participants, requested_action, basis["event_id"])
        contact["combat_pending"] = handoff
        encounter["contact"] = contact
        validate_contact(contact, encounter.get("id"), contact_instance_id=(
            encounter.get("contact_instance_id", encounter.get("id"))))
        return handoff
    if len(participants) > MAX_CROSSING_PARTICIPANTS:
        raise ValueError(
            f"This vessel's peaceful transfer deck holds at most {MAX_CROSSING_PARTICIPANTS} people including the captain")
    request_id = _stable_request_id(encounter, "peaceful_transfer", tick,
                                    participants, requested_action)
    request = {"request_id": request_id, "mode": mode,
               "participants": list(participants), "tick": tick, "status": "pending"}
    contact["crossing"] = request
    encounter["contact"] = contact
    validate_contact(contact, encounter.get("id"), contact_instance_id=(
        encounter.get("contact_instance_id", encounter.get("id"))))
    return request


def request_cannon(world, encounter, tick, cannon):
    """Create a durable cannon-fire intent without firing or consuming shot."""
    contact = contact_of(encounter, create=False)
    if contact is None:
        contact = new_contact()
    if cannon not in ("port_cannon", "starboard_cannon"):
        raise ValueError("Choose an installed ship cannon")
    if contact["combat_pending"] is not None:
        existing = contact["combat_pending"]
        requested = {"kind": "fire_cannon", "cannon": cannon}
        if existing["cause"] == "cannon_fire" and existing["requested_action"] == requested:
            return existing
        raise ValueError("A different combat handoff request is already pending")
    if contact["crossing"] is not None or contact["trade_pending"] is not None:
        raise ValueError("Withdraw the peaceful crossing request before declaring cannon fire")
    basis = hostile_commitment_basis(world, encounter)
    if basis is None:
        raise ValueError(
            "Cannon fire is prohibited: no witnessed attack or verified warrant supports this contact.")
    if type(tick) is not int or tick < 0:
        raise ValueError("Cannon request time must be a non-negative tick count")
    if world.ship.ammo.get(cannon, 0) < 1 or not world.ship.layout.available(cannon):
        raise ValueError("That cannon needs an installed station and a loaded charge")
    participants = [world.player.id] + [sailor.id for sailor in world.ship.crew
                                       if sailor.health > 0 and sailor.labors.get("load", 0) > 0]
    handoff = build_handoff(world, encounter, "cannon_fire", tick, participants,
                            {"kind": "fire_cannon", "cannon": cannon},
                            basis["event_id"])
    contact["combat_pending"] = handoff
    encounter["contact"] = contact
    validate_contact(contact, encounter.get("id"), contact_instance_id=(
        encounter.get("contact_instance_id", encounter.get("id"))))
    return handoff


def withdraw_crossing(encounter):
    contact = contact_of(encounter)
    request = contact.get("crossing")
    if not request:
        if contact.get("combat_pending"):
            raise ValueError("A combat handoff is already pending and cannot be withdrawn here")
        raise ValueError("There is no pending peaceful crossing request")
    contact["crossing"] = None
    return request
