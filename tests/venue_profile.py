"""A data-driven check of one exchange adapter: what it asks, what it reads, how it classifies, what it observes.

A ``Profile`` is a table of cases for one venue (requests it must build, bodies it must read into exact bars and instruments, bodies it must
refuse, errors it must classify, headers it must observe). ``profile_failures`` runs the table against the adapter of a real or mutated
module. The cases are written from the venue's documentation and from the byte-exact responses recorded in ``fixtures/venues_live``; the
bodies in the public suite are synthetic imitations of those shapes, and the golden tests on the recordings skip when the recordings are absent.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori.capture import RawCapture, new_capture_id
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.jsonnum import JsonError
from hoshitori.venue_data import VenueDataError, check_bars

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def epoch(moment: datetime) -> int:
    return int(moment.timestamp())


@dataclass
class CandleCase:
    label: str
    args: dict[str, Any]
    body: bytes
    bars: list[tuple[Any, ...]]  # (open epoch s, open, high, low, close, volume, quote volume, trades, confirmed)
    server_time: datetime | None = None
    unfinished: int | None = None  # expected excluded_unfinished when a server time is given
    missing: tuple[int, ...] | None = None  # expected open epochs of missing slots
    pending: tuple[int, ...] | None = None


@dataclass
class InstrumentCase:
    label: str
    args: dict[str, Any]
    body: bytes
    expected: list[dict[str, Any]]  # the fields of each Instrument that matter, by name
    unmapped: list[tuple[str | None, str]] = field(default_factory=list)  # (raw id, text the reason must contain)


@dataclass
class ErrorCase:
    label: str
    status: int
    headers: dict[str, str]
    body: bytes
    error_class: str
    wait: float | None
    message: str  # what the message must start with: the venue's own words when the body carries them, else the HTTP status
    length: int | None = None  # the exact length of the message, where it is cut


@dataclass
class Profile:
    name: str
    venue: str
    adapter: Callable[[types.ModuleType], Any]
    intervals: set[int]
    kinds: tuple[str, ...]
    instrument_ok: list[str]
    instrument_bad: list[Any]
    requests: list[tuple[str, dict[str, Any], tuple[str, str, bytes | None]]]
    time_ok: list[tuple[bytes, datetime]]
    time_bad: list[bytes]
    candle_ok: list[CandleCase]
    candle_bad: list[tuple[str, dict[str, Any], bytes]]
    instruments_ok: list[InstrumentCase]
    instruments_bad: list[tuple[str, bytes] | tuple[str, bytes, dict[str, Any]]]  # (label, body) or (label, body, the arguments it answers)
    errors: list[ErrorCase]
    rate: list[tuple[str, dict[str, str], dict[str, Any] | None]]
    costs: list[tuple[str, dict[str, Any], float]]
    declared_rate: tuple[str, float, float]  # (model, limit, window in seconds) the venue documents for public requests
    interval_bad: list[Any] = field(default_factory=lambda: [0, 61, 120, 7200, True, "60", 60.0, None])
    request_errors: list[tuple[str, dict[str, Any]]] = field(
        default_factory=list
    )  # arguments that normalise but that the venue cannot be asked for
    now: datetime = T0


def _capture(
    operation: str, arguments: dict[str, Any], status: int | None, body: bytes, headers: dict[str, str] | None = None
) -> RawCapture:
    return RawCapture(
        new_capture_id(),
        "venue",
        {"operation": operation, "arguments": arguments},
        status,
        headers or {},
        body,
        T0,
        T0 + timedelta(seconds=1),
    )


def _first_difference(got: list[dict[str, Any]], expected: list[dict[str, Any]]) -> str:
    for index, (have, want) in enumerate(zip(got, expected, strict=False)):
        if have != want:
            fields = {key: (have.get(key), want.get(key)) for key in set(have) | set(want) if have.get(key) != want.get(key)}
            return f"row {index} ({want.get('raw_id')}) differs: {fields}"
    return f"{len(got)} rows, expected {len(expected)}: {[r['raw_id'] for r in got[len(expected) :]]} extra, {[r['raw_id'] for r in expected[len(got) :]]} missing"


def _only_declared(name: str, what: str, call: Callable[[], object]) -> list[str]:
    """A parser raises ``VenueDataError`` or ``JsonError`` for a body it refuses, and nothing else: the broad backstop in ``classify`` is for the
    bodies nobody thought of, not for the ones the parser was written for."""
    try:
        call()
    except (VenueDataError, JsonError):
        return []
    except Exception as exc:
        return [f"{name}: {what}: raised {type(exc).__name__} instead of a declared refusal ({exc})"]
    return [f"{name}: {what}: was read"]


def profile_failures(mod: types.ModuleType, profile: Profile) -> list[str]:
    bad: list[str] = []
    name = profile.name
    adapter = profile.adapter(mod)
    E = ErrorClass
    now = profile.now

    def classify(operation: str, arguments: dict[str, Any], status: int | None, body: bytes, headers: dict[str, str] | None = None) -> Any:
        return adapter.classify(_capture(operation, arguments, status, body, headers), now)

    # --- the shape of the venue ------------------------------------------------------------------------------------------------------------
    if set(adapter.intervals) != profile.intervals:
        bad.append(f"{name}: intervals {sorted(adapter.intervals)}, expected {sorted(profile.intervals)}")
    if tuple(adapter.instrument_kinds) != profile.kinds:
        bad.append(f"{name}: instrument kinds {adapter.instrument_kinds}")
    if adapter.operations() != frozenset({"time", "candles", "instruments"}):
        bad.append(f"{name}: operations {sorted(adapter.operations())}")
    got_rate = (adapter.rate.model.value, adapter.rate.limit, adapter.rate.window_s)
    if got_rate != profile.declared_rate:
        bad.append(f"{name}: the declared rate is {got_rate}, expected {profile.declared_rate}")
    if adapter.venue != profile.venue:
        bad.append(f"{name}: venue {adapter.venue!r}, expected {profile.venue!r}")
    try:
        adapter.build_request("bogus", {})
        bad.append(f"{name}: a request for an unknown operation was built")
    except KeyError:
        pass
    window = {"interval_s": min(profile.intervals), "start": "2026-10-29T12:00:00.000Z", "end": "2026-10-29T12:05:00.000Z"}
    for raw in profile.instrument_ok:
        try:
            adapter.normalise("candles", {"instrument": raw, **window})
        except Exception as exc:
            bad.append(f"{name}: instrument {raw!r} refused ({exc})")
    for raw in profile.instrument_bad:
        try:
            adapter.normalise("candles", {"instrument": raw, **window})
            bad.append(f"{name}: instrument {raw!r} accepted")
        except ConnectorError:
            pass
    first = profile.instrument_ok[0]
    for interval in sorted(profile.intervals):
        try:
            adapter.normalise(
                "candles", {"instrument": first, "interval_s": interval, "start": window["start"], "end": "2026-10-29T12:30:00.000Z"}
            )
        except Exception as exc:
            bad.append(f"{name}: interval {interval} refused ({exc})")
    for interval in profile.interval_bad:
        try:
            adapter.normalise("candles", {"instrument": first, **{**window, "interval_s": interval}})
            bad.append(f"{name}: interval {interval!r} accepted")
        except Exception:  # noqa: S110
            pass

    # --- what it asks ---------------------------------------------------------------------------------------------------------------------
    for operation, args, expected in profile.requests:
        try:
            normalised = adapter.normalise(operation, args)
            request = adapter.build_request(operation, normalised)
        except Exception as exc:
            bad.append(f"{name}: request for {operation} {args}: raised {type(exc).__name__}: {exc}")
            continue
        got = (request.method, request.path, request.body)
        if got != expected:
            bad.append(f"{name}: request for {operation} {args}:\n    got      {got}\n    expected {expected}")

    for operation, args in profile.request_errors:
        try:
            adapter.build_request(operation, adapter.normalise(operation, args))
            bad.append(f"{name}: a request for {operation} {args} was built")
        except ConnectorError as exc:
            if exc.error_class is not E.INVALID_ARGS:
                bad.append(f"{name}: a request for {operation} {args} failed as {exc.error_class.value}, not INVALID_ARGS")

    # --- time -----------------------------------------------------------------------------------------------------------------------------------
    for body, expected_time in profile.time_ok:
        try:
            got_time = adapter.parse_time_body(body)
        except Exception as exc:
            bad.append(f"{name}: time {body[:60]!r}: raised {type(exc).__name__}: {exc}")
            continue
        if got_time != expected_time:
            bad.append(f"{name}: time {body[:60]!r} = {got_time}, expected {expected_time}")
        verdict = classify("time", {}, 200, body)
        if verdict is not None:
            bad.append(f"{name}: a good time was classed {verdict.error_class.value}: {verdict.message}")
    for body in profile.time_bad:
        verdict = classify("time", {}, 200, body)
        if verdict is None or verdict.error_class is not E.MALFORMED:
            bad.append(f"{name}: time {body[:60]!r} should be MALFORMED, got {verdict and verdict.error_class.value}")
        bad += _only_declared(name, f"time {body[:40]!r}", lambda b=body: adapter.parse_time_body(b))

    # --- candles --------------------------------------------------------------------------------------------------------------------------------
    for case in profile.candle_ok:
        try:
            args = adapter.normalise("candles", case.args)
            bars = adapter.parse_candles_body(case.body, args)
        except Exception as exc:
            bad.append(f"{name}: candles {case.label}: raised {type(exc).__name__}: {exc}")
            continue
        got_bars = [(epoch(b.open_time), b.open, b.high, b.low, b.close, b.volume, b.quote_volume, b.trades, b.confirmed) for b in bars]
        if got_bars != case.bars:
            bad.append(f"{name}: candles {case.label}:\n    got      {got_bars}\n    expected {case.bars}")
        if any(b.close_time - b.open_time != timedelta(seconds=args["interval_s"]) for b in bars):
            bad.append(f"{name}: candles {case.label}: a bar's length is not the interval")
        verdict = classify("candles", args, 200, case.body)
        if verdict is not None:
            bad.append(f"{name}: candles {case.label} was classed {verdict.error_class.value}: {verdict.message}")
        if case.server_time is not None:
            series = adapter.parse(_capture("candles", args, 200, case.body), case.server_time)
            if case.unfinished is not None and series.excluded_unfinished != case.unfinished:
                bad.append(f"{name}: candles {case.label}: {series.excluded_unfinished} unfinished, expected {case.unfinished}")
            if case.missing is not None and tuple(epoch(m) for m in series.missing) != case.missing:
                bad.append(f"{name}: candles {case.label}: missing {[epoch(m) for m in series.missing]}, expected {list(case.missing)}")
            if case.pending is not None and tuple(epoch(m) for m in series.pending) != case.pending:
                bad.append(f"{name}: candles {case.label}: pending {[epoch(m) for m in series.pending]}, expected {list(case.pending)}")
    for label, args_in, body in profile.candle_bad:
        args = adapter.normalise("candles", args_in)
        verdict = classify("candles", args, 200, body)
        if verdict is None or verdict.error_class is not E.MALFORMED:
            bad.append(f"{name}: candles {label} should be MALFORMED, got {verdict and verdict.error_class.value}")
        bad += _only_declared(
            name, f"candles {label}", lambda b=body, a=args: check_bars(adapter.parse_candles_body(b, a), a["interval_s"])
        )

    # --- instruments ----------------------------------------------------------------------------------------------------------------------
    for case in profile.instruments_ok:
        try:
            args = adapter.normalise("instruments", case.args)
            listing = adapter.parse_instruments_body(case.body, args)
        except Exception as exc:
            bad.append(f"{name}: instruments {case.label}: raised {type(exc).__name__}: {exc}")
            continue
        got_rows = [
            {
                key: (value.value if hasattr(value, "value") else value)
                for key, value in vars(item).items()
                if key in (case.expected[0] if case.expected else {})
            }
            for item in listing.instruments
        ]
        if got_rows != case.expected:
            bad.append(f"{name}: instruments {case.label}: {_first_difference(got_rows, case.expected)}")
        got_unmapped = [(u.raw_id, u.reason) for u in listing.unmapped]
        if [raw for raw, _ in got_unmapped] != [raw for raw, _ in case.unmapped] or any(
            text not in reason for (_, text), (_, reason) in zip(case.unmapped, got_unmapped, strict=False)
        ):
            bad.append(f"{name}: instruments {case.label}: unmapped {got_unmapped}, expected {case.unmapped}")
        if listing.venue != adapter.venue:
            bad.append(f"{name}: instruments {case.label}: venue {listing.venue}")
        verdict = classify("instruments", args, 200, case.body)
        if verdict is not None:
            bad.append(f"{name}: instruments {case.label} was classed {verdict.error_class.value}: {verdict.message}")
    for entry in profile.instruments_bad:
        label, body = entry[0], entry[1]
        args = adapter.normalise("instruments", entry[2] if len(entry) == 3 else {})
        verdict = classify("instruments", args, 200, body)
        if verdict is None or verdict.error_class is not E.MALFORMED:
            bad.append(f"{name}: instruments {label} should be MALFORMED, got {verdict and verdict.error_class.value}")
        bad += _only_declared(name, f"instruments {label}", lambda b=body, a=args: adapter.parse_instruments_body(b, a))

    # --- errors and rates -----------------------------------------------------------------------------------------------------------------------
    candle_args = adapter.normalise("candles", {"instrument": first, **window})
    for case in profile.errors:
        verdict = classify("candles", candle_args, case.status, case.body, case.headers)
        if verdict is None:
            bad.append(f"{name}: error {case.label}: expected {case.error_class}, got a usable answer")
            continue
        if verdict.error_class.value != case.error_class:
            bad.append(f"{name}: error {case.label}: classed {verdict.error_class.value}, expected {case.error_class} ({verdict.message})")
        if verdict.retry_after_s != case.wait:
            bad.append(f"{name}: error {case.label}: wait {verdict.retry_after_s}, expected {case.wait}")
        if not verdict.message.startswith(case.message):
            bad.append(f"{name}: error {case.label}: message {verdict.message[:80]!r} does not start with {case.message!r}")
        if case.length is not None and len(verdict.message) != case.length:
            bad.append(f"{name}: error {case.label}: message is {len(verdict.message)} characters, expected {case.length}")
    for label, headers, expected in profile.rate:
        obs = adapter.rate_observation(_capture("time", {}, 200, b"{}", headers))
        if expected is None:
            if obs is not None:
                bad.append(f"{name}: rate {label}: expected none, got {obs}")
            continue
        if obs is None:
            bad.append(f"{name}: rate {label}: expected {expected}, got none")
            continue
        got = {
            "remaining": obs.remaining,
            "limit": obs.limit,
            "reset": obs.reset_at and epoch(obs.reset_at),
            "date": obs.response_date and epoch(obs.response_date),
        }
        if got != expected:
            bad.append(f"{name}: rate {label}: {got}, expected {expected}")
    for operation, args, expected_cost in profile.costs:
        got_cost = adapter.plan(operation, adapter.normalise(operation, args))
        if got_cost != expected_cost:
            bad.append(f"{name}: cost of {operation} {args} = {got_cost}, expected {expected_cost}")
    return bad
