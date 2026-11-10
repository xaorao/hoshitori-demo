"""Scenarios for the decision alerts (``hoshitori.decision_alerts``), runnable against the real module or a mutant of it.

Every expected sentence is written out by hand from the module's docstring. The records are in the shape the ledger writes; only the
members the alerts read are filled in.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any

from hoshitori import practice_view

HOUR = 3_600_000


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def call(
    fid: str, *, symbol: str = "BTC", arm: str = "L1", hours: int = 4, contract: str = "D", invalidation: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        "type": "forecast",
        "id": fid,
        "body": {"symbol": symbol, "arm_id": arm, "horizon_ms": hours * HOUR, "contract": contract, "invalidation": list(invalidation)},
    }


def settled(fid: str, at: str, *, state: str = "SETTLED", outcome: str | None = "1", price: str | None = "100.50000000") -> dict[str, Any]:
    return {
        "type": "settlement",
        "created_at": at,
        "body": {"forecast_id": fid, "state": state, "outcome": outcome, "outcome_price": price},
    }


def check_settled_and_invalidated(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records = [
        call("fc-up", invalidation=("ret_1h above 0", "close below 101")),  # closes at 100.5: below 101, met
        settled("fc-up", "2026-11-09T12:00:00.000Z"),
        call("fc-held", symbol="ETH", arm="L0", hours=1, invalidation=("close below 99",)),  # 100.5 is not below 99: not met
        settled("fc-held", "2026-11-09T11:00:00.000Z", outcome="0"),
        call("fc-above", symbol="SOL", arm="L2", invalidation=("last_close above 100.4",)),  # 100.5 above 100.4: met
        settled("fc-above", "2026-11-09T10:00:00.000Z"),
        call("fc-vague", symbol="XRP", invalidation=("ret_4h below -0.01", "close below entry minus 1.5 ATR14", "atr14 above 1")),
        settled("fc-vague", "2026-11-09T09:00:00.000Z", price="1"),  # no closing-price level: nothing is guessed
        call("fc-edge", symbol="ADA", invalidation=("close below 100.5",)),  # equal to the level is not below it
        settled("fc-edge", "2026-11-09T08:00:00.000Z"),
        call("fc-noprice", symbol="DOT", invalidation=("close below 1000",)),
        settled("fc-noprice", "2026-11-09T07:00:00.000Z", price=None),  # no closing price to check against
    ]
    got = mod.alerts(records)
    c.eq(
        "the calls proved wrong by their own closing level first, newest first",
        [(a.kind, a.forecast_id, a.text) for a in got if a.kind == "INVALIDATED"],
        [
            (
                "INVALIDATED",
                "fc-up",
                "BTC, 4 hours, single model: its own condition for being wrong was met: the window closed at 100.5, below 101.",
            ),
            (
                "INVALIDATED",
                "fc-above",
                "SOL, 4 hours, debate: its own condition for being wrong was met: the window closed at 100.5, above 100.4.",
            ),
        ],
    )
    c.eq(
        "then every settlement, newest first",
        [(a.forecast_id, a.text) for a in got if a.kind == "SETTLED"],
        [
            ("fc-up", "BTC, 4 hours, single model: settled; the price rose."),
            ("fc-held", "ETH, 1 hour, rules: settled; the price fell."),
            ("fc-above", "SOL, 4 hours, debate: settled; the price rose."),
            ("fc-vague", "XRP, 4 hours, single model: settled; the price rose."),
            ("fc-edge", "ADA, 4 hours, single model: settled; the price rose."),
            ("fc-noprice", "DOT, 4 hours, single model: settled; the price rose."),
        ],
    )
    edges = mod.alerts(
        [
            call("fc-at", invalidation=("last_close above 100.5",)),  # equal to the level is not above it
            settled("fc-at", "2026-11-09T12:00:00.000Z"),
            call("fc-two", invalidation=("close below 101", "close below 102")),  # two met: one alert, the first named
            settled("fc-two", "2026-11-09T11:00:00.000Z"),
        ]
    )
    c.eq(
        "a level met exactly is not crossed; two met conditions make one alert",
        [(a.forecast_id, a.text) for a in edges if a.kind == "INVALIDATED"],
        [("fc-two", "BTC, 4 hours, single model: its own condition for being wrong was met: the window closed at 100.5, below 101.")],
    )
    c.eq("the kinds come in their order", [a.kind for a in got][:3], ["INVALIDATED", "INVALIDATED", "SETTLED"])
    first = got[0]
    c.eq(
        "an alert names its call, token, agent and time",
        (first.at, first.symbol, first.arm, first.book),
        ("2026-11-09T12:00:00.000Z", "BTC", "L1", None),
    )
    c.eq("the kinds", mod.KINDS, ("INVALIDATED", "THESIS_CHANGED", "REVISED", "RISK_STEP", "SETTLED"))
    return c.bad


def check_other_ends(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records = [
        call("fc-void"),
        settled("fc-void", "2026-11-09T12:00:00.000Z", state="UNSETTLEABLE_MISSING", outcome=None, price=None),
        call("fc-t", contract="B", arm="L0"),
        settled("fc-t", "2026-11-09T11:00:00.000Z", outcome="TARGET"),
        call("fc-s", contract="B", arm="L0", symbol="ETH"),
        settled("fc-s", "2026-11-09T10:00:00.000Z", outcome="STOP"),
        call("fc-n", contract="B", arm="L0", symbol="SOL", hours=1),
        settled("fc-n", "2026-11-09T09:00:00.000Z", outcome="TIMEOUT"),
        call("fc-b-level", contract="B", symbol="ADA", invalidation=("close below 1000",)),  # a level on a barrier call is not checked
        settled("fc-b-level", "2026-11-09T08:00:00.000Z", outcome="TIMEOUT"),
    ]
    c.eq(
        "could not be settled, and the three ends of a target-or-stop call",
        [a.text for a in mod.alerts(records)],
        [
            "BTC, 4 hours, single model: could not be settled (unsettleable missing).",
            "BTC, 4 hours, rules: settled; target first.",
            "ETH, 4 hours, rules: settled; stop first.",
            "SOL, 1 hour, rules: settled; neither level was reached.",
            "ADA, 4 hours, single model: settled; neither level was reached.",
        ],
    )
    try:
        mod.alerts([settled("fc-ghost", "2026-11-09T12:00:00.000Z")])
        c.bad.append("a settlement of a call the ledger does not hold was read")
    except mod.AlertError:
        pass
    return c.bad


def check_thesis_changed(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records = [
        call("fc-a"),
        {"type": "post_snapshot_event", "id": "ev1", "body": {"first_seen_at": "2026-11-09T13:00:00.000Z"}},
        {
            "type": "thesis_revision",
            "id": "rv1",
            "body": {"kind": "MATERIAL_UPDATE", "thesis_id": "fc-a", "event_id": "ev1", "changed": ["the venue delisted the pair"]},
        },
        {"type": "thesis_revision", "id": "rv2", "body": {"kind": "NO_CHANGE", "thesis_id": "fc-a", "event_id": "ev1", "changed": []}},
    ]
    got = mod.alerts(records)
    c.eq(
        "a changed premise, at the time the event was first seen; other revisions say nothing",
        [(a.kind, a.at, a.forecast_id, a.text) for a in got],
        [
            (
                "THESIS_CHANGED",
                "2026-11-09T13:00:00.000Z",
                "fc-a",
                "BTC, 4 hours, single model: a premise changed after the call was made (the venue delisted the pair); "
                "the call stays as it was recorded.",
            )
        ],
    )
    for label, broken in (
        ("an unknown call", {**records[2], "body": {**records[2]["body"], "thesis_id": "fc-x"}}),
        ("an unknown event", {**records[2], "body": {**records[2]["body"], "event_id": "ev-x"}}),
    ):
        try:
            mod.alerts([records[0], records[1], broken])
            c.bad.append(f"a revision naming {label} was read")
        except mod.AlertError:
            pass
    return c.bad


def book(steps: tuple[Any, ...], level: str = "DOUBLE_FEES") -> Any:
    return practice_view.PracticeBook(
        "L1:" + level,
        "L1",
        "Single model",
        level,
        "10",
        "5",
        False,
        "o",
        "l",
        "1",
        "1",
        "1",
        "1",
        "0",
        "0",
        "ACTIVE",
        False,
        "0",
        0,
        0,
        0,
        (),
        (),
        (),
        steps,
    )


def opened(fid: str, p: str, origin: str, *, arm: str = "L1") -> list[dict[str, Any]]:
    body = {"symbol": "BTC", "arm_id": arm, "asset_id": "as-btc", "cycle_id": f"cy-{fid}", "horizon_ms": 4 * HOUR, "contract": "D", "p": p}
    return [
        {"type": "forecast", "id": fid, "body": {**body, "invalidation": []}},
        {"type": "forecast_state", "body": {"forecast_id": fid, "state": "OPEN", "origin": origin}},
    ]


def check_revised(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records = [*opened("fc-a", "0.42", "2026-11-09T12:00:00.000Z"), *opened("fc-b", "0.551", "2026-11-09T13:00:00.000Z")]
    c.eq(
        "a decisive reversal of an open call is an alert on the later call",
        mod.alerts(records),
        (
            mod.Alert(
                "REVISED",
                "2026-11-09T13:00:00.000Z",
                "BTC, 4 hours, single model: now puts a rise at 55% while its call of 12:00 UTC (42%) is still open; both stay as recorded.",
                "fc-b",
                "BTC",
                "L1",
                None,
            ),
        ),
    )
    small = [*opened("fc-a", "0.52", "2026-11-09T12:00:00.000Z"), *opened("fc-b", "0.48", "2026-11-09T13:00:00.000Z")]
    c.eq("a swing across an even chance is not an alert", mod.alerts(small), ())
    c.eq(
        "a half rounds up, as the page rounds it",
        (mod._percent("0.535"), mod._percent("0.545"), mod._percent("0.5")),
        ("54%", "55%", "50%"),
    )
    return c.bad


def check_risk_steps(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    steps = (
        practice_view.Step("2026-11-09T10:00:00.000Z", "HALVED", "0.051234"),
        practice_view.Step("2026-11-09T11:00:00.000Z", "FLAT", "0.075"),
        practice_view.Step("2026-11-09T12:00:00.000Z", "HALTED", "0.12"),
        practice_view.Step("2026-11-09T09:00:00.000Z", "ACTIVE", "0.01"),
    )
    got = mod.alerts([], [book(steps)])
    c.eq(
        "each rung of a practice book, newest first, the fall to one decimal of a percent",
        [(a.kind, a.at, a.book, a.arm, a.forecast_id, a.text) for a in got],
        [
            (
                "RISK_STEP",
                "2026-11-09T12:00:00.000Z",
                "L1:DOUBLE_FEES",
                "L1",
                None,
                "Practice, single model at double fees costs: stopped trading after a fall of 12.0% from its peak (a simulated portfolio's rule).",
            ),
            (
                "RISK_STEP",
                "2026-11-09T11:00:00.000Z",
                "L1:DOUBLE_FEES",
                "L1",
                None,
                "Practice, single model at double fees costs: closed its positions after a fall of 7.5% from its peak (a simulated portfolio's rule).",
            ),
            (
                "RISK_STEP",
                "2026-11-09T10:00:00.000Z",
                "L1:DOUBLE_FEES",
                "L1",
                None,
                "Practice, single model at double fees costs: halved its sizes after a fall of 5.1% from its peak (a simulated portfolio's rule).",
            ),
            (
                "RISK_STEP",
                "2026-11-09T09:00:00.000Z",
                "L1:DOUBLE_FEES",
                "L1",
                None,
                "Practice, single model at double fees costs: went back to its planned sizes after a fall of 1.0% from its peak (a simulated portfolio's rule).",
            ),
        ],
    )
    c.eq("a book that never moved says nothing", mod.alerts([], [book(())]), ())
    return c.bad


def check_price_alone(mod: types.ModuleType) -> list[str]:
    """A price that moved, with no call settled and nothing revised, is no alert: the records of a cycle with a big move but nothing that
    ended say nothing."""
    c = Case(mod)
    records = [
        call("fc-open"),
        {"type": "evidence", "id": "ev-big", "body": {"source": "hoshitori.features", "fields": {"ret_1h": "0.25"}}},
        {"type": "practice", "body": {"event": {"event": "mark", "prices": {"BTC": "1"}}}},
    ]
    c.eq("a move alone raises nothing", mod.alerts(records), ())
    return c.bad


def check_order_and_frozen(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records = [
        call("fc-a", invalidation=("close below 1000",)),
        settled("fc-a", "2026-11-09T08:00:00.000Z"),
        call("fc-b"),
        {"type": "post_snapshot_event", "id": "ev1", "body": {"first_seen_at": "2026-11-09T07:00:00.000Z"}},
        {
            "type": "thesis_revision",
            "id": "rv1",
            "body": {"kind": "MATERIAL_UPDATE", "thesis_id": "fc-b", "event_id": "ev1", "changed": ["x"]},
        },
        call("fc-c"),
        settled("fc-c", "2026-11-09T08:00:00.000Z"),  # the same time as fc-a: ordered by call
    ]
    records = [records[5], records[6], *records[:5]]  # fc-c written before fc-a: the order is by call, not by the ledger
    got = mod.alerts(records, [book((practice_view.Step("2026-11-09T06:00:00.000Z", "HALVED", "0.05"),))])
    c.eq(
        "proved wrong, then a changed premise, then a rung, then settlements; ties by call",
        [(a.kind, a.forecast_id or a.book) for a in got],
        [("INVALIDATED", "fc-a"), ("THESIS_CHANGED", "fc-b"), ("RISK_STEP", "L1:DOUBLE_FEES"), ("SETTLED", "fc-a"), ("SETTLED", "fc-c")],
    )
    try:
        got[0].text = "x"  # type: ignore[misc]
        c.bad.append("an alert can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "settled_and_invalidated": check_settled_and_invalidated,
    "other_ends": check_other_ends,
    "thesis_changed": check_thesis_changed,
    "revised": check_revised,
    "risk_steps": check_risk_steps,
    "price_alone": check_price_alone,
    "order_and_frozen": check_order_and_frozen,
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
