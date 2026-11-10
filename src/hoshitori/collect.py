"""Collection of a cycle's evidence for the manifest's candidates, through the gateway, and nothing else.

Contract sections 6 and 25 and ``GATEWAY_DESIGN`` are the rules. After the manifest is sealed, one pass reads what the arms
will need for each candidate and turns it into evidence items; ``snapshot.freeze`` then fixes what was collected. This module
does the reading.

* **Candidates only.** The input is the manifest's eligible union. Each candidate's asset id names one token and one
  instrument (the registry's asset: the oracle instrument), so the instrument to read comes from the registry as of now and
  is never guessed from a symbol. An asset the registry no longer resolves is a failure of that candidate, listed with its
  reason.
* **The venue's clock first.** Each venue is asked for its time once; the candle window ends on the grid below that time, so
  every series is the venue's state as of its own clock and a bar that has not closed by then is never read as closed.
* **Everything through the gateway.** Every call is a league-namespace call of the highest priority, with a deadline, so the
  governor, the breaker and the capture store apply as they do to any call. Nothing here opens a connection.
* **A failure is a record, not a hole.** A candidate that could not be read gets a failure with a closed reason and
  contributes no item; the snapshot then lists the source as missing for it and its slot ends in ``FAILED_EVIDENCE``. A
  failure never becomes an empty series and never becomes a number.
* **What is returned is what is to be written.** The evidence records are returned in the shape the ledger takes, with the
  items already in the store (so ``freeze`` can read them), and the offsets between each venue's clock and ours, which the
  cycle uses to abandon a cycle whose clocks disagree.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, cast

from hoshitori import canonical
from hoshitori.capture import CaptureStore, RawCapture
from hoshitori.clock import Clock
from hoshitori.connector import ConnectorError
from hoshitori.evidence import EvidenceError, EvidenceItem, record_digest
from hoshitori.evidence_sources import SourceError, funding_item, series_item
from hoshitori.features import features_item
from hoshitori.gateway import Call, CallResult, Outcome
from hoshitori.governor import Priority
from hoshitori.jsonnum import JsonError
from hoshitori.lineage import EvidenceStore, LineageError
from hoshitori.registry import Asset, JoinCode, Registry, Want
from hoshitori.runner import Runner
from hoshitori.snapshot import Candidate
from hoshitori.timeline import to_ms
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_base import VenueAdapter
from hoshitori.venue_data import CandleSeries, InstrumentKind, VenueDataError
from hoshitori.venue_funding import OPERATION as FUNDING_OPERATION
from hoshitori.venue_funding import Funding

ADAPTER_VERSION: Final = "collect-1"
FUNDING_SOURCE: Final = "binance-funding"  # the optional source this collector knows how to read
FUNDING_PERPETUAL_VENUE: Final = "binance-usdm"  # where the registry lists the perpetuals it is joined to
FUNDING_ADAPTER_VERSION: Final = "collect-funding-1"
MAX_DETAIL: Final = 200


class CollectError(ValueError):
    """The collection is set up wrongly (a programming error), as opposed to a candidate that could not be read."""


class Reason(StrEnum):
    NO_ASSET = "NO_ASSET"  # the registry no longer resolves the candidate's asset
    NO_ADAPTER = "NO_ADAPTER"  # the asset's venue has no adapter attached
    NO_SERVER_TIME = "NO_SERVER_TIME"  # the venue did not tell its time
    BAD_REQUEST = "BAD_REQUEST"  # the window cannot be asked of this venue
    CALL_FAILED = "CALL_FAILED"  # the gateway did not bring an answer
    UNREADABLE = "UNREADABLE"  # an answer came and could not be read as a series or as evidence
    TIMED_OUT = "TIMED_OUT"  # no result by the time we stopped waiting
    NO_PERPETUAL = "NO_PERPETUAL"  # the registry does not join the candidate's token to a perpetual of the funding venue
    # the registry holds no accepted list of the funding venue's perpetuals (none read yet, or the one read was refused): the fault is in
    # the list or its configuration, not in the token, and the detail says which refusal
    PERPETUAL_LIST_UNUSABLE = "PERPETUAL_LIST_UNUSABLE"


@dataclass(frozen=True)
class Failure:
    candidate_id: str
    reason: Reason
    detail: str
    interval_s: int | None = None  # the window that failed, where one did
    source: str = ""  # the source that could not be read: a venue, the optional funding source, the registry, or RYO


@dataclass(frozen=True)
class Window:
    """One series to read per candidate: ``bars`` bars of ``interval_s`` seconds, ending on the grid below the venue's clock."""

    interval_s: int
    bars: int


DEFAULT_WINDOWS: Final = (Window(60, 120), Window(3600, 40))  # what the features of the rules arm need


@dataclass(frozen=True)
class Offset:
    """A venue's clock against ours at the moment its answer arrived (positive when the venue is ahead)."""

    venue: str
    server_ms: int
    local_ms: int

    @property
    def offset_ms(self) -> int:
        return self.server_ms - self.local_ms


@dataclass(frozen=True)
class Collection:
    by_candidate: tuple[tuple[str, tuple[str, ...]], ...]
    records: tuple[dict[str, canonical.JsonValue], ...]
    failures: tuple[Failure, ...]
    offsets: tuple[Offset, ...]

    def ids(self) -> dict[str, tuple[str, ...]]:
        return dict(self.by_candidate)


@dataclass(frozen=True)
class Environment:
    runner: Runner
    captures: CaptureStore
    adapters: Mapping[str, VenueAdapter]  # by venue name
    registry: Registry
    store: EvidenceStore
    clock: Clock
    data_mode: str = "live"  # "live" for what the exchanges said just now; a replay says what it is
    # the clusters of tokens that move together, found once for a UTC day and a set of tokens (the daily closes change once a day); the
    # practice pass reads and fills it. It is a cache of a deterministic reading and holds nothing a record needs.
    day_clusters: dict[tuple[int, tuple[tuple[str, str, str], ...]], tuple[tuple[str, ...], ...]] = field(
        default_factory=dict, compare=False
    )


UNREADABLE: Final = (ConnectorError, VenueDataError, JsonError)  # what reading a hostile body may raise (the gateway catches the same)


def why(exc: Exception) -> str:
    return detail(exc.message if isinstance(exc, ConnectorError) else str(exc))


def detail(text: str) -> str:
    return " ".join(text.split())[:MAX_DETAIL]


def now_ms(clock: Clock) -> int:
    """The clock's instant in whole milliseconds, a fraction of a millisecond cut off (a real clock has microseconds)."""
    moment = clock.now()
    return to_ms(moment.replace(microsecond=moment.microsecond // 1000 * 1000))


def grid_floor(moment: datetime, interval_s: int) -> datetime:
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % interval_s, tz=moment.tzinfo)


@dataclass(frozen=True)
class Problem:
    reason: Reason
    detail: str


def answer(captures: CaptureStore, future: Future[CallResult], wait_s: float) -> RawCapture | Problem:
    """The capture a call brought, or why it brought none."""
    try:
        result = future.result(timeout=wait_s)
    except TimeoutError:
        return Problem(Reason.TIMED_OUT, "no result before the wait ended")
    if result.outcome is not Outcome.OK or result.capture_id is None:
        why = result.error.message if result.error is not None else result.reason
        return Problem(Reason.CALL_FAILED, detail(f"{result.outcome.value}: {why}"))
    return captures.get(result.capture_id)


def _check_windows(windows: Sequence[Window]) -> None:
    if not windows or len({w.interval_s for w in windows}) != len(windows):
        raise CollectError("a collection has at least one window and no interval twice")
    for w in windows:
        if isinstance(w.bars, bool) or not isinstance(w.bars, int) or w.bars < 1:
            raise CollectError("a collection reads at least one bar")
        if isinstance(w.interval_s, bool) or not isinstance(w.interval_s, int) or w.interval_s < 1:
            raise CollectError("an interval is a whole number of seconds")


def collect(
    env: Environment,
    candidates: Sequence[Candidate],
    *,
    source_urn: str,
    caller: str = "cycle",
    windows: Sequence[Window] = DEFAULT_WINDOWS,
    wait_s: float = 30.0,
    earmark: int | None = None,
    optional_sources: Sequence[str] = (),
) -> Collection:
    """Read the oracle series of every candidate and return the items, their records and what failed.

    When the windows include the one-minute and the one-hour series, each candidate that has both also gets the features item that
    stands on them (``features``), after its series items in the records.
    """
    _check_windows(windows)
    unknown = [name for name in optional_sources if name != FUNDING_SOURCE]
    if unknown:
        raise CollectError(f"an optional source this collector cannot read: {unknown[0]!r}")
    if isinstance(wait_s, bool) or not isinstance(wait_s, int | float) or wait_s <= 0:
        raise CollectError("the wait is a positive number of seconds")
    started = env.clock.now()
    deadline = started + timedelta(seconds=wait_s)
    failures: list[Failure] = []
    wanted: list[tuple[Candidate, str, str, str]] = []  # candidate, venue, instrument id of the registry, raw id
    resolved: dict[str, Asset] = {}  # the registry's asset of each candidate in ``wanted``
    for candidate in sorted(candidates, key=lambda c: c.candidate_id):
        asset = env.registry.lookup_asset(candidate.asset_id, started)
        if asset is None:
            failures.append(
                Failure(candidate.candidate_id, Reason.NO_ASSET, "the registry does not resolve the candidate's asset", source="registry")
            )
        elif asset.venue not in env.adapters:
            failures.append(Failure(candidate.candidate_id, Reason.NO_ADAPTER, detail(f"no adapter for {asset.venue}"), source=asset.venue))
        else:
            wanted.append((candidate, asset.venue, asset.instrument_id, asset.raw_id))
            resolved[candidate.candidate_id] = asset

    def call(venue: str, operation: str, args: Mapping[str, object]) -> Call:
        return Call(env.adapters[venue].provider, operation, args, Priority.P0, deadline, caller, window=earmark)

    clocks = {venue: env.runner.submit(call(venue, "time", {})) for venue in dict.fromkeys(venue for _, venue, _, _ in wanted)}
    server: dict[str, datetime] = {}
    offsets: list[Offset] = []
    broken: dict[str, tuple[Reason, str]] = {}
    for venue, future in clocks.items():
        capture = answer(env.captures, future, wait_s)
        if isinstance(capture, Problem):
            broken[venue] = (Reason.NO_SERVER_TIME, capture.detail)
            continue
        try:
            told = cast(datetime, env.adapters[venue].parse(capture))
        except UNREADABLE as exc:
            broken[venue] = (Reason.NO_SERVER_TIME, why(exc))
            continue
        server[venue] = told
        offsets.append(Offset(venue, to_ms(told), to_ms(capture.received_at)))

    pending: list[tuple[Candidate, str, str, Window, Future[CallResult]]] = []
    for candidate, venue, instrument_id, raw_id in wanted:
        if venue in broken:
            failures.append(Failure(candidate.candidate_id, *broken[venue], source=venue))
            continue
        for window in windows:
            end = grid_floor(server[venue], window.interval_s)
            try:
                args = env.adapters[venue].normalise(
                    "candles",
                    {
                        "instrument": raw_id,
                        "interval_s": window.interval_s,
                        "start": format_rfc3339_ms(end - timedelta(seconds=window.interval_s * window.bars)),
                        "end": format_rfc3339_ms(end),
                    },
                )
            except ConnectorError as exc:
                failures.append(Failure(candidate.candidate_id, Reason.BAD_REQUEST, detail(exc.message), window.interval_s, venue))
                continue
            pending.append((candidate, venue, instrument_id, window, env.runner.submit(call(venue, "candles", args))))

    funding_pending = _funding_calls(env, wanted, resolved, started, failures, call) if FUNDING_SOURCE in optional_sources else []
    read: dict[str, dict[int, tuple[EvidenceItem, str, CandleSeries]]] = {}
    records: dict[str, list[dict[str, canonical.JsonValue]]] = {}
    for candidate, venue, instrument_id, window, future in pending:
        capture = answer(env.captures, future, wait_s)
        if isinstance(capture, Problem):
            failures.append(Failure(candidate.candidate_id, capture.reason, capture.detail, window.interval_s, venue))
            continue
        try:
            item, series = series_evidence(env.adapters[venue], capture, server[venue], candidate.asset_id, instrument_id, env.data_mode)
            record = item.to_record(source_urn, item.known_at_ms)
            digest = record_digest(record)
            env.store.add(item, digest)
        except (*UNREADABLE, SourceError, EvidenceError, LineageError) as exc:
            failures.append(Failure(candidate.candidate_id, Reason.UNREADABLE, why(exc), window.interval_s, venue))
            continue
        read.setdefault(candidate.candidate_id, {})[window.interval_s] = (item, digest, series)
        records.setdefault(candidate.candidate_id, []).append(record)

    funding = _funding_items(env, funding_pending, failures, source_urn, wait_s)
    found: dict[str, tuple[str, ...]] = {}
    out: list[dict[str, canonical.JsonValue]] = []
    for cid, got in read.items():
        ids = [item.evidence_id for item, _, _ in got.values()]
        if 60 in got and 3600 in got:
            minute, hourly = got[60], got[3600]
            built = features_item(minute[0], hourly[0], minute[2], hourly[2], (minute[1], hourly[1]), now_ms(env.clock))
            record = built.to_record(source_urn, built.known_at_ms)
            env.store.add(built, record_digest(record))
            ids.append(built.evidence_id)
            records[cid].append(record)
        if cid in funding:
            ids.append(funding[cid][0])
            records[cid].append(funding[cid][1])
        found[cid] = tuple(ids)
        out.extend(records[cid])
    for cid, (ident, record) in funding.items():
        if cid not in found:  # a candidate whose series failed still keeps the funding reading it got
            found[cid] = (ident,)
            out.append(record)
    return Collection(tuple(found.items()), tuple(out), tuple(sorted(failures, key=lambda f: f.candidate_id)), tuple(offsets))


def series_evidence(
    adapter: VenueAdapter, capture: RawCapture, server_time: datetime, asset_id: str, instrument_id: str, data_mode: str
) -> tuple[EvidenceItem, CandleSeries]:
    series = cast(CandleSeries, adapter.parse(capture, server_time))
    item = series_item(capture, series, ADAPTER_VERSION, data_mode)
    return dataclasses.replace(item, asset_refs=((asset_id, instrument_id, "RESOLVED"),)), series


def perpetual_of(registry: Registry, asset: Asset, at: datetime) -> tuple[str, str] | str:
    """The USDT perpetual of the asset's token on the funding venue, as the registry joins it (its id and the venue's own name for it), or
    why there is none. It is found by the token, never by a name made from a symbol: the primary venue's spelling of a token is not the
    funding venue's (Kraken calls bitcoin ``XXBT``). The registry refuses two pairs of one token and quote on one venue, so at most one
    perpetual of the venue can be joined."""
    want = Want(multiplier="1", token_id=asset.token_id, kind=InstrumentKind.PERPETUAL, quote_class="USDT")
    refused: list[str] = []
    for iid in registry.instruments_of(asset.token_id, at):
        found = registry.instrument_at(iid, at)
        if found is None or found.venue != FUNDING_PERPETUAL_VENUE:
            continue
        joined = registry.join(iid, want, at)
        if joined.code is JoinCode.OK:
            return iid, found.raw_id
        refused.append(f"{found.raw_id}: {joined.detail}")
    return min(refused) if refused else f"the registry lists no perpetual of the token on {FUNDING_PERPETUAL_VENUE}"


def perpetual_list_problem(registry: Registry, at: datetime) -> str | None:
    """Why the registry has no usable list of the funding venue's perpetuals, or none if it has one. A list the registry refused keeps the
    reason it gave (``SCOPE_MISMATCH`` for a list that also holds dated futures), so a setting that cannot work is not read as a token
    that has no perpetual."""
    if registry.list_age(FUNDING_PERPETUAL_VENUE, "perpetual", at) is not None:
        return None
    refused = registry.last_rejection(FUNDING_PERPETUAL_VENUE, "perpetual")
    if refused is not None:
        return f"the registry refused the list of perpetuals of {FUNDING_PERPETUAL_VENUE}: {refused[1]}"
    return f"the registry has not yet accepted a list of perpetuals of {FUNDING_PERPETUAL_VENUE}"


def _funding_calls(
    env: Environment,
    wanted: Sequence[tuple[Candidate, str, str, str]],
    resolved: Mapping[str, Asset],
    at: datetime,
    failures: list[Failure],
    call: Callable[[str, str, Mapping[str, object]], Call],
) -> list[tuple[Candidate, str, Future[CallResult]]]:
    """Ask for the funding of each candidate whose token the registry joins to a USDT perpetual of the funding venue.

    The perpetual is the registry's (``perpetual_of``): the same token, a multiplier of one, the same quote class. A token with no such
    perpetual (none listed, a thousand-token contract, another quote) is a failure of the optional source, not a reading.
    """
    out: list[tuple[Candidate, str, Future[CallResult]]] = []
    if FUNDING_SOURCE not in env.adapters:
        failures.extend(
            Failure(c.candidate_id, Reason.NO_ADAPTER, detail(f"no adapter for {FUNDING_SOURCE}"), source=FUNDING_SOURCE)
            for c, _, _, _ in wanted
        )
        return out
    adapter = env.adapters[FUNDING_SOURCE]
    for candidate, _, _, _ in wanted:
        found = perpetual_of(env.registry, resolved[candidate.candidate_id], at)
        if isinstance(found, str):
            unusable = perpetual_list_problem(env.registry, at)
            if unusable is not None:  # no accepted list: say so, instead of saying the token has no perpetual
                failures.append(Failure(candidate.candidate_id, Reason.PERPETUAL_LIST_UNUSABLE, detail(unusable), source=FUNDING_SOURCE))
            else:
                failures.append(Failure(candidate.candidate_id, Reason.NO_PERPETUAL, detail(found), source=FUNDING_SOURCE))
            continue
        perpetual, raw = found
        try:
            args = adapter.normalise(FUNDING_OPERATION, {"instrument": raw})
        except ConnectorError as exc:
            failures.append(Failure(candidate.candidate_id, Reason.BAD_REQUEST, detail(exc.message), source=FUNDING_SOURCE))
            continue
        out.append((candidate, perpetual, env.runner.submit(call(FUNDING_SOURCE, FUNDING_OPERATION, args))))
    return out


def _funding_items(
    env: Environment,
    pending: Sequence[tuple[Candidate, str, Future[CallResult]]],
    failures: list[Failure],
    source_urn: str,
    wait_s: float,
) -> dict[str, tuple[str, dict[str, canonical.JsonValue]]]:
    """The funding items that came back, each with its record, by candidate; the ones that did not are failures."""
    out: dict[str, tuple[str, dict[str, canonical.JsonValue]]] = {}
    for candidate, perpetual, future in pending:
        capture = answer(env.captures, future, wait_s)
        if isinstance(capture, Problem):
            failures.append(Failure(candidate.candidate_id, capture.reason, capture.detail, source=FUNDING_SOURCE))
            continue
        try:
            reading = cast(Funding, env.adapters[FUNDING_SOURCE].parse(capture))
            item = dataclasses.replace(
                funding_item(capture, reading, FUNDING_ADAPTER_VERSION, env.data_mode),
                asset_refs=((candidate.asset_id, perpetual, "RESOLVED"),),
            )
            record = item.to_record(source_urn, item.known_at_ms)
            env.store.add(item, record_digest(record))
        except (*UNREADABLE, SourceError, EvidenceError, LineageError) as exc:
            failures.append(Failure(candidate.candidate_id, Reason.UNREADABLE, why(exc), source=FUNDING_SOURCE))
            continue
        out[candidate.candidate_id] = (item.evidence_id, record)
    return out
