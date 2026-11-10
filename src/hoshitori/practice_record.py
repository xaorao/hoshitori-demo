"""A book event of the practice portfolio as a record (``SIMULATOR_SPEC`` section 5: every state change is a ledger event).

The record names the book (arm and cost level) and, for a purchase or a sale, the forecast it follows, by id and digest. Its
id is natural: it is the id of what the body says, so a pass that is run twice writes the same record and the ledger takes it
once. The reader is as strict as the writer: the envelope, the members, the event read by ``book.from_body``, and an id that
is the id of the content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

from hoshitori import book, canonical, envelope
from hoshitori.ids import natural_id
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

TYPE: Final = "practice"
TYPE_VERSION: Final = 1
DETAILS_VERSION: Final = 2  # a trade that carries how it was filled; an event without details is still version 1
MAX_INSTANT_MS: Final = 253_402_300_799_999
_MEMBERS: Final = frozenset({"book", "forecast_id", "event"})
_DETAIL_MEMBERS: Final = frozenset({"exit_kind", "fill_bar_ms", "held_from_ms", "latency_ms", "signal_bar_ms"})
EXIT_KINDS: Final = ("STOP", "TARGET", "TIME", "GAP_EXIT")  # the fills' kinds of exit (a check holds the two lists equal)


class PracticeRecordError(ValueError):
    """An event that cannot be recorded, or a record that is not one."""


@dataclass(frozen=True)
class Details:
    """How a trade was filled (contract section 4: stored per trade). Times are milliseconds since the epoch, the opens of the bars."""

    exit_kind: str | None  # a sale: why and how it was made (STOP, TARGET, TIME or GAP_EXIT); a purchase: none
    fill_bar_ms: int  # the bar the trade was taken from
    signal_bar_ms: int | None  # a sale decided by a stop or a target: the bar that decided it
    latency_ms: int  # the latency in force: no fill before the authorisation plus this
    held_from_ms: int | None  # a sale: the bar its purchase was taken from, where the holding window began


@dataclass(frozen=True)
class Entry:
    name: str  # "arm:level"
    forecast_id: str | None
    forecast_digest: str | None
    event: book.Event
    details: Details | None = None


def book_id(arm: str, level: str) -> str:
    return f"{arm}:{level}"


def record_id(name: str, forecast_id: str | None, event: book.Event) -> str:
    """The id of an event: unique per book for an opening, per forecast for a purchase or a sale, per moment for a mark."""
    body = book.to_body(event)
    key: dict[str, canonical.JsonValue] = {"book": name, "forecast": forecast_id, "kind": body["event"]}
    if isinstance(event, book.Mark):
        key["at_ms"] = event.at_ms
    return natural_id("pr", key)


def _check(name: str, forecast: tuple[str, str] | None, event: book.Event) -> None:
    if not isinstance(name, str) or not name or len(name) > 200:
        raise PracticeRecordError("the book is named in at most 200 characters")
    needs = isinstance(event, book.Buy | book.Sell)
    if needs != (forecast is not None):
        raise PracticeRecordError("a purchase and a sale name the forecast they follow; an opening and a mark name none")


def _instant(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INSTANT_MS:
        raise PracticeRecordError("a time in the details is a whole number of milliseconds")


def _check_details(event: book.Event, details: Details) -> None:
    if not isinstance(event, book.Buy | book.Sell):
        raise PracticeRecordError("an opening and a mark carry no fill details")
    _instant(details.fill_bar_ms)
    for moment in (details.signal_bar_ms, details.held_from_ms):
        if moment is not None:
            _instant(moment)
    if isinstance(details.latency_ms, bool) or not isinstance(details.latency_ms, int) or details.latency_ms < 0:
        raise PracticeRecordError("the latency is a whole number of milliseconds, zero or more")
    if isinstance(event, book.Buy):
        if details.exit_kind is not None or details.signal_bar_ms is not None or details.held_from_ms is not None:
            raise PracticeRecordError("a purchase has no exit kind, signal bar or holding start")
        return
    if details.exit_kind is None or details.held_from_ms is None:
        raise PracticeRecordError("a sale names its exit kind and where the holding began")
    if details.exit_kind not in EXIT_KINDS:
        raise PracticeRecordError(f"an exit kind is one of {list(EXIT_KINDS)}")


def _details_body(details: Details) -> dict[str, canonical.JsonValue]:
    return {
        "exit_kind": details.exit_kind,
        "fill_bar_ms": details.fill_bar_ms,
        "held_from_ms": details.held_from_ms,
        "latency_ms": details.latency_ms,
        "signal_bar_ms": details.signal_bar_ms,
    }


def to_record(
    name: str, event: book.Event, forecast: tuple[str, str] | None, source: str, created_at_ms: int, details: Details | None = None
) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger. ``forecast`` is the id and the digest of the forecast record a trade follows;
    ``details`` is how the trade was filled, and with them the record is of the details version."""
    _check(name, forecast, event)
    if details is not None:
        _check_details(event, details)
    if isinstance(created_at_ms, bool) or not 0 <= created_at_ms <= MAX_INSTANT_MS:
        raise PracticeRecordError("the creation time is a whole number of milliseconds")
    forecast_id = None if forecast is None else forecast[0]
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION if details is None else DETAILS_VERSION,
        "id": record_id(name, forecast_id, event),
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": {"book": name, "forecast_id": forecast_id, "event": book.to_body(event)}
        if details is None
        else {"book": name, "forecast_id": forecast_id, "event": book.to_body(event), "fill": _details_body(details)},
        "refs": [] if forecast is None else [{"id": forecast[0], "sha256": forecast[1]}],
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise PracticeRecordError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def from_record(record: dict[str, canonical.JsonValue]) -> Entry:
    """The book event a record holds, read as strictly as it was written."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise PracticeRecordError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] not in (TYPE_VERSION, DETAILS_VERSION):
        raise PracticeRecordError("not a practice record of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])
    with_details = record["type_version"] == DETAILS_VERSION
    if with_details and "fill" not in body:
        raise PracticeRecordError("a record of the details version names its fill details")
    if set(body) != (_MEMBERS | {"fill"} if with_details else _MEMBERS):
        raise PracticeRecordError(f"the body has the members {sorted(_MEMBERS)}")
    name, forecast_id, raw = body["book"], body["forecast_id"], body["event"]
    if not isinstance(name, str) or not isinstance(raw, dict) or (forecast_id is not None and not isinstance(forecast_id, str)):
        raise PracticeRecordError("the book and the forecast are text, and the event an object")
    try:
        event = book.from_body(raw)
    except book.BookError as exc:
        raise PracticeRecordError(f"the event: {exc}") from exc
    refs = cast(list[dict[str, str]], record.get("refs", []))
    if forecast_id is None:
        if refs:
            raise PracticeRecordError("an event that follows no forecast names none")
        digest = None
    else:
        if len(refs) != 1 or refs[0]["id"] != forecast_id:
            raise PracticeRecordError("a trade names exactly the forecast it follows")
        digest = refs[0]["sha256"]
    _check(name, None if forecast_id is None else (forecast_id, cast(str, digest)), event)
    if record["id"] != record_id(name, forecast_id, event):
        raise PracticeRecordError("the id is not the id of what the record says")
    details = None
    if with_details:
        fill = body["fill"]
        if not isinstance(fill, dict) or set(fill) != _DETAIL_MEMBERS:
            raise PracticeRecordError(f"the fill details have the members {sorted(_DETAIL_MEMBERS)}")
        details = Details(
            cast(str | None, fill["exit_kind"]),
            cast(int, fill["fill_bar_ms"]),
            cast(int | None, fill["signal_bar_ms"]),
            cast(int, fill["latency_ms"]),
            cast(int | None, fill["held_from_ms"]),
        )
        _check_details(event, details)
    return Entry(name, forecast_id, digest, event, details)
