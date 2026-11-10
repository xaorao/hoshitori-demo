"""Checks of the evidence builders (``hoshitori.evidence_sources``), runnable against a mutant."""

from __future__ import annotations

import copy
import hashlib
import json
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori import canonical, evidence, jsonnum, ryo_envelope, ryo_fields
from hoshitori.capture import RawCapture
from hoshitori.timeline import to_ms
from hoshitori.venue_data import Candle, build_series
from hoshitori.venue_funding import Funding

T0 = datetime(2026, 10, 29, 21, 56, 40, tzinfo=UTC)
AS_OF = "2026-10-29T21:56:35Z"
CAP = "cap-" + "a" * 32

FULL: dict[str, Any] = {
    "asset": {"chain": None, "contract": None, "name": "Bitcoin", "rank": 1, "symbol": "BTC"},
    "market": {"price_usd": 79012.5, "market_cap_usd": 0, "volume_24h_usd": 0, "fully_diluted_value_usd": None},
    "performance": {"change_1h_pct": 0, "change_24h_pct": -0.25, "change_7d_pct": 1.5, "change_30d_pct": 2.2e1},
    "technical_analysis": {"rsi_14": 55.1, "atr_14_pct": 2.5, "momentum_30d_pct": 3, "trend": "up"},
    "intelligence": {"narrative": "", "catalysts": [], "risks": ["liquidity"]},
    "verdict": "BUY",
}


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str | None = None) -> None:
        try:
            call()
        except self.mod.SourceError as exc:
            if says is not None and says != str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def answer(
    data: dict[str, Any] | None = None,
    *,
    status: str = "ok",
    availability: dict[str, str] | None = None,
    request: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> tuple[RawCapture, Any, Any]:
    body = {
        "schema_version": "1.0",
        "tool": "analyze_token",
        "status": status,
        "data_mode": "live",
        "as_of": AS_OF,
        "request": request if request is not None else {"symbol": "BTC"},
        "data": copy.deepcopy(FULL if data is None else data),
        "summary": {"headline": "h", "key_points": []},
        "availability": availability if availability is not None else {"market_data": "available", "technical_analysis": "available"},
        "warnings": warnings or [],
    }
    raw = json.dumps(body).encode()
    parsed = jsonnum.parse(raw)
    envelope = ryo_envelope.read_envelope(parsed, "analyze_token")
    walked = ryo_fields.walk("analyze_token", envelope.data)
    capture = RawCapture(CAP, "ryo", {"tool": "analyze_token"}, 200, {}, raw, T0 - timedelta(seconds=1), T0)
    return capture, envelope, walked


def items(mod: types.ModuleType, **kwargs: Any) -> Any:
    capture, envelope, walked = answer(**kwargs)
    return mod.ryo_items(capture, envelope, walked, "ryo-1")


def fields(item: Any) -> dict[str, Any]:
    return dict(item.fields)


def unknown(item: Any) -> dict[str, str]:
    return {path: reason.value for path, reason in item.unknown_fields}


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("ok words", mod.OK_WORDS, frozenset({"available", "not_requested"}))
    c.eq("the fields that can never be zero", sorted(mod.RYO_ZERO_INVALID), [
        "asset.rank", "candidates[].market_cap_usd", "candidates[].price_usd", "market.circulating_supply", "market.fdv_usd", "market.fully_diluted_value_usd",
        "market.market_cap_usd", "market.mcap_usd", "market.price_usd", "market.total_market_cap_usd", "tokens[].metrics.market_cap_usd", "tokens[].price_usd",
        "tokens[].rank", "top_movers.gainers[].price_usd", "top_movers.losers[].price_usd"])  # fmt: skip
    for pattern in mod.RYO_ZERO_INVALID:
        known = {row[0] for tool in ryo_fields.SPECS for row in ryo_fields.classification_table(tool)}
        c.eq(f"{pattern} names a field of some tool", pattern in known, True)
    for path, want in (
        ("market.price_usd", "market.price_usd"),
        ("tokens[3].metrics.market_cap_usd", "tokens[].metrics.market_cap_usd"),
        ("coverage_by_token{SOL}.market_data", "coverage_by_token{}.market_data"),
        ("top_movers.gainers[12].price_usd", "top_movers.gainers[].price_usd"),
        ("factors[0].scores{BTC}", "factors[].scores{}"),
        ("a[1][2]", "a[][]"),
        ("a{x}{y}", "a{}{}"),
        ("", ""),
    ):
        c.eq(f"the pattern of {path!r}", mod.pattern_of(path), want)
    return c.bad


def check_recorded_answer(mod: types.ModuleType) -> list[str]:
    """A replayed answer carries the word live inside its body, because a body is evidence and is never edited; the item built from it
    must say recorded, from the capture's own mode, so that no replay can pass as a live read."""
    c = Case(mod)
    capture, envelope, walked = answer()
    live = mod.ryo_items(capture, envelope, walked, "ryo-1")
    c.eq("a live read keeps the word of its body", (live.observation.data_mode, live.interpretation.data_mode), ("live", "live"))
    replay = RawCapture(
        CAP,
        "ryo-recorded",
        {"mode": "recorded", "operation": "analyze_token", "arguments": {}},
        200,
        {},
        capture.body,
        capture.t_send,
        capture.received_at,
    )
    built = mod.ryo_items(replay, envelope, walked, "ryo-1")
    c.eq(
        "a replayed read says recorded, though its body says live",
        (built.observation.data_mode, built.interpretation.data_mode),
        ("recorded", "recorded"),
    )
    c.eq("and still names the recorded provider as its source", built.observation.source, "ryo-recorded")
    other = RawCapture(
        CAP, "ryo", {"mode": "live", "operation": "analyze_token"}, 200, {}, capture.body, capture.t_send, capture.received_at
    )
    c.eq(
        "a capture that names another mode keeps the body's word",
        mod.ryo_items(other, envelope, walked, "ryo-1").observation.data_mode,
        "live",
    )
    return c.bad


def check_ryo_items(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    result = items(mod)
    o, i = result.observation, result.interpretation
    c.eq(
        "two items from one answer",
        (o.kind, o.path, i.kind, i.path),
        (evidence.Kind.OBSERVATION, "observation", evidence.Kind.INTERPRETATION, "interpretation"),
    )
    c.eq(
        "they share the capture and are two items",
        (o.capture_id, i.capture_id, o.raw_sha256 == i.raw_sha256, o.evidence_id != i.evidence_id),
        (CAP, CAP, True, True),
    )
    c.eq("the capture's hash", o.raw_sha256, hashlib.sha256(answer()[0].body).hexdigest())
    c.eq("observation fields", fields(o), {
        "asset.name": "Bitcoin", "asset.rank": "1", "asset.symbol": "BTC", "market.price_usd": "79012.5", "market.volume_24h_usd": "0", "performance.change_1h_pct": "0",
        "performance.change_24h_pct": "-0.25", "performance.change_30d_pct": "22.0", "performance.change_7d_pct": "1.5", "technical_analysis.atr_14_pct": "2.5",
        "technical_analysis.momentum_30d_pct": "3", "technical_analysis.rsi_14": "55.1"})  # fmt: skip
    c.eq(
        "observation unknowns",
        unknown(o),
        {"asset.chain": "NULL", "asset.contract": "NULL", "market.fully_diluted_value_usd": "NULL", "market.market_cap_usd": "MALFORMED"},
    )
    c.eq("interpretation fields", fields(i), {"intelligence.risks[0]": "liquidity", "technical_analysis.trend": "up", "verdict": "BUY"})
    c.eq("interpretation unknowns", unknown(i), {"intelligence.narrative": "ABSENT"})
    c.eq("no interpretation field is in the observation", set(fields(o)) & set(fields(i)), set())
    c.eq(
        "the common attributes",
        [(x.source, x.origin, x.tool, x.status, x.data_mode, x.redistribution) for x in (o, i)],
        [("ryo", evidence.RYO, "analyze_token", "ok", "live", evidence.Redistribution.PRIVATE_ONLY)] * 2,
    )
    c.eq("the request", o.request, (("symbol", "BTC"),))
    c.eq("availability", o.availability, (("market_data", "available"), ("technical_analysis", "available")))
    c.eq(
        "the four times",
        (o.event_time_ms, o.first_seen_at_ms, o.received_at_ms, o.known_at_ms),
        (to_ms(datetime(2026, 10, 29, 21, 56, 35, tzinfo=UTC)), None, to_ms(T0), to_ms(T0)),
    )
    c.eq(
        "the interpretation has the same times",
        (i.event_time_ms, i.received_at_ms, i.known_at_ms),
        (o.event_time_ms, o.received_at_ms, o.known_at_ms),
    )
    c.eq("no derivation", (o.derived_from, o.transform, o.supersedes, o.asset_refs), ((), None, None, ()))
    c.eq("warnings are kept", items(mod, warnings=["one", "two"]).observation.warnings, ("one", "two"))
    c.eq("status is kept", items(mod, status="partial", availability={"market_data": "available"}).observation.status, "partial")
    for item in (o, i):
        rec = item.to_record("urn:x", item.known_at_ms)
        c.eq(f"{item.path} is a valid record", evidence.from_record(canonical.parse_record(canonical.dumps(rec))), item)
    # an answer with nothing but its identity: both items exist, and say what they do not know
    bare = items(mod, data={"asset": {"symbol": "BTC"}})
    c.eq("a bare answer's observation holds what there is", fields(bare.observation), {"asset.symbol": "BTC"})
    c.eq(
        "and says the rest is absent, an object missing as a whole being one absent member",
        unknown(bare.observation),
        {
            "asset.chain": "ABSENT",
            "asset.contract": "ABSENT",
            "asset.name": "ABSENT",
            "asset.rank": "ABSENT",
            "market": "ABSENT",
            "performance": "ABSENT",
            "technical_analysis": "ABSENT",
        },
    )
    c.eq(
        "its interpretation holds nothing and says so, an object of both kinds being absent from both",
        (bare.interpretation.fields, unknown(bare.interpretation)),
        ((), {"intelligence": "ABSENT", "technical_analysis": "ABSENT", "verdict": "ABSENT"}),
    )
    # a field named by the contract and absent from the answer
    cut = copy.deepcopy(FULL)
    del cut["market"]["volume_24h_usd"]
    del cut["verdict"]
    missing = items(mod, data=cut)
    c.eq("a missing observation field is unknown, absent", unknown(missing.observation).get("market.volume_24h_usd"), "ABSENT")
    c.eq("a missing interpretation field goes to the interpretation", unknown(missing.interpretation).get("verdict"), "ABSENT")
    c.eq("and not to the observation", "verdict" in unknown(missing.observation), False)
    c.eq(
        "a missing field is in neither list of known fields",
        ("market.volume_24h_usd" in fields(missing.observation), "verdict" in fields(missing.interpretation)),
        (False, False),
    )
    return c.bad


def check_zeros(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    full = items(mod).observation
    c.eq(
        "in a fully available answer a zero that may be zero is kept",
        (fields(full)["market.volume_24h_usd"], fields(full)["performance.change_1h_pct"]),
        ("0", "0"),
    )
    c.eq("a zero that can never be zero is malformed even then", unknown(full)["market.market_cap_usd"], "MALFORMED")
    not_requested = items(mod, availability={"market_data": "available", "theme_filter": "not_requested"}).observation
    c.eq("not requested counts as available", fields(not_requested)["market.volume_24h_usd"], "0")
    partial = items(mod, availability={"market_data": "available", "token_profile": "partial"}).observation
    c.eq(
        "one section partial makes every zero unknown",
        (unknown(partial)["market.volume_24h_usd"], unknown(partial)["performance.change_1h_pct"]),
        ("DEGRADED_ZERO", "DEGRADED_ZERO"),
    )
    c.eq("and a zero that can never be zero stays malformed", unknown(partial)["market.market_cap_usd"], "MALFORMED")
    c.eq("a value that is not zero is kept in a partial answer", fields(partial)["market.price_usd"], "79012.5")
    c.eq(
        "and so are small values",
        fields(
            items(
                mod, data={**FULL, "performance": {**FULL["performance"], "change_1h_pct": 0.001}}, availability={"a": "partial"}
            ).observation
        )["performance.change_1h_pct"],
        "0.001",
    )
    unavailable = items(mod, availability={"market_data": "unavailable"}).observation
    c.eq("an unavailable section", unknown(unavailable)["performance.change_1h_pct"], "DEGRADED_ZERO")
    odd = items(mod, availability={"market_data": "Available"}).observation
    c.eq("a word other than the two is not available (case matters)", unknown(odd)["performance.change_1h_pct"], "DEGRADED_ZERO")
    degraded_status = items(mod, status="partial", availability={"market_data": "available"}).observation
    c.eq("a status other than ok makes zeros unknown too", unknown(degraded_status)["performance.change_1h_pct"], "DEGRADED_ZERO")
    none = items(mod, availability={}).observation
    c.eq("an answer with no availability at all is entirely available", fields(none)["performance.change_1h_pct"], "0")
    for text in ("0.0", "0.00", "-0"):
        data = copy.deepcopy(FULL)
        data["performance"]["change_1h_pct"] = float(text)
        built = items(mod, data=data, availability={"a": "partial"}).observation
        c.eq(f"{text} is a zero", unknown(built).get("performance.change_1h_pct"), "DEGRADED_ZERO")
    # text that merely looks like zero is not a number field
    c.eq(
        "a text field of 0 is text",
        fields(items(mod, data={**FULL, "verdict": "0"}, availability={"a": "partial"}).interpretation)["verdict"],
        "0",
    )
    return c.bad


def check_request(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    built = items(
        mod,
        request={
            "top_n": 5,
            "theme": "ai",
            "limit": 12345678901234567890,
            "ratio": 1.5,
            "flag": True,
            "nothing": None,
            "list": [1, "a", 2.5],
            "nested": {"b": 2, "a": 1},
        },
    ).observation
    c.eq(
        "a request as canonical data",
        dict(built.request),
        {
            "top_n": 5,
            "theme": "ai",
            "limit": "12345678901234567890",
            "ratio": "1.5",
            "flag": True,
            "nothing": None,
            "list": [1, "a", "2.5"],
            "nested": {"b": 2, "a": 1},
        },
    )
    c.eq("in key order", [k for k, _ in built.request], sorted(dict(built.request)))
    c.eq("a negative integer", dict(items(mod, request={"n": -7}).observation.request), {"n": -7})
    c.eq("an integer of fifteen digits", dict(items(mod, request={"n": 123456789012345}).observation.request), {"n": 123456789012345})
    c.eq("one of sixteen digits is text", dict(items(mod, request={"n": 1234567890123456}).observation.request), {"n": "1234567890123456"})
    c.eq("a decimal with an exponent is plain", dict(items(mod, request={"n": 2.5e2}).observation.request), {"n": "250.0"})
    return c.bad


def series(mod: types.ModuleType, count: int = 3, venue: str = "binance-spot") -> Any:
    start = datetime(2026, 10, 29, 21, 0, 0, tzinfo=UTC)
    bars = [
        Candle(
            start + timedelta(minutes=k),
            start + timedelta(minutes=k + 1),
            f"{100 + k}.0",
            f"{101 + k}.5",
            f"{99 + k}.5",
            f"{100 + k}.8",
            "12.5",
            None,
            None,
            None,
        )
        for k in range(count)
    ]
    return build_series(venue, "BTCUSDT", 60, bars, start + timedelta(minutes=count, seconds=3), start, start + timedelta(minutes=count))


def check_series(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    cap = RawCapture(
        "cap-" + "b" * 32, "binance-spot", {"url": "https://example.test/klines"}, 200, {}, b"[]", T0 - timedelta(seconds=1), T0
    )
    s = series(mod)
    item = mod.series_item(cap, s, "binance-1")
    c.eq("a series is live unless it is said to be a replay", item.data_mode, "live")
    c.eq("and says what a replay is", mod.series_item(cap, s, "binance-1", "recorded").data_mode, "recorded")
    c.eq(
        "an observation of an exchange",
        (item.kind, item.path, item.source, item.origin, item.tool, item.capture_id, item.redistribution),
        (
            evidence.Kind.OBSERVATION,
            "series",
            "binance-spot",
            evidence.Family(evidence.FamilyKind.EXCHANGE, "binance-spot"),
            None,
            "cap-" + "b" * 32,
            evidence.Redistribution.PRIVATE_ONLY,
        ),
    )
    c.eq("its fields", fields(item), {
        "bars_sha256": mod.bars_digest(s), "count": "3", "excluded_unfinished": "0", "first_open": "2026-10-29T21:00:00.000Z", "interval_s": "60", "last_close": "102.8",
        "last_close_time": "2026-10-29T21:03:00.000Z", "last_open": "2026-10-29T21:02:00.000Z", "missing": "0", "pending": "0", "server_time": "2026-10-29T21:03:03.000Z"})  # fmt: skip
    c.eq("nothing is unknown", item.unknown_fields, ())
    c.eq(
        "the request",
        dict(item.request),
        {"end": "2026-10-29T21:03:00.000Z", "instrument": "BTCUSDT", "interval_s": 60, "start": "2026-10-29T21:00:00.000Z"},
    )
    c.eq(
        "the four times (the event time is the venue's own clock when it answered)",
        (item.event_time_ms, item.first_seen_at_ms, item.received_at_ms, item.known_at_ms),
        (to_ms(datetime(2026, 10, 29, 21, 3, 3, tzinfo=UTC)), None, to_ms(T0), to_ms(T0)),
    )
    c.eq("the digest is of the canonical bars", mod.bars_digest(s), hashlib.sha256(canonical.dumps({"bars": [
        {"open_ms": to_ms(b.open_time), "close_ms": to_ms(b.close_time), "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": "12.5"} for b in s.candles]})).hexdigest())  # fmt: skip
    c.eq("a different bar, a different digest", mod.bars_digest(series(mod, count=2)) != mod.bars_digest(s), True)
    full = Candle(s.candles[0].open_time, s.candles[0].close_time, "1.0", "2.0", "0.5", "1.5", "3", "4", 7, True)
    one = build_series("binance-spot", "X", 60, [full], s.candles[0].close_time, s.candles[0].open_time, s.candles[0].close_time)
    c.eq("optional bar values are part of the digest", mod.bars_digest(one), hashlib.sha256(canonical.dumps({"bars": [
        {"open_ms": to_ms(full.open_time), "close_ms": to_ms(full.close_time), "open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5", "volume": "3", "quote_volume": "4", "trades": 7}]})).hexdigest())  # fmt: skip
    start = datetime(2026, 10, 29, 21, 0, 0, tzinfo=UTC)
    nothing = build_series("okx", "BTC-USDT", 60, [], start + timedelta(minutes=3), start, start + timedelta(minutes=3))
    e = mod.series_item(cap, nothing, "okx-1")
    c.eq(
        "a series with no finished bar",
        (fields(e)["count"], fields(e)["missing"], e.event_time_ms, unknown(e)),
        (
            "0",
            "3",
            to_ms(start + timedelta(minutes=3)),
            {"first_open": "ABSENT", "last_close": "ABSENT", "last_close_time": "ABSENT", "last_open": "ABSENT"},
        ),
    )
    c.eq("the venue is the family", e.origin, evidence.Family(evidence.FamilyKind.EXCHANGE, "okx"))
    c.refuses(
        "a venue that cannot be a family name",
        lambda: mod.series_item(cap, series(mod, venue="bad venue"), "v1"),
        "the venue 'bad venue' cannot be an origin family: the family EXCHANGE has a name of 1 to 64 letters, digits and . _ @ -",
    )
    c.refuses(
        "an adapter version that is not one",
        lambda: mod.series_item(cap, s, "a b"),
        "the series cannot be recorded as evidence: an adapter version is 1 to 64 letters, digits and . _ + -",
    )
    rec = item.to_record("urn:x", item.known_at_ms)
    c.eq("the item is a valid record", evidence.from_record(canonical.parse_record(canonical.dumps(rec))), item)
    return c.bad


def check_funding(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    cap = RawCapture(
        "cap-" + "c" * 32, "binance-funding", {"url": "https://example.test/premiumIndex"}, 200, {}, b"{}", T0 - timedelta(seconds=1), T0
    )
    f = Funding(
        "binance-funding",
        "BTCUSDT",
        "-0.00005000",
        "77550.55747101",
        "77571.79565217",
        datetime(2026, 10, 31, 16, 0, tzinfo=UTC),
        datetime(2026, 10, 31, 12, 17, 49, tzinfo=UTC),
    )
    item = mod.funding_item(cap, f, "binance-funding-1")
    c.eq(
        "an observation of an exchange",
        (item.kind, item.path, item.source, item.origin, item.tool, item.capture_id, item.redistribution, item.data_mode),
        (
            evidence.Kind.OBSERVATION,
            "funding",
            "binance-funding",
            evidence.Family(evidence.FamilyKind.EXCHANGE, "binance-funding"),
            None,
            "cap-" + "c" * 32,
            evidence.Redistribution.PRIVATE_ONLY,
            "live",
        ),
    )
    c.eq(
        "its fields, as the venue gave them",
        fields(item),
        {
            "index_price": "77571.79565217",
            "last_funding_rate": "-0.00005000",
            "mark_price": "77550.55747101",
            "next_funding_time": "2026-10-31T16:00:00.000Z",
            "server_time": "2026-10-31T12:17:49.000Z",
        },
    )
    c.eq("nothing is unknown and nothing is derived", (item.unknown_fields, item.derived_from, item.transform), ((), (), None))
    c.eq("the request names the instrument", dict(item.request), {"instrument": "BTCUSDT"})
    c.eq(
        "the times (the event time is the venue's own clock when it answered)",
        (item.event_time_ms, item.first_seen_at_ms, item.received_at_ms, item.known_at_ms),
        (to_ms(f.server_time), None, to_ms(T0), to_ms(T0)),
    )
    c.eq("a replay says so", mod.funding_item(cap, f, "binance-funding-1", "recorded").data_mode, "recorded")
    c.eq("the raw digest is of the capture", item.raw_sha256, cap.body_sha256)
    c.refuses(
        "a venue that cannot be a family name",
        lambda: mod.funding_item(cap, Funding("bad venue", "BTCUSDT", "0", "1", "1", f.next_funding_time, f.server_time), "v1"),
        "the venue 'bad venue' cannot be an origin family: the family EXCHANGE has a name of 1 to 64 letters, digits and . _ @ -",
    )
    c.refuses(
        "an adapter version that is not one",
        lambda: mod.funding_item(cap, f, "a b"),
        "the funding reading cannot be recorded as evidence: an adapter version is 1 to 64 letters, digits and . _ + -",
    )
    rec = item.to_record("urn:x", item.known_at_ms)
    c.eq("the item is a valid record", evidence.from_record(canonical.parse_record(canonical.dumps(rec))), item)
    return c.bad


def check_errors(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    capture, envelope, walked = answer()
    c.refuses(
        "an adapter version that is not one",
        lambda: mod.ryo_items(capture, envelope, walked, "a b"),
        "the answer cannot be recorded as evidence: an adapter version is 1 to 64 letters, digits and . _ + -",
    )
    long = copy.deepcopy(FULL)
    long["verdict"] = "x" * 20_001
    cap2, env2, walked2 = answer(data=long)
    c.refuses(
        "a field value that is too long",
        lambda: mod.ryo_items(cap2, env2, walked2, "ryo-1"),
        "the answer cannot be recorded as evidence: a field value is a non-empty string of at most 20000 characters",
    )
    ok = copy.deepcopy(FULL)
    ok["verdict"] = "x" * 20_000
    cap3, env3, walked3 = answer(data=ok)
    c.eq("a field value of 20000 characters", len(fields(mod.ryo_items(cap3, env3, walked3, "ryo-1").interpretation)["verdict"]), 20_000)
    return c.bad


def check_internals(mod: types.ModuleType) -> list[str]:
    """The small functions the builders stand on, asked directly where an answer cannot reach their edges."""
    c = Case(mod)
    c.eq("a record of evidence is frozen", mod.RyoEvidence.__dataclass_params__.frozen, True)
    c.refuses(
        "a request value of a type JSON does not have", lambda: mod._json(object()), "a request value of type object cannot be recorded"
    )
    c.eq("plain values pass through", [mod._json(x) for x in (None, True, "a", [])], [None, True, "a", []])
    patterns = {"a.x": "OBSERVATION", "a.y": "INTERPRETATION", "m.z": "META", "list[]": "OBSERVATION", "keyed{}": "INTERPRETATION"}
    OBS, INT = ryo_fields.FieldClass.OBSERVATION, ryo_fields.FieldClass.INTERPRETATION
    c.eq("a leaf", mod._classes_under("a.x", patterns), {OBS})
    c.eq("an object of both kinds", mod._classes_under("a", patterns), {OBS, INT})
    c.eq("an object with only meta fields holds nothing that matters", mod._classes_under("m", patterns), set())
    c.eq("a meta leaf", mod._classes_under("m.z", patterns), set())
    c.eq("a list", mod._classes_under("list", patterns), {OBS})
    c.eq("a keyed map", mod._classes_under("keyed", patterns), {INT})
    c.eq("a member the table does not know is an observation", mod._classes_under("zzz", patterns), {OBS})
    # the order of the fields does not depend on the order the walk happened to produce them in
    capture, envelope, _ = answer()
    F = ryo_fields.FieldValue
    walked = ryo_fields.Walked(
        fields=[
            F("verdict", INT, "text", "BUY"),
            F("asset.symbol", OBS, "text", "BTC"),
            F("market.price_usd", OBS, "number", "1"),
            F("a-b.c", OBS, "number", "2"),
            F("intent", ryo_fields.FieldClass.META, "text", "swing"),
            F("asset.chain", OBS, "text", None),
            F("z.null", OBS, "text", None),
        ],
        missing=["asset.name", "technical_analysis"],
    )
    built = mod.ryo_items(capture, envelope, walked, "ryo-1")
    c.eq("observation fields are put in order", [p for p, _ in built.observation.fields], ["a-b.c", "asset.symbol", "market.price_usd"])
    c.eq(
        "unknown fields are put in order, those that were null and those that were absent together",
        [p for p, _ in built.observation.unknown_fields],
        ["asset.chain", "asset.name", "technical_analysis", "z.null"],
    )
    c.eq(
        "a meta field is in neither item", ("intent" in fields(built.observation), "intent" in fields(built.interpretation)), (False, False)
    )
    c.eq(
        "interpretation fields and unknowns",
        (fields(built.interpretation), unknown(built.interpretation)),
        ({"verdict": "BUY"}, {"technical_analysis": "ABSENT"}),
    )
    # an availability given out of order is put in order
    shuffled_capture, shuffled_env, shuffled_walked = answer(availability={"technical_analysis": "available", "market_data": "available"})
    c.eq(
        "availability is put in order",
        mod.ryo_items(shuffled_capture, shuffled_env, shuffled_walked, "ryo-1").observation.availability,
        (("market_data", "available"), ("technical_analysis", "available")),
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_recorded_answer,
        check_ryo_items,
        check_zeros,
        check_request,
        check_series,
        check_funding,
        check_errors,
        check_internals,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
