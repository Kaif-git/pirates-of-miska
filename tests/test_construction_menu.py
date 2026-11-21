from pirates.game import World
from pirates.management import construction_menu


class MenuRenderer:
    def __init__(self, choices):
        self.choices = iter(choices)
        self.rendered = []

    def render_list(self, *args, **kwargs):
        self.rendered.append((args, kwargs))

    def choose(self, count):
        return next(self.choices)

    def wait_any(self):
        pass


def test_adjacent_menu_queues_without_ticking():
    world = World()
    world.player.x, world.player.y = 78, 28
    before = world.clock.total_ticks
    renderer = MenuRenderer([0, 1])
    construction_menu(renderer, world)
    job = next(j for j in world.ship.jobs if j.construction)
    assert job.target == (78, 27)
    assert job.construction["kind"] == "door"
    assert world.clock.total_ticks == before
    rendered = [args for args, _ in renderer.rendered]
    assert any("Patch hull labor" in row for row in rendered[1][2])
    assert "Patch hull (repair) labor" in rendered[1][3]
    assert "carpentry" not in rendered[1][3].lower()


def test_menu_escape_does_not_queue():
    world = World()
    construction_menu(MenuRenderer([None]), world)
    assert not any(j.construction for j in world.ship.jobs)
