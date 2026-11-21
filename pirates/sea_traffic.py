"""Bounded chart-space observations for sea traffic.

These helpers describe map-scale movement only. They are not tactical ship
geometry, collision rules, or hidden NPC intent.
"""
from __future__ import annotations

import math

from tales_sim.clock import TICKS_PER_SECOND


SEA_HOUR_TICKS = 360_000
NPC_LANE_HOP_TICKS = SEA_HOUR_TICKS
LOOKOUT_RADIUS = 1.25
CONTACT_LOSS_RADIUS = LOOKOUT_RADIUS + 0.25
CONTACT_LOSS_DELAY_TICKS = 60 * TICKS_PER_SECOND


def interpolate_chart_edge(start, end, fraction):
    """Return the bounded linear position along one chart lane edge."""
    if (not isinstance(start, (tuple, list)) or len(start) != 2
            or not isinstance(end, (tuple, list)) or len(end) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in (*start, *end))
            or type(fraction) not in (int, float) or not math.isfinite(fraction)):
        raise ValueError("Invalid chart edge interpolation input")
    amount = max(0.0, min(1.0, float(fraction)))
    return (float(start[0]) + (float(end[0]) - float(start[0])) * amount,
            float(start[1]) + (float(end[1]) - float(start[1])) * amount)


def relative_observation(player_now, vessel_now, *, player_before=None,
                         vessel_before=None, elapsed_seconds=0):
    """Return a lookout-safe range/bearing and sampled relative-motion label.

    Motion labels require two chart samples. They describe observed relative
    displacement only; the first sample is explicitly unknown.
    """
    for point in (player_now, vessel_now):
        if (not isinstance(point, (tuple, list)) or len(point) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in point)):
            raise ValueError("Invalid lookout chart position")
    dx = float(vessel_now[0]) - float(player_now[0])
    dy = float(vessel_now[1]) - float(player_now[1])
    distance = math.hypot(dx, dy)
    bearing = None if distance <= 1e-9 else math.degrees(math.atan2(dy, dx)) % 360.0
    label = "unknown"
    if (player_before is not None and vessel_before is not None
            and type(elapsed_seconds) in (int, float) and elapsed_seconds > 0):
        for point in (player_before, vessel_before):
            if (not isinstance(point, (tuple, list)) or len(point) != 2
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           for value in point)):
                raise ValueError("Invalid prior lookout chart position")
        old_dx = float(vessel_before[0]) - float(player_before[0])
        old_dy = float(vessel_before[1]) - float(player_before[1])
        old_distance = math.hypot(old_dx, old_dy)
        radial_rate = (distance - old_distance) / float(elapsed_seconds)
        player_dx = float(player_now[0]) - float(player_before[0])
        player_dy = float(player_now[1]) - float(player_before[1])
        vessel_dx = float(vessel_now[0]) - float(vessel_before[0])
        vessel_dy = float(vessel_now[1]) - float(vessel_before[1])
        player_speed = math.hypot(player_dx, player_dy)
        vessel_speed = math.hypot(vessel_dx, vessel_dy)
        course_dot = None
        if player_speed > 1e-9 and vessel_speed > 1e-9:
            course_dot = ((player_dx * vessel_dx + player_dy * vessel_dy)
                          / (player_speed * vessel_speed))
        angle_delta = 0.0
        if distance > 1e-9 and old_distance > 1e-9:
            old_bearing = math.degrees(math.atan2(old_dy, old_dx))
            new_bearing = math.degrees(math.atan2(dy, dx))
            angle_delta = (new_bearing - old_bearing + 180.0) % 360.0 - 180.0
        if radial_rate < -0.00002:
            label = ("overtaking" if course_dot is not None and course_dot >= 0.9
                     else "closing")
        elif radial_rate > 0.00002:
            label = "opening"
        elif abs(angle_delta) >= 0.05:
            label = "crossing"
        else:
            if course_dot is not None:
                label = "following" if course_dot >= 0.9 else "steady"
            else:
                label = "steady"
    return {
        "range_units": round(distance, 4),
        "bearing_deg": None if bearing is None else round(bearing, 1),
        "relative_motion": label,
    }
