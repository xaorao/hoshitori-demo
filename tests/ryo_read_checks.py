"""Scenarios for arm R's reads (``hoshitori.ryo_read``), runnable against the real module or a mutant of it.

Every expected value is worked out by hand from the reading table and the rules in the module's docstring (``ARM_R_SPEC``).
"""

from __future__ import annotations

import copy
import json
import types
from collections.abc import Callable
from typing import Any

SOURCE = "urn:hoshitori:test"
AT = 1_794_157_200_000


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
        except self.mod.RyoReadError:
            return
        except Exception as exc:  # the wrong kind of refusal is a defect too
            self.bad.append(f"{label}: raised {type(exc).__name__}, not RyoReadError")
            return
        self.bad.append(f"{label}: accepted")


def answer(plan: object = "default", availability: str = "available", call: str = "cautious", symbol: str = "SOL") -> bytes:
    """A REST answer of deep_analysis in the shape RYO sends (the recorded answer's structure, values chosen by hand)."""
    trade_plan = {"entry": 100.448, "stop": 94.7376, "targets": [106.1585, 111.869], "preview_only": True} if plan == "default" else plan
    data: dict[str, Any] = {"asset": {"symbol": symbol}, "verdict": {"call": call}}
    if trade_plan is not None:
        data["trade_plan"] = trade_plan
    result = {
        "schema_version": "1.0",
        "tool": "deep_analysis",
        "status": "ok",
        "data_mode": "live",
        "as_of": "2026-11-08T12:00:00Z",
        "request": {"symbol": symbol},
        "data": data,
        "summary": {"headline": "SOL deep analysis: cautious.", "key_points": []},
        "availability": {"trade_plan": availability},
        "warnings": [],
    }
    return json.dumps({"tool": "deep_analysis", "result": result, "latency_ms": 120}).encode()


def body(mod: types.ModuleType, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "cycle_id": "cyabc",
        "candidate_id": "cdxyz",
        "symbol": "SOL",
        "call": "cautious",
        "plan": mod.Plan("100.448", "94.7376", "106.1585"),
        "plan_availability": "available",
        "evidence_id": "ev1",
        "capture_id": "cap-1",
    }
    args.update(kw)
    return dict(mod.body_of(**args))


def check_the_table(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    want = {
        "accumulate": "UP",
        "bullish": "UP",
        "constructive": "UP",
        "positive": "UP",
        "bearish": "DOWN",
        "negative": "DOWN",
        "avoid": "NOT_LONG",
        "cautious": "NOT_LONG",
        "neutral": "NONE",
    }
    for word, reading in want.items():
        c.eq(f"{word} is read as {reading}", mod.reading_of(word), reading)
    c.eq("the table holds those words and no other", dict(mod.READINGS), want)
    c.eq("case and spaces around the word do not matter", mod.reading_of("  Cautious "), "NOT_LONG")
    c.eq("a word the table does not know is unread", mod.reading_of("strong buy"), "UNREAD")
    c.eq("the table has a version", mod.TABLE_VERSION, "ryo-reading-1")
    c.eq("the kinds", mod.KINDS, ("UP", "DOWN", "NOT_LONG", "NONE", "UNREAD"))
    c.eq("the scored kinds", mod.SCORED, ("UP", "DOWN", "NOT_LONG"))
    c.eq("every kind has words for the page", sorted(mod.READING_WORDS), sorted(mod.KINDS))
    c.eq("the words on the page", dict(mod.READING_WORDS), {
        "UP": "read as up", "DOWN": "read as down", "NOT_LONG": "read as advice against buying", "NONE": "no direction",
        "UNREAD": "a word our table does not read",
    })  # fmt: skip
    c.eq("the longest call RYO may give", mod.MAX_WORD, 64)
    frozen = mod.Plan("10", "9", "11")
    try:
        frozen.entry = "12"
        c.bad.append("a plan can be changed after it is read")
    except AttributeError:
        pass
    c.eq("a read is for one day", (mod.HORIZON_HOURS, mod.TOOL, mod.TYPE, mod.TYPE_VERSION), (24, "deep_analysis", "ryo_read", 1))
    return c.bad


def check_hits(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    table = {
        ("UP", "ROSE"): True,
        ("UP", "FELL"): False,
        ("DOWN", "FELL"): True,
        ("DOWN", "ROSE"): False,
        ("NOT_LONG", "FELL"): True,
        ("NOT_LONG", "ROSE"): False,
    }
    for (reading, direction), want in table.items():
        c.eq(f"{reading} after it {direction}", mod.hit(reading, direction), want)
    for reading in ("UP", "DOWN", "NOT_LONG", "NONE", "UNREAD"):
        c.eq(f"{reading} after a tie is not scored", mod.hit(reading, "TIE"), None)
    for reading in ("NONE", "UNREAD"):
        for direction in ("ROSE", "FELL"):
            c.eq(f"{reading} is never scored ({direction})", mod.hit(reading, direction), None)
    c.raises("a reading not in the table", lambda: mod.hit("SIDEWAYS", "ROSE"))
    c.raises("a direction not in the list", lambda: mod.hit("UP", "FLAT"))
    return c.bad


def check_the_plan(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("a plan as RYO sends it", mod.plan_from(answer()), (mod.Plan("100.448", "94.7376", "106.1585"), "available"))
    c.eq("no plan, with RYO's word", mod.plan_from(answer(plan=None, availability="unavailable")), (None, "unavailable"))
    c.eq("a plan that is not an object", mod.plan_from(answer(plan="soon", availability="partial")), (None, "partial"))
    bad_orders = [
        {"entry": 100, "stop": 101, "targets": [110]},  # stop above entry
        {"entry": 100, "stop": 90, "targets": [99]},  # target below entry
        {"entry": 100, "stop": 100, "targets": [110]},  # stop at entry
        {"entry": 100, "stop": 90, "targets": [100]},  # target at entry
    ]
    for plan in bad_orders:
        c.eq(f"a plan out of order is malformed: {plan}", mod.plan_from(answer(plan=plan)), (None, "malformed"))
    missing = [
        {"stop": 90, "targets": [110]},
        {"entry": 100, "targets": [110]},
        {"entry": 100, "stop": 90},
        {"entry": 100, "stop": 90, "targets": []},
        {"entry": 100, "stop": 90, "targets": "110"},
        {"entry": "100", "stop": 90, "targets": [110]},
        {"entry": 100, "stop": 0, "targets": [110]},
        {"entry": 100, "stop": -5, "targets": [110]},
    ]
    for plan in missing:
        c.eq(
            f"a plan with a part missing or not a plain number is malformed: {plan}", mod.plan_from(answer(plan=plan)), (None, "malformed")
        )
    c.eq(
        "a malformed plan keeps RYO's word when it is not available",
        mod.plan_from(answer(plan={"entry": 1}, availability="partial")),
        (None, "partial"),
    )
    c.eq(
        "the first target is used", mod.plan_from(answer(plan={"entry": 10, "stop": 9, "targets": [11, 12]}))[0], mod.Plan("10", "9", "11")
    )
    exponent = answer(plan={"entry": 100, "stop": 90, "targets": [110]}).replace(b'"entry": 100', b'"entry": 1e2')
    c.eq("a number in exponent form is not a plain decimal", mod.plan_from(exponent), (None, "malformed"))
    c.eq("an answer that is not readable", mod.plan_from(b"not json"), (None, "unreadable"))
    c.eq(
        "a price below one is a price",
        mod.plan_from(answer(plan={"entry": 0.8, "stop": 0.5, "targets": [1.2]}))[0],
        mod.Plan("0.8", "0.5", "1.2"),
    )
    no_word = answer(plan=None).replace(b'"availability": {"trade_plan": "available"}', b'"availability": {}')
    c.eq("no availability word is absent", mod.plan_from(no_word), (None, "absent"))
    c.eq(
        "an answer of another tool is not readable",
        mod.plan_from(answer().replace(b'"tool": "deep_analysis", "result"', b'"tool": "analyze_token", "result"')),
        (None, "unreadable"),
    )
    return c.bad


def check_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    b = body(mod)
    c.eq("the body", b, {
        "cycle_id": "cyabc", "candidate_id": "cdxyz", "symbol": "SOL", "horizon_hours": 24, "tool": "deep_analysis", "call": "cautious",
        "reading": "NOT_LONG", "table_version": "ryo-reading-1", "plan": {"entry": "100.448", "stop": "94.7376", "target": "106.1585"},
        "plan_availability": "available", "evidence_id": "ev1", "capture_id": "cap-1",
    })  # fmt: skip
    c.eq("no plan is kept as none", body(mod, plan=None, plan_availability="unavailable")["plan"], None)
    record = mod.to_record(b, SOURCE, AT)
    c.eq("the record's id is the cycle and the candidate", record["id"], mod.record_id("cyabc", "cdxyz"))
    c.eq("the id's form is fixed", mod.record_id("cyabc", "cdxyz"), "rrg7gbespgg7vxumxgcftbrxjrau")
    c.eq("the same cycle and candidate is the same id", mod.record_id("cyabc", "cdxyz") == mod.record_id("cyabc", "cdother"), False)
    c.eq("the record's type", (record["type"], record["type_version"], record["privacy"]), ("ryo_read", 1, "public"))
    c.eq("it reads back", mod.from_record(record), b)
    c.eq("a committed time is allowed on reading", mod.from_record({**record, "committed_at": "2026-11-08T12:00:00.000Z"}), b)

    def edited(change: Callable[[dict[str, Any]], None]) -> Callable[[], object]:
        def go() -> object:
            r = copy.deepcopy(record)
            change(r)
            return mod.from_record(r)

        return go

    c.raises("a record of another type", edited(lambda r: r.update(type="forecast")))
    c.raises("a record of another version", edited(lambda r: r.update(type_version=2)))
    c.raises("an id that is not the id of the body", edited(lambda r: r.update(id=mod.record_id("cyabc", "cdother"))))
    c.raises("a reading that is not the table's", edited(lambda r: r["body"].update(reading="UP")))
    c.raises("another table", edited(lambda r: r["body"].update(table_version="ryo-reading-2")))
    c.raises("another horizon", edited(lambda r: r["body"].update(horizon_hours=4)))
    c.raises("a horizon that is true", edited(lambda r: r["body"].update(horizon_hours=True)))
    c.raises("another tool", edited(lambda r: r["body"].update(tool="analyze_token")))
    c.raises("a member too many", edited(lambda r: r["body"].update(extra=1)))
    c.raises("a member missing", edited(lambda r: r["body"].pop("capture_id")))
    c.raises("an empty call", edited(lambda r: r["body"].update(call="", reading="UNREAD")))
    c.raises("a call too long", edited(lambda r: r["body"].update(call="x" * 65, reading="UNREAD")))
    c.raises("a call that is not text", edited(lambda r: r["body"].update(call=7)))
    c.raises("a call with a control character", edited(lambda r: r["body"].update(call="cau\ntious", reading="UNREAD")))
    c.raises("an envelope the reader refuses", edited(lambda r: r.update(privacy="secret")))
    c.raises("an envelope without its spec", edited(lambda r: r.pop("spec")))
    c.eq("a call of the longest length", body(mod, call="x" * 64)["reading"], "UNREAD")
    c.eq("a call of one letter", body(mod, call="x")["reading"], "UNREAD")
    c.eq("a symbol of the longest length", body(mod, symbol="A" * 20)["symbol"], "A" * 20)
    c.eq("a symbol of one letter", body(mod, symbol="X")["symbol"], "X")
    c.raises("the writer checks the body", lambda: body(mod, symbol="S OL"))
    small = body(mod, plan=mod.Plan("0.8", "0.5", "1.2"))["plan"]
    c.eq("a plan below one is recorded", small, {"entry": "0.8", "stop": "0.5", "target": "1.2"})
    c.raises("a symbol with a space", edited(lambda r: r["body"].update(symbol="S OL")))
    c.raises("a symbol too long", edited(lambda r: r["body"].update(symbol="A" * 21)))
    c.raises("an empty symbol", edited(lambda r: r["body"].update(symbol="")))
    c.raises("an id that is not an identifier", edited(lambda r: r["body"].update(evidence_id="ev 1")))
    c.raises("an availability that is not a word", edited(lambda r: r["body"].update(plan_availability="Available!")))
    c.raises("a plan with a member too many", edited(lambda r: r["body"]["plan"].update(size="1")))
    c.raises("a plan that is not an object", edited(lambda r: r["body"].update(plan="100")))
    c.raises("a plan with a number that is not plain", edited(lambda r: r["body"]["plan"].update(entry="1e2")))
    c.raises("a plan with a zero", edited(lambda r: r["body"]["plan"].update(stop="0")))
    c.raises("a plan out of order", edited(lambda r: r["body"]["plan"].update(stop="200")))
    c.raises("a plan whose stop is its entry", edited(lambda r: r["body"]["plan"].update(stop="100.448")))
    c.raises("a plan whose target is not above its entry", edited(lambda r: r["body"]["plan"].update(target="100.448")))
    c.raises("a creation time that is negative", lambda: mod.to_record(b, SOURCE, -1))
    c.raises("a creation time that is true", lambda: mod.to_record(b, SOURCE, True))
    c.raises("a creation time past the last instant", lambda: mod.to_record(b, SOURCE, mod.MAX_INSTANT_MS + 1))
    c.eq("the first instant is allowed", mod.to_record(b, SOURCE, 0)["created_at"], "1970-01-01T00:00:00.000Z")
    c.eq("the last instant is allowed", mod.to_record(b, SOURCE, mod.MAX_INSTANT_MS)["type"], "ryo_read")
    c.raises("a source the envelope refuses", lambda: mod.to_record(b, "", AT))
    c.raises("a body the writer refuses", lambda: mod.to_record({**b, "reading": "UP"}, SOURCE, AT))
    return c.bad


def check_the_read(mod: types.ModuleType) -> list[str]:
    """``read_of`` keeps an accepted answer's verdict word and plan; ``Read.body`` is the checked body of that read in a cycle."""
    c = Case(mod)
    got = mod.read_of("cdxyz", "SOL", "ev1", "  cautious ", "cap-1", answer())
    want = mod.Read("cdxyz", "SOL", "cautious", mod.Plan("100.448", "94.7376", "106.1585"), "available", "ev1", "cap-1")
    c.eq("an answer with a verdict word is read, the word trimmed and the plan taken from the answer", got, want)
    no_plan = mod.read_of("cdxyz", "SOL", "ev1", "bullish", "cap-1", answer(plan=None, availability="unavailable"))
    c.eq(
        "an answer without a plan is read with RYO's own word for it",
        no_plan,
        mod.Read("cdxyz", "SOL", "bullish", None, "unavailable", "ev1", "cap-1"),
    )
    for call in (None, 7, "", "   "):
        c.eq(f"no verdict word, nothing is read: {call!r}", mod.read_of("cdxyz", "SOL", "ev1", call, "cap-1", answer()), None)
    c.eq("the body of a read in a cycle is the checked body", got.body("cyabc") if got is not None else None, body(mod))
    c.eq(
        "the body of a read without a plan",
        no_plan.body("cyabc") if no_plan is not None else None,
        body(mod, call="bullish", plan=None, plan_availability="unavailable"),
    )
    try:
        setattr(want, "call", "moon")  # noqa: B010 - the point is to assign to a frozen member by name
    except AttributeError:  # dataclasses.FrozenInstanceError is an AttributeError
        pass
    else:
        c.bad.append("a read can be changed after it is made")
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "table": check_the_table,
    "hits": check_hits,
    "plan": check_the_plan,
    "records": check_records,
    "read": check_the_read,
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
