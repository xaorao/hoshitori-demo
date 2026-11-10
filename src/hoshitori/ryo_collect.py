"""Reading RYO in a cycle: the scan that proposes candidates, and the evidence behind each candidate.

Two jobs, both over the gateway (this module makes no network call of its own):

* ``scan`` asks ``scan_market`` for the protocol's top N and turns the answer into the manifest's rows (RYO's own rank and symbol) with the
  capture they were read from. An answer that cannot be a scan is a closed failure reason (``RYO_SCAN_*``); the route then contributes
  nothing and the manifest says why. A capture that came is kept even when the answer is refused, so the refusal can be checked later.
* ``collect_ryo`` asks every candidate ``analyze_token`` and ``deep_analysis`` by symbol, and the cycle ``market_overview`` and
  ``monitor_market_sentiment_shift`` once. Each accepted answer becomes an observation item for the snapshot and, where the answer has
  interpretation fields, a separate interpretation item that goes into the records and into no list an independent arm reads
  (``ryo_evidence``). One answer that fails costs only itself: the candidate keeps what it did get, and the failure names the tool.

Nothing here decides whether RYO is wanted in a league; that is the caller's setting. Time is bounded by one deadline for the whole
collection: an answer that has not come when it passes is a ``TIMED_OUT`` failure, never an empty success.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from hoshitori import canonical, ryo_read
from hoshitori.capture import CaptureStore, RawCapture
from hoshitori.clock import Clock
from hoshitori.collect import UNREADABLE, Failure, Problem, Reason, answer, detail, why
from hoshitori.connector import ConnectorError
from hoshitori.evidence import EvidenceError, EvidenceItem, record_digest
from hoshitori.gateway import Call, CallResult
from hoshitori.governor import Priority
from hoshitori.lineage import EvidenceStore, LineageError
from hoshitori.manifest import MAX_ROWS, Capture, Failed, ScanRow
from hoshitori.runner import Runner
from hoshitori.ryo import PROVIDER
from hoshitori.ryo_envelope import read_rest_success, read_whoami
from hoshitori.ryo_evidence import RyoEvidenceError, items_from
from hoshitori.ryo_fields import walk
from hoshitori.snapshot import Candidate

ADAPTER_VERSION: Final = "ryo-evidence-1"
QUICK_TOOL: Final = "analyze_token"  # asked of every candidate first
DEEP_TOOL: Final = "deep_analysis"  # asked of every candidate last: slower, and the first to go when the budget is short
CYCLE_TOOLS: Final = ("market_overview", "monitor_market_sentiment_shift")
CYCLE_OWNER: Final = "cycle"  # the "candidate" a failure of a cycle-wide tool is filed under
SOURCE: Final = "ryo"  # the name a failure of RYO's tools carries

SCAN_CALL_FAILED: Final = "RYO_SCAN_CALL_FAILED"
SCAN_TIMED_OUT: Final = "RYO_SCAN_TIMED_OUT"
SCAN_UNREADABLE: Final = "RYO_SCAN_UNREADABLE"
SCAN_UNAVAILABLE: Final = "RYO_SCAN_UNAVAILABLE"
SCAN_NOT_LIVE: Final = "RYO_SCAN_NOT_LIVE"
SCAN_EMPTY: Final = "RYO_SCAN_EMPTY"
KEY_NOT_ALLOWED: Final = "RYO_KEY_NOT_ALLOWED"  # RYO's key status says this key may not call now: nothing is asked
KEY_SPENT: Final = "RYO_ALLOWANCE_SPENT"  # RYO's key status leaves no call in the allowance's period: nothing is asked


class RyoCollectError(ValueError):
    """The collection is set up wrongly (a programming error), as opposed to an answer that could not be read."""


@dataclass(frozen=True)
class RyoEnvironment:
    runner: Runner
    captures: CaptureStore
    store: EvidenceStore
    clock: Clock


@dataclass(frozen=True)
class Scan:
    rows: tuple[ScanRow, ...] | Failed
    capture: Capture | None  # the stored response the rows were read from, kept when one came even if it was refused
    detail: str  # why the scan failed, in words; empty when it did not
    left: int | None = None  # the calls RYO's key status leaves for the rest of the cycle; None when it said nothing about a limit


@dataclass(frozen=True)
class KeyStatus:
    allowed: bool | None  # RYO's own word on whether this key may call now; None when it was not said or could not be read
    remaining: int | None  # calls left in the allowance's period; None when not said or not read
    detail: str  # why the status could not be read, in words; empty when it was


@dataclass(frozen=True)
class RyoCollection:
    by_candidate: tuple[tuple[str, tuple[str, ...]], ...]
    shared: tuple[str, ...]  # the observation items of the cycle-wide tools
    records: tuple[dict[str, canonical.JsonValue], ...]  # every item's record, interpretation items included
    failures: tuple[Failure, ...]
    reads: tuple[ryo_read.Read, ...] = ()  # RYO's own verdict and plan for each candidate its deep analysis answered (arm R)

    def ids(self) -> dict[str, tuple[str, ...]]:
        return dict(self.by_candidate)


def _wait_check(wait_s: object) -> None:
    if isinstance(wait_s, bool) or not isinstance(wait_s, int | float) or wait_s <= 0:
        raise RyoCollectError("the wait is a positive number of seconds")


def _failed(reason: str, text: str, capture: RawCapture | None = None) -> Scan:
    kept = None if capture is None else Capture(capture.capture_id, capture.body_sha256)
    return Scan(Failed(reason), kept, detail(text))


def key_status(env: RyoEnvironment, *, wait_s: float, caller: str = "cycle") -> KeyStatus:
    """RYO's own word on whether this key may call, from ``GET /whoami``, which spends no tool call (the builder guide). A status that
    cannot be read says nothing either way: the cycle goes on and its calls fail on their own if they must, so a broken status never
    invents a refusal and never invents a permission."""
    _wait_check(wait_s)
    deadline = env.clock.now() + timedelta(seconds=wait_s)
    got = answer(env.captures, env.runner.submit(Call(PROVIDER, "_whoami", {}, Priority.P0, deadline, caller)), wait_s)
    if isinstance(got, Problem):
        return KeyStatus(None, None, detail(f"whoami: {got.detail}"))
    try:
        who = read_whoami(got.body)
    except UNREADABLE as exc:
        return KeyStatus(None, None, detail(f"whoami: {why(exc)}"))
    return KeyStatus(who.call_allowed, who.call_remaining, "")


def scan(env: RyoEnvironment, *, top_n: int, wait_s: float, caller: str = "cycle", earmark: int | None = None) -> Scan:
    """Ask ``scan_market`` for the top ``top_n`` and read the answer as the manifest's rows."""
    _wait_check(wait_s)
    deadline = env.clock.now() + timedelta(seconds=wait_s)
    call = Call(PROVIDER, "scan_market", {"top_n": top_n}, Priority.P0, deadline, caller, window=earmark)
    got = answer(env.captures, env.runner.submit(call), wait_s)
    if isinstance(got, Problem):
        return _failed(SCAN_TIMED_OUT if got.reason is Reason.TIMED_OUT else SCAN_CALL_FAILED, got.detail)
    try:
        envelope = read_rest_success(got.body, "scan_market")
        if envelope.status == "unavailable":
            return _failed(SCAN_UNAVAILABLE, "RYO says the scan is unavailable", got)
        if envelope.data_mode != "live":
            return _failed(SCAN_NOT_LIVE, f"RYO's data mode is {envelope.data_mode!r}, not 'live'", got)
        walked = walk("scan_market", envelope.data)
    except UNREADABLE as exc:
        return _failed(SCAN_UNREADABLE, why(exc), got)
    found: dict[int, dict[str, str | bool | None]] = {}
    for item in walked.fields:
        head, _, member = item.path.partition("].")
        if head.startswith("candidates[") and member in ("rank", "symbol"):
            found.setdefault(int(head[len("candidates[") :]), {})[member] = item.value
    if not found:
        return _failed(SCAN_EMPTY, "the scan holds no candidate", got)
    try:
        rows = [ScanRow(int(str(row["rank"])), str(row["symbol"])) for row in found.values()]
    except (KeyError, ValueError):
        return _failed(SCAN_UNREADABLE, "a candidate has no whole-number rank and symbol", got)
    ranks = [row.rank for row in rows]
    if len(rows) > MAX_ROWS or min(ranks) < 1 or len(set(ranks)) != len(ranks) or len({row.symbol for row in rows}) != len(rows):
        return _failed(SCAN_UNREADABLE, "the candidates do not have distinct ranks from 1 and distinct symbols", got)
    return Scan(tuple(sorted(rows, key=lambda row: row.rank)), Capture(got.capture_id, got.body_sha256), "")


def _arguments(tool: str, symbol: str | None) -> dict[str, canonical.JsonValue]:
    if symbol is None:
        return {}
    return {"symbol": symbol, "include_perp": True} if tool == DEEP_TOOL else {"symbol": symbol}


def collect_ryo(
    env: RyoEnvironment,
    candidates: Sequence[Candidate],
    symbols: Mapping[str, str],
    *,
    source_urn: str,
    wait_s: float = 600.0,
    caller: str = "cycle",
    earmark: int | None = None,
    budget: int | None = None,
) -> RyoCollection:
    """The evidence of RYO for every candidate (by its symbol) and for the cycle, from one deadline of ``wait_s`` seconds.

    ``budget`` is how many calls may still be asked of RYO. They are asked in the order of what matters most: ``analyze_token`` for every
    candidate, then the two cycle-wide tools, then ``deep_analysis`` for every candidate; what the budget does not cover is not asked and is
    a failure that says so.
    """
    _wait_check(wait_s)
    if any(c.candidate_id not in symbols or not symbols[c.candidate_id] for c in candidates):
        raise RyoCollectError("every candidate has the symbol RYO is asked about")
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int) or budget < 0):
        raise RyoCollectError("the budget is a whole number of calls, none or more")
    deadline = env.clock.now() + timedelta(seconds=wait_s)

    def submit(tool: str, args: dict[str, canonical.JsonValue]) -> Future[CallResult]:
        return env.runner.submit(Call(PROVIDER, tool, args, Priority.P0, deadline, caller, window=earmark))

    ordered = sorted(candidates, key=lambda c: c.candidate_id)
    plan: list[tuple[str, Candidate | None]] = [(QUICK_TOOL, c) for c in ordered]
    plan += [(tool, None) for tool in CYCLE_TOOLS]
    plan += [(DEEP_TOOL, c) for c in ordered]
    failures: list[Failure] = []
    pending: list[tuple[str, Candidate | None, dict[str, canonical.JsonValue], Future[CallResult]]] = []
    for position, (tool, asked) in enumerate(plan):
        owner = CYCLE_OWNER if asked is None else asked.candidate_id
        if budget is not None and position >= budget:
            failures.append(
                Failure(owner, Reason.CALL_FAILED, detail(f"{tool}: not asked: the RYO budget for this hour is spent"), source=SOURCE)
            )
            continue
        args = _arguments(tool, None if asked is None else symbols[asked.candidate_id])
        pending.append((tool, asked, args, submit(tool, args)))
    ids: dict[str, list[str]] = {}
    shared: list[str] = []
    records: list[dict[str, canonical.JsonValue]] = []
    reads: list[ryo_read.Read] = []
    for tool, candidate, args, future in pending:
        owner = CYCLE_OWNER if candidate is None else candidate.candidate_id
        remaining = max((deadline - env.clock.now()).total_seconds(), 0.0)
        got = answer(env.captures, future, remaining)
        if isinstance(got, Problem):
            failures.append(Failure(owner, got.reason, detail(f"{tool}: {got.detail}"), source=SOURCE))
            continue
        try:
            made = items_from(got, tool, args, ADAPTER_VERSION)
            items = [made.observation] + ([made.interpretation] if made.interpretation is not None else [])
            if candidate is not None:
                ref = ((candidate.asset_id, f"ryo:{symbols[candidate.candidate_id]}", "RESOLVED"),)
                items = [dataclasses.replace(item, asset_refs=ref) for item in items]
            built: list[tuple[EvidenceItem, dict[str, canonical.JsonValue]]] = []
            for item in items:
                record = item.to_record(source_urn, item.known_at_ms)
                env.store.add(item, record_digest(record))
                built.append((item, record))
        except (*UNREADABLE, RyoEvidenceError, EvidenceError, LineageError) as exc:
            failures.append(
                Failure(
                    owner,
                    Reason.UNREADABLE,
                    detail(f"{tool}: {why(exc) if isinstance(exc, ConnectorError) else str(exc)}"),
                    source=SOURCE,
                )
            )
            continue
        records.extend(record for _, record in built)
        if tool == DEEP_TOOL and candidate is not None and made.interpretation is not None:
            read = ryo_read.read_of(
                candidate.candidate_id,
                symbols[candidate.candidate_id],
                built[-1][0].evidence_id,
                made.interpretation.field("verdict.call"),
                got.capture_id,
                got.body,
            )
            if read is not None:
                reads.append(read)
        listed = built[0][0].evidence_id  # the observation item; the interpretation item stays out of every list an arm reads
        if candidate is None:
            shared.append(listed)
        else:
            ids.setdefault(candidate.candidate_id, []).append(listed)
    return RyoCollection(
        tuple((cid, tuple(found)) for cid, found in sorted(ids.items())),
        tuple(shared),
        tuple(records),
        tuple(sorted(failures, key=lambda f: (f.candidate_id, f.detail))),
        tuple(reads),  # in candidate order: the deep reads are asked in that order
    )
