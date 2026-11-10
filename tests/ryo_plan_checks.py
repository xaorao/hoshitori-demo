"""Scenarios for arm R's plan settlement (``hoshitori.ryo_plan``), runnable against the real module or a mutant of it.

Every expected value is worked out by hand from the module's docstring: a long plan from the window's start price, already past when the
start price is at or beyond a level, and a record as strict as the settlement records of the agents.
"""

from __future__ import annotations

import copy
import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from hoshitori import ryo_read
from hoshitori.settlement import Settlement, Side, State

SOURCE = "urn:hoshitori:test"
AT = 1_794_157_200_000
ORIGIN = 1_794_160_800_000
FIXED_ID = "rp2ruzuwq7swirpj4muwo6h226m6"
PLAN = ryo_read.Plan("100.5", "95.0", "110.0")


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def raises(self, label: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except self.mod.RyoPlanError:
            return
        except Exception as exc:  # the wrong kind of refusal is a defect too
            self.bad.append(f"{label}: raised {type(exc).__name__}, not RyoPlanError")
            return
        self.bad.append(f"{label}: accepted")


def window(mod: types.ModuleType, p0: str = "100") -> Any:
    return mod.Window("fcwindow", ORIGIN, "USDT", "A", p0)


def settled(
    state: State,
    outcome: str | None,
    *,
    reason: str | None = None,
    touch: int | None = None,
    p0: str = "100",
    fid: str = "rr1",
    contract: str = "B",
) -> Settlement:
    return Settlement("st", fid, contract, "A", "1", state, outcome, reason, p0, None, touch, ())


def check_the_barrier(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    b = mod.barrier_of("rr1", PLAN, window(mod))
    c.eq(
        "a long barrier from the start price: 10 to the target, 5 to the stop, over 24 hours",
        (b.forecast_id, b.origin_ms, b.horizon_ms, b.quote_class, b.side, b.target_offset, b.stop_offset),
        ("rr1", ORIGIN, 86_400_000, "USDT", Side.LONG, Decimal("10.0"), Decimal("5.0")),
    )
    for p0 in ("110.0", "110", "120", "95.0", "95", "90"):
        c.eq(f"a start price of {p0} is at or beyond a level: already past", mod.barrier_of("rr1", PLAN, window(mod, p0)), None)
    c.eq("just inside the target", mod.barrier_of("rr1", PLAN, window(mod, "109.99")).target_offset, Decimal("0.01"))
    c.eq("just inside the stop", mod.barrier_of("rr1", PLAN, window(mod, "95.01")).stop_offset, Decimal("0.01"))
    c.eq("the horizon", mod.HORIZON_MS, 86_400_000)
    c.eq(
        "the states and outcomes",
        (mod.STATES, mod.OUTCOMES),
        (("SETTLED", "AMBIGUOUS", "VOID", "UNSETTLEABLE", "ALREADY_PAST"), ("TARGET", "STOP", "TIMEOUT", "AMBIGUOUS")),
    )
    w = window(mod)
    try:
        w.p0 = "1"
        c.bad.append("a window can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


def check_bodies(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = window(mod)
    t = ORIGIN + 89 * 60_000

    def brief(body: dict[str, Any]) -> tuple[Any, ...]:
        return (body["state"], body["outcome"], body["reason"], body["touch_open_ms"])

    c.eq("the target first", brief(mod.body_of("rr1", PLAN, w, settled(State.SETTLED, "TARGET", touch=t))), ("SETTLED", "TARGET", None, t))
    c.eq("the stop first", brief(mod.body_of("rr1", PLAN, w, settled(State.SETTLED, "STOP", touch=t))), ("SETTLED", "STOP", None, t))
    c.eq("neither", brief(mod.body_of("rr1", PLAN, w, settled(State.SETTLED, "TIMEOUT"))), ("SETTLED", "TIMEOUT", None, None))
    c.eq(
        "both in one minute",
        brief(mod.body_of("rr1", PLAN, w, settled(State.AMBIGUOUS, "AMBIGUOUS", reason="SAME_CANDLE", touch=t))),
        ("AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", t),
    )
    c.eq(
        "the oracles disagreed",
        brief(mod.body_of("rr1", PLAN, w, settled(State.UNSETTLEABLE, None, reason="UNSETTLEABLE_DISAGREE"))),
        ("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", None),
    )
    c.eq(
        "delisted",
        brief(mod.body_of("rr1", PLAN, w, settled(State.VOID, None, reason="VOID_DELISTED"))),
        ("VOID", None, "VOID_DELISTED", None),
    )
    past = mod.body_of("rr1", PLAN, window(mod, "112"), None)
    c.eq("already past", past, {
        "read_id": "rr1", "window_forecast_id": "fcwindow", "pair_id": "A", "state": "ALREADY_PAST", "outcome": None, "reason": None,
        "p0": "112", "stop": "95.0", "target": "110.0", "touch_open_ms": None,
    })  # fmt: skip
    c.eq(
        "a start price written another way is the same price",
        mod.body_of("rr1", PLAN, w, settled(State.SETTLED, "TIMEOUT", p0="100.00"))["p0"],
        "100",
    )
    c.raises("a settlement of another read", lambda: mod.body_of("rr1", PLAN, w, settled(State.SETTLED, "TIMEOUT", fid="rr2")))
    c.raises("a directional settlement", lambda: mod.body_of("rr1", PLAN, w, settled(State.SETTLED, "1", contract="D")))
    c.raises("a settlement that does not end", lambda: mod.body_of("rr1", PLAN, w, settled(State.PENDING, None)))
    c.raises("a start price that is not the window's", lambda: mod.body_of("rr1", PLAN, w, settled(State.SETTLED, "TIMEOUT", p0="101")))
    c.eq(
        "a settlement with no start price is the window's",
        mod.body_of("rr1", PLAN, w, Settlement("st", "rr1", "B", "A", "1", State.VOID, None, "VOID_OUTAGE", None, None, None, ()))["p0"],
        "100",
    )
    c.raises("already past claimed when the start price is inside", lambda: mod.body_of("rr1", PLAN, w, None))
    c.raises(
        "a settlement claimed when the start price is past",
        lambda: mod.body_of("rr1", PLAN, window(mod, "120"), settled(State.SETTLED, "TIMEOUT", p0="120")),
    )
    return c.bad


def check_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    t = ORIGIN + 60_000
    body = mod.body_of("rr1", PLAN, window(mod), settled(State.SETTLED, "TARGET", touch=t))
    record = mod.to_record(body, SOURCE, AT)
    c.eq("the id is the read's", record["id"], mod.record_id("rr1"))
    c.eq("another read is another id", mod.record_id("rr1") == mod.record_id("rr2"), False)
    c.eq("the record's type", (record["type"], record["type_version"], record["privacy"]), ("ryo_plan_settlement", 1, "public"))
    c.eq("it reads back", mod.from_record(record), body)
    c.eq("a committed time is allowed", mod.from_record({**record, "committed_at": "2026-11-09T00:00:00.000Z"}), body)

    def edited(change: Callable[[dict[str, Any]], None]) -> Callable[[], object]:
        def go() -> object:
            r = copy.deepcopy(record)
            change(r)
            return mod.from_record(r)

        return go

    b = lambda **kw: lambda r: r["body"].update(**kw)  # noqa: E731
    for label, change in (
        ("another type", lambda r: r.update(type="settlement")),
        ("another version", lambda r: r.update(type_version=2)),
        ("an id that is not the read's", lambda r: r.update(id=mod.record_id("rr2"))),
        ("an envelope the reader refuses", lambda r: r.update(privacy="secret")),
        ("a member more", b(extra=1)),
        ("a member missing", lambda r: r["body"].pop("reason")),
        ("an id that is not an identifier", b(read_id="rr 1")),
        ("a window id that is not an identifier", b(window_forecast_id="")),
        ("a pair that is not an identifier", b(pair_id=7)),
        ("a start price that is not plain", b(p0="1e2")),
        ("a start price of zero", b(p0="0")),
        ("a stop that is not a number", b(stop="low")),
        ("a target below the stop", b(target="90", stop="95.0")),
        ("a target at the stop", b(target="95.0")),
        ("another state", b(state="PENDING")),
        ("a reason that is not a code", b(state="VOID", outcome=None, touch_open_ms=None, reason="void delisted")),
        ("a touch time that is negative", b(touch_open_ms=-1)),
        ("a touch time that is true", b(touch_open_ms=True)),
        ("a touch time past the last instant", b(touch_open_ms=mod.MAX_INSTANT_MS + 1)),
        ("a settled plan with no outcome", b(outcome=None)),
        ("a settled plan with a reason", b(reason="WHY")),
        ("a settled first touch with no time", b(touch_open_ms=None)),
        ("neither touched, with a time", b(outcome="TIMEOUT")),
        ("an outcome that is not one", b(outcome="UP")),
        ("an ambiguous plan with no time", b(state="AMBIGUOUS", outcome="AMBIGUOUS", reason="SAME_CANDLE", touch_open_ms=None)),
        ("an ambiguous plan with another reason", b(state="AMBIGUOUS", outcome="AMBIGUOUS", reason="OTHER")),
        ("an ambiguous plan with another outcome", b(state="AMBIGUOUS", outcome="TARGET", reason="SAME_CANDLE")),
        ("a void plan with an outcome", b(state="VOID", outcome="TARGET", reason="VOID_DELISTED", touch_open_ms=None)),
        ("a void plan with a time", b(state="VOID", outcome=None, reason="VOID_DELISTED")),
        ("a void plan with no reason", b(state="VOID", outcome=None, reason=None, touch_open_ms=None)),
        ("already past with a start price inside", b(state="ALREADY_PAST", outcome=None, reason=None, touch_open_ms=None)),
        ("already past with an outcome", b(state="ALREADY_PAST", outcome="TARGET", reason=None, touch_open_ms=None, p0="120")),
        ("already past with a reason", b(state="ALREADY_PAST", outcome=None, reason="WHY", touch_open_ms=None, p0="120")),
        ("already past with a time", b(state="ALREADY_PAST", outcome=None, reason=None, p0="120")),
        ("settled with a start price past", b(p0="120")),
        ("settled with a start price at the stop", b(p0="95")),
    ):
        c.raises(label, edited(change))
    for label, change, text in (
        ("a start price of zero names it", b(p0="0"), "the start price is a positive plain decimal"),
        ("a zero stop names it", b(stop="0"), "the stop is a positive plain decimal"),
        ("a zero target names it", b(target="0"), "the target is a positive plain decimal"),
        (
            "an unknown state with nothing else",
            b(state="PENDING", outcome=None, touch_open_ms=None, reason="WAITING"),
            "the state is one of ('SETTLED', 'AMBIGUOUS', 'VOID', 'UNSETTLEABLE', 'ALREADY_PAST')",
        ),
        (
            "already past with a target below the stop",
            b(state="ALREADY_PAST", outcome=None, reason=None, touch_open_ms=None, stop="120", target="110", p0="115"),
            "the stop is below the target",
        ),
        (
            "already past with the stop at the target",
            b(state="ALREADY_PAST", outcome=None, reason=None, touch_open_ms=None, stop="110", target="110", p0="115"),
            "the stop is below the target",
        ),
    ):
        try:
            edited(change)()
            c.bad.append(f"{label}: accepted")
        except mod.RyoPlanError as exc:
            c.eq(label, str(exc), text)
    small = mod.body_of("rr1", ryo_read.Plan("0.5", "0.4", "0.6"), window(mod, "0.5"), settled(State.SETTLED, "TIMEOUT", p0="0.5"))
    gone = mod.Window("fcwindow", ORIGIN, "USDT", "A", None)
    void = mod.body_of("rr1", PLAN, gone, settled(State.UNSETTLEABLE, None, reason="UNSETTLEABLE_MISSING", p0=None))
    c.eq(
        "a window with no start price: the plan takes its state",
        (void["state"], void["reason"], void["p0"]),
        ("UNSETTLEABLE", "UNSETTLEABLE_MISSING", None),
    )
    c.raises("a window with no start price settles no barrier", lambda: mod.barrier_of("rr1", PLAN, gone))
    c.raises(
        "a settlement with a start price on a window with none", lambda: mod.body_of("rr1", PLAN, gone, settled(State.SETTLED, "TIMEOUT"))
    )
    for label, change, text in (
        ("no start price on a settled plan", b(p0=None), "only a void or unsettleable plan has no start price"),
        (
            "no start price on a plan already past",
            b(p0=None, state="ALREADY_PAST", outcome=None, touch_open_ms=None),
            "only a void or unsettleable plan has no start price",
        ),
        (
            "no start price, with an outcome",
            b(p0=None, state="VOID", reason="VOID_DELISTED", touch_open_ms=None),
            "a void or unsettleable plan has a reason and no outcome",
        ),
        (
            "no start price, with a time",
            b(p0=None, state="VOID", reason="VOID_DELISTED", outcome=None),
            "a void or unsettleable plan has a reason and no outcome",
        ),
        (
            "no start price, with no reason",
            b(p0=None, state="VOID", reason=None, outcome=None, touch_open_ms=None),
            "a void or unsettleable plan has a reason and no outcome",
        ),
    ):
        try:
            edited(change)()
            c.bad.append(f"{label}: accepted")
        except mod.RyoPlanError as exc:
            c.eq(label, str(exc), text)
    c.eq(
        "a void plan with no start price reads back",
        mod.check_body({**body, "p0": None, "state": "VOID", "reason": "VOID_DELISTED", "outcome": None, "touch_open_ms": None}),
        None,
    )
    c.eq("prices below one are prices", (small["p0"], small["stop"], small["target"]), ("0.5", "0.4", "0.6"))
    c.eq("the id's literal form", mod.record_id("rr1"), FIXED_ID)
    c.eq("a touch at the last instant", mod.check_body({**body, "touch_open_ms": mod.MAX_INSTANT_MS}), None)
    c.eq("a touch at the first instant", mod.check_body({**body, "touch_open_ms": 0}), None)
    c.eq(
        "an unsettleable plan",
        mod.check_body({**body, "state": "UNSETTLEABLE", "outcome": None, "touch_open_ms": None, "reason": "UNSETTLEABLE_MISSING"}),
        None,
    )
    c.raises("a body that is not an object", lambda: mod.check_body([]))
    c.raises("a negative creation time", lambda: mod.to_record(body, SOURCE, -1))
    c.raises("a creation time that is true", lambda: mod.to_record(body, SOURCE, True))
    c.raises("a creation time past the last instant", lambda: mod.to_record(body, SOURCE, mod.MAX_INSTANT_MS + 1))
    c.eq("the first instant", mod.to_record(body, SOURCE, 0)["created_at"], "1970-01-01T00:00:00.000Z")
    c.eq("the last instant", mod.to_record(body, SOURCE, mod.MAX_INSTANT_MS)["type"], "ryo_plan_settlement")
    c.raises("a source the envelope refuses", lambda: mod.to_record(body, "", AT))
    c.raises("a body the writer refuses", lambda: mod.to_record({**body, "state": "PENDING"}, SOURCE, AT))
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "barrier": check_the_barrier,
    "bodies": check_bodies,
    "records": check_records,
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
