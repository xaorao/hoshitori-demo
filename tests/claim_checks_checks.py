"""Checks of the checks run on recorded claims (``hoshitori.claim_checks``), runnable against a mutant."""

from __future__ import annotations

import json
import types
from typing import Any

from hoshitori import claims_file, move_check
from tests.claim_extract_checks import reply
from tests.claims_file_checks import made
from tests.model_client_checks import answer
from tests.move_check_checks import held

ASSETS = ("BTC", "ETH", "SOL")


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def reading_of(ident: str, text: str, nodes: list[dict[str, str]]) -> dict[str, Any]:
    body = {"nodes": nodes, "links": [], "implied": []}
    return made(claims_file, tuple(reply(body, i) for i in (1, 2, 3)), text, ident, ASSETS)


def claims() -> tuple[Any, ...]:
    unread = made(claims_file, (answer(None, 1, finish="length"),) * 3, "BTC fell 4% in the last 24 hours", "unread", ASSETS)
    records = [
        reading_of(
            "move", "BTC fell 4% in the last 24 hours", [{"id": "a", "type": "OBSERVATION", "quote": "BTC fell 4% in the last 24 hours"}]
        ),
        reading_of(
            "mixed",
            "ETH dropped today. SOL will rise 5% in 24 hours. Whales are accumulating SOL",
            [
                {"id": "a", "type": "OBSERVATION", "quote": "ETH dropped today"},
                {"id": "b", "type": "FORECAST", "quote": "SOL will rise 5% in 24 hours"},
                {"id": "c", "type": "OBSERVATION", "quote": "Whales are accumulating SOL"},
            ],
        ),
        reading_of("opinion", "I like BTC", [{"id": "a", "type": "OPINION", "quote": "I like BTC"}]),
        reading_of(
            "unheld", "SOL fell 5% in the last 24 hours", [{"id": "a", "type": "OBSERVATION", "quote": "SOL fell 5% in the last 24 hours"}]
        ),
        reading_of(
            "odd", "BTC fell 4% in the last 3 days", [{"id": "a", "type": "OBSERVATION", "quote": "BTC fell 4% in the last 3 days"}]
        ),
        reading_of(
            "kind", "BTC rose 5% in the last 24 hours", [{"id": "a", "type": "INTERPRETATION", "quote": "BTC rose 5% in the last 24 hours"}]
        ),
        unread,
    ]
    return claims_file.read_claims(json.dumps(records).encode())


def check_checks(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    at = held(move_check, ret_24h="-0.039000000000")
    got = mod.checks_for(claims(), at)
    c.eq(
        "only the observations about a move are checked",
        [(x.claim_id, x.node_id, x.status) for x in got],
        [
            ("move", "a", "SUPPORTED"),
            ("mixed", "a", "UNRESOLVED"),
            ("unheld", "a", "NOT_FOUND_IN_SOURCES_CHECKED"),
            ("odd", "a", "NOT_CHECKABLE"),
        ],
    )
    first = got[0]
    c.eq(
        "a result names its evidence and its time",
        (first.words, first.reason, first.evidence_id, first.cycle_id),
        ("BTC fell 4% in the last 24 hours", "BTC moved -3.90% over the 24 hours; the claim says 4%", at["BTC"].evidence_id, "cy1"),
    )
    c.eq("the snapshot time is an RFC 3339 instant", first.snapshot_at, "1970-01-01T00:00:01.000Z")
    c.eq("a check that found no evidence has no time", (got[1].evidence_id, got[1].cycle_id, got[1].snapshot_at), (None, None, None))
    c.eq("the reason of an open claim", got[1].reason, "the words leave open: window")
    c.eq("an asset the claim was not read with is not found", got[2].reason, "no committed snapshot holds SOL")
    c.eq("a window the league does not hold", got[3].reason, "the league holds moves over 1, 4, 24 hours, not 72")
    c.eq("no claims, no checks", mod.checks_for((), at), ())
    c.eq(
        "the same claims with no evidence",
        [x.status for x in mod.checks_for(claims(), {})],
        ["NOT_FOUND_IN_SOURCES_CHECKED", "UNRESOLVED", "NOT_FOUND_IN_SOURCES_CHECKED", "NOT_CHECKABLE"],
    )
    c.eq("frozen", mod.Check.__dataclass_params__.frozen, True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_checks,)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
