# =============================================================================
# instances.py
# =============================================================================

EXAMPLE_A = {
    "grid": {"w": 3, "h": 3},
    "warehouse": (2, 2),
    "robot_start": (1, 1),
    "pavilions": {
        "P1": {"type": "rose", "pos": (3, 3), "needs": {"red": 1, "pink": 1}}
    },
    "expected": {"astar_cost": 6},
}

EXAMPLE_B = {
    "grid": {"w": 5, "h": 5},
    "warehouse": (2, 2),
    "robot_start": (2, 2),
    "pavilions": {
        "P1": {"type": "rose", "pos": (2, 3), "needs": {"red": 2, "pink": 2}},
        "P2": {"type": "tulip", "pos": (3, 2), "needs": {"yellow": 2}},
        "P3": {"type": "goliat", "pos": (4, 2), "needs": {"yellow": 2}},
    },
    "expected": {"astar_cost": 9},
}

NO_SHARED_COLOR = {
    "grid": {"w": 3, "h": 3},
    "warehouse": (2, 2),
    "robot_start": (2, 2),
    "pavilions": {
        "P1": {"type": "rose", "pos": (1, 2), "needs": {"red": 1}},
        "P2": {"type": "tulip", "pos": (3, 2), "needs": {"green": 1}},
    },
    "expected": {"astar_cost": 7},
}

ASSIGNMENT_EXAMPLE = {
    "grid": {"w": 5, "h": 5},
    "warehouse": (3, 2),
    "robot_start": (3, 1),
    "pavilions": {
        "P1": {
            "type": "rose",
            "pos": (2, 4),
            "needs": {"red": 2, "pink": 1, "white": 1},
        },
        "P2": {"type": "tulip", "pos": (4, 3), "needs": {"red": 3, "yellow": 1}},
        "P3": {"type": "orchid", "pos": (4, 5), "needs": {"purple": 2, "pink": 1}},
        "P4": {"type": "goliat", "pos": (5, 2), "needs": {"gold": 2, "light_pink": 2}},
    },
}
