"""How the Desk runs one check of its menu: one gateway call (or the two a price check needs), turned into evidence in a store of its own.

Every call goes through the gateway in the on-demand namespace, in the class of an on-demand investigation, called by the Desk run, so it
is paid after the league and never read as the league's own (a league snapshot is never altered by a Desk run). A call that cannot finish
before the run's deadline, by the worst latency recorded for it, is not started. A failure has a closed class and never leaves evidence
behind; a reading that came and could not be read keeps its captures for the log. What the binding says (live, or recorded for a replay of
captures) must match what the capture is: a replay is never a live read, and a live read is never labelled a replay.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final, Protocol, cast

from hoshitori import address_screen, canonical
from hoshitori.capture import CaptureStore, RawCapture
from hoshitori.clock import Clock
from hoshitori.collect import (
    FUNDING_ADAPTER_VERSION,
    UNREADABLE,
    Problem,
    answer,
    detail,
    grid_floor,
    perpetual_of,
    series_evidence,
    why,
)
from hoshitori.connector import ConnectorError
from hoshitori.desk_menu import MenuCheck
from hoshitori.evidence import EvidenceError, EvidenceItem, record_digest
from hoshitori.evidence_sources import SourceError, funding_item, ryo_items
from hoshitori.gateway import ON_DEMAND, Call, CallResult
from hoshitori.governor import Priority
from hoshitori.lineage import EvidenceStore, LineageError
from hoshitori.registry import Asset, JoinCode, Registry, Want
from hoshitori.ryo import PROVIDER as RYO_PROVIDER
from hoshitori.ryo_envelope import read_rest_success
from hoshitori.ryo_evidence import RyoEvidenceError, items_from
from hoshitori.ryo_fields import walk
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_base import VenueAdapter
from hoshitori.venue_data import CandleSeries
from hoshitori.venue_funding import Funding

OUTCOMES: Final = ("OK", "FAILED", "NOT_STARTED")
ERROR_CLASSES: Final = (
    "DEADLINE_UNREACHABLE",
    "NO_ADAPTER",
    "NO_ASSET",
    "NO_INSTRUMENT",
    "BAD_REQUEST",
    "CALL_FAILED",
    "TIMED_OUT",
    "UNREADABLE",
    "NOT_LIVE",
    "BINDING_MISMATCH",
    "UNKNOWN_CHECK_TYPE",
)
BARS: Final = 60  # one-minute bars a price check reads
SOURCE_PREFIX: Final = "urn:hoshitori:desk:"
ADAPTER_VERSION: Final = "desk-ryo-1"
RYO_TYPES: Final = frozenset({"RYO_TOKEN_READ", "RYO_DEEP_ANALYSIS", "MARKET_CONTEXT_OVERVIEW", "MARKET_SENTIMENT_SHIFT"})
ASSET_BOUND: Final = frozenset({"RYO_TOKEN_READ", "RYO_DEEP_ANALYSIS"})


class Submitter(Protocol):
    """Whatever takes a call and answers with a future result: the gateway's runner, or a runner that replays recordings."""

    def submit(self, call: Call) -> Future[CallResult]: ...


@dataclass(frozen=True)
class DeskEnv:
    runner: Submitter
    captures: CaptureStore
    adapters: Mapping[str, VenueAdapter]  # by venue name, as the collector's
    registry: Registry
    store: EvidenceStore  # the Desk's own, never the league's
    clock: Clock
    data_mode: str = "live"  # "live", or "recorded" for a replay of captures
    wait_s: float = 30.0  # how long to wait for one answer
    screens: bool = False  # the address screen is a provider of this gateway


@dataclass(frozen=True)
class RunContext:
    run_id: str
    asset_id: str
    symbol: str
    deadline: datetime  # no call is started that cannot finish before it


@dataclass(frozen=True)
class Acquired:
    outcome: str
    capture_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    error_class: str | None
    detail: str
    spent: int  # calls made
    series: CandleSeries | None = None  # the candles a price check read
    reading: str | None = None  # what a screen said of the address: one of address_screen.READINGS


def providers_of(env: DeskEnv) -> dict[str, frozenset[str]]:
    """The providers the Desk can ask, with the operations the menu uses, for building a menu."""
    found = {a.provider: frozenset(a.operations() & {"candles", "funding"}) for a in env.adapters.values()}
    if env.screens:
        found[address_screen.PROVIDER] = frozenset({"check"})
    return found


class _Stop(Exception):
    def __init__(self, error_class: str, text: str) -> None:
        super().__init__(text)
        self.error_class = error_class
        self.text = text


class _Calls:
    """The calls of one acquisition: submission, the wait, and the count."""

    def __init__(self, env: DeskEnv, run: RunContext) -> None:
        self.env, self.run = env, run
        self.spent = 0
        self.captures: list[str] = []

    def ask(self, provider: str, operation: str, args: Mapping[str, object]) -> RawCapture:
        caller = f"desk:{self.run.run_id}"
        call = Call(provider, operation, args, Priority.P2, self.run.deadline, caller, namespace=ON_DEMAND)
        future: Future[CallResult] = self.env.runner.submit(call)
        self.spent += 1
        got = answer(self.env.captures, future, self.env.wait_s)
        if isinstance(got, Problem):
            raise _Stop(got.reason.value, got.detail)
        self.captures.append(got.capture_id)
        return got


def _instrument_on(registry: Registry, asset: Asset, venue: str, at: datetime) -> tuple[str, str] | None:
    """The instrument of the asset's token on ``venue`` that the registry joins to the asset's own kind and quote, and the venue's name for
    it."""
    want = Want(multiplier=asset.multiplier, token_id=asset.token_id, kind=asset.kind, quote_class=asset.quote_class)
    for iid in registry.instruments_of(asset.token_id, at):
        found = registry.instrument_at(iid, at)
        if found is None or found.venue != venue:
            continue
        if registry.join(iid, want, at).code is JoinCode.OK:
            return iid, found.raw_id
    return None


def _store(env: DeskEnv, item: EvidenceItem, source: str) -> str:
    record = item.to_record(source, item.known_at_ms)
    env.store.add(item, record_digest(record))
    return item.evidence_id


def _adapter_of(env: DeskEnv, provider: str) -> tuple[str, VenueAdapter]:
    for venue, adapter in env.adapters.items():
        if adapter.provider == provider:
            return venue, adapter
    raise _Stop("NO_ADAPTER", detail(f"no adapter for {provider}"))


def _price(env: DeskEnv, check: MenuCheck, run: RunContext, calls: _Calls) -> tuple[tuple[str, ...], CandleSeries | None]:
    venue, adapter = _adapter_of(env, check.provider)
    at = env.clock.now()
    asset = env.registry.lookup_asset(run.asset_id, at)
    if asset is None:
        raise _Stop("NO_ASSET", "the registry does not resolve the asset")
    found = _instrument_on(env.registry, asset, venue, at)
    if found is None:
        raise _Stop("NO_INSTRUMENT", detail(f"the registry lists no instrument of the token on {venue}"))
    instrument_id, raw_id = found
    server = cast(datetime, adapter.parse(calls.ask(adapter.provider, "time", {})))
    end = grid_floor(server, 60)
    try:
        args = adapter.normalise(
            "candles",
            {
                "instrument": raw_id,
                "interval_s": 60,
                "start": format_rfc3339_ms(end - timedelta(seconds=60 * BARS)),
                "end": format_rfc3339_ms(end),
            },
        )
    except ConnectorError as exc:
        raise _Stop("BAD_REQUEST", detail(exc.message)) from exc
    capture = calls.ask(adapter.provider, "candles", args)
    try:
        item, series = series_evidence(adapter, capture, server, run.asset_id, instrument_id, env.data_mode)
        return (_store(env, item, SOURCE_PREFIX + run.run_id),), series
    except (*UNREADABLE, SourceError, EvidenceError, LineageError) as exc:
        raise _Stop("UNREADABLE", why(exc)) from exc


def _funding(env: DeskEnv, check: MenuCheck, run: RunContext, calls: _Calls) -> tuple[str, ...]:
    _, adapter = _adapter_of(env, check.provider)
    at = env.clock.now()
    asset = env.registry.lookup_asset(run.asset_id, at)
    if asset is None:
        raise _Stop("NO_ASSET", "the registry does not resolve the asset")
    found = perpetual_of(env.registry, asset, at)
    if isinstance(found, str):
        raise _Stop("NO_INSTRUMENT", detail(found))
    perpetual, raw_id = found
    try:
        args = adapter.normalise(check.operation, {"instrument": raw_id})
    except ConnectorError as exc:
        raise _Stop("BAD_REQUEST", detail(exc.message)) from exc
    capture = calls.ask(adapter.provider, check.operation, args)
    try:
        reading = cast(Funding, adapter.parse(capture))
        item = dataclasses.replace(
            funding_item(capture, reading, FUNDING_ADAPTER_VERSION, env.data_mode),
            asset_refs=((run.asset_id, perpetual, "RESOLVED"),),
        )
        return (_store(env, item, SOURCE_PREFIX + run.run_id),)
    except (*UNREADABLE, SourceError, EvidenceError, LineageError) as exc:
        raise _Stop("UNREADABLE", why(exc)) from exc


def _ryo_arguments(check: MenuCheck) -> dict[str, canonical.JsonValue]:
    return {key: (value == "true" if key == "include_perp" else value) for key, value in check.args}


def _ryo(env: DeskEnv, check: MenuCheck, run: RunContext, calls: _Calls) -> tuple[str, ...]:
    args = _ryo_arguments(check)
    capture = calls.ask(RYO_PROVIDER, check.operation, args)
    replay = capture.request.get("mode") == "recorded"
    if replay != (env.data_mode == "recorded"):
        raise _Stop("BINDING_MISMATCH", "a replay is never a live read, and a live read is never labelled a replay")
    refs = ((run.asset_id, f"ryo:{run.symbol}", "RESOLVED"),) if check.check_type in ASSET_BOUND else ()
    try:
        if replay:
            envelope = read_rest_success(capture.body, check.operation)
            made = ryo_items(capture, envelope, walk(check.operation, envelope.data), ADAPTER_VERSION)
            items = [made.observation, made.interpretation]
        else:
            if read_rest_success(capture.body, check.operation).data_mode != "live":
                raise _Stop("NOT_LIVE", "RYO's data mode is not live")
            built = items_from(capture, check.operation, args, ADAPTER_VERSION)
            items = [built.observation] + ([built.interpretation] if built.interpretation is not None else [])
        source = SOURCE_PREFIX + run.run_id
        return tuple(_store(env, dataclasses.replace(item, asset_refs=refs), source) for item in items)
    except (*UNREADABLE, RyoEvidenceError, SourceError, EvidenceError, LineageError) as exc:
        raise _Stop("UNREADABLE", why(exc) if isinstance(exc, ConnectorError) else str(exc)) from exc


def _address_flag(env: DeskEnv, check: MenuCheck, run: RunContext, calls: _Calls) -> tuple[tuple[str, ...], str]:
    """The screen of a token's contract: the answer of the skill, read as strictly as it was written, and one reading for each source that
    answered. No source answering is a failed call; some answering is an incomplete screen, which is never read as no flag."""
    args = dict(check.args)
    capture = calls.ask(address_screen.PROVIDER, check.operation, args)
    replay = capture.request.get("mode") == "recorded"
    if replay != (env.data_mode == "recorded"):
        raise _Stop("BINDING_MISMATCH", "a replay is never a live read, and a live read is never labelled a replay")
    try:
        screen = address_screen.read_screen(capture.body, chain=args["chain"], address=args["address"])
        if screen.status == "unavailable":
            raise _Stop("CALL_FAILED", "no source of the screen answered")
        items = address_screen.screen_items(capture, screen)
        source = SOURCE_PREFIX + run.run_id
        return tuple(_store(env, item, source) for item in items), address_screen.reading(screen)
    except (address_screen.ScreenError, EvidenceError, LineageError) as exc:
        raise _Stop("UNREADABLE", str(exc)) from exc


def acquire(env: DeskEnv, check: MenuCheck, run: RunContext) -> Acquired:
    """Run one check of the menu and return what it brought or why it brought nothing."""
    kind = check.check_type
    if kind not in RYO_TYPES and kind not in ("INDEPENDENT_PRICE_CHECK", "DERIVATIVES_CONTEXT", "ADDRESS_FLAG_CHECK"):
        return Acquired("FAILED", (), (), "UNKNOWN_CHECK_TYPE", detail(f"nothing serves a check of type {kind}"), 0)
    if kind == "ADDRESS_FLAG_CHECK" and not env.screens:
        return Acquired("FAILED", (), (), "NO_ADAPTER", detail("this Desk has no address screen"), 0)
    remaining = (run.deadline - env.clock.now()).total_seconds()
    worst = Decimal(check.cost.worst_seconds) if check.cost.worst_seconds is not None else Decimal(0)
    if remaining <= 0 or Decimal(remaining) < worst:
        return Acquired("NOT_STARTED", (), (), "DEADLINE_UNREACHABLE", "the call cannot finish before the deadline", 0)
    calls = _Calls(env, run)
    try:
        series: CandleSeries | None = None
        if kind == "ADDRESS_FLAG_CHECK":
            ids, reading = _address_flag(env, check, run, calls)
            return Acquired("OK", tuple(calls.captures), ids, None, "", calls.spent, None, reading)
        if kind == "INDEPENDENT_PRICE_CHECK":
            ids, series = _price(env, check, run, calls)
        elif kind == "DERIVATIVES_CONTEXT":
            ids = _funding(env, check, run, calls)
        else:
            ids = _ryo(env, check, run, calls)
    except _Stop as stop:
        return Acquired("FAILED", tuple(calls.captures), (), stop.error_class, detail(stop.text), calls.spent)
    return Acquired("OK", tuple(calls.captures), ids, None, "", calls.spent, series)
