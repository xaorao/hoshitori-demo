"""The recorded failure drills as data, for the Drill page: what was broken, what the league was expected to do, what it did.

A drill is a run of the real league code against a scripted exchange, model or time-stamp authority with one thing broken
(``scripts/make_drill.py`` makes them, and a test fails if any no longer holds). The file the page shows is read here, strictly, so
that the page cannot show a drill that says it held when the run did not match what was expected: ``held`` must agree with a
comparison of ``observed`` and ``expected_observed``, which the file carries beside the words.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

OBSERVED: Final = ("cycle", "level", "slots", "reasons", "forecasts")


class DrillError(ValueError):
    """A drill file that cannot be shown."""


@dataclass(frozen=True)
class Drill:
    id: str
    title: str
    injected: str  # what was broken, in words
    expected: str  # what the league is meant to do, in words
    observed: dict[str, Any]  # what it did: cycle state, commitment level, slots by state, reasons in words, forecasts made
    held: bool


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DrillError(f"{what} is a non-empty text")
    return value


def _observed(value: object, id_: str) -> dict[str, Any]:
    if not isinstance(value, dict) or tuple(value) != OBSERVED:
        raise DrillError(f"{id_}: observed has exactly {', '.join(OBSERVED)}, in that order")
    slots = value["slots"]
    if not isinstance(slots, list) or not all(
        isinstance(x, list) and len(x) == 2 and isinstance(x[0], str) and isinstance(x[1], int) and not isinstance(x[1], bool) and x[1] > 0
        for x in slots
    ):
        raise DrillError(f"{id_}: slots are pairs of a state word and a positive count")
    reasons = value["reasons"]
    if not isinstance(reasons, list) or reasons != sorted(set(reasons)) or not all(isinstance(r, str) and r for r in reasons):
        raise DrillError(f"{id_}: reasons are sorted texts, once each")
    forecasts = value["forecasts"]
    if isinstance(forecasts, bool) or not isinstance(forecasts, int) or forecasts < 0:
        raise DrillError(f"{id_}: forecasts is a whole number")
    _text(value["cycle"], f"{id_}: the cycle state")
    if value["level"] is not None:
        _text(value["level"], f"{id_}: the level")
    return value


def read_drills(data: bytes) -> tuple[Drill, ...]:
    """The drills of a file: a list, each with its words, what was observed, what was expected of it and whether the two agree."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DrillError(f"the drill file is not JSON: {exc}") from exc
    if not isinstance(doc, list) or not doc:
        raise DrillError("the drill file is a list of at least one drill")
    out: list[Drill] = []
    for item in doc:
        if not isinstance(item, dict) or set(item) != {"id", "title", "injected", "expected", "observed", "expected_observed", "held"}:
            raise DrillError("a drill has id, title, injected, expected, observed, expected_observed and held")
        id_ = _text(item["id"], "a drill id")
        if id_ in {d.id for d in out}:
            raise DrillError(f"{id_}: a drill is named once")
        observed = _observed(item["observed"], id_)
        wanted = _observed(item["expected_observed"], id_)
        if not isinstance(item["held"], bool):
            raise DrillError(f"{id_}: held is true or false")
        if item["held"] != (observed == wanted):
            raise DrillError(
                f"{id_}: held says {item['held']} but observed and expected_observed {'agree' if observed == wanted else 'differ'}"
            )
        out.append(
            Drill(
                id_,
                _text(item["title"], f"{id_}: the title"),
                _text(item["injected"], f"{id_}: injected"),
                _text(item["expected"], f"{id_}: expected"),
                observed,
                item["held"],
            )
        )
    return tuple(out)
