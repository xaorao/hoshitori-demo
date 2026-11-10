"""The records the skill checks feed to ``run``. Each is built so that one rule of ``skill.py`` decides the answer."""

from __future__ import annotations

from typing import Any


def o(symbol: str, reading: str, direction: str | None, hit: Any = None, plan: str = "NO_PLAN") -> dict[str, Any]:
    return {"symbol": symbol, "reading": reading, "state": "x", "direction": direction, "hit": hit, "plan": plan}


def many(symbol: str, reading: str, directions: list[str]) -> list[dict[str, Any]]:
    return [o(symbol, reading, d) for d in directions]


WIDE = many("A", "UP", ["ROSE"] * 20) + many("A", "DOWN", ["ROSE"] * 20) + many("A", "NOT_LONG", ["FELL"] * 20)
MIXED: dict[str, Any] = {
    "outcomes": [
        o("SOL", "UP", "ROSE", True, "TARGET"),
        o("SOL", "UP", "ROSE", False, "STOP"),
        o("ETH", "UP", "FELL", None, "TIMEOUT"),
        o("SOL", "DOWN", "FELL", True, "ALREADY_PAST"),
        o("ETH", "DOWN", "FELL", "yes", "MOON"),
        o("BTC", "NOT_LONG", "ROSE", True),
        o("BTC", "UP", None),
        o("BTC", "NONE", "TIE"),
        {"symbol": "X", "reading": "BAD", "direction": None},
        "junk",
        {"symbol": 3, "reading": "UP", "direction": None},
        {"symbol": "A", "reading": "UP", "direction": "UP"},
    ]
}

# name -> arguments of run()
SCENARIOS: dict[str, dict[str, Any]] = {
    "empty": {"record": []},
    "unscored": {
        "record": [
            o("SOL", "UP", None, plan="WAITING"),
            o("SOL", "NONE", "ROSE"),
            o("ETH", "UNREAD", "FELL"),
            o("ETH", "DOWN", "TIE", plan="AMBIGUOUS"),
        ]
    },
    "mixed": {"record": MIXED, "min_scored": 2},
    "mixed_sol": {"record": MIXED, "min_scored": 2, "symbol": "SOL"},
    "wide": {"record": WIDE},
    "wide_min20": {"record": WIDE, "min_scored": 20},
    "wide_min21": {"record": WIDE, "min_scored": 21},
    "allrose2": {"record": many("A", "UP", ["ROSE", "ROSE"]), "min_scored": 1},
    "allrose1": {"record": many("A", "UP", ["ROSE"]), "min_scored": 1},
    "allfell1": {"record": many("A", "UP", ["FELL"]), "min_scored": 1},
    "allfell": {"record": many("A", "UP", ["FELL"] * 3) + many("A", "DOWN", ["FELL"] * 3), "min_scored": 3},
    "allrose_ud": {"record": many("A", "UP", ["ROSE"] * 3) + many("A", "DOWN", ["ROSE"] * 3), "min_scored": 3},
    "notdist": {"record": many("A", "UP", ["ROSE", "FELL"] * 5), "min_scored": 10},
    "bare_list": {"record": [o("A", "UP", "ROSE", True, "TARGET"), o("A", "DOWN", "FELL", True, "STOP")], "min_scored": 1},
}
