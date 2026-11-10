"""Checks of the settlement record (``hoshitori.settlement_record``), runnable against a mutant."""

from __future__ import annotations

import types
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from hoshitori import canonical
from hoshitori.ids import natural_id
from hoshitori.settlement import Settlement, State, void

DIGEST = "d" * 64
SRC = "urn:hoshitori:cycle:test"
AT = 1_793_000_000_000


def sid(forecast: str, contract: str = "D", pair: str = "A") -> str:
    return natural_id("st", {"forecast": forecast, "pair": pair, "rule": "settle/1"})


def make(contract: str = "D", **changes: Any) -> Settlement:
    base: dict[str, Any] = {
        "settlement_id": sid("fc-1"),
        "forecast_id": "fc-1",
        "contract": contract,
        "pair_id": "A",
        "rule_version": "settle/1",
        "state": State.SETTLED,
        "outcome": "1",
        "reason": None,
        "p0": "100.50",
        "outcome_price": "101.25",
        "touch_open_ms": None,
        "flags": (),
    }
    if contract == "B":
        base.update(outcome="TARGET", outcome_price=None, touch_open_ms=AT)
    base.update(changes)
    return Settlement(**base)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.SettlementRecordError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def rec(mod: types.ModuleType, s: Settlement) -> dict[str, Any]:
    return mod.to_record(s, DIGEST, SRC, AT)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("type", (mod.TYPE, mod.TYPE_VERSION), ("settlement", 1))
    c.eq("recorded states", mod.RECORDED, (State.SETTLED, State.AMBIGUOUS, State.VOID, State.UNSETTLEABLE))
    c.eq("barrier outcomes", mod.BARRIER_OUTCOMES, ("TARGET", "STOP", "TIMEOUT"))
    c.eq("flags", mod.FLAGS, frozenset({"BASIS_STRESS"}))
    c.eq("a late year", mod.MAX_INSTANT_MS, 253_402_300_799_999)
    return c.bad


def check_record(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    s = make()
    r = rec(mod, s)
    c.eq("envelope", {k: r[k] for k in ("spec", "type", "type_version", "id", "source", "created_at", "privacy")}, {
        "spec": r["spec"], "type": "settlement", "type_version": 1, "id": s.settlement_id, "source": SRC, "created_at": "2026-10-26T07:33:20.000Z", "privacy": "public"})  # fmt: skip
    c.eq("it names the forecast by id and digest", r["refs"], [{"id": "fc-1", "sha256": DIGEST}])
    c.eq("the body", r["body"], {
        "forecast_id": "fc-1", "contract": "D", "pair_id": "A", "rule_version": "settle/1", "state": "SETTLED", "outcome": "1", "reason": None,
        "p0": "100.50", "outcome_price": "101.25", "touch_open_ms": None, "flags": []})  # fmt: skip
    c.eq("it reads back", mod.from_record(r), (s, DIGEST))
    c.eq("and through bytes", mod.from_record(canonical.parse_record(canonical.dumps(r))), (s, DIGEST))
    c.eq("a committed record is also read", mod.from_record({**r, "committed_at": "2026-10-26T07:33:21.000Z"}), (s, DIGEST))
    for label, s2 in {
        "a miss": make(outcome="0"),
        "a barrier target": make("B"),
        "a barrier stop": make("B", outcome="STOP"),
        "a barrier timeout": make("B", outcome="TIMEOUT", touch_open_ms=None),
        "an ambiguous minute": make("B", state=State.AMBIGUOUS, outcome="AMBIGUOUS", reason="SAME_CANDLE"),
        "a tie": make(state=State.VOID, outcome=None, reason="VOID_TIE"),
        "a delisting": void("fc-1", "D", "A", "VOID_DELISTED"),
        "a gap": void("fc-1", "B", "A", "VOID_GAP"),
        "a missing candle": make(state=State.UNSETTLEABLE, outcome=None, reason="UNSETTLEABLE_MISSING", p0=None, outcome_price=None),
        "disagreeing oracles": make(state=State.UNSETTLEABLE, outcome=None, reason="UNSETTLEABLE_DISAGREE"),
        "a flagged basis": make(flags=("BASIS_STRESS",)),
        "a barrier disagreement": make("B", state=State.UNSETTLEABLE, outcome=None, reason="UNSETTLEABLE_DISAGREE", touch_open_ms=None),
    }.items():
        if label == "a delisting" or label == "a gap":
            s2 = replace(s2, settlement_id=sid("fc-1"))
        try:
            got = mod.from_record(rec(mod, s2))
            c.eq(f"{label} round trips", got, (s2, DIGEST))
        except Exception as exc:
            c.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
    c.refuses(
        "a record made before the epoch", lambda: mod.to_record(s, DIGEST, SRC, -1), "the creation time is a whole number of milliseconds"
    )
    c.refuses("a bool time", lambda: mod.to_record(s, DIGEST, SRC, True), "the creation time is a whole number of milliseconds")
    mod.to_record(s, DIGEST, SRC, 0)
    mod.to_record(s, DIGEST, SRC, mod.MAX_INSTANT_MS)
    c.refuses(
        "past year 9999",
        lambda: mod.to_record(s, DIGEST, SRC, mod.MAX_INSTANT_MS + 1),
        "the creation time is a whole number of milliseconds",
    )
    c.refuses(
        "a source that is not a urn",
        lambda: mod.to_record(s, DIGEST, "x", AT),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    check = mod.check
    c.refuses("empty forecast id", lambda: check(make(forecast_id="")), "the forecast id is a non-empty string of at most 200 characters")
    c.refuses("contract P", lambda: check(make(contract="P")), "a settlement is of contract D or B")
    c.refuses("a bad settlement is not made into a record", lambda: rec(mod, make(contract="P")), "a settlement is of contract D or B")
    long = "x" * 200
    check(make(forecast_id=long, settlement_id=sid(long)))
    c.refuses(
        "a forecast id one character too long",
        lambda: check(make(forecast_id=long + "x", settlement_id=sid(long + "x"))),
        "the forecast id is a non-empty string of at most 200 characters",
    )
    c.refuses("pair C", lambda: check(make(pair_id="C")), f"the oracle pair is one of {('A', 'B')}")
    c.refuses("rule version", lambda: check(make(rule_version="settle/2")), "the rule version is settle/1")
    c.refuses(
        "pending",
        lambda: check(make(state=State.PENDING)),
        "only a settlement that ends a slot is recorded: settled, ambiguous, void or unsettleable",
    )
    c.refuses(
        "a foreign id", lambda: check(make(settlement_id="st-x")), "the settlement id is not the id of this forecast, pair and rule version"
    )
    pd = "is a plain decimal number written the way the settlement writes it"
    c.refuses("p0 not a decimal", lambda: check(make(p0="abc")), f"p0 {pd}")
    c.refuses("p0 exponent", lambda: check(make(p0="1E+2")), f"p0 {pd}")
    c.refuses("p0 nan", lambda: check(make(p0="NaN")), f"p0 {pd}")
    c.refuses("p0 inf", lambda: check(make(p0="Infinity")), f"p0 {pd}")
    c.refuses("p0 not text", lambda: check(make(p0=5)), f"p0 {pd}")
    c.refuses("price not plain", lambda: check(make(outcome_price="1e3")), f"the outcome price {pd}")
    c.refuses("touch negative", lambda: check(make("B", touch_open_ms=-1)), "the touch time is a whole number of milliseconds")
    c.refuses("touch bool", lambda: check(make("B", touch_open_ms=True)), "the touch time is a whole number of milliseconds")
    make("B", touch_open_ms=0)
    check(make("B", touch_open_ms=0))
    check(make("B", touch_open_ms=mod.MAX_INSTANT_MS))
    c.refuses(
        "touch past 9999",
        lambda: check(make("B", touch_open_ms=mod.MAX_INSTANT_MS + 1)),
        "the touch time is a whole number of milliseconds",
    )
    fl = "flags are some of ['BASIS_STRESS'], sorted and once each"
    c.refuses("unknown flag", lambda: check(make(flags=("X",))), fl)
    c.refuses("repeated flag", lambda: check(make(flags=("BASIS_STRESS", "BASIS_STRESS"))), fl)
    # settled
    sr = "a settled forecast has a reference price and no reason"
    c.refuses("settled with a reason", lambda: check(make(reason="VOID_TIE")), sr)
    c.refuses("settled without p0", lambda: check(make(p0=None)), sr)
    dr = "a directional outcome is 1 or 0 with the outcome price, and has no touch"
    c.refuses("directional outcome 2", lambda: check(make(outcome="2")), dr)
    c.refuses("directional without a price", lambda: check(make(outcome_price=None)), dr)
    c.refuses("directional with a touch", lambda: check(make(touch_open_ms=5)), dr)
    br = "a barrier outcome is one of ('TARGET', 'STOP', 'TIMEOUT'), with no outcome price"
    c.refuses("barrier outcome up", lambda: check(make("B", outcome="1")), br)
    c.refuses("barrier with a price", lambda: check(make("B", outcome_price="1.0")), br)
    c.refuses(
        "timeout with a touch",
        lambda: check(make("B", outcome="TIMEOUT")),
        "a barrier forecast that timed out has no touch, and one that reached a level has one",
    )
    c.refuses(
        "target without a touch",
        lambda: check(make("B", touch_open_ms=None)),
        "a barrier forecast that timed out has no touch, and one that reached a level has one",
    )
    # ambiguous
    am = "an ambiguous settlement is a barrier forecast whose minute touched both levels"
    amb = {"state": State.AMBIGUOUS, "outcome": "AMBIGUOUS", "reason": "SAME_CANDLE"}
    check(make("B", **amb))
    c.refuses("ambiguous directional", lambda: check(make("D", **amb)), am)
    c.refuses("ambiguous outcome", lambda: check(make("B", **{**amb, "outcome": "TARGET"})), am)
    c.refuses("ambiguous reason", lambda: check(make("B", **{**amb, "reason": "X"})), am)
    c.refuses("ambiguous without p0", lambda: check(make("B", p0=None, **amb)), am)
    c.refuses("ambiguous without touch", lambda: check(make("B", touch_open_ms=None, **amb)), am)
    c.refuses("ambiguous with a price", lambda: check(make("B", outcome_price="1.0", **amb)), "a barrier settlement has no outcome price")
    # void and unsettleable
    vo = "a void or unsettleable settlement has no outcome and no touch"
    c.refuses("void with an outcome", lambda: check(make(state=State.VOID, outcome="1", reason="VOID_TIE")), vo)
    c.refuses("void with a touch", lambda: check(make("B", state=State.VOID, outcome=None, reason="VOID_GAP", touch_open_ms=1)), vo)
    c.refuses(
        "void with a bad reason",
        lambda: check(make(state=State.VOID, outcome=None, reason="X")),
        f"a void settlement has a reason from {sorted(mod.VOID_REASONS)}",
    )
    c.refuses(
        "void with no reason",
        lambda: check(make(state=State.VOID, outcome=None, reason=None)),
        f"a void settlement has a reason from {sorted(mod.VOID_REASONS)}",
    )
    c.refuses(
        "unsettleable with a void reason",
        lambda: check(make(state=State.UNSETTLEABLE, outcome=None, reason="VOID_TIE")),
        f"an unsettleable settlement has a reason from {sorted(mod.UNSETTLEABLE_REASONS)}",
    )
    c.refuses(
        "barrier void with a price",
        lambda: check(make("B", state=State.VOID, outcome=None, reason="VOID_GAP", touch_open_ms=None, outcome_price="1.0")),
        "a barrier settlement has no outcome price",
    )
    # a tie is a result of a directional forecast only: a barrier forecast has no tie (contract section 3)
    tie = "only a directional forecast can end in a tie"
    barrier_tie = make("B", state=State.VOID, outcome=None, reason="VOID_TIE", touch_open_ms=None)
    c.refuses("a barrier forecast in a tie", lambda: check(barrier_tie), tie)
    c.refuses("a barrier tie is not made into a record", lambda: rec(mod, barrier_tie), tie)
    check(make(state=State.VOID, outcome=None, reason="VOID_TIE"))
    check(make("B", state=State.VOID, outcome=None, reason="VOID_GAP", touch_open_ms=None))
    return c.bad


def check_reading(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = rec(mod, make())

    def body(**changes: Any) -> dict[str, Any]:
        return {**r, "body": {**r["body"], **changes}}

    c.refuses(
        "not a settlement", lambda: mod.from_record({**r, "type": "forecast"}), "not a settlement record of a version this reader knows"
    )
    c.refuses(
        "another version", lambda: mod.from_record({**r, "type_version": 2}), "not a settlement record of a version this reader knows"
    )
    c.refuses(
        "a bad envelope",
        lambda: mod.from_record({k: v for k, v in r.items() if k != "source"}),
        "the envelope: missing envelope members: ['source']",
    )
    c.refuses(
        "a member short",
        lambda: mod.from_record({**r, "body": {k: v for k, v in r["body"].items() if k != "reason"}}),
        "the body has the members ['reason'] wrong",
    )
    c.refuses("a member over", lambda: mod.from_record(body(extra=1)), "the body has the members ['extra'] wrong")
    c.refuses("no reference", lambda: mod.from_record({**r, "refs": []}), "a settlement names exactly the forecast it settles")
    c.refuses("two references", lambda: mod.from_record({**r, "refs": r["refs"] * 2}), "a settlement names exactly the forecast it settles")
    c.refuses(
        "another forecast referenced",
        lambda: mod.from_record({**r, "refs": [{"id": "fc-2", "sha256": DIGEST}]}),
        "a settlement names exactly the forecast it settles",
    )
    c.refuses("an unknown state", lambda: mod.from_record(body(state="LOST")), "the state is not a settlement state")
    c.refuses("a state that is not text", lambda: mod.from_record(body(state=3)), "the state is not a settlement state")
    c.refuses("flags not a list", lambda: mod.from_record(body(flags="x")), "flags are a list of strings")
    c.refuses("flags of numbers", lambda: mod.from_record(body(flags=[1])), "flags are a list of strings")
    c.refuses("touch a string", lambda: mod.from_record(body(touch_open_ms="1")), "the touch time is a whole number or null")
    c.refuses("touch a bool", lambda: mod.from_record(body(touch_open_ms=True)), "the touch time is a whole number or null")
    c.refuses("forecast id a number", lambda: mod.from_record(body(forecast_id=1)), "forecast_id is a string")
    c.refuses("contract a number", lambda: mod.from_record(body(contract=1)), "contract is a string")
    c.refuses("pair a number", lambda: mod.from_record(body(pair_id=1)), "pair_id is a string")
    c.refuses("rule version a number", lambda: mod.from_record(body(rule_version=1)), "rule_version is a string")
    c.refuses("outcome a number", lambda: mod.from_record(body(outcome=1)), "outcome is a string or null")
    c.refuses("reason a number", lambda: mod.from_record(body(reason=1)), "reason is a string or null")
    c.refuses("p0 a number", lambda: mod.from_record(body(p0=1)), "p0 is a string or null")
    c.refuses("price a number", lambda: mod.from_record(body(outcome_price=1)), "outcome_price is a string or null")
    c.refuses(
        "an id that is not the body's",
        lambda: mod.from_record({**r, "id": sid("other")}),
        "the settlement id is not the id of this forecast, pair and rule version",
    )
    c.refuses(
        "a state that disagrees with its outcome",
        lambda: mod.from_record(body(state="VOID")),
        "a void or unsettleable settlement has no outcome and no touch",
    )
    tied = rec(mod, make(state=State.VOID, outcome=None, reason="VOID_TIE"))
    c.eq("a directional tie is read", mod.from_record(tied)[0].reason, "VOID_TIE")
    c.refuses(
        "a tie of a barrier forecast is not read",
        lambda: mod.from_record({**tied, "body": {**tied["body"], "contract": "B", "outcome_price": None}}),
        "only a directional forecast can end in a tie",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_record, check_refusals, check_reading):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
