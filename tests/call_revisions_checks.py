"""Scenarios for reversals of open calls (``hoshitori.call_revisions``), runnable against the real module or a mutant of it.

Each world is a handful of records written by hand: calls with their probabilities and start times, the snapshots their cycles froze and
the features in them, so every expected revision is worked out on paper.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any

HOUR = 3_600_000
T0 = 1_794_225_600_000  # 2026-11-09T12:00:00Z


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def iso(ms: int) -> str:
    from hoshitori.timeline import from_ms
    from hoshitori.timeutil import format_rfc3339_ms

    return format_rfc3339_ms(from_ms(ms))


def call(
    fid: str,
    p: str | None,
    start: int,
    *,
    arm: str = "L1",
    asset: str = "as-btc",
    hours: int = 4,
    contract: str = "D",
    cycle: str | None = None,
) -> list[dict[str, Any]]:
    body = {"arm_id": arm, "asset_id": asset, "symbol": "BTC", "horizon_ms": hours * HOUR, "contract": contract, "p": p}
    body["cycle_id"] = cycle or f"cy-{fid}"
    return [
        {"type": "forecast", "id": fid, "body": body},
        {"type": "forecast_state", "body": {"forecast_id": fid, "state": "OPEN", "origin": iso(start)}},
    ]


def frozen(cycle: str, fields: dict[str, Any], asset: str = "as-btc", ident: str | None = None) -> list[dict[str, Any]]:
    ident = ident or f"ev-{cycle}"
    return [
        {"type": "evidence", "id": ident, "body": {"source": "hoshitori.features", "fields": fields}},
        {"type": "evidence", "id": f"{ident}-x", "body": {"source": "binance-spot", "fields": {"close": "999"}}},
        {
            "type": "snapshot",
            "body": {
                "cycle_id": cycle,
                "candidates": [{"asset_id": asset, "items": [{"evidence_id": f"{ident}-x"}, {"evidence_id": ident}]}],
            },
        },
    ]


def check_reversal(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records = [
        *call("fc-a", "0.42", T0),
        *call("fc-b", "0.55", T0 + HOUR),
        *frozen("cy-fc-a", {"close": "100", "ret_4h": "-0.03", "ret_1h": "0.5"}),
        *frozen("cy-fc-b", {"close": "101", "ret_4h": "0.004"}),
    ]
    (got,) = mod.revisions(records)
    c.eq(
        "a later call the other way while the first is open is a revision",
        got,
        mod.Revision(
            "fc-a",
            "fc-b",
            "L1",
            "BTC",
            4,
            "0.42",
            "0.55",
            iso(T0),
            iso(T0 + HOUR),
            mod.Reading("100", "-0.03"),
            mod.Reading("101", "0.004"),
            True,
        ),
    )
    small = [*call("fc-a", "0.52", T0), *call("fc-b", "0.48", T0 + HOUR)]
    (weak,) = mod.revisions(small)
    c.eq(
        "a small swing is linked but not decisive, and with no snapshot it reads nothing",
        (weak.decisive, weak.earlier_reading),
        (False, mod.Reading(None, None)),
    )
    edge = [*call("fc-a", "0.55", T0), *call("fc-b", "0.45", T0 + HOUR)]
    c.eq("exactly the margin from even on both sides is decisive", mod.revisions(edge)[0].decisive, True)
    one_short = [*call("fc-a", "0.55", T0), *call("fc-b", "0.4501", T0 + HOUR)]
    c.eq("one side short of the margin is not", mod.revisions(one_short)[0].decisive, False)
    c.eq("the margin", mod.MARGIN, mod.Decimal("0.05"))
    first_short = [*call("fc-a", "0.52", T0), *call("fc-b", "0.40", T0 + HOUR)]
    c.eq("the earlier call short of the margin is not decisive either", mod.revisions(first_short)[0].decisive, False)
    backwards = [*call("fc-b", "0.55", T0 + HOUR), *call("fc-a", "0.42", T0)]
    c.eq(
        "records in any order: the earlier call is the one that started first",
        [(v.earlier, v.later) for v in mod.revisions(backwards)],
        [("fc-a", "fc-b")],
    )
    after = [
        *call("fc-a", "0.42", T0),
        *call("fc-b", "0.55", T0 + HOUR),
        {"type": "evidence", "id": "ev-f", "body": {"source": "hoshitori.features", "fields": {"close": "100", "ret_4h": "-0.03"}}},
        {"type": "evidence", "id": "ev-s", "body": {"source": "binance-spot", "fields": {"close": "999", "ret_4h": "9"}}},
        {
            "type": "snapshot",
            "body": {
                "cycle_id": "cy-fc-a",
                "candidates": [{"asset_id": "as-btc", "items": [{"evidence_id": "ev-f"}, {"evidence_id": "ev-s"}]}],
            },
        },
    ]
    c.eq(
        "only the features are read, whatever else the snapshot holds after them",
        mod.revisions(after)[0].earlier_reading,
        mod.Reading("100", "-0.03"),
    )
    return c.bad


def check_what_is_not_a_revision(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the same direction is not", mod.revisions([*call("fc-a", "0.4", T0), *call("fc-b", "0.3", T0 + HOUR)]), ())
    c.eq(
        "a call that starts when the first has closed is a new call",
        mod.revisions([*call("fc-a", "0.4", T0), *call("fc-b", "0.6", T0 + 4 * HOUR)]),
        (),
    )
    c.eq(
        "one minute before it closed is still a revision",
        len(mod.revisions([*call("fc-a", "0.4", T0), *call("fc-b", "0.6", T0 + 4 * HOUR - 60_000)])),
        1,
    )
    c.eq("another agent is not", mod.revisions([*call("fc-a", "0.4", T0), *call("fc-b", "0.6", T0 + HOUR, arm="L0")]), ())
    c.eq("another horizon is not", mod.revisions([*call("fc-a", "0.4", T0), *call("fc-b", "0.6", T0 + HOUR, hours=24)]), ())
    c.eq("another token is not", mod.revisions([*call("fc-a", "0.4", T0), *call("fc-b", "0.6", T0 + HOUR, asset="as-eth")]), ())
    c.eq("an abstention is not a call", mod.revisions([*call("fc-a", "0.4", T0), *call("fc-b", None, T0 + HOUR)]), ())
    c.eq("a target-or-stop call is not read", mod.revisions([*call("fc-a", "0.4", T0), *call("fc-b", "0.6", T0 + HOUR, contract="B")]), ())
    even = [*call("fc-a", "0.6", T0), *call("fc-b", "0.5", T0 + HOUR), *call("fc-c", "0.4", T0 + 2 * HOUR)]
    c.eq(
        "an even chance points nowhere: the calls on each side of it are compared",
        [(v.earlier, v.later) for v in mod.revisions(even)],
        [("fc-a", "fc-c")],
    )
    unopened = [*call("fc-a", "0.4", T0), {"type": "forecast", "id": "fc-b", "body": {**call("fc-b", "0.6", T0)[0]["body"]}}]
    c.eq("a call whose window never opened is not", mod.revisions(unopened), ())
    return c.bad


def check_order_and_readings(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records = [
        *call("fc-b1", "0.4", T0 + HOUR, arm="L2"),
        *call("fc-b2", "0.6", T0 + 2 * HOUR, arm="L2"),
        *call("fc-a1", "0.6", T0),
        *call("fc-a2", "0.4", T0 + HOUR),
        *call("fc-a3", "0.6", T0 + 2 * HOUR),
    ]
    got = mod.revisions(records)
    c.eq(
        "every consecutive reversal, by when the later call started, then the agent",
        [(v.earlier, v.later) for v in got],
        [("fc-a1", "fc-a2"), ("fc-a2", "fc-a3"), ("fc-b1", "fc-b2")],
    )
    c.eq(
        "a reading takes the close and the return over the call's own horizon",
        mod._reading({"close": "1", "ret_4h": "2", "ret_1h": "3"}, 1),
        mod.Reading("1", "3"),
    )
    c.eq("a number that is not written as text is not read", mod._reading({"close": 1, "ret_4h": None}, 4), mod.Reading(None, None))
    c.eq("a missing field is none", mod._reading({}, 4), mod.Reading(None, None))
    try:
        got[0].decisive = False  # type: ignore[misc]
        c.bad.append("a revision can be changed after it is made")
    except AttributeError:
        pass
    try:
        got[0].earlier_reading.close = "x"  # type: ignore[misc]
        c.bad.append("a reading can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "reversal": check_reversal,
    "what_is_not_a_revision": check_what_is_not_a_revision,
    "order_and_readings": check_order_and_readings,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
