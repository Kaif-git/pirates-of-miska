from pirates.game import World
from pirates.management import construction_menu


class MenuRenderer:
    def __init__(self, choices):
        self.choices = iter(choices)

    def render_list(self, *args, **kwargs):
        pass

    def choose(self, count):
        return next(self.choices)

    def wait_any(self):
        pass


def test_adjacent_menu_queues_without_ticking():
    world = World()
    world.player.x, world.player.y = 78, 28
    before = world.clock.total_ticks
    construction_menu(MenuRenderer([0, 1]), world)
    job = next(j for j in world.ship.jobs if j.construction)
    assert job.target == (78, 27)
    assert job.construction["kind"] == "door"
    assert world.clock.total_ticks == before


def test_menu_escape_does_not_queue():
    world = World()
    construction_menu(MenuRenderer([None]), world)
    assert not any(j.construction for j in world.ship.jobs)
