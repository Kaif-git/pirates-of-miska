"""Bounded per-actor sight/hearing contacts for group combat."""


AUDIBLE_EVENTS = {"ACTION_START", "MOVE", "WHIFF", "STATUS", "HIT", "WOUND", "BLOCK", "PARRY", "SHOVE", "FALL",
                  "INCAPACITATED", "DECAPITATION", "TERMINAL_OUTCOME", "FEAR_BREAK", "FIGHT_END"}


def _bearing(origin, source):
    return ((source[0] > origin[0]) - (source[0] < origin[0]),
            (source[1] > origin[1]) - (source[1] < origin[1]))


def refresh_contacts(state, events=()):
    """Remember visible positions exactly; hearing stores only a coarse bearing."""
    if len(state.fighters) <= 2:
        return
    tick = state.tick
    turn = state.config["time"]["ticks_per_turn"]
    group = state.config["group"]
    ttl = group["contact_memory_exchanges"] * turn
    fighters = {fighter.id: fighter for fighter in state.fighters}

    for observer in state.fighters:
        memory = observer.contact_memory
        for target in state.visible_hostiles_for(observer.id):
            row = memory.setdefault(target.id, {
                "position": None, "last_seen_tick": None, "last_heard_tick": None, "bearing": None})
            row["position"] = target.pos
            row["last_seen_tick"] = tick
            row["bearing"] = None

        for event in events:
            if event.type.value not in AUDIBLE_EVENTS or observer.id not in event.hearers:
                continue
            sources = [fighters[identity] for identity in (event.actor, event.target)
                       if identity in fighters and identity != observer.id
                       and fighters[identity].team_id != observer.team_id]
            sources = [source for source in sources
                       if max(abs(observer.pos[0]-source.pos[0]),
                              abs(observer.pos[1]-source.pos[1])) <= group["hearing_range"]]
            if not sources:
                continue
            source = min(sources, key=lambda item: (
                max(abs(observer.pos[0]-item.pos[0]), abs(observer.pos[1]-item.pos[1])), item.id))
            source_pos = tuple(event.deltas.get("to", source.pos)) if event.actor == source.id else source.pos
            if max(abs(observer.pos[0]-source_pos[0]), abs(observer.pos[1]-source_pos[1])) > group["hearing_range"]:
                continue
            row = memory.setdefault(source.id, {
                "position": None, "last_seen_tick": None, "last_heard_tick": None, "bearing": None})
            row["last_heard_tick"] = tick
            row["bearing"] = _bearing(observer.pos, source_pos)

        expired = [identity for identity, row in memory.items()
                   if tick-max(row["last_seen_tick"] or 0, row["last_heard_tick"] or 0) > ttl]
        for identity in expired:
            del memory[identity]
        if len(memory) > 64:
            oldest = sorted(memory, key=lambda identity: max(
                memory[identity]["last_seen_tick"] or 0,
                memory[identity]["last_heard_tick"] or 0))
            for identity in oldest[:len(memory)-64]:
                del memory[identity]


def apply_witnessed_morale(state, events, exchange_id):
    """Apply at most one strongest witnessed morale response per ally/exchange."""
    if len(state.fighters) <= 2:
        return
    tuning = state.config["group"]["witnessed_morale"]
    fighters = {fighter.id: fighter for fighter in state.fighters}
    responses = {}

    def offer(team_id, evidence_actor, delta, reason, event):
        if not delta:
            return
        for witness in state.fighters:
            if (witness.team_id != team_id or witness.id not in event.witnesses + event.hearers
                    or witness.id == evidence_actor
                    or witness.morale_reacted_exchange == exchange_id):
                continue
            candidate = (abs(delta), reason, evidence_actor, delta, event.type.value)
            previous = responses.get(witness.id)
            if previous is None or candidate[0] > previous[0]:
                responses[witness.id] = candidate

    for event in events:
        victim = fighters.get(event.target or event.actor)
        if event.type.value == "WOUND" and victim:
            offer(victim.team_id, victim.id, tuning["ally_wounded"], "ALLY_WOUNDED", event)
        elif event.type.value in {"INCAPACITATED", "DECAPITATION", "TERMINAL_OUTCOME"} and victim:
            if state.leaders.get(victim.team_id) == victim.id:
                delta, reason = tuning["leader_lost"], "LEADER_LOST"
            elif (event.type.value == "TERMINAL_OUTCOME"
                  and event.deltas.get("reason") in {"yield", "flee"}):
                delta, reason = tuning["ally_withdrew"], "ALLY_WITHDREW"
            else:
                delta, reason = tuning["ally_incapacitated"], "ALLY_INCAPACITATED"
            offer(victim.team_id, victim.id, delta, reason, event)
        elif event.type.value == "WHIFF" and event.deltas.get("kind") == "DASH_LIGHT":
            actor = fighters.get(event.actor)
            if actor:
                offer(actor.team_id, actor.id, tuning["failed_rush"], "FAILED_RUSH", event)
        elif (event.type.value == "STATUS"
              and event.deltas.get("status") == "movement_blocked"
              and event.deltas.get("kind") in {"DASH", "DASH_LIGHT", "RUN"}):
            actor = fighters.get(event.actor)
            if actor:
                offer(actor.team_id, actor.id, tuning["failed_rush"], "FAILED_RUSH", event)

    for fighter_id, (_, reason, source_id, delta, evidence_type) in responses.items():
        fighter = fighters[fighter_id]
        before = fighter.morale
        fighter.morale = max(0, min(state.config["defaults"]["pool_max"], before + delta))
        fighter.morale_reacted_exchange = exchange_id
        state.emit("POOL_CHANGE", fighter_id, source_id,
                   deltas={"morale": fighter.morale-before, "group_response": reason,
                           "evidence_type": evidence_type, "exchange": exchange_id},
                   cause_tags=("WITNESSED_GROUP_MORALE", reason))
