import collections
import collections.abc

collections.Mapping = collections.abc.Mapping  # experta needs this on py3.10+

import contextlib
import io
import math
import time
from collections import namedtuple
from itertools import combinations

from experta import (
    AS,
    EXISTS,
    MATCH,
    NOT,
    TEST,
    DefFacts,
    Fact,
    Field,
    KnowledgeEngine,
    Rule,
)

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


class Visited(Fact):
    """The closed set, as facts -- best node seen per state."""

    pos = Field(tuple, mandatory=True)
    load = Field(Bag, mandatory=True)
    needs = Field(tuple, mandatory=True)
    g = Field(int, mandatory=True)
    nid = Field(int, mandatory=True)


class MinF(Fact):
    """The open list's current minimum f, tracked as a fact."""

    value = Field(int, mandatory=True)


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
# engine.py -- SearchEngine: shared setup, spawning, path printing
# =============================================================================


class SearchEngine(KnowledgeEngine):
    def __init__(self, instance):
        super().__init__()
        self.instance = instance
        self.goal = None

    @DefFacts()
    def _initial_facts(self):
        self.world = build_world(self.instance)
        root = build_root_state(self.instance, self.world)
        self.nodes = {root["nid"]: root}
        self._next_nid = 1
        yield self.world
        yield root

    def next_nid(self):
        nid = self._next_nid
        self._next_nid += 1
        return nid

    def spawn(self, parent, op, pos=None, load=None, needs=None):
        """Build + declare a child as 'open'. De-duplication is done by the
        Visited rules, not a Python set."""
        pos = parent["pos"] if pos is None else pos
        load = parent["load"] if load is None else load
        needs = parent["needs"] if needs is None else needs
        g = parent["g"] + 1

        gw, gh = self.world["grid"]
        if not (1 <= pos[0] <= gw and 1 <= pos[1] <= gh):
            return None

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
    """Depth-first search, with the closed set kept as Visited facts."""

    @Rule(
        State(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.g, nid=MATCH.nid),
        NOT(Visited(pos=MATCH.p, load=MATCH.l, needs=MATCH.n)),
        salience=100,
    )
    def record_visited(self, p, l, n, g, nid):
        self.declare(Visited(pos=p, load=l, needs=n, g=g, nid=nid))

    @Rule(
        State(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.g, nid=MATCH.nid),
        AS.v << Visited(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.bg),
        TEST(lambda g, bg: g < bg),
        salience=100,
    )
    def update_visited(self, v, g, nid):
        self.modify(v, g=g, nid=nid)

    @Rule(
        AS.s
        << State(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.g, nid=MATCH.nid),
        Visited(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.bg, nid=MATCH.bnid),
        TEST(lambda nid, bnid, g, bg: nid != bnid and g >= bg),
        salience=100,
    )
    def prune_duplicate(self, s):
        self.retract(s)

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
# engine.py -- AStarEngine: best-first search via the MinF fact (no heap)
# =============================================================================


class AStarEngine(SearchEngine):
    """A* with a rule-native open list and closed set.

    Salience: closed-set (100) > MinF maintenance (60/55) > goal_check (30) >
    generators (20) > close_current (15) > select_best (10). Only one 'current'
    state is expanded at a time; select_best promotes the open state whose f
    equals MinF, which is kept synced to the true frontier minimum by
    lower_minf / raise_minf.

    NOTE: picking the open state with f == MinF, and keeping MinF itself in
    sync, both require rescanning every open State fact -- O(n) per pick
    instead of a heap's O(log n). Likewise, record_visited/update_visited/
    prune_duplicate rescan Visited facts -- O(n) per dedup instead of a
    dict's O(1). For the ~17,500-node ASSIGNMENT_EXAMPLE this is ~O(n^2) Rete
    operations, i.e. minutes. See smart_flower_optimized.py for the
    heapq/dict-based fix.
    """

    @DefFacts()
    def _initial_facts(self):
        self.world = build_world(self.instance)
        root = build_root_state(self.instance, self.world)
        self.nodes = {root["nid"]: root}
        self._next_nid = 1
        yield self.world
        yield root
        yield MinF(value=root["f"])

    # -- closed set as facts ---------------------------------------------------

    @Rule(
        State(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.g, nid=MATCH.nid),
        NOT(Visited(pos=MATCH.p, load=MATCH.l, needs=MATCH.n)),
        salience=100,
    )
    def record_visited(self, p, l, n, g, nid):
        self.declare(Visited(pos=p, load=l, needs=n, g=g, nid=nid))

    @Rule(
        State(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.g, nid=MATCH.nid),
        AS.v << Visited(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.bg),
        TEST(lambda g, bg: g < bg),
        salience=100,
    )
    def update_visited(self, v, g, nid):
        self.modify(v, g=g, nid=nid)

    @Rule(
        AS.s
        << State(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.g, nid=MATCH.nid),
        Visited(pos=MATCH.p, load=MATCH.l, needs=MATCH.n, g=MATCH.bg, nid=MATCH.bnid),
        TEST(lambda nid, bnid, g, bg: nid != bnid and g >= bg),
        salience=100,
    )
    def prune_duplicate(self, s):
        self.retract(s)

    # -- successor generators (expand the 'current' state) -------------------

    @Rule(
        AS.goal << State(needs=MATCH.needs, load=MATCH.load, status="current"),
        TEST(lambda needs, load: not load and all(not n for n in needs)),
        salience=30,
    )
    def goal_check(self, goal, needs, load):
        self.print_solution(goal)
        self.halt()

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

    # -- the open list as facts -------------------------------------

    @Rule(
        AS.m << MinF(value=MATCH.cur),
        State(status="open", f=MATCH.nf),
        TEST(lambda nf, cur: nf < cur),
        salience=60,
    )
    def lower_minf(self, m, nf):
        self.modify(m, value=nf)

    @Rule(
        AS.m << MinF(value=MATCH.cur),
        EXISTS(State(status="open")),
        NOT(State(status="open", f=MATCH.cur)),
        salience=55,
    )
    def raise_minf(self, m, cur):
        self.modify(m, value=cur + 1)

    @Rule(
        AS.cur << State(status="current"),
        salience=15,
    )
    def close_current(self, cur):
        self.modify(cur, status="closed")

    @Rule(
        NOT(State(status="current")),
        MinF(value=MATCH.cur),
        AS.best << State(status="open", f=MATCH.cur),
        salience=10,
    )
    def select_best(self, best):
        self.modify(best, status="current")


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
    # Small instances: the rule-native A* finds the optimal cost instantly.
    # run(AStarEngine, ASSIGNMENT_EXAMPLE, "A* / 4-pavilion assignment (expect 27)")
    run(AStarEngine, EXAMPLE_A, "A* / Example A (expect 6)")
    run(AStarEngine, EXAMPLE_B, "A* / Example B (expect 9)")
    run(AStarEngine, NO_SHARED_COLOR, "A* / no-shared-color (expect 7)")

    # DFS (uninformed, default recency): valid but non-optimal, also fast here.
    run(FlowerEngine, EXAMPLE_A, "DFS / Example A")
    run(FlowerEngine, NO_SHARED_COLOR, "DFS / no-shared-color")

    # The 4-pavilion ASSIGNMENT_EXAMPLE needs ~17,500 nodes (optimal cost 27).
    # The rule-native open list (MinF) and closed set (Visited) cost O(n) per
    # pick/dedup instead of O(log n)/O(1), so this one instance takes minutes
    # here. Uncomment to run:
    # run(AStarEngine, ASSIGNMENT_EXAMPLE, "A* / 4-pavilion assignment (SLOW, expect 27)")
    # run(FlowerEngine, ASSIGNMENT_EXAMPLE, "DFS / 4-pavilion assignment (SLOW)")


if __name__ == "__main__":
    main()
