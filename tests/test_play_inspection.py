from types import SimpleNamespace

import play


def test_mouse_inspection_uses_coordinates_and_ignores_combat_modifier(monkeypatch):
    calls = []
    monkeypatch.setattr(play, "inspect", lambda *args: calls.append(args))
    renderer = object()
    world = SimpleNamespace(player=SimpleNamespace(x=2, y=3))

    play._inspect_action(renderer, world, ("inspect_at", 8, 6, True))

    assert calls == [(renderer, world, 8, 6)]


def test_keyboard_inspection_targets_the_player_tile(monkeypatch):
    calls = []
    monkeypatch.setattr(play, "inspect", lambda *args: calls.append(args))
    renderer = object()
    world = SimpleNamespace(player=SimpleNamespace(x=2, y=3))

    play._inspect_action(renderer, world, ("inspect",))

    assert calls == [(renderer, world, 2, 3)]
