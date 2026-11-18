# Shipboard crew events and relationships

Status: implementation in progress; measurement/tuning and final integration verification remain.

## Implementation evidence (Nov 18, 2026)

- Phase 0 audit confirmed existing relationship scores, shared-meal attendance, hardship grievance, needs/rest recovery, crew profile and save migration surfaces; work duration/watch participation is not tracked reliably, so no fairness or watch-duty accusation is in scope.
- Phase 1 adds a bounded 64-entry social evidence ledger. Real attended shared meals record no relationship change; completed nearby stories attribute the existing +1 relationship score subject to the measured +2-per-pair-per-game-day ceiling. Crew profile shows recent causes and names, and marks repeated companionship only after three recorded shared occasions. No extra relationship reward is applied.
- Phase 2: persistent hardship concerns open only when the existing grievance reaches 60 during its existing sustained-hardship condition, record exact trigger categories, and resolve only when existing grievance falls below 35 and morale reaches 42. The detail view points to legal duty-priority changes, real rest/sleep, supplies or injury treatment; it does not reset grievance or award friendship. Departed participants archive concerns.
- Baseline tuning: a normal fresh-world three-day run before tuning produced 313 completed stories (104/103/106 per day), 23 shared meals (9/8/6), 0 concerns, 41 completed jobs, food 26.0→19.5 kg and clean water 46.5→16.25 L. Morale remained 64–77. The largest pair rose by 32 in day one (Ivo–Tavi reached 88 by day three), too fast for a quiet autonomous social loop. Applied one conservative tuning group: cap the existing story score increase at +2 per pair per game day; stories still complete and appear in evidence, but later stories that day give no extra score. The cap resets on day boundary and persists through saves. No new relationship authority or rewards.
- Matched three-day rerun after tuning produced the same 313 stories and 23 meals, 0 concerns, 41 jobs, and identical supplies/morale. Because the fresh world starts at 08:00, the 72-hour horizon crosses four calendar-day IDs; the largest pairwise score delta was +8, exactly bounded by +2 on each game day. Ledger remained capped at 64 retained entries. This is a narrow fresh-world baseline, not broad balance proof.
- New coverage: `tests/test_ship_social_events.py` exercises actual meal/story attribution, repeated-companionship threshold, score cap and reset/save continuation, profile purity, legacy migration, invalid state rejection, bounded history, concern lifecycle and archived participant persistence. Existing `tests/test_shared_meals.py` also verifies scheduled duty is not interrupted, critical thirst, unreachable mess and food shortage without false attendance/rewards. Latest focused run across social events/shared meals/crew detail/ship life/crew lifecycle: 116 passed in 211.48s.
- `tools/preview_crew_social_events.py --output C:/Users/Warp/AppData/Local/Temp/opencode/pirates-crew-social-events` renders four native frames (repeated companionship, roster badge, open concern, resolved concern). The concern state is explicitly a controlled presentation fixture; this proves UI rendering only, not event frequency or ordinary-voyage balance. All four captures leave game clock and ship seconds unchanged.

## Goal

Make ordinary voyages produce understandable companionship, disagreements and captain decisions without turning crew care into another compulsory chore. Events must arise from things that actually happened aboard, have modest consequences, and offer practical recovery.

## Existing systems to extend

- `pirates/ship.py:178` — sailor relationships; `:197` — grievance. Reuse these authorities rather than introduce competing friendship or resentment scores.
- `pirates/ship.py:466` — grievance/desertion handling; preserve existing warnings and recovery routes.
- `pirates/ship.py:1036,1066` — shared-meal requests and attendance; `:2019` — existing social relationship gains. Audit both before adding any gain to avoid double rewards.
- `pirates/ship.py:1216` — ship event recording; `:2184,2378` — persisted meal-window state. Extend normal ship persistence for social evidence and event lifecycle.
- `pirates/management.py:41,72` — crew grievance and friendship reports. Extend existing crew detail and interaction surfaces.
- Existing crew/NPC plans and society code must be reviewed for overlap before implementation. Keep shipboard social events separate from headless `sim/` and tactical-duel emotions.

## Design rules

- No invented meetings, off-screen conversations or accusations without recorded evidence.
- Shared meals count only when people actually attend together; blocked routes and missed attendance grant nothing.
- Similar duties are not necessarily unfair. Compare capable crew, enabled labor, availability and actual completed work; exclude injury, emergency duty and necessary specialist work from simplistic blame.
- Do not claim night-watch unfairness until individual watch participation is actually tracked.
- Relationship changes are bounded, slow and attributable. Never override survival needs, critical orders, wages or existing desertion rules.
- Viewing reports is read-only and time-frozen. A conversation/action has an explicit time cost; duty changes use normal management controls.
- One open concern per cause/participant group; cooldowns and bounded history prevent notification spam and score farming.
- Captain acknowledgement is not a cure. Promises recover trust only after observable follow-through; failure requires evidence too.
- Preserve deterministic advancement, old saves, finite supplies/money and unrelated concurrent work.

## Phase 0 — Audit and settle the model

1. Trace existing friendship changes, social work, meals, grievance, stress, rest and captain retention actions.
2. Identify authoritative work/rest timestamps and whether comparable workload can be measured reliably.
3. Define a minimal persisted concern record: stable ID, cause, participants, source-event IDs/timestamps, status, response, cooldown and follow-through condition.
4. Choose a small centralized tuning table for thresholds, per-day effect caps and retention limits. Record numerical defaults as experiments, not proven balance.

Acceptance: a field/source map identifies ownership for every new value; no duplicate relationship or grievance authority; unsupported triggers are excluded.

## Phase 1 — Evidence and a quiet social ledger

1. Record bounded evidence for co-attended meals, actual shared social activity, completed duties and interrupted/recovered rest where supported.
2. Attribute existing relationship changes to their real cause rather than adding a second reward.
3. Add an unobtrusive recent-events section to crew detail: who, what happened, when, and why their opinion changed.
4. Persist stable event IDs and processed checkpoints so repeated reads, reloads and chunked advancement cannot duplicate effects.

Acceptance: a genuine shared event has one attributable result; absent/blocked crew receive none; repeated reading and save/reload preserve identical state.

## Phase 2 — Three grounded event families

Start with these, not a general dialogue generator:

| Event | Required evidence | Initial outcome | Recovery |
| --- | --- | --- | --- |
| Growing companionship | Repeated real shared meals/social activity | Existing friendship gains become visible; occasional short crew remark | No management action required |
| Exhausted worker raises a concern | Sustained actual work plus repeated missed/interrupted rest | Small bounded grievance contribution and a named concern | Allow real rest or redistribute feasible labor |
| Repeated unequal burden | Comparable available crew plus sustained measured workload difference | Complaint citing specific work, not an invented accusation | Change assignments and observe a fairer subsequent interval |

If comparable workload cannot be established in Phase 0, ship the exhausted-worker concern first; do not fake a fairness judgment. Record that scope decision explicitly.

Acceptance: each event can be reproduced from ordinary gameplay and includes its evidence; emergency work does not create an unfairness accusation; idle safe voyages do not manufacture conflict.

## Phase 3 — Captain responses and follow-through

1. Add a compact concern interaction through the existing crew UI; no forced modal interruption during navigation or emergencies.
2. Offer acknowledge/listen, open duty management, propose rest, or decline/ignore. Show time/resource costs and likely limitations before committing.
3. Rest/duty remedies use existing legal orders and needs scheduling; do not teleport crew, reset fatigue or silently disable essential duties.
4. Mark concerns improving/resolved only after the relevant rest or workload change occurs. Explain why a remedy remains blocked.
5. Let ignored concerns persist with capped effects and reminders, not an endless escalation multiplier.

Acceptance: promises alone cannot farm friendship or erase grievance; real follow-through resolves a concern; lack of food, access or eligible relief remains visible.

## Phase 4 — Balance and native playtest

Run matched multi-day voyages with the same initial state and seed:

- Ordinary adequate supplies and reasonable duties: companionship may emerge; complaints remain uncommon.
- Sustained overwork: a concern appears with a concrete cause and feasible remedy.
- Corrected assignments/rest: concern improves after real recovery.
- Ignored concern: measured, capped consequences; no sudden mutiny or new desertion shortcut.
- Scarcity/emergency, blocked mess/rest route, injured specialist, lone eligible worker and departed participant: accurate attribution and safe lifecycle.

Record event counts, notifications/day, relationship/grievance deltas, crew needs, jobs, supply use, spending and runtime. Adjust one connected tuning group at a time. Inspect native crew detail, concern, blocked-remedy and resolved-concern screens at supported window sizes.

Acceptance: social events add personality without materially breaking the verified provisioning/work loop or demanding constant intervention. Numeric frequency/effect targets are set after baseline measurement, not invented in advance.

## Phase 5 — Persistence, regressions and handoff

- Old saves initialize empty evidence/concern state without retroactive rewards or complaints.
- One uninterrupted advance equals chunked advance with mid-event save/reload.
- Removed/dead/departed participants close or archive concerns safely; IDs are never reassigned to another person.
- Test attribution, cooldowns, daily caps, negative cases, response atomicity, UI purity and genuine recovery.
- Re-run focused ship/meal/crew/economy/voyage tests, compile checks, whitespace audit and native previews. Attempt the repository suite and report unrelated failures/time limits honestly.
- Update this plan with commands, measured evidence and limits; update the global project skill without overwriting concurrent additions.

## Completion checklist

- [x] Existing social/crew systems audited and reused.
- [x] Real shared activities produce visible, non-duplicated relationship evidence.
- [x] Work/rest concerns cite existing hardship causes; unsupported workload/watch accusations are excluded.
- [x] Captain responses use existing labor priorities, natural rest/needs and paid retention action; concern resolution requires existing recovery conditions.
- [x] Relationship effects are capped at +2 per pair/game day and measured over a matched 72-hour run; concern notifications are one per open hardship concern.
- [ ] Reports are read-only; saves and deterministic continuation remain compatible.
- [ ] Native UI and regression evidence recorded; unrelated work preserved.

## Explicitly deferred

Romance, full dialogue trees, procedural gossip, factions/cliques, sabotage, mutiny, combat bonuses, a new relationship currency, and expansion of headless world simulation. Revisit only after this small shipboard loop is proven useful.
