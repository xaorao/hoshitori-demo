"""Checks of the practice record (``hoshitori.practice_record``), runnable against a mutant."""

from __future__ import annotations

import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from hoshitori import book, canonical, envelope
from hoshitori.ids import natural_id

D = Decimal
DIGEST = "ab" * 32
SOURCE = "urn:hoshitori:league:test"
NAME = "L0:BASE"
OPEN = book.Open("L0", "BASE", D("100000"), 1_000)
BUY = book.Buy("BTC", D("0.5"), D("10000"), D("5"), 2_000)
SELL = book.Sell("BTC", D("0.5"), D("10100"), D("5"), 3_000)
MARK = book.Mark((("BTC", D("10050")),), 2_500)


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
        except self.mod.PracticeRecordError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("type and version", (mod.TYPE, mod.TYPE_VERSION), ("practice", 1))
    c.eq("Entry is frozen", mod.Entry.__dataclass_params__.frozen, True)
    c.eq("a book is named by arm and level", mod.book_id("L0", "STRESS"), "L0:STRESS")
    return c.bad


def check_ids(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("an opening is one per book", mod.record_id(NAME, None, OPEN), natural_id("pr", {"book": NAME, "forecast": None, "kind": "open"}))
    c.eq(
        "a purchase is one per book and forecast",
        mod.record_id(NAME, "fc1", BUY),
        natural_id("pr", {"book": NAME, "forecast": "fc1", "kind": "buy"}),
    )
    c.eq("and a sale", mod.record_id(NAME, "fc1", SELL), natural_id("pr", {"book": NAME, "forecast": "fc1", "kind": "sell"}))
    c.eq(
        "a mark is one per book and moment",
        mod.record_id(NAME, None, MARK),
        natural_id("pr", {"book": NAME, "forecast": None, "kind": "mark", "at_ms": 2_500}),
    )
    ids = {
        mod.record_id(NAME, "fc1", BUY),
        mod.record_id(NAME, "fc2", BUY),
        mod.record_id("L0:STRESS", "fc1", BUY),
        mod.record_id(NAME, "fc1", SELL),
    }
    c.eq("another forecast, another book or another kind is another id", len(ids), 4)
    c.eq(
        "the same purchase at another time or price is the same id",
        mod.record_id(NAME, "fc1", book.Buy("BTC", D(1), D(1), D(0), 9)),
        mod.record_id(NAME, "fc1", BUY),
    )
    c.eq(
        "a mark at another moment is another id",
        mod.record_id(NAME, None, book.Mark((), 9)) != mod.record_id(NAME, None, book.Mark((), 10)),
        True,
    )
    return c.bad


def check_roundtrip(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for label, event, forecast in (
        ("opening", OPEN, None),
        ("purchase", BUY, ("fc1", DIGEST)),
        ("sale", SELL, ("fc1", DIGEST)),
        ("mark", MARK, None),
    ):
        record = mod.to_record(NAME, event, forecast, SOURCE, 5_000)
        envelope.validate_producer_record(record)
        read = mod.from_record(canonical.parse_record(canonical.dumps(record)))
        c.eq(
            f"the {label} reads back whole",
            read,
            mod.Entry(NAME, None if forecast is None else forecast[0], None if forecast is None else forecast[1], event),
        )
        c.eq(
            f"the {label} record: type, source, privacy, time",
            (record["type"], record["source"], record["privacy"], record["created_at"]),
            ("practice", SOURCE, "public", "1970-01-01T00:00:05.000Z"),
        )
        c.eq(
            f"the {label} names the forecast it follows, or none",
            record["refs"],
            [] if forecast is None else [{"id": "fc1", "sha256": DIGEST}],
        )
    record = mod.to_record(NAME, BUY, ("fc1", DIGEST), SOURCE, 5_000)
    committed = {**record, "committed_at": "1970-01-01T00:00:06.000Z"}  # as the ledger returns it
    c.eq("a record as the ledger returns it, with its commit time, reads the same", mod.from_record(committed), mod.from_record(record))
    c.eq("the body", record["body"], {"book": NAME, "forecast_id": "fc1", "event": book.to_body(BUY)})
    c.eq("the id is the id of the content", record["id"], mod.record_id(NAME, "fc1", BUY))
    return c.bad


def check_details(mod: types.ModuleType) -> list[str]:
    """How a trade was filled is part of its record (contract section 4 says it is stored per trade): the bar it was
    taken from, why and where a sale was decided, the latency in force and where the holding began. A trade with details is version 2; an
    earlier record, with none, still reads."""
    c = Case(mod)
    buy_details = mod.Details(None, 120_000, None, 15_000, None)
    sell_details = mod.Details("STOP", 300_000, 240_000, 15_000, 120_000)
    c.eq("the version that carries details", mod.DETAILS_VERSION, 2)
    for label, event, details in (("purchase", BUY, buy_details), ("sale", SELL, sell_details)):
        record = mod.to_record(NAME, event, ("fc1", DIGEST), SOURCE, 5_000, details)
        envelope.validate_producer_record(record)
        c.eq(f"the {label} with details is version 2", record["type_version"], 2)
        c.eq(f"{label}: its id is the id of the same event without them", record["id"], mod.record_id(NAME, "fc1", event))
        read = mod.from_record(canonical.parse_record(canonical.dumps(record)))
        c.eq(f"the {label} reads back with its details", read, mod.Entry(NAME, "fc1", DIGEST, event, details))
    record = mod.to_record(NAME, SELL, ("fc1", DIGEST), SOURCE, 5_000, sell_details)
    c.eq(
        "the sale's body names every detail",
        record["body"]["fill"],  # type: ignore[index]
        {"exit_kind": "STOP", "fill_bar_ms": 300_000, "held_from_ms": 120_000, "latency_ms": 15_000, "signal_bar_ms": 240_000},
    )
    plain = mod.to_record(NAME, BUY, ("fc1", DIGEST), SOURCE, 5_000)
    c.eq("a trade without details is still version 1, as written before", (plain["type_version"], "fill" in plain["body"]), (1, False))  # type: ignore[operator]
    c.eq("and reads back with none", mod.from_record(plain).details, None)
    for kind in ("TIME", "TARGET", "GAP_EXIT"):
        got = mod.to_record(NAME, SELL, ("fc1", DIGEST), SOURCE, 5_000, mod.Details(kind, 300_000, None, 0, 120_000))
        c.eq(f"a sale of kind {kind} is recorded", mod.from_record(got).details.exit_kind, kind)  # type: ignore[union-attr]
    for label, event, forecast, details, says in (
        ("details on an opening", OPEN, None, buy_details, "an opening and a mark carry no fill details"),
        ("details on a mark", MARK, None, buy_details, "an opening and a mark carry no fill details"),
        (
            "a purchase that names an exit kind",
            BUY,
            ("fc1", DIGEST),
            mod.Details("STOP", 1, None, 0, None),
            "a purchase has no exit kind, signal bar or holding start",
        ),
        (
            "a purchase that names a signal bar",
            BUY,
            ("fc1", DIGEST),
            mod.Details(None, 1, 0, 0, None),
            "a purchase has no exit kind, signal bar or holding start",
        ),
        (
            "a sale without an exit kind",
            SELL,
            ("fc1", DIGEST),
            mod.Details(None, 1, None, 0, 0),
            "a sale names its exit kind and where the holding began",
        ),
        (
            "a sale without a holding start",
            SELL,
            ("fc1", DIGEST),
            mod.Details("TIME", 1, None, 0, None),
            "a sale names its exit kind and where the holding began",
        ),
        (
            "an exit kind that is not one",
            SELL,
            ("fc1", DIGEST),
            mod.Details("PANIC", 1, None, 0, 0),
            "an exit kind is one of ['STOP', 'TARGET', 'TIME', 'GAP_EXIT']",
        ),
        (
            "a bar before the epoch",
            BUY,
            ("fc1", DIGEST),
            mod.Details(None, -60_000, None, 0, None),
            "a time in the details is a whole number of milliseconds",
        ),
        (
            "a time that is a boolean",
            BUY,
            ("fc1", DIGEST),
            mod.Details(None, True, None, 0, None),
            "a time in the details is a whole number of milliseconds",
        ),
        (
            "a latency below zero",
            BUY,
            ("fc1", DIGEST),
            mod.Details(None, 0, None, -1, None),
            "the latency is a whole number of milliseconds, zero or more",
        ),
    ):
        c.refuses(label, lambda e=event, f=forecast, d=details: mod.to_record(NAME, e, f, SOURCE, 1, d), says)
    good = canonical.parse_record(canonical.dumps(mod.to_record(NAME, SELL, ("fc1", DIGEST), SOURCE, 5_000, sell_details)))
    no_fill = {**good, "body": {k: v for k, v in good["body"].items() if k != "fill"}}  # type: ignore[union-attr]
    c.refuses(
        "a version 2 record without its details", lambda: mod.from_record(no_fill), "a record of the details version names its fill details"
    )
    old = {**canonical.parse_record(canonical.dumps(plain)), "body": {**plain["body"], "fill": good["body"]["fill"]}}  # type: ignore[dict-item,index]
    c.refuses(
        "a version 1 record that carries details",
        lambda: mod.from_record(old),
        f"the body has the members {sorted({'book', 'forecast_id', 'event'})}",
    )
    c.eq("the details are frozen", mod.Details.__dataclass_params__.frozen, True)
    when = "a time in the details is a whole number of milliseconds"
    c.refuses(
        "a sale with a signal bar before the epoch",
        lambda: mod.to_record(NAME, SELL, ("fc1", DIGEST), SOURCE, 1, mod.Details("STOP", 1, -1, 0, 1)),
        when,
    )
    c.refuses(
        "a sale with a holding start before the epoch",
        lambda: mod.to_record(NAME, SELL, ("fc1", DIGEST), SOURCE, 1, mod.Details("TIME", 1, None, 0, -1)),
        when,
    )
    c.refuses(
        "a sale with a holding start that is a boolean",
        lambda: mod.to_record(NAME, SELL, ("fc1", DIGEST), SOURCE, 1, mod.Details("TIME", 1, None, 0, True)),
        when,
    )
    shaped = "the fill details have the members " + str(sorted({"exit_kind", "fill_bar_ms", "held_from_ms", "latency_ms", "signal_bar_ms"}))

    def fill_of(change: Callable[[Any], Any]) -> dict[str, Any]:
        record = canonical.parse_record(canonical.dumps(mod.to_record(NAME, SELL, ("fc1", DIGEST), SOURCE, 5_000, sell_details)))
        record["body"]["fill"] = change(record["body"]["fill"])  # type: ignore[index]
        return record  # type: ignore[return-value]

    c.refuses("a fill that is not an object", lambda: mod.from_record(fill_of(lambda f: "x")), shaped)
    c.refuses("a fill with a member more", lambda: mod.from_record(fill_of(lambda f: {**f, "note": "x"})), shaped)
    c.refuses(
        "a fill with a member less", lambda: mod.from_record(fill_of(lambda f: {k: v for k, v in f.items() if k != "latency_ms"})), shaped
    )
    c.refuses(
        "a recorded sale with no exit kind",
        lambda: mod.from_record(fill_of(lambda f: {**f, "exit_kind": None})),
        "a sale names its exit kind and where the holding began",
    )
    c.refuses(
        "a recorded sale with an exit kind that is not one",
        lambda: mod.from_record(fill_of(lambda f: {**f, "exit_kind": "PANIC"})),
        "an exit kind is one of ['STOP', 'TARGET', 'TIME', 'GAP_EXIT']",
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    follows = "a purchase and a sale name the forecast they follow; an opening and a mark name none"
    c.refuses("a purchase with no forecast", lambda: mod.to_record(NAME, BUY, None, SOURCE, 1), follows)
    c.refuses("a sale with no forecast", lambda: mod.to_record(NAME, SELL, None, SOURCE, 1), follows)
    c.refuses("an opening with a forecast", lambda: mod.to_record(NAME, OPEN, ("fc1", DIGEST), SOURCE, 1), follows)
    c.refuses("a mark with a forecast", lambda: mod.to_record(NAME, MARK, ("fc1", DIGEST), SOURCE, 1), follows)
    c.refuses("no book name", lambda: mod.to_record("", OPEN, None, SOURCE, 1), "the book is named in at most 200 characters")
    c.refuses(
        "a book name too long", lambda: mod.to_record("x" * 201, OPEN, None, SOURCE, 1), "the book is named in at most 200 characters"
    )
    c.eq("a book name of 200 characters is allowed", mod.to_record("x" * 200, OPEN, None, SOURCE, 1)["body"]["book"], "x" * 200)  # type: ignore[index]
    when = "the creation time is a whole number of milliseconds"
    c.refuses("a time before the epoch", lambda: mod.to_record(NAME, OPEN, None, SOURCE, -1), when)
    c.refuses("a time that is a boolean", lambda: mod.to_record(NAME, OPEN, None, SOURCE, True), when)
    c.refuses("a time after the year 9999", lambda: mod.to_record(NAME, OPEN, None, SOURCE, 253_402_300_800_000), when)
    c.eq(
        "the last instant is allowed",
        mod.to_record(NAME, OPEN, None, SOURCE, 253_402_300_799_999)["created_at"],
        "9999-12-31T23:59:59.999Z",
    )
    c.eq("time zero is allowed", mod.to_record(NAME, OPEN, None, SOURCE, 0)["created_at"], "1970-01-01T00:00:00.000Z")
    c.refuses(
        "a source that is not a urn",
        lambda: mod.to_record(NAME, OPEN, None, "league", 1),
        "the record does not satisfy the envelope: " + _env_why(mod, "league"),
    )
    good = mod.to_record(NAME, BUY, ("fc1", DIGEST), SOURCE, 5_000)

    def tampered(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        copy = canonical.parse_record(canonical.dumps(good))
        change(copy)  # type: ignore[arg-type]
        return copy  # type: ignore[return-value]

    c.refuses(
        "another type",
        lambda: mod.from_record(tampered(lambda r: r.update(type="settlement"))),
        "not a practice record of a version this reader knows",
    )
    c.refuses(
        "another version",
        lambda: mod.from_record(tampered(lambda r: r.update(type_version=3))),
        "not a practice record of a version this reader knows",
    )
    c.refuses(
        "a member short",
        lambda: mod.from_record(tampered(lambda r: r["body"].pop("book"))),
        "the body has the members ['book', 'event', 'forecast_id']",
    )
    c.refuses(
        "a book that is not text",
        lambda: mod.from_record(tampered(lambda r: r["body"].update(book=5))),
        "the book and the forecast are text, and the event an object",
    )
    c.refuses(
        "a forecast that is not text",
        lambda: mod.from_record(tampered(lambda r: r["body"].update(forecast_id=5))),
        "the book and the forecast are text, and the event an object",
    )
    c.refuses(
        "an event that is not an object",
        lambda: mod.from_record(tampered(lambda r: r["body"].update(event=[]))),
        "the book and the forecast are text, and the event an object",
    )
    c.refuses(
        "an event that is not one",
        lambda: mod.from_record(tampered(lambda r: r["body"].update(event={"event": "gift"}))),
        "the event: a book event is an open, a buy, a sell or a mark with its members",
    )
    c.refuses(
        "no forecast named in refs",
        lambda: mod.from_record(tampered(lambda r: r.update(refs=[]))),
        "a trade names exactly the forecast it follows",
    )
    c.refuses(
        "another forecast in refs",
        lambda: mod.from_record(tampered(lambda r: r.update(refs=[{"id": "fc9", "sha256": DIGEST}]))),
        "a trade names exactly the forecast it follows",
    )
    c.refuses(
        "two forecasts in refs",
        lambda: mod.from_record(tampered(lambda r: r.update(refs=[{"id": "fc1", "sha256": DIGEST}, {"id": "fc1", "sha256": DIGEST}]))),
        "a trade names exactly the forecast it follows",
    )
    c.refuses(
        "an id that is not the content's",
        lambda: mod.from_record(tampered(lambda r: r.update(id="prAAAAAAAAAAAAAAAAAAAAAAAAAA"))),
        "the id is not the id of what the record says",
    )
    opening = mod.to_record(NAME, OPEN, None, SOURCE, 5_000)
    stray = canonical.parse_record(canonical.dumps(opening))
    stray["refs"] = [{"id": "fc1", "sha256": DIGEST}]  # type: ignore[index]
    c.refuses("an opening that names a forecast in refs", lambda: mod.from_record(stray), "an event that follows no forecast names none")  # type: ignore[arg-type]
    unfollowed = canonical.parse_record(canonical.dumps(good))
    unfollowed["body"]["forecast_id"] = None  # type: ignore[index]
    unfollowed["refs"] = []  # type: ignore[index]
    c.refuses(
        "a purchase that follows no forecast",
        lambda: mod.from_record(unfollowed),
        "a purchase and a sale name the forecast they follow; an opening and a mark name none",
    )  # type: ignore[arg-type]
    c.refuses(
        "a committed record is read all the same, an uncommitted one with a broken envelope is not",
        lambda: mod.from_record(tampered(lambda r: r.update(spec="x"))),
        "the envelope: " + _spec_why(good),
    )
    return c.bad


def _env_why(mod: types.ModuleType, source: str) -> str:
    record = mod.to_record(NAME, OPEN, None, SOURCE, 1)
    record["source"] = source
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        return str(exc)
    return ""


def _spec_why(good: dict[str, Any]) -> str:
    record = dict(good)
    record["spec"] = "x"
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        return str(exc)
    return ""


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_ids, check_roundtrip, check_details, check_refusals):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
