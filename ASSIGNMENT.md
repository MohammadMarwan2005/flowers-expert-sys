# Smart Flower Exhibition — Assignment Spec & Instance Data

Problem description and instance data for the Experta project. **Build it per `PLAN.md`** (the design/build guide) — this file is the *what*, `PLAN.md` is the *how*. Deliverables: a rule-based expert system that solves the robot delivery problem with **DFS** and **A\*** (both should hit the expected costs).

---

## 1. Problem

A robot delivers bouquets from a central warehouse to pavilions on a grid.

- **Grid:** size `w × h`. The robot moves one cell at a time: right / left / up / down. Each move costs 1.
- **Warehouse:** one cell; holds an unlimited supply of every bouquet.
- **Robot:** starts at a cell, carrying nothing.
- **Pavilions:** each has a location, a single flower **type**, and color-based **needs** (color → quantity).
- **Bouquet:** one unit = (flower type + color).

**Goal:** every pavilion's needs are fully met **and** the robot is carrying nothing.
**Objective:** minimum total cost.

---

## 2. Flower catalog (static — fixed domain)

```python
CATALOG = {
    "rose":   {"red", "pink", "white", "yellow", "maroon"},
    "tulip":  {"red", "yellow", "violet", "orange", "green", "mauve", "purple"},
    "orchid": {"purple", "white", "pink", "rosy"},
    "goliat": {"gold", "light_pink", "yellow"},   # spec lists "Gold, Light Pink, Yellow…" (partial)
}
```

> Note: the spec gives Goliat Rose's colors as "Gold, Light Pink, Yellow…" with a trailing ellipsis. Treat the set above as complete unless told otherwise.

---

## 3. Rules

**Movement** — one step R/L/U/D, cost 1 each. Cannot leave the grid.

**Max load** = the largest total number of bouquets needed by any *single* pavilion. (So any one pavilion's full needs always fit in one load.)

**Loading** (at the warehouse, all at once, cost 1) — exactly one of:
- **Option A:** bouquets of **different types** but **all the same color** (e.g. red rose + red tulip).
- **Option B:** bouquets of **different colors** but **all the same type** (e.g. red rose + pink rose).
- **Forbidden:** mixing two different types *and* two different colors in one load.
- Total bouquets in a load ≤ max load.

**Unloading** (at a pavilion, cost 1):
- Only bouquets whose **type matches** the pavilion can be unloaded there.
- A color is unloaded only if the robot carries **at least** the needed quantity of it.
- Partial delivery is allowed: a pavilion may be visited multiple times, unloading different colors each time.
- One unload operation costs 1 regardless of how many colors/bouquets it drops.

**Cost** = (number of moves) + (number of loads) + (number of unloads).

---

## 4. Coordinate convention (IMPORTANT)

The original spec's notation is internally inconsistent (its table header says `(Y, X)` while body text uses `(X, Y)`, and the robot's start is contradictory). **All instance data below is normalized to a single convention:**

> `(x, y)` where `x` = column (right = `+x`) and `y` = row (up = `+y`).
> `move_right`: `x+1`, `move_left`: `x-1`, `move_up`: `y+1`, `move_down`: `y-1`.

---

## 5. Instances

### 5.1 Assignment example (4 pavilions)

Converted from the spec's `(Y, X)` listing into our `(x, y)`.

| Pavilion | Type | pos (x,y) | Needs |
|---|---|---|---|
| P1 | rose | (2, 4) | red 2, pink 1, white 1 |
| P2 | tulip | (4, 3) | red 3, yellow 1 |
| P3 | orchid | (4, 5) | purple 2, pink 1 |
| P4 | goliat | (5, 2) | gold 2, light_pink 2 |

- Warehouse: (3, 2) · Robot start: **see note** · Max load: **4** (P1, P2, P4 each total 4).

```python
ASSIGNMENT_EXAMPLE = {
    "grid": {"w": 5, "h": 5},          # size not stated in spec; 5x5 contains all points — CONFIRM
    "warehouse": (3, 2),
    "robot_start": (3, 1),             # AMBIGUOUS in spec — see note below
    "pavilions": {
        "P1": {"type": "rose",   "pos": (2, 4), "needs": {"red": 2, "pink": 1, "white": 1}},
        "P2": {"type": "tulip",  "pos": (4, 3), "needs": {"red": 3, "yellow": 1}},
        "P3": {"type": "orchid", "pos": (4, 5), "needs": {"purple": 2, "pink": 1}},
        "P4": {"type": "goliat", "pos": (5, 2), "needs": {"gold": 2, "light_pink": 2}},
    },
}
```

> **Two things to confirm against the original diagram:**
> 1. **Robot start.** The spec lists `(3,1)` but its own note says "Y=1, X=3". Read as `(Y,X)` it would be `(x=1, y=3)`; the note means `(x=3, y=1)`. I used `(3, 1)` = `(x=3, y=1)`. Verify.
> 2. **Grid size** isn't stated; `5×5` is the smallest box containing every point. Verify.

### 5.2 Test fixture A — single pavilion (known optimal)

Smallest sanity case; optimal A\* cost = **6**.

```python
EXAMPLE_A = {
    "grid": {"w": 3, "h": 3},
    "warehouse": (2, 2),
    "robot_start": (1, 1),
    "pavilions": {
        "P1": {"type": "rose", "pos": (3, 3), "needs": {"red": 1, "pink": 1}},
    },
    "expected": {"astar_cost": 6},
}
```

### 5.3 Test fixture B — Option A saves a trip (known optimal)

P2 and P3 share the color *yellow* across different types, so one **Option-A** load serves both → 2 trips instead of 3. Optimal A\* cost = **9**. (Good A\*-vs-DFS contrast.)

```python
EXAMPLE_B = {
    "grid": {"w": 5, "h": 5},
    "warehouse": (2, 2),
    "robot_start": (2, 2),
    "pavilions": {
        "P1": {"type": "rose",   "pos": (2, 3), "needs": {"red": 2, "pink": 2}},
        "P2": {"type": "tulip",  "pos": (3, 2), "needs": {"yellow": 2}},
        "P3": {"type": "goliat", "pos": (4, 2), "needs": {"yellow": 2}},
    },
    "expected": {"astar_cost": 9},
}
```

---

## 6. Notes for the build

- `max_load` is derived from the instance, not hardcoded.
- Start with **fixture A** (optimal 6), then **fixture B** (optimal 9, exercises Option A), then the assignment example.
- A\* optimality is guaranteed by the admissible heuristic in `PLAN.md`. **DFS cost is exploration-order dependent** — if a specific expected DFS number must be matched, the order of the move/load/unload rules may need tuning; the expected DFS/A\* numbers for the assignment example aren't in this file yet.
