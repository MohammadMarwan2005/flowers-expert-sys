import collections
import collections.abc

collections.Mapping = collections.abc.Mapping  # experta needs this on py3.10+

import contextlib
import heapq
import io
import math
import time
from collections import namedtuple
from itertools import combinations

from experta import AS, MATCH, NOT, TEST, DefFacts, Fact, Field, KnowledgeEngine, Rule

from flower_instances import ASSIGNMENT_EXAMPLE, EXAMPLE_A, EXAMPLE_B, NO_SHARED_COLOR

PRINT_PATH = True
PRINT_GRID = True


# =============================================================================
# model.py -- Core data structures: Bag, CATALOG, facts, heuristic
# =============================================================================

CATALOG = {
    "rose": {"red", "pink", "white", "yellow", "maroon"},
    "tulip": {"red", "yellow", "violet", "orange", "green", "mauve", "purple"},
    "orchid": {"purple", "white", "pink", "rosy"},
    "goliat": {"gold", "light_pink", "yellow"},
}


class Bag:
    """Frozen, hashable multiset (key -> positive quantity)."""

    __slots__ = ("_map", "_items")

    def __init__(self, mapping=None):
        clean = {k: v for k, v in (mapping or {}).items() if v}
        object.__setattr__(self, "_map", clean)
        object.__setattr__(self, "_items", tuple(sorted(clean.items())))

    def get(self, key, default=0):
        return self._map.get(key, default)

    def items(self):
        return self._items

    def total(self):
        return sum(self._map.values())

    def __eq__(self, other):
        return isinstance(other, Bag) and self._items == other._items

    def __hash__(self):
        return hash(self._items)

    def __bool__(self):
        return bool(self._items)

    def __repr__(self):
        if not self._items:
            return "Bag()"
        return "Bag(" + ", ".join(f"{k}:{v}" for k, v in self._items) + ")"


Pavilion = namedtuple("Pavilion", "id type pos")


class World(Fact):
    """Static instance data: grid, warehouse, max load, pavilion table."""

    grid = Field(tuple, mandatory=True)
    warehouse = Field(tuple, mandatory=True)
    robot_start = Field(tuple, mandatory=True)
    max_load = Field(int, mandatory=True)
    pavilions = Field(tuple, mandatory=True)


class State(Fact):
    """One search-tree node: semantic state + search bookkeeping."""

    pos = Field(tuple, mandatory=True)
    load = Field(Bag, mandatory=True)
    needs = Field(tuple, mandatory=True)
    g = Field(int, mandatory=True)
    h = Field(int, mandatory=True)
    f = Field(int, mandatory=True)
    op = Field(object, mandatory=True)
    parent = Field(object, mandatory=True)
    nid = Field(int, mandatory=True)
    status = Field(str, mandatory=True)


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def heuristic(world, pos, load, needs):
    """h = LB_unload + LB_load + LB_move (sum of admissible lower bounds)."""
    lb_unload = sum(1 for need in needs if need)

    total_needed = 0
    total_useful = 0
    for pavilion, need in zip(world["pavilions"], needs):
        for color, qty in need.items():
            total_needed += qty
            total_useful += min(qty, load.get((pavilion.type, color)))
    remaining = total_needed - total_useful
    lb_load = math.ceil(remaining / world["max_load"]) if remaining > 0 else 0

    mandatory = [p.pos for p, need in zip(world["pavilions"], needs) if need]
    if remaining > 0:
        mandatory.append(world["warehouse"])
    lb_move = max((manhattan(pos, point) for point in mandatory), default=0)

    return lb_unload + lb_load + lb_move


def build_world(instance):
    pavilions = tuple(
        Pavilion(id=pid, type=p["type"], pos=tuple(p["pos"]))
        for pid, p in instance["pavilions"].items()
    )
    max_load = max(sum(p["needs"].values()) for p in instance["pavilions"].values())
    return World(
        grid=(instance["grid"]["w"], instance["grid"]["h"]),
        warehouse=tuple(instance["warehouse"]),
        robot_start=tuple(instance["robot_start"]),
        max_load=max_load,
        pavilions=pavilions,
    )


def build_root_state(instance, world):
    needs = tuple(Bag(p["needs"]) for p in instance["pavilions"].values())
    load = Bag()
    pos = tuple(instance["robot_start"])
    h = heuristic(world, pos, load, needs)
    return State(
        pos=pos,
        load=load,
        needs=needs,
        g=0,
        h=h,
        f=h,
        op="start",
        parent=None,
        nid=0,
        status="open",
    )


# =============================================================================
# operators.py -- Mechanical helpers (domain math; loops allowed here)
# =============================================================================


def candidate_baskets(pavilions, needs, max_load):
    """All valid Option-A/Option-B loads (Bags), restricted to still-needed (type,color)."""
    demand = {}
    for pavilion, need in zip(pavilions, needs):
        for color, qty in need.items():
            key = (pavilion.type, color)
            demand[key] = demand.get(key, 0) + qty

    baskets = set()

    by_type = {}
    for (ptype, color), qty in demand.items():
        by_type.setdefault(ptype, []).append((color, qty))
    for ptype, colors in by_type.items():
        for r in range(1, len(colors) + 1):
            for combo in combinations(colors, r):
                if sum(q for _, q in combo) <= max_load:
                    baskets.add(Bag({(ptype, c): q for c, q in combo}))

    by_color = {}
    for (ptype, color), qty in demand.items():
        by_color.setdefault(color, []).append((ptype, qty))
    for color, types in by_color.items():
        if len(types) < 2:
            continue
        for r in range(2, len(types) + 1):
            for combo in combinations(types, r):
                if sum(q for _, q in combo) <= max_load:
                    baskets.add(Bag({(t, color): q for t, q in combo}))

    return baskets


def deliverable_colors(pavilion_type, load, need):
    return [
        color for color, qty in need.items() if load.get((pavilion_type, color)) >= qty
    ]


def apply_unload(pavilion_type, load, need):
    colors = deliverable_colors(pavilion_type, load, need)
    if not colors:
        return None
    new_load = dict(load.items())
    new_need = dict(need.items())
    for color in colors:
        qty = new_need.pop(color)
        key = (pavilion_type, color)
        remaining = new_load[key] - qty
        if remaining:
            new_load[key] = remaining
        else:
            del new_load[key]
    return Bag(new_load), Bag(new_need)


# =============================================================================
# engine.py -- SearchEngine: shared setup, spawning (heap + closed dict)
# =============================================================================


class SearchEngine(KnowledgeEngine):
    """Shared bookkeeping: instance setup, child-spawning, path reconstruction.

    The closed set (`self.seen`, best g per (pos, load, needs)) and the open
    list (`self.open_heap`, a heapq of (f, nid)) are plain Python -- O(1)
    dict lookups and O(log n) heap ops, instead of the O(n) Visited-fact and
    MinF-fact rescans in smart_flower_slow_by_rules.py.
    """

    def __init__(self, instance):
        super().__init__()
        self.instance = instance
        self.goal = None

    @DefFacts()
    def _initial_facts(self):
        self.world = build_world(self.instance)
        root = build_root_state(self.instance, self.world)
        self.seen = {(root["pos"], root["load"], root["needs"]): root["g"]}
        self.nodes = {root["nid"]: root}
        self._next_nid = 1
        self.open_heap = [(root["f"], root["nid"])]
        yield self.world
        yield root

    def next_nid(self):
        nid = self._next_nid
        self._next_nid += 1
        return nid

    def spawn(self, parent, op, pos=None, load=None, needs=None):
        """Build a child state, bounds- and closed-set-checking it, and
        declaring it as 'open' if it's new (or strictly cheaper)."""
        pos = parent["pos"] if pos is None else pos
        load = parent["load"] if load is None else load
        needs = parent["needs"] if needs is None else needs
        g = parent["g"] + 1

        gw, gh = self.world["grid"]
        if not (1 <= pos[0] <= gw and 1 <= pos[1] <= gh):
            return None

        key = (pos, load, needs)
        if self.seen.get(key, math.inf) <= g:
            return None  # dominated -- never declared, no Rete cost at all
        self.seen[key] = g

        h = heuristic(self.world, pos, load, needs)
        child = State(
            pos=pos,
            load=load,
            needs=needs,
            g=g,
            h=h,
            f=g + h,
            op=op,
            parent=parent["nid"],
            nid=self.next_nid(),
            status="open",
        )
        self.nodes[child["nid"]] = child
        self.declare(child)
        heapq.heappush(self.open_heap, (child["f"], child["nid"]))
        print("  " * child["g"] + repr(child))
        return child

    # -- goal + path -----------------------------------------------------------

    def reconstruct_path(self, goal):
        path = []
        node = goal
        while node is not None:
            path.append(node)
            node = self.nodes.get(node["parent"])
        path.reverse()
        return path

    def print_solution(self, goal):
        self.goal = goal
        path = self.reconstruct_path(goal)
        ops = [node["op"] for node in path[1:]]
        print("Solution:", " -> ".join(ops))
        print("Cost:", goal["g"])

    def print_solution_path(self):
        if self.goal is None:
            return
        path = self.reconstruct_path(self.goal)
        pavilions = self.world["pavilions"]
        print(f"=== Path ({len(path) - 1} steps, cost {self.goal['g']}) ===")
        if PRINT_GRID:
            print(
                "Grid legend: R=robot  W=warehouse  Pn=pavilion  "
                "(joined with '+' when sharing a cell, e.g. R+W)"
            )
        for step, node in enumerate(path):
            print(
                f"Step {step:>2} [{str(node['op']):<11}] "
                f"pos={node['pos']}  load={node['load']}  "
                f"g={node['g']} h={node['h']} f={node['f']}"
            )
            needs_str = "  ".join(
                f"{p.id}={n}" for p, n in zip(pavilions, node["needs"])
            )
            print(f"             needs: {needs_str}")
            if PRINT_GRID:
                for line in self.render_grid(node):
                    print(line)
                print()
        print()

    def render_grid(self, node):
        gw, gh = self.world["grid"]
        robot = node["pos"]
        pavilions = self.world["pavilions"]
        warehouse = self.world["warehouse"]

        def label(x, y):
            parts = []
            if (x, y) == robot:
                parts.append("R")
            for p in pavilions:
                if p.pos == (x, y):
                    parts.append(p.id)
            if (x, y) == warehouse:
                parts.append("W")
            return "+".join(parts) if parts else "."

        cell_w = 5
        border = "    +" + ("-" * cell_w + "+") * gw
        lines = [border]
        for y in range(gh, 0, -1):
            row = (
                f"y={y:>2}|"
                + "|".join(label(x, y).center(cell_w) for x in range(1, gw + 1))
                + "|"
            )
            lines.append(row)
        lines.append(border)
        x_axis = "     " + "".join(
            f"x={x}".center(cell_w) + " " for x in range(1, gw + 1)
        )
        lines.append(x_axis)
        return lines


# =============================================================================
# engine.py -- FlowerEngine: depth-first search (default recency strategy)
# =============================================================================


class FlowerEngine(SearchEngine):
    @Rule(
        AS.goal << State(needs=MATCH.needs, load=MATCH.load, status="open"),
        TEST(lambda needs, load: not load and all(not n for n in needs)),
        salience=30,
    )
    def goal_check(self, goal, needs, load):
        self.print_solution(goal)
        self.halt()

    @Rule(
        World(grid=MATCH.grid),
        AS.parent << State(pos=MATCH.pos, status="open"),
        TEST(lambda pos, grid: pos[0] < grid[0]),
    )
    def move_right(self, parent, pos, grid):
        x, y = pos
        self.spawn(parent, "move_right", pos=(x + 1, y))

    @Rule(
        AS.parent << State(pos=MATCH.pos, status="open"),
        TEST(lambda pos: pos[0] > 1),
    )
    def move_left(self, parent, pos):
        x, y = pos
        self.spawn(parent, "move_left", pos=(x - 1, y))

    @Rule(
        World(grid=MATCH.grid),
        AS.parent << State(pos=MATCH.pos, status="open"),
        TEST(lambda pos, grid: pos[1] < grid[1]),
    )
    def move_up(self, parent, pos, grid):
        x, y = pos
        self.spawn(parent, "move_up", pos=(x, y + 1))

    @Rule(
        AS.parent << State(pos=MATCH.pos, status="open"),
        TEST(lambda pos: pos[1] > 1),
    )
    def move_down(self, parent, pos):
        x, y = pos
        self.spawn(parent, "move_down", pos=(x, y - 1))

    @Rule(
        World(
            warehouse=MATCH.warehouse,
            max_load=MATCH.max_load,
            pavilions=MATCH.pavilions,
        ),
        AS.parent
        << State(pos=MATCH.pos, load=MATCH.load, needs=MATCH.needs, status="open"),
        TEST(lambda pos, warehouse: pos == warehouse),
        TEST(lambda load: not load),
    )
    def load(self, parent, pos, load, needs, warehouse, max_load, pavilions):
        for basket in candidate_baskets(pavilions, needs, max_load):
            self.spawn(parent, "load", load=basket)

    @Rule(
        World(pavilions=MATCH.pavilions),
        AS.parent
        << State(pos=MATCH.pos, load=MATCH.load, needs=MATCH.needs, status="open"),
    )
    def unload(self, parent, pos, load, needs, pavilions):
        for idx, pavilion in enumerate(pavilions):
            if pavilion.pos != pos or not needs[idx]:
                continue
            result = apply_unload(pavilion.type, load, needs[idx])
            if result is None:
                continue
            new_load, new_need = result
            new_needs = needs[:idx] + (new_need,) + needs[idx + 1 :]
            self.spawn(parent, "unload", load=new_load, needs=new_needs)


# =============================================================================
# engine.py -- AStarEngine: best-first search via a real heap + closed dict
# =============================================================================


class AStarEngine(SearchEngine):
    """A*: a real open-list with best-first selection.

    Lifecycle (by salience): bootstrap (100) > goal_check (30) >
    generators (20) > close_current (10) > select_best (0). Only one
    'current' state exists at a time; generators expand it, close_current
    retires it, and select_best -- gated by NOT(State(status='current')), so
    it only fires once nothing is being expanded -- pops the min-f node from
    `self.open_heap` and promotes it to 'current'.
    """

    @Rule(
        AS.f << State(nid=0, status="open"),
        salience=100,
    )
    def bootstrap(self, f):
        """DefFacts copies yielded facts before declaring them, so the root
        State object built in _initial_facts isn't the one Experta actually
        holds. Re-point self.nodes[0] at the real (declared) fact so later
        self.modify(self.nodes[0], ...) calls retract a fact that exists."""
        self.nodes[0] = f

    @Rule(
        AS.goal << State(needs=MATCH.needs, load=MATCH.load, status="current"),
        TEST(lambda needs, load: not load and all(not n for n in needs)),
        salience=30,
    )
    def goal_check(self, goal, needs, load):
        self.print_solution(goal)
        self.halt()

    # -- successor generators (expand the 'current' state) -------------------

    @Rule(
        World(grid=MATCH.grid),
        AS.parent << State(pos=MATCH.pos, status="current"),
        TEST(lambda pos, grid: pos[0] < grid[0]),
        salience=20,
    )
    def move_right(self, parent, pos, grid):
        x, y = pos
        self.spawn(parent, "move_right", pos=(x + 1, y))

    @Rule(
        AS.parent << State(pos=MATCH.pos, status="current"),
        TEST(lambda pos: pos[0] > 1),
        salience=20,
    )
    def move_left(self, parent, pos):
        x, y = pos
        self.spawn(parent, "move_left", pos=(x - 1, y))

    @Rule(
        World(grid=MATCH.grid),
        AS.parent << State(pos=MATCH.pos, status="current"),
        TEST(lambda pos, grid: pos[1] < grid[1]),
        salience=20,
    )
    def move_up(self, parent, pos, grid):
        x, y = pos
        self.spawn(parent, "move_up", pos=(x, y + 1))

    @Rule(
        AS.parent << State(pos=MATCH.pos, status="current"),
        TEST(lambda pos: pos[1] > 1),
        salience=20,
    )
    def move_down(self, parent, pos):
        x, y = pos
        self.spawn(parent, "move_down", pos=(x, y - 1))

    @Rule(
        World(
            warehouse=MATCH.warehouse,
            max_load=MATCH.max_load,
            pavilions=MATCH.pavilions,
        ),
        AS.parent
        << State(pos=MATCH.pos, load=MATCH.load, needs=MATCH.needs, status="current"),
        TEST(lambda pos, warehouse: pos == warehouse),
        TEST(lambda load: not load),
        salience=20,
    )
    def load(self, parent, pos, load, needs, warehouse, max_load, pavilions):
        for basket in candidate_baskets(pavilions, needs, max_load):
            self.spawn(parent, "load", load=basket)

    @Rule(
        World(pavilions=MATCH.pavilions),
        AS.parent
        << State(pos=MATCH.pos, load=MATCH.load, needs=MATCH.needs, status="current"),
        salience=20,
    )
    def unload(self, parent, pos, load, needs, pavilions):
        for idx, pavilion in enumerate(pavilions):
            if pavilion.pos != pos or not needs[idx]:
                continue
            result = apply_unload(pavilion.type, load, needs[idx])
            if result is None:
                continue
            new_load, new_need = result
            new_needs = needs[:idx] + (new_need,) + needs[idx + 1 :]
            self.spawn(parent, "unload", load=new_load, needs=new_needs)

    # -- A* control: retire 'current', then re-select the min-f 'open' state -

    @Rule(
        AS.cur << State(status="current"),
        salience=10,
    )
    def close_current(self, cur):
        self.modify(cur, status="closed")

    @Rule(
        NOT(State(status="current")),
        salience=0,
    )
    def select_best(self):
        if not self.open_heap:
            return
        _, nid = heapq.heappop(self.open_heap)
        self.modify(self.nodes[nid], status="current")


# =============================================================================
# main.py -- run an engine on an instance, capturing/printing its output
# =============================================================================


def run(engine_cls, instance, label):
    engine = engine_cls(instance)
    engine.reset()
    buf = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(buf):
        engine.run()
    elapsed = time.time() - t0
    lines = buf.getvalue().splitlines()
    cost_lines = [l for l in lines if l.startswith("Cost")]
    cost = int(cost_lines[0].split(":")[1]) if cost_lines else None
    print(f"--- {label} ---")
    print(f"Cost: {cost}  ({elapsed:.2f}s)")
    if PRINT_PATH:
        engine.print_solution_path()
    return cost


def main():
    run(AStarEngine, ASSIGNMENT_EXAMPLE, "A* / 4-pavilion assignment (expect 27)")
    run(AStarEngine, EXAMPLE_A, "A* / Example A (expect 6)")
    run(AStarEngine, EXAMPLE_B, "A* / Example B (expect 9)")
    run(AStarEngine, NO_SHARED_COLOR, "A* / no-shared-color (expect 7)")

    # DFS (uninformed, default recency): valid but non-optimal, also fast here.
    run(FlowerEngine, EXAMPLE_A, "DFS / Example A")
    run(FlowerEngine, NO_SHARED_COLOR, "DFS / no-shared-color")


if __name__ == "__main__":
    main()
