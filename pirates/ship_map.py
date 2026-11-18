"""Starter-vessel migration and deck-zero projection onto the inherited map.

Fixtures remain authored for now. Their floors belong to the structural model;
their glyphs are a separate overlay, never a substitute for structural data.
"""
from omnisim.world import tile as T
from pirates.ship_model import ShipModel

ORIGIN = (69, 12)
WIDTH, HEIGHT = 23, 32


def capture_starter(tiles):
    model = ShipModel()
    fixtures = {}
    for y in range(HEIGHT):
        for x in range(WIDTH):
            tile = int(tiles[y + ORIGIN[1], x + ORIGIN[0]])
            if tile == T.WATER:
                continue
            model.place(x, y, 0, "floor")
            if tile in (T.WALL_WOOD, T.DOOR):
                model.place(x, y, 0, "door" if tile == T.DOOR else "wall")
            elif tile != T.FLOOR:
                fixtures[x, y] = tile
    return model, fixtures


def project(model, fixtures, game_map, z=0):
    """Project one structural deck into the inherited two-dimensional map."""
    if type(z) is not int or z < 0:
        raise ValueError("Invalid active deck elevation")
    cells = {}
    for component in model.components:
        if not (0 <= component.x < WIDTH and 0 <= component.y < HEIGHT
                and 0 <= component.z <= 8):
            raise ValueError("Ship component outside supported deck bounds")
        if component.z != z:
            continue
        cells.setdefault((component.x, component.y), {})[component.kind] = component
    for at, parts in cells.items():
        if "floor" not in parts:
            if set(parts) == {"beam"}:
                # Beams may bridge empty cells above a lower-deck opening.
                continue
            raise ValueError("Live components require a deck floor")
    for at in fixtures:
        if at not in cells or "wall" in cells[at] or "door" in cells[at]:
            raise ValueError("Construction would remove or obstruct an installed fixture")
    ox, oy = ORIGIN
    old = game_map.tiles[oy:oy + HEIGHT, ox:ox + WIDTH].copy()
    deck = old.copy()
    deck[:] = T.WATER
    for (x, y), parts in cells.items():
        deck[y, x] = (T.WALL_WOOD if "wall" in parts else
                      T.DOOR if "door" in parts else
                      fixtures.get((x, y), T.FLOOR))
    game_map.tiles[oy:oy + HEIGHT, ox:ox + WIDTH] = deck
    doors = game_map.doors[oy:oy + HEIGHT, ox:ox + WIDTH]
    # New doors start closed; surviving doors preserve their state.
    doors[(old != T.DOOR) | (deck != T.DOOR)] = False
