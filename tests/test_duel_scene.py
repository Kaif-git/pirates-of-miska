from dataclasses import dataclass

from pirates.duel_scene import pile_visible_actor_indices


@dataclass
class Actor:
    grounded: bool


def test_grounded_pile_sprites_alternate_without_hiding_other_actor_frames():
    actors = [Actor(False), Actor(True)]
    positions = [(4, 3), (4, 3)]

    assert pile_visible_actor_indices(actors, positions, 0.1) == frozenset({0})
    assert pile_visible_actor_indices(actors, positions, 0.4) == frozenset({1})
    assert pile_visible_actor_indices(actors, positions, 0.8) == frozenset({0})


def test_non_pile_coincident_positions_are_not_silently_blink_filtered():
    actors = [Actor(False), Actor(False)]
    assert pile_visible_actor_indices(actors, [(1, 1), (1, 1)], 0.4) == frozenset({0, 1})
