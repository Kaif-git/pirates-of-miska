"""Pixel duel dashboard. Presentation only; receives immutable combat data."""
import math
import re
import time
import textwrap

import numpy as np
from PIL import ImageDraw
from PIL import Image


BG = (10, 9, 14)
PANEL = (23, 20, 29)
LINE = (65, 56, 72)
TEXT = (225, 219, 202)
DIM = (160, 151, 143)
GOLD = (232, 207, 122)
GREEN = (124, 196, 147)
RED = (255, 154, 122)
PRIMARY_LABELS = ["Light", "Heavy", "Thrust", "Defend", "Dash", "Move", "Run",
                  "Dash+Light", "Feint", "Shove", "Disarm", "Disengage", "Wait",
                  "Bandage", "More", "Sheet", "Help"]


def upload_layer(display, renderer, image, slot):
    """Reuse two SDL textures instead of allocating/destroying one per frame."""
    from tcod.sdl.render import BlendMode
    layers = getattr(display, "layer_textures", None)
    if layers is None:
        layers = display.layer_textures = {}
    key = (id(renderer.context), image.size)
    layer = layers.get(slot)
    if layer is None or layer[0] != key:
        layer = layers[slot] = [key, [None, None], 0]
    index = layer[2]
    layer[2] = 1-index
    pixels = np.asarray(image, dtype=np.uint8)
    texture = layer[1][index]
    if texture is None:
        texture = renderer.context.sdl_renderer.upload_texture(pixels)
        texture.blend_mode = BlendMode.BLEND
        layer[1][index] = texture
    else:
        texture.update(pixels)
    return texture


def paint_text(display, image, xy, value, font, color):
    """Rasterize stable labels once, not with FreeType on every animation frame."""
    value = str(value)
    cache = getattr(display, "text_cache", None)
    if cache is None:
        cache = display.text_cache = {}
    key = (getattr(font, "path", ""), font.size, value, color)
    if key not in cache:
        if len(cache) >= 1024:
            cache.clear()
        patch = Image.new("RGBA", (max(1, math.ceil(font.getlength(value))+3), font.size*2+6))
        ImageDraw.Draw(patch).text((0, 0), value, font=font, fill=(*color, 255))
        cache[key] = patch
    image.alpha_composite(cache[key], (round(xy[0]), round(xy[1])))


def draw_feedback(display, renderer, model, arena, screens):
    """Only the small arena overlay changes during a tween, not the dashboard."""
    ax, ay, aw, ah = arena
    now = time.monotonic()
    active_popups = any(now-started < 1.35 for started, _ in getattr(display, "popup_started", {}).values())
    feedback_key = (id(model), arena, tuple(screens), display.animating, id(renderer.context))
    if not active_popups and getattr(display, "feedback_key", None) == feedback_key:
        renderer.context.sdl_renderer.copy(display.feedback_texture, dest=arena)
        return
    image = Image.new("RGBA", (aw, ah), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = renderer.ui.font(size=12, mono=True)
    tw, th = renderer.tile_width, renderer.tile_height
    local = [(x-ax, y-ay) for x, y in screens]
    def text(x, y, value, color):
        paint_text(display, image, (x, y), value, font, color)
    actors = model.actors or ()
    actor_positions = {actor.actor_id: screen for actor, screen in zip(actors, local)}
    for index, (actor, (px, py)) in enumerate(zip(actors, local)):
        name = ("YOU" if actor.is_player else f"[{actor.glyph}]" if model.group_combat
                else actor.name)
        color = GREEN if actor.is_player else RED if actor.hostile else (120, 165, 226)
        label_y = py-18 if actor.is_player else py+th+2
        text(min(max(5, px), aw-font.getlength(name)-6), max(3, min(ah-17, label_y)), name, color)
        action = model.player_action if actor.is_player else actor.action
        if display.animating and action != "IDLE":
            aim = model.player_aim if actor.is_player else ""
            cue = action.replace("_", "+").title() + (f" → {aim.title()}" if aim else "")
            cue_y = max(3, label_y-18) if index == 0 else min(ah-17, label_y+18)
            color = GOLD if actor.is_player else RED if actor.hostile else (120, 165, 226)
            text(min(max(5, px-20), aw-font.getlength(cue)-6), cue_y, cue, color)
            draw.rectangle((px-1, py-1, px+tw, py+th), outline=(*color, 255), width=2)
            remaining = model.player_remaining if actor.is_player else None
            if action in {"LIGHT", "HEAVY", "DASH_LIGHT"} and remaining is not None:
                target = actor_positions.get(actor.target_id)
                if target is None:
                    continue
                other_x, other_y = target
                sx, sy = px+tw/2, py+th/2
                dx, dy = other_x-px, other_y-py
                distance = max(1, (dx*dx+dy*dy)**.5)
                reach = min(distance, tw*1.4)
                draw.line((sx, sy, sx+dx/distance*reach, sy+dy/distance*reach),
                          fill=(*color, 255), width=3 if remaining <= 1 else 1)
    if not hasattr(display, "popup_started") or model.tick < getattr(display, "feedback_tick", 0):
        display.popup_started, display.popup_seen = {}, set()
    display.feedback_tick = model.tick
    for popup in model.popups:
        key = (popup.tick, popup.text, popup.pos)
        if key not in display.popup_seen:
            display.popup_seen.add(key)
            display.popup_started[key] = (now, popup)
    display.popup_started = {key: value for key, value in display.popup_started.items() if now-value[0] < 1.35}
    lanes = {}
    priority = lambda popup: (100 if popup.text.startswith(("DEATH", "FALLEN", "SEVERED")) else
                              80 if "DEEP" in popup.text or "impact" in popup.text.lower() else
                              70 if popup.text in {"Flinched", "Recoil!", "Bleeding wound"} else
                              50 if popup.text.startswith("Stability") else 10)
    selected_popups = []
    per_actor = {}
    for started, popup in sorted(display.popup_started.values(),
                                 key=lambda row: (priority(row[1]), row[0]), reverse=True):
        if per_actor.get(popup.pos, 0) >= 2:
            continue
        selected_popups.append((started, popup))
        per_actor[popup.pos] = per_actor.get(popup.pos, 0)+1
    for started, popup in selected_popups:
        age = now-started
        mx, my, mtw, mth = display.map_rect
        px, py = mx-ax+popup.pos[0]*mtw, my-ay+popup.pos[1]*mth
        lane = lanes.get(popup.pos, 0)
        lanes[popup.pos] = lane+1
        # Opposing hits need separate lanes, especially when adjacent fighters
        # trade at the same tick. Do not paint CUT and DEEP over one another.
        if popup.pos != model.player_pos:
            py = min(ah-24, py+mth+18-age*16+lane*21)
        else:
            py = max(22, py-26-age*22-lane*21)
        severe = popup.text.startswith(('DEEP', 'SEVERED', 'Hand severed', 'Guard broken'))
        popup_font = renderer.ui.font(size=14 if severe else 12, mono=True)
        pw = popup_font.getlength(popup.text)+12
        px = max(5, min(aw-pw-5, px-pw/2))
        color = GOLD if any(word in popup.text.lower() for word in ("parr", "block", "too far")) else RED if severe or popup.pos == model.player_pos else GREEN
        draw.rounded_rectangle((px, py, px+pw, py+(23 if severe else 20)), radius=4,
                               fill=(24, 12, 16, 235), outline=(*color, 255), width=2 if severe else 1)
        paint_text(display, image, (px+6, py+2), popup.text, popup_font, color)
        reduced_motion = bool(dict(model.presentation).get("reduced_motion", False))
        if age < .2 and severe and not reduced_motion:
            draw.rounded_rectangle((1, 1, aw-2, ah-2), radius=8, outline=(*RED, 255), width=2)
    sdl = renderer.context.sdl_renderer
    texture = upload_layer(display, renderer, image, "feedback")
    sdl.copy(texture, dest=arena)
    display.feedback_key = feedback_key if not display.popup_started else None
    display.feedback_texture = texture
    display.feedback_model = model


def context_labels(rows, model=None):
    """The same contextual controls serve the native preview and live duel."""
    if any("[Retry][Next]" in row for row in rows):
        return ["Retry", "Next", "Sheet", "Log", "Help"]
    if "[Confirm][Back]" in rows:
        return ["Confirm", "Back"]
    if any(row.startswith("PICK UP WEAPON:") for row in rows):
        count = sum(bool(re.search(r"\[Pick \d+\]", row)) for row in rows)
        return [*(f"Pick {index}" for index in range(1, count+1)), "Back"]
    if any("Choose target:" in row for row in rows):
        return ["Eye", "Head", "Torso", "Arm", "Hand", "Leg", "Back"]
    if any("Choose a visible hostile:" in row for row in rows):
        targets = sorted({int(match) for row in rows
                          for match in re.findall(r"\[Target (\d+)\]", row)})
        return [*(f"Target {index}" for index in targets),
                *( ["Prev Targets"] if any("[Prev Targets]" in row for row in rows) else []),
                *( ["Next Targets"] if any("[Next Targets]" in row for row in rows) else []), "Back"]
    if any("[Tactics]" in row for row in rows):
        return ["Tactics", "Run", "Dash+Light", "Bandage", "Eat", "Drink",
                "Pickup", "Notebook", "Log", "Guide", "Strategy", "Timeline", "Back"]
    if any(row.startswith("TACTICAL ACTIONS") for row in rows):
        return ["Feint", "Shove", "Disengage", "Thrust", "Disarm", "Adrenaline", "Back"]
    if any(row.strip() == "[Tactics][Run][Dash+Light]" for row in rows):
        return ["Run", "Dash+Light", "Bandage", "Eat", "Drink", "Pickup", "Notebook", "Log", "Guide", "Strategy", "Timeline", "Back"]
    if any("DISENGAGE — CHOOSE DIRECTION" in row for row in rows):
        return ["1 tile", "Back", "NW", "N", "NE", "W", "E", "SW", "S", "SE"]
    if any("CHOOSE DIRECTION" in row for row in rows):
        return ["1 tile", "2 tiles", "3 tiles", "Back", "NW", "N", "NE", "W", "E", "SW", "S", "SE"]
    if any("[Back]" in row for row in rows):
        return ["Back"]
    if model and model.player_grounded:
        return ["Rise", "Crawl", "Cover", "Sheet", "Help"]
    return list(PRIMARY_LABELS)


def dashboard_geometry(width, height, count, *, detailed=False):
    """Fixed spatial anchors: state/content never resizes the arena or footer."""
    margin, gap = 12, 10
    wide = width >= 1000
    card_width = min(250, (width - 2*margin - 2*gap - 300)//2) if wide else (width-2*margin-gap)//2
    card_height = 190 if wide else 120 if height < 650 else 154
    controls_columns = 6 if wide or count > 10 else 5
    control_rows = max(2, (count + controls_columns - 1)//controls_columns)
    controls_height = 40 + control_rows*48
    controls_top = height-margin-controls_height
    arena_left = margin+card_width+gap if wide else margin
    arena_top = 76 if wide else 76+card_height+gap
    arena_width = width-2*arena_left if wide else width-2*margin
    # Outcome and contextual explanation get dedicated, non-overlapping space.
    info_height = 88 if count > 12 else 96 if height < 650 else 116
    arena_bottom = controls_top-gap-info_height-gap
    return dict(wide=wide, card_width=card_width, card_height=card_height,
                arena=(arena_left, arena_top, arena_width, max(60, arena_bottom-arena_top)),
                info=(margin, arena_bottom+gap, width-2*margin, info_height),
                controls=(margin, controls_top, width-2*margin, controls_height),
                columns=controls_columns)


def draw_dashboard(display, renderer, model, rows, *, present=True):
    renderer._sync_console_size()
    sdl = renderer.context.sdl_renderer
    width, height = sdl.output_size
    transition = None
    positions = tuple(actor.pos for actor in model.actors) or (model.player_pos, model.enemy_pos)
    display.animating = False
    playback = getattr(display, "playback", None)
    if playback:
        sampled, positions, finished, transition = playback.sample()
        if finished:
            display.playback = None
            positions = tuple(actor.pos for actor in model.actors) or (model.player_pos, model.enemy_pos)
            transition = None
        else:
            display.animating = True
            model = sampled
    labels = [*PRIMARY_LABELS, "Skip"] if display.animating else context_labels(rows, model)
    layout = dashboard_geometry(width, height, len(labels))
    display.layout = layout
    reading = bool(rows and rows[0].startswith(("EXCHANGE DETAILS", "STAT:", "CHARACTER STATS", "STRATEGY COACH")))
    display.reading_modal = reading
    display.modal_rect = (24, 76, width-48, layout["controls"][1]-86) if reading else None
    cache_key = (id(model), width, height, rows, display.hover_action, display.scroll,
                  getattr(display, "message", ""), getattr(display, "round_result", ()),
                  getattr(display, "round_summary", ()),
                  getattr(display, "stat_enemy", False),
                 getattr(display, "coach_enabled", False),
                 getattr(display, "selected_distance", ""), display.animating,
                 getattr(display, "exchange_number", 1), getattr(display, "rounds", True), id(renderer.context))
    from pirates.duel_scene import render_scene
    if getattr(display, "dashboard_key", None) == cache_key:
        sdl.draw_color = (*BG, 255)
        sdl.clear()
        screens = render_scene(display, renderer, model, layout["arena"], positions, transition)
        sdl.copy(display.dashboard_texture, dest=(0, 0, width, height))
        if not reading:
            draw_feedback(display, renderer, model, layout["arena"], screens)
        if present:
            sdl.present()
        return
    image = renderer.ui.new_overlay(width, height)
    draw = ImageDraw.Draw(image)
    font = renderer.ui.font(size=14, mono=True)
    small = renderer.ui.font(size=12, mono=True)
    title_font = renderer.ui.font(size=18, mono=True)
    display.buttons = []

    def panel(rect, outline=LINE):
        x, y, w, h = rect
        draw.rounded_rectangle((x, y, x+w-1, y+h-1), radius=8, fill=(*PANEL, 255), outline=(*outline, 255))

    def text(x, y, value, color=TEXT, face=font):
        paint_text(display, image, (x, y), value, face, color)

    def lines(rect, content, *, scroll=False):
        x, y, w, h = rect
        columns = max(8, int(w/small.getlength("M")))
        wrapped = []
        for row in content:
            color = TEXT
            if isinstance(row, tuple) and len(row) == 2 and isinstance(row[1], tuple):
                row, color = row
            wrapped.extend((part, color) for part in textwrap.wrap(str(row), columns) or [""])
        page = max(1, h//17)
        if scroll:
            display.max_scroll = max(0, len(wrapped)-page)
            display.scroll = min(max(0, display.scroll), display.max_scroll)
        start = display.scroll if scroll else 0
        for index, (value, color) in enumerate(wrapped[start:start+page]):
            text(x, y+index*17, value, color, small)

    def meter(x, y, w, label, value, maximum, color):
        fraction = min(1, max(0, value/max(1, maximum)))
        draw.rounded_rectangle((x, y, x+w, y+18), radius=3, fill=(9, 9, 13, 255))
        if fraction:
            draw.rounded_rectangle((x, y, x+max(3, int(w*fraction)), y+18), radius=3, fill=(*color, 255))
        text(x+5, y+1, f"{label} {value:.0f}/{maximum:.0f}", face=small)

    ended = any(word in model.player_state for word in ("DEFEAT", "VICTORY", "FLED"))
    round_mode = getattr(display, "rounds", True)
    banner = "RESOLVING EXCHANGE" if display.animating else "FIGHT OVER" if ended else "CHOOSE ONE ACTION" if round_mode else model.player_state
    panel((12, 10, width-24, 56), GOLD)
    text(24, 16, banner, GOLD, title_font)
    exchange = (f"ROUND {getattr(display, 'exchange_number', 1)}" if round_mode else
                next((row.split(" — ")[0] for row in rows if row.startswith("ROUND ")), "SWORD DUEL"))
    if display.animating:
        exchange = f"ROUND {getattr(display, 'exchange_number', 1)}"
    subtitle = f"{exchange}  |  ONE ACTION EACH  |  gap {model.gap}" if round_mode else model.instructions
    coach_hint = None
    if getattr(display, 'coach_enabled', False) and not display.animating and not ended:
        from pirates.duel_coach import coach
        coach_hint = coach(model, getattr(display, 'round_summary', ()))
        subtitle = "COACH: " + coach_hint.headline
    elif model.functional_alerts and not display.animating:
        subtitle += " | " + model.functional_alerts[0]
    subtitle = textwrap.shorten(subtitle, width=max(20, int((width-175)/small.getlength('M'))), placeholder="…")
    text(24, 42, subtitle, DIM, small)
    if not display.animating and not reading:
        for bx, by, bw, bh, caption, label in (
                (width-152, 14, 126, 22, "Coach ON [I]" if getattr(display, 'coach_enabled', False) else "Coach OFF [I]", "Coach"),
                (width-152, 40, 126, 19, "Strategy", "Strategy")):
            draw.rounded_rectangle((bx, by, bx+bw, by+bh), radius=4, fill=(*BG, 255), outline=(*GOLD, 255))
            text(bx+5, by+2, caption, GOLD, small)
            display.buttons.append(((bx, by, bw, bh), label))
    cw, ch = layout["card_width"], layout["card_height"]
    enemy_x = width-12-cw
    pools = {name: (value, maximum) for name, value, maximum in model.pools}
    enemy_pools = {name: (value, maximum) for name, value, maximum in model.enemy_pools}
    enemy_resources = [("Blood", *enemy_pools["BLD"], (153, 40, 46))] if enemy_pools else []
    if model.enemy_visible and not model.group_combat:
        enemy_resources += [("Stamina", *enemy_pools.get("STA", (model.enemy_stamina*100, 100)), (57, 115, 77)),
                            ("Fatigue", *enemy_pools.get("FAT", (model.enemy_fatigue*100, 100)), (137, 101, 36))]
    if model.group_combat:
        enemy_resources = []
        threat_rows = [f"[{actor.glyph}] {actor.name[:10]} · ~{actor.distance} "
                       f"{'tile' if actor.distance == 1 else 'tiles'} · {actor.posture}"
                       for actor in model.threats]
        if model.threat_overflow:
            threat_rows.append(f"+{model.threat_overflow} more visible")
        if not threat_rows:
            threat_rows.append("No visible hostiles")
        enemy_card_name = "VISIBLE THREATS"
    else:
        threat_rows = []
        enemy_card_name = model.enemy_name
    for x, name, level, mood, resources, wounds, color in (
            (12, "YOU", model.player_level, model.player_state,
              [("Blood", *pools["BLD"], (153, 40, 46)), ("Stamina", *pools["STA"], (57, 115, 77)),
               ("Fatigue", *pools["FAT"], (137, 101, 36)),
               (f"Stability (bal {model.player_balance:.0f})", model.player_stability, 100, (174, 135, 61))],
             [*model.player_emotion_rows[:1], *model.functional_alerts[:1], *model.player_status_rows[:1],
                *(model.player_injury_rows or ("No bleeding wounds",))], GREEN),
             (enemy_x, enemy_card_name, model.enemy_level, model.enemy_mood,
               enemy_resources,
                  (threat_rows if model.group_combat else
                   [*model.enemy_tell[:1], *model.enemy_status_rows[:1], *(model.enemy_injury_rows or
                   ["Next action unknown" if model.enemy_action == "IDLE" else model.enemy_action.replace("_", " ")])]
                   if model.enemy_visible else ["No current visual contact."]), RED)):
        panel((x, 76, cw, ch), color)
        text(x+10, 84, f"{name[:16]}" if model.group_combat and x == enemy_x
             else f"{name[:16]}  Lv{level}", color)
        readout = (dict(model.own_readout) if x == 12 else
                   {} if model.group_combat else dict(model.enemy_readout))
        if readout:
            sw = (cw-20)//3
            for index, (name, short) in enumerate((("Speed", "SPD"), ("Strength", "STR"), ("Accuracy", "ACC"))):
                value, fresh = readout[name], readout.get('fresh_'+name, readout[name])
                tone = RED if value < fresh-.05 else GREEN if value > fresh+.05 else DIM
                text(x+10+index*sw, 105, f"{short} {value:.1f}", tone, small)
                if not reading and not display.animating:
                    display.buttons.append(((x+10+index*sw, 103, sw, 16), ("Stat:" if x == 12 else "Enemy stat:")+name))
        elif not model.group_combat or x == 12:
            text(x+10, 105, mood[:max(12, int((cw-20)/small.getlength("M")))], DIM, small)
        meter_gap = 18 if ch < 150 else 24
        meter_top = 119 if ch < 150 else 127
        for i, (label, value, maximum, bar_color) in enumerate(resources):
            meter(x+10, meter_top+i*meter_gap, cw-20, label, value, maximum, bar_color)
            if not reading and not display.animating:
                stat = label.split(" (bal ", 1)[0].replace(" %", "")
                display.buttons.append(((x+10, meter_top+i*meter_gap, cw-20, 19),
                                        ("Enemy stat:" if x == enemy_x else "Stat:")+stat))
        lines((x+10, meter_top+len(resources)*meter_gap, cw-20,
               max(17, 76+ch-meter_top-len(resources)*meter_gap-3)), wounds)
    display.header_rect = (enemy_x, 76, cw, ch)

    ax, ay, aw, ah = layout["arena"]
    panel((ax, ay, aw, ah), (43, 68, 57))
    text(ax+10, ay+6, "ARENA", GREEN, small)
    sdl.draw_color = (*BG, 255)
    sdl.clear()
    screens = render_scene(display, renderer, model, layout["arena"], positions, transition)
    vx, vy, vwidth, vheight = display.arena_rect
    draw.rectangle((vx, vy, vx+vwidth-1, vy+vheight-1), fill=(0, 0, 0, 0))
    ix, iy, iw, ih = layout["info"]
    panel((ix, iy, iw, ih))
    contextual = labels != PRIMARY_LABELS
    if display.animating:
        recovering = model.player_action == model.enemy_action == "IDLE"
        content = ["EXCHANGE OUTCOME" if recovering else f"YOU: {model.player_action.replace('_', ' ')}   |   {model.enemy_name}: {model.enemy_action.replace('_', ' ')}",
                   "Returning control..." if recovering else "Both commitments are locked. Watch the exchange.", *model.log[-3:]]
    elif contextual and not ended:
        if model.player_grounded and labels[0] == "Rise":
            option_details = {option.label: option.details for option in model.actions}
            content = ["GROUNDED RECOVERY", model.instructions,
                       option_details.get("Rise", "Rise is timed and exposed."),
                       option_details.get("Crawl", "Crawl is slow and keeps you grounded."),
                       *model.log[-3:]]
        elif labels and labels[0].startswith("Target ") and model.group_combat:
            chooser_index = next((index for index, row in enumerate(rows)
                                  if row == "Choose a visible hostile:"), len(rows))
            choices = [row for row in rows[chooser_index+1:]
                       if row.startswith(("[Target ", "[Prev Targets]", "[Next Targets]"))]
            dash_light = any("Dash+Light target area" in row for row in rows)
            content = (["DASH + LIGHT — CHOOSE TARGET",
                        "Only hostiles with a clear approach ending in weapon reach are listed.",
                        "Direction and distance are chosen automatically."] if dash_light else
                       ["CHOOSE VISIBLE HOSTILE", "Select one target for this action."])
            content.extend(choices)
        else:
            content = [row for row in rows if not row.startswith("PgUp/PgDn")]
        if labels[0] in {"Head", "Eye"}:
            content = [rows[0],
                       *(rows[1:2] if rows[0].startswith("DASH + LIGHT") else []),
                       f"Accuracy varies ±{dict(model.combat_rules).get('jitter', 0):g}; amber targets may drift. Final choice commits." if round_mode else "Choose a target to preview.",
                       *(f"{z.zone.value.title()}: Accuracy {z.accuracy:.1f} / {z.difficulty:g} needed. "
                         + dict(model.target_effects).get(z.zone.value, '') for z in model.zones),
                       "Hand needs exposure at the strike. Guard can block head/torso/arm; no outcome is guaranteed."]
    else:
        result = getattr(display, "round_summary", ())
        message = getattr(display, "message", "Choose an action below.")
        if result:
            content = ["LATEST EXCHANGE", *result]
        else:
            content = ["READY", message, *(model.log or ("No strikes exchanged yet.",))]
        if ended and not result:
            content = [model.player_state, model.instructions, *(result or model.log)]
        if getattr(display, 'coach_enabled', False) and coach_hint and not result:
            content = ["STRATEGY (conditional)", coach_hint.headline, *coach_hint.advantages[:1], *coach_hint.cautions[:1]]
    if model.ground_weapons and not display.animating:
        content.append("Ground weapons: w on map; More > Pickup to select/replace.")
    display.max_scroll = 0
    lines((ix+12, iy+8, iw-30, ih-16), content, scroll=True)
    if display.max_scroll:
        text(ix+iw-115, iy+ih-18, "Wheel: more", DIM, small)
    if getattr(display, "round_result", ()) and not display.animating and not reading:
        button = (ix+iw-105, iy+4, 94, 21)
        draw.rounded_rectangle((button[0], button[1], button[0]+button[2], button[1]+button[3]), radius=4,
                               fill=(*PANEL, 255), outline=(*GOLD, 255))
        text(button[0]+6, button[1]+3, "Details [O]", GOLD, small)
        display.buttons.append((button, "Details"))

    cx, cy, cwidth, cheight = layout["controls"]
    panel((cx, cy, cwidth, cheight), GOLD)
    heading = "CHOOSE TARGET" if labels[0] in {"Head", "Eye"} else "CHOOSE DISTANCE / DIRECTION" if labels[0] == "1 tile" else "ACTIONS"
    text(cx+10, cy+7, heading, GOLD, small)
    columns = layout["columns"]
    bw = (cwidth-20-(columns-1)*6)//columns
    options = {a.label: a for a in model.actions}
    keys = {"Light": "Space", "Heavy": "F", "Guard": "G", "Dash": "D", "Move": "M", "Wait": ".", "Tactics": "T", "More": "C", "Help": "H",
            "Rise": "U", "Crawl": "C", "Cover": "G",
            "Head": "H", "Torso": "T", "Arm": "A", "Hand": "X", "Leg": "L", "Back": "Esc"}
    for index, label in enumerate(labels):
        bx, by = cx+10+(index % columns)*(bw+6), cy+30+(index//columns)*48
        option = options.get(label)
        enabled = label == "Skip" if display.animating else option is None or option.enabled
        selected = display.hover_action == label or label == getattr(display, "selected_distance", "")
        target = next((z for z in model.zones if z.zone.value == label.upper()), None)
        aim_risk = target and (target.accuracy-dict(model.combat_rules).get('jitter', 0) < target.difficulty or not target.exposed)
        draw.rounded_rectangle((bx, by, bx+bw-1, by+41), radius=5,
                               fill=(40, 34, 42, 255) if selected else (29, 26, 35, 255),
                               outline=(*GOLD, 255) if selected or aim_risk or coach_hint and coach_hint.action == label else (*LINE, 255))
        key = keys.get(label)
        caption = f"{label} [{key}]" if key else label
        text(bx+6, by+3, caption + (' ?' if aim_risk else ''), GOLD if aim_risk else GREEN if enabled else DIM, small)
        hint = "choose target" if label in {"Light", "Heavy", "Thrust"} else "choose direction" if label in {"Move", "Run", "Dash", "Dash+Light", "Crawl", "Disengage"} else "resolve exchange" if label in {"Guard", "Cover", "Wait", "Rise", "Bandage", "Eat", "Drink", "Pickup", "Feint", "Shove", "Disarm", "Adrenaline"} else "open" if label in {"More", "Tactics", "Sheet", "Help", "Log", "Notebook", "Strategy"} else "select"
        if label in {"NW", "N", "NE", "W", "E", "SW", "S", "SE"}:
            hint = "move here"
        if label == "Skip":
            hint = "Enter / Space"
        elif option is not None and not option.enabled:
            hint = "locked · hover for reason"
        if option:
            cost = re.search(r"Costs ([\d.]+) stamina", option.details)
            if cost:
                hint = f"{cost.group(1)} stamina"
        if display.animating and label != "Skip":
            hint = "locked"
        if label in {'Head', 'Torso', 'Arm', 'Hand', 'Leg'}:
            hint = {'Head': 'hurt accuracy', 'Torso': 'bleed', 'Arm': 'weaken force', 'Hand': 'weaken grip', 'Leg': 'slow movement'}[label]
        text(bx+6, by+22, hint, DIM, small)
        # Keep unavailable controls hoverable so their legality reason can be
        # read. DuelUI rejects clicks on disabled ActionOptions without advancing time.
        if not display.animating or label == "Skip":
            display.buttons.append(((bx, by, bw, 42), label))

    if display.hover_action and display.hover_action in options:
        option = options[display.hover_action]
        # Use the fixed information panel, never cover buttons with a tooltip.
        panel((ix, iy, iw, ih), GOLD)
        lines((ix+12, iy+8, iw-24, ih-16), [display.hover_action.upper(), option.details,
              "Choosing the final target/direction commits this exchange." if round_mode else "Review before confirming."])
    if not reading and not display.animating and (display.hover_action or "").startswith(("Stat:", "Enemy stat:")):
        from pirates.duel_stat_help import stat_rows
        name = display.hover_action.split(":", 1)[1]
        enemy = display.hover_action.startswith("Enemy")
        panel((ix, iy, iw, ih), GOLD)
        lines((ix+12, iy+8, iw-24, ih-16), (*stat_rows(model, name, enemy=enemy)[:2], "Click for current influences."))
    if reading:
        # A read-only overlay covers the anchored scene; it never pushes it.
        rx, ry, rw, rh = display.modal_rect
        panel(display.modal_rect, GOLD)
        text(rx+12, ry+8, rows[0], GOLD)
        offset = 36
        display.buttons = [(rect, label) for rect, label in display.buttons if label == "Back"]
        if rows[0].startswith(("STAT:", "CHARACTER STATS")):
            from pirates.duel_stat_help import STATS
            sw = (rw-24-4*5)//5
            for index, name in enumerate(STATS):
                sx, sy = rx+12+(index % 5)*(sw+5), ry+34+(index//5)*29
                draw.rounded_rectangle((sx, sy, sx+sw-1, sy+24), radius=4, fill=(*BG, 255), outline=(*LINE, 255))
                text(sx+5, sy+4, name, GREEN, small)
                prefix = "Enemy stat:" if getattr(display, "stat_enemy", False) and rows[0].startswith("STAT:") else "Stat:"
                display.buttons.append(((sx, sy, sw, 25), prefix+name))
            offset = 34 + math.ceil(len(STATS)/5)*29 + 8
        lines((rx+12, ry+offset, rw-24, rh-offset-24), [row for row in rows[1:] if row != "[Back]"], scroll=True)
        if display.max_scroll:
            text(rx+rw-170, ry+rh-20, "Wheel / PgUp / PgDn", DIM, small)
    overlay = upload_layer(display, renderer, image, "dashboard")
    sdl.copy(overlay, dest=(0, 0, width, height))
    display.dashboard_key, display.dashboard_texture = cache_key, overlay
    display.dashboard_model = model  # Keep the identity cache key alive.
    display.dashboard_builds = getattr(display, "dashboard_builds", 0)+1
    if not reading:
        draw_feedback(display, renderer, model, layout["arena"], screens)
    if present:
        sdl.present()
