"""One cycle of the league, from the slot to the committed forecasts: the steps of ``CYCLE_RUNNER_SPEC`` section 2 in order.

The services are given (the registry, the gateway's runner, the ledger, the attestor, the clock, the protocol and the frozen
mappings); nothing here opens a socket or reads a file. A cycle either ends ``COMMITTED``, with every slot of every arm and
horizon accounted for, or ends ``ABANDONED`` with a reason from the closed list, with every slot of its manifest recorded as
failed and nothing committed.

* **What is appended as the cycle goes, and what is committed together.** The steps of the cycle up to the running state, and
  the manifest as soon as it is sealed, are appended one by one (so a restart can see how far a cycle got and, from the
  manifest, which slots it had). The evidence, the snapshot, the forecasts and the first steps of every slot, and the
  committing step, go into one batch (``commit``), so that one checkpoint holds them and one set of tokens stamps them.
  Everything that needs a level (the committed and no-call steps of the slots, the origin wait, the cycle's final step) comes
  after the batch, because it names what the batch reached.
* **The batch root of the committing step** is the Merkle root of the digests of the batch's records, in the order they are
  submitted, without the committing record itself (a record cannot hold the root of a tree that contains it).
* **Failures.** An error that is not an outcome (a defect, a ledger that refuses) ends the cycle ``ABANDONED(PROCESS_LOST)``
  as far as the ledger still takes records, and is raised again: nothing is swallowed. The outcomes that are not defects (a
  venue clock that disagrees with ours, a snapshot taken too long after the discovery, a snapshot too old by the time it
  would be committed) abandon the cycle with their own reason and return normally.
* **A slot that was a no-call or a failure of evidence is not a forecast.** The rules arm's no-call reasons ``UNMAPPED`` and
  ``NO_RANGE`` are recorded as the lifecycle's ``OUT_OF_SCOPE`` (the state is outside what the frozen mapping covers); a
  missing or unknown feature is ``FAILED_EVIDENCE`` and never an abstention.
* **The origin is after the close.** Each forecast becomes ``AWAITING_ORIGIN`` with the eligible time of its batch; a horizon
  whose gap from the snapshot to the origin is beyond its bound is void for that horizon at once (``VOID_GAP``), with its
  settlement record.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, cast

from hoshitori import (
    arm_run_record,
    barrier_arm,
    canonical,
    collect,
    crosscheck,
    cycle,
    forecast,
    lifecycle,
    manifest,
    model_arms,
    model_call_record,
    oracle,
    rules_arm,
    ryo_collect,
    settlement,
    settlement_record,
    snapshot,
)
from hoshitori import ryo_read as arm_r
from hoshitori.barrier_rates import K_STOP, K_TARGET, BarrierRates
from hoshitori.commit import Attestor, Commitment, commit_batch
from hoshitori.evidence import FamilyKind, record_digest
from hoshitori.ids import natural_id
from hoshitori.ledger import Ledger
from hoshitori.merkle import MerkleTree, leaf_hash
from hoshitori.protocol import Protocol
from hoshitori.rejudge import Judged
from hoshitori.rules_arm import BaseRates, Verdict
from hoshitori.ryo import PROVIDER
from hoshitori.settlement import Side
from hoshitori.timeline import cycle_order_ok, from_ms, origin, void_gap
from hoshitori.triggers import Impact, equal_impact, registry_triggers
from hoshitori.trusted_root import TimeStampRules

HOUR_MS: Final = 3_600_000
SCAN_OFF: Final = "the RYO scan route is off until the metered probe is approved"
RYO_SOURCE: Final = "ryo"  # the optional source of a protocol whose league reads RYO
SCAN_NOT_THIS_HOUR: Final = "RYO_READ_ONCE_A_DAY"  # the manifest's reason in the cycles outside the daily RYO hour
SCAN_BUDGET: Final = "RYO_BUDGET_SPENT"  # the manifest's reason when the hour's RYO budget was spent before the scan
NO_CALL_REASON: Final = "OUT_OF_SCOPE"  # the lifecycle's name for a state the frozen mapping does not cover
ABANDONED_FAILURE: Final = "CYCLE_ABANDONED"


class LeagueError(ValueError):
    """The services or the slot are not ones a cycle can run with."""


@dataclass(frozen=True)
class RyoWiring:
    """RYO as a source of this league: the gateway objects it is read through, and how long a cycle waits for it.

    ``scan_wait_s`` bounds the scan that proposes candidates, before discovery is fixed; ``collect_wait_s`` bounds the per-candidate and
    cycle-wide reads that come before the venue collection, so that they end well inside the ten minutes a snapshot may be taken after
    discovery. Both are waits for answers RYO gave in 2 to 46 seconds in one measured session and in 40 to 161 seconds in an earlier one.
    """

    env: ryo_collect.RyoEnvironment
    scan_wait_s: float = 200.0
    collect_wait_s: float = 360.0
    calls_per_hour: int = 40  # what a league may ask of RYO in an hour; counted from the capture store, so a restart does not reset it
    daily_hour: int | None = (
        None  # when set, RYO is read only in the cycle planned for this UTC hour: a key's monthly allowance lasts the month
    )

    def __post_init__(self) -> None:
        if self.daily_hour is not None and (isinstance(self.daily_hour, bool) or not 0 <= self.daily_hour <= 23):
            raise LeagueError("the daily RYO hour is a UTC hour, 0 to 23")


@dataclass(frozen=True)
class Services:
    protocol: Protocol
    mappings: Mapping[str, BaseRates]  # by arm id; each must be the mapping the protocol names
    env: collect.Environment
    ledger: Ledger
    attestor: Attestor
    timing_rules: TimeStampRules
    source_urn: str
    impact: Impact = equal_impact
    barrier_mappings: Mapping[str, BarrierRates] = field(default_factory=dict)  # by arm id; the arms that also forecast contract B
    models: model_arms.ModelArms | None = None  # the model arms the protocol pins, with the client that asks the model
    ryo: RyoWiring | None = None  # RYO, when the protocol lists it as an optional source; absent, the scan route stays off
    rejudge: Callable[[list[dict[str, Any]]], Judged | None] | None = None  # what the kept time-stamps support for a batch, for recovery


class Fate(StrEnum):
    FORECAST = "FORECAST"  # committed, awaiting its origin
    VOID_GAP = "VOID_GAP"  # committed, and void for this horizon because the origin is too far from the snapshot
    NO_CALL = "NO_CALL"
    FAILED_EVIDENCE = "FAILED_EVIDENCE"
    FAILED_LATE = "FAILED_LATE"  # a model arm answered after its cutoff; the answer is discarded
    FAILED = "FAILED"  # the cycle was abandoned, or a model arm could not answer


@dataclass(frozen=True)
class SlotResult:
    forecast_id: str
    candidate_id: str
    symbol: str
    arm_id: str
    hours: int
    fate: Fate
    reason: str | None
    level: str | None  # the level the commitment reached for this slot, if it was committed
    origin_ms: int | None  # when the forecast's window opens, for the forecasts that are awaiting it
    contract: str


@dataclass(frozen=True)
class Outcome:
    cycle_id: str
    history: cycle.History
    reason: str | None  # why the cycle was abandoned
    commitment: Commitment | None
    slots: tuple[SlotResult, ...]
    failures: tuple[collect.Failure, ...]

    @property
    def state(self) -> cycle.State:
        return cycle.current_state(self.history)


# --- helpers ----------------------------------------------------------------------------------------------------------------------------
def _check_services(sv: Services) -> None:
    protocol = sv.protocol
    if not sv.source_urn.startswith("urn:"):
        raise LeagueError("the source is a urn")
    if set(sv.mappings) != {arm for arm, _ in protocol.arms}:
        raise LeagueError("the mappings are those of the protocol's arms, no more and no fewer")
    for arm, mapping_hash in protocol.arms:
        if sv.mappings[arm].sha256 != mapping_hash:
            raise LeagueError(f"the mapping of arm {arm} is not the one the protocol pins")
        if (
            sv.mappings[arm].period_end_ms > protocol.schedule_start_ms
        ):  # tuning is allowed only on data before the scored period (contract 9)
            raise LeagueError(f"the mapping of arm {arm} was calibrated on data that runs into the scored period")
    if set(sv.barrier_mappings) != {arm for arm, _ in protocol.barrier_arms}:
        raise LeagueError("the barrier mappings are those of the protocol's barrier arms, no more and no fewer")
    for arm, mapping_hash in protocol.barrier_arms:
        if sv.barrier_mappings[arm].sha256 != mapping_hash:
            raise LeagueError(f"the barrier mapping of arm {arm} is not the one the protocol pins")
        if sv.barrier_mappings[arm].period_end_ms > protocol.schedule_start_ms:
            raise LeagueError(f"the barrier mapping of arm {arm} was calibrated on data that runs into the scored period")
    if protocol.primary_venue not in sv.env.adapters:
        raise LeagueError("the environment has an adapter for the protocol's primary oracle venue")
    if (sv.ryo is not None) != (RYO_SOURCE in protocol.optional_sources):
        raise LeagueError("RYO is wired exactly when the protocol lists it as an optional source")
    if sv.ryo is not None and (sv.ryo.env.store is not sv.env.store or sv.ryo.env.captures is not sv.env.captures):
        raise LeagueError("RYO is read into the cycle's own evidence store and capture store")
    try:
        model_arms.check_against(sv.models, protocol)
    except model_arms.ModelArmsError as exc:
        raise LeagueError(str(exc)) from exc


def _unread(failures: Sequence[collect.Failure], candidates: Sequence[snapshot.Candidate]) -> dict[str, list[snapshot.Unavailable]]:
    """What the collection could not read, by candidate, for the snapshot: the source, the closed reason and the window, never the words."""
    known = {c.candidate_id for c in candidates}
    out: dict[str, list[snapshot.Unavailable]] = {}
    for f in failures:
        if f.candidate_id in known and f.source:
            out.setdefault(f.candidate_id, []).append(snapshot.Unavailable(f.source, f.reason.value, f.interval_s))
    return {cid: list(dict.fromkeys(entries)) for cid, entries in out.items()}  # the same failure twice is one entry


def _scan_failure(scan: ryo_collect.Scan) -> tuple[collect.Failure, ...]:
    """The scan's failure in words, for the outcome: the manifest holds only the closed reason, and a person wants to know why."""
    if not scan.detail:
        return ()
    return (
        collect.Failure(
            ryo_collect.CYCLE_OWNER,
            collect.Reason.CALL_FAILED,
            collect.detail(f"scan_market: {scan.detail}"),
            source=ryo_collect.SOURCE,
        ),
    )


def _left(sv: Services) -> int:
    """How many calls the hour's budget leaves for RYO: the budget less every attempt it has been sent in the last hour."""
    assert sv.ryo is not None
    spent = sv.ryo.env.captures.count_since(PROVIDER, sv.env.clock.now() - timedelta(hours=1))
    return max(sv.ryo.calls_per_hour - spent, 0)


def _reads_ryo(sv: Services, slot_ms: int) -> bool:
    """Whether the cycle planned for ``slot_ms`` reads RYO: RYO is wired and, if it is read once a day, this is the cycle of that hour."""
    return sv.ryo is not None and (sv.ryo.daily_hour is None or slot_ms // HOUR_MS % 24 == sv.ryo.daily_hour)


def _scan(sv: Services, protocol: Protocol, slot_ms: int) -> ryo_collect.Scan:
    """The RYO scan of a cycle, or the closed statement that the route is off, that RYO is read in another hour, or that the hour's
    budget is spent."""
    if sv.ryo is None:
        return ryo_collect.Scan(manifest.Failed(SCAN_OFF), None, "")
    if not _reads_ryo(sv, slot_ms):
        return ryo_collect.Scan(manifest.Failed(SCAN_NOT_THIS_HOUR), None, "RYO is read once a day, in another hour")
    if _left(sv) == 0:
        return ryo_collect.Scan(manifest.Failed(SCAN_BUDGET), None, "the RYO budget for this hour is spent")
    status = ryo_collect.key_status(sv.ryo.env, wait_s=sv.ryo.scan_wait_s)
    if status.allowed is False:
        return ryo_collect.Scan(manifest.Failed(ryo_collect.KEY_NOT_ALLOWED), None, "RYO's key status says this key may not call now", 0)
    if status.remaining == 0:
        return ryo_collect.Scan(manifest.Failed(ryo_collect.KEY_SPENT), None, "RYO's key status leaves no call in this period", 0)
    scanned = ryo_collect.scan(sv.ryo.env, top_n=protocol.scan_n, wait_s=sv.ryo.scan_wait_s)
    return replace(scanned, left=None if status.remaining is None else status.remaining - 1)


def _ryo(
    sv: Services, candidates: tuple[snapshot.Candidate, ...], symbols: Mapping[str, str], left: int | None
) -> ryo_collect.RyoCollection:
    """RYO's evidence for the candidates, within the hour's budget and within what RYO's key status leaves (``left``), whichever is less."""
    assert sv.ryo is not None
    budget = _left(sv) if left is None else min(_left(sv), left)
    return ryo_collect.collect_ryo(sv.ryo.env, candidates, symbols, source_urn=sv.source_urn, wait_s=sv.ryo.collect_wait_s, budget=budget)


def _merged(venues: Mapping[str, tuple[str, ...]], read: ryo_collect.RyoCollection | None) -> dict[str, tuple[str, ...]]:
    """The evidence ids of each candidate: the venues' first, RYO's after."""
    merged = dict(venues)
    if read is not None:
        for candidate_id, ids in read.ids().items():
            merged[candidate_id] = (*merged.get(candidate_id, ()), *ids)
    return merged


def _price_checks(
    sv: Services, by_candidate: Mapping[str, tuple[str, ...]], built_at_ms: int
) -> tuple[dict[str, tuple[str, ...]], tuple[dict[str, canonical.JsonValue], ...]]:
    """The comparison of RYO's price with the exchange's for each candidate that has both, added to the evidence store: the items' ids by
    candidate and their records. Where one of the two is missing there is nothing to compare and nothing is added."""
    store = sv.env.store
    ids: dict[str, tuple[str, ...]] = {}
    records: list[dict[str, canonical.JsonValue]] = []
    for candidate_id, held in sorted(by_candidate.items()):
        made = crosscheck.candidate_checks(((store.get(i), store.digest(i)) for i in held), built_at_ms)
        if not made:
            continue
        for item in made:
            record = item.to_record(sv.source_urn, built_at_ms)
            store.add(item, record_digest(record))
            records.append(record)
        ids[candidate_id] = tuple(item.evidence_id for item in made)
    return ids, tuple(records)


def _trigger_log_hash(triggers: tuple[manifest.Trigger, ...]) -> str:
    rows: list[canonical.JsonValue] = [
        {
            "id": t.trigger_id,
            "rule": t.rule,
            "version": t.rule_version,
            "symbol": t.symbol,
            "primary": t.primary,
            "families": cast(list[canonical.JsonValue], sorted(f"{f.kind.value}:{f.name}" for f in t.families)),
            "first_seen_at_ms": t.first_seen_at_ms,
            "impact": format(t.impact, "f"),
        }
        for t in triggers
    ]
    return hashlib.sha256(canonical.dumps({"triggers": rows})).hexdigest()


def batch_root(records: list[dict[str, canonical.JsonValue]]) -> str:
    """The Merkle root of the digests of a batch's records in submission order: what the committing step names."""
    return MerkleTree(leaf_hash(bytes.fromhex(record_digest(r))) for r in records).root().hex()


def put(ledger: Ledger, records: list[dict[str, canonical.JsonValue]]) -> None:
    """Acknowledge the records together and append them: a process killed in between leaves all of them queued or none, and the next tick
    appends what was queued (``scheduler.tick``), so a step of a cycle is never half there."""
    ledger.submit_many(records)
    ledger.append_pending()


@dataclass
class _Slot:
    forecast_id: str
    candidate_id: str
    asset_id: str
    symbol: str
    arm_id: str
    hours: int
    history: lifecycle.History
    contract: str
    forecast_record: dict[str, canonical.JsonValue] | None = None
    reason: str | None = None
    no_call_reason: str = NO_CALL_REASON  # a rules arm's no-call is recorded as out of scope; a model arm's has its own reason


class _Cycle:
    """The state of one run: the services, the history of the cycle and of each slot, and what has not yet been committed."""

    def __init__(self, sv: Services, history: cycle.History) -> None:
        self.sv, self.history = sv, history
        self.cycle_id = history[0].cycle_id
        self.batch: list[dict[str, canonical.JsonValue]] = []
        self.arm_records: list[dict[str, canonical.JsonValue]] = []  # what the model arms did, committed with the forecasts
        self.slots: dict[str, _Slot] = {}
        self.failures: tuple[collect.Failure, ...] = ()
        self.committed = (
            False  # the batch was committed and time-stamped: its forecasts are never abandoned, the recovery rolls them forward
        )

    def now(self) -> int:
        return collect.now_ms(self.sv.env.clock)

    def step(self, step: cycle.Step, *, append: bool = True) -> None:
        self.history = cycle.advance(self.history, step)
        record = cycle.to_record(self.history[-1], self.sv.source_urn, step.at_ms)
        if append:
            put(self.sv.ledger, [record])
        else:
            self.batch.append(record)

    def move(self, slot: _Slot, step: lifecycle.Step) -> lifecycle.Transition:
        slot.history = lifecycle.advance(slot.history, step)
        return slot.history[-1]

    def slot_record(self, slot: _Slot, transition: lifecycle.Transition) -> dict[str, canonical.JsonValue]:
        return lifecycle.to_record(transition, self.sv.source_urn, transition.step.at_ms)


def _abandon(run: _Cycle, reason: str) -> Outcome:
    """End the cycle: every slot of the manifest, if there is one, is recorded as failed, and nothing of the batch is committed."""
    at = run.now()
    records: list[dict[str, canonical.JsonValue]] = []
    for slot in run.slots.values():
        if lifecycle.current_state(slot.history) not in lifecycle.TERMINAL:
            step = lifecycle.Step(lifecycle.State.FAILED, max(at, slot.history[-1].step.at_ms), reason=f"{ABANDONED_FAILURE}: {reason}")
            run.move(slot, step)
        records.extend(run.slot_record(slot, t) for t in slot.history)
    records.extend(run.batch)  # the committing step, if the cycle got that far: the history in the ledger has no gap
    if cycle.current_state(run.history) not in cycle.TERMINAL:
        end = cycle.abandon_step(reason, max(at, run.history[-1].step.at_ms))
        run.history = cycle.advance(run.history, end)
        records.append(cycle.to_record(run.history[-1], run.sv.source_urn, end.at_ms))
    put(run.sv.ledger, records)
    return Outcome(run.cycle_id, run.history, reason, None, _results(run, None), run.failures)


def _results(run: _Cycle, commitment: Commitment | None) -> tuple[SlotResult, ...]:
    out = []
    for slot in sorted(run.slots.values(), key=lambda s: s.forecast_id):
        state = lifecycle.current_state(slot.history)
        level = None if commitment is None else _level(run, slot, commitment)
        opened = next((t.step for t in slot.history if t.step.state is lifecycle.State.AWAITING_ORIGIN), None)
        fates = {
            lifecycle.State.AWAITING_ORIGIN: Fate.FORECAST,
            lifecycle.State.VOID: Fate.VOID_GAP,
            lifecycle.State.NO_CALL: Fate.NO_CALL,
            lifecycle.State.FAILED_EVIDENCE: Fate.FAILED_EVIDENCE,
            lifecycle.State.FAILED_LATE: Fate.FAILED_LATE,
            lifecycle.State.FAILED: Fate.FAILED,
        }
        origin_ms = None if opened is None or state is not lifecycle.State.AWAITING_ORIGIN else origin(cast(int, opened.eligible_after_ms))
        out.append(
            SlotResult(
                slot.forecast_id,
                slot.candidate_id,
                slot.symbol,
                slot.arm_id,
                slot.hours,
                fates[state],
                slot.reason,
                level,
                origin_ms,
                slot.contract,
            )
        )
    return tuple(out)


def _level(run: _Cycle, slot: _Slot, commitment: Commitment) -> str | None:
    ident = slot.forecast_id if slot.forecast_record is not None else _decided_id(slot)
    if ident is None:
        return None
    try:
        return commitment.level_of(run.sv.source_urn, ident)
    except KeyError:
        return None


def _decided_id(slot: _Slot) -> str | None:
    found = next((t for t in slot.history if t.step.state is lifecycle.State.DECIDED), None)
    return None if found is None else found.transition_id


# --- the cycle --------------------------------------------------------------------------------------------------------------------------
def run_cycle(sv: Services, slot_ms: int, *, since_ms: int) -> Outcome:
    """Run the cycle planned for ``slot_ms``; ``since_ms`` is when the registry was last read for changes (the previous discovery)."""
    _check_services(sv)
    if not sv.protocol.is_slot(slot_ms):
        raise LeagueError("the slot is a slot of the protocol's schedule")
    if isinstance(since_ms, bool) or not isinstance(since_ms, int) or not 0 <= since_ms <= slot_ms:
        raise LeagueError("the registry was last read for changes no later than the slot")
    now = collect.now_ms(sv.env.clock)
    if now < slot_ms:
        raise LeagueError("the slot has not come")
    run = _Cycle(sv, cycle.begin(sv.protocol.sha256, slot_ms, now))
    if sv.ledger.find(sv.source_urn, run.history[0].transition_id) is not None:
        raise LeagueError("this slot has already been started: a cycle is never run twice")
    put(sv.ledger, [cycle.to_record(run.history[0], sv.source_urn, now)])
    try:
        return _go(run, since_ms)
    except Exception:
        try:
            if cycle.current_state(run.history) not in cycle.TERMINAL and not run.committed:
                _abandon(run, "PROCESS_LOST")
        except Exception:  # noqa: S110 - the original error is the one to raise; the ledger may be what failed
            pass
        raise


def _go(run: _Cycle, since_ms: int) -> Outcome:
    sv, protocol = run.sv, run.sv.protocol
    env = sv.env
    # discovery: the scan is read first, so that the discovery time is the time its answer was in hand
    slot_ms = cast(int, run.history[0].step.planned_for_ms)
    scan = _scan(sv, protocol, slot_ms)
    discovered = run.now()
    run.step(cycle.Step(cycle.State.DISCOVERING, discovered, discovery_at_ms=discovered))
    triggers = registry_triggers(env.registry, after_ms=min(since_ms, discovered), until_ms=discovered, impact=sv.impact)
    # the manifest
    sealed = run.now()
    resolve = oracle.resolver(env.registry, protocol, from_ms(sealed))
    inputs = manifest.Inputs(
        run.cycle_id,
        protocol.sha256,
        discovered,
        sealed,
        scan.rows,
        scan.capture,
        triggers,
        manifest.Capture(natural_id("tl", {"cycle": run.cycle_id}), _trigger_log_hash(triggers)),
        protocol.watchlist,
    )
    plan = manifest.build(inputs, protocol.manifest_rules(), resolve)
    put(sv.ledger, [manifest.to_record(plan, sv.source_urn, sealed)])  # at once: a restart finds the slots of a lost cycle from it
    run.step(cycle.Step(cycle.State.SEALED, sealed, sealed_at_ms=sealed, manifest_hash=plan.sha256))
    candidates = snapshot.candidates_of(plan)
    symbols = {entry.candidate_id: entry.symbol for entry in plan.union()}
    for candidate in candidates:
        for arm, contract, hours in protocol.slot_plan():  # the same plan the recovery of a lost cycle looks for
            horizon = hours * HOUR_MS
            fid = forecast.forecast_id(run.cycle_id, arm, candidate.asset_id, contract, horizon)
            history = lifecycle.begin(fid, contract, horizon, sealed)
            history = lifecycle.advance(history, lifecycle.Step(lifecycle.State.CREATED, sealed))
            run.slots[fid] = _Slot(
                fid, candidate.candidate_id, candidate.asset_id, symbols[candidate.candidate_id], arm, hours, history, contract
            )
    # collection. RYO is read first, at the pace its fan-out limit allows (five calls a minute: minutes for a few candidates). The exchange
    # series are read last because they must be younger than their limit of a minute when the snapshot is taken, and RYO's limit is longer.
    run.step(cycle.Step(cycle.State.COLLECTING, run.now()))
    ryo_read = _ryo(sv, candidates, symbols, scan.left) if _reads_ryo(sv, slot_ms) else None
    collection = collect.collect(
        env,
        candidates,
        source_urn=sv.source_urn,
        caller="cycle",
        windows=protocol.collect_windows(),
        wait_s=protocol.wait_s,
        optional_sources=tuple(name for name in protocol.optional_sources if name != RYO_SOURCE),
    )
    run.failures = (*_scan_failure(scan), *collection.failures, *(ryo_read.failures if ryo_read is not None else ()))
    if any(abs(offset.offset_ms) > protocol.clock_offset_limit_ms for offset in collection.offsets):
        return _abandon(run, "CLOCK_OFFSET")
    # the snapshot
    taken = run.now()
    if not cycle_order_ok(discovered, sealed, taken):
        return _abandon(run, "DISCOVERY_SNAPSHOT_GAP")
    freshness = {FamilyKind(name): limit for name, limit in protocol.freshness_ms} or snapshot.DEFAULT_FRESHNESS
    held = _merged(collection.ids(), ryo_read)
    checked, check_records = _price_checks(sv, held, taken)
    frozen = snapshot.freeze(
        env.store,
        cycle_id=run.cycle_id,
        manifest_hash=plan.sha256,
        snapshot_at_ms=taken,
        candidates=candidates,
        shared=ryo_read.shared if ryo_read is not None else [],
        by_candidate={cid: (*ids, *checked.get(cid, ())) for cid, ids in held.items()},
        required=protocol.required_sources,
        freshness=freshness,
        unavailable=_unread(run.failures, candidates),
    )
    run.step(cycle.Step(cycle.State.SNAPSHOT_FROZEN, taken, snapshot_at_ms=taken, snapshot_hash=frozen.sha256))
    # the arms
    run.step(cycle.Step(cycle.State.RUNNING, run.now()))
    reader = snapshot.Reader(frozen, snapshot.Access.INDEPENDENT)
    digests = {item.evidence_id: digest for item, digest in frozen.items}
    decided = run.now()
    for slot in run.slots.values():
        if slot.arm_id not in model_arms.ORDER:
            _decide(run, slot, reader, digests, taken, decided)
    _decide_models(run, reader, digests, candidates, symbols, taken)
    # the commitment
    if cycle.snapshot_expired(run.history, run.now(), protocol.snapshot_freshness_ms):
        return _abandon(run, "SNAPSHOT_TOO_OLD")
    snapshot_record = snapshot.to_record(frozen, sv.source_urn, taken)
    reads = [arm_r.to_record(read.body(run.cycle_id), sv.source_urn, taken) for read in (ryo_read.reads if ryo_read is not None else ())]
    members = [*collection.records, *(ryo_read.records if ryo_read is not None else ()), *reads, *check_records, snapshot_record]
    for slot in run.slots.values():
        if slot.forecast_record is not None:
            members.append(slot.forecast_record)
        members.extend(run.slot_record(slot, t) for t in slot.history)
    members.extend(run.arm_records)
    root = batch_root(members)
    run.step(cycle.Step(cycle.State.COMMITTING, run.now(), batch_root=root, batch_size=len(members)), append=False)
    commitment = commit_batch(sv.ledger, [*members, *run.batch], attestor=sv.attestor, rules=sv.timing_rules)
    run.committed = True
    _after(run, commitment, taken)
    return Outcome(run.cycle_id, run.history, None, commitment, _results(run, commitment), run.failures)


def _decide(run: _Cycle, slot: _Slot, reader: snapshot.Reader, digests: dict[str, str], taken: int, decided: int) -> None:
    protocol = run.sv.protocol
    run.move(slot, lifecycle.Step(lifecycle.State.EVIDENCE_FROZEN, taken))
    if slot.contract == "B":
        barrier_mapping = run.sv.barrier_mappings[slot.arm_id]
        outcome = barrier_arm.decide(reader, slot.candidate_id, barrier_mapping, slot.hours)
        version = barrier_arm.agent_version_hash(barrier_mapping)
        verdict, reason, evidence_ids, observation_hash, invalidation = (
            outcome.verdict,
            outcome.reason,
            outcome.evidence_ids,
            outcome.observation_set_hash,
            outcome.invalidation,
        )
    else:
        mapping = run.sv.mappings[slot.arm_id]
        decision = rules_arm.decide(reader, slot.candidate_id, mapping, slot.hours)
        version = rules_arm.agent_version_hash(mapping)
        verdict, reason, evidence_ids, observation_hash, invalidation = (
            decision.verdict,
            decision.reason,
            decision.evidence_ids,
            decision.observation_set_hash,
            decision.invalidation,
        )
    if verdict is Verdict.FAILED_EVIDENCE:
        slot.reason = cast(rules_arm.Reason, reason).value
        run.move(slot, lifecycle.Step(lifecycle.State.FAILED_EVIDENCE, decided, reason=slot.reason))
        return
    run.move(slot, lifecycle.Step(lifecycle.State.DECIDED, decided))
    if verdict is Verdict.NO_CALL:
        slot.reason = cast(rules_arm.Reason, reason).value
        return
    common = forecast.Common(
        run.cycle_id,
        slot.arm_id,
        version,
        protocol.sha256,
        slot.contract,
        slot.asset_id,
        slot.symbol,
        protocol.quote_class,
        protocol.instrument_kind,
        protocol.oracle_pair,
        protocol.primary_venue,
        protocol.second_venue,
        taken,
        slot.hours * HOUR_MS,
        invalidation,
        tuple((ident, digests[ident]) for ident in evidence_ids),
        observation_hash,
        False,
    )
    made: forecast.Forecast
    if slot.contract == "B":
        p_target, p_stop, p_timeout = cast(tuple[Decimal, Decimal, Decimal], outcome.p)
        made = forecast.BarrierForecast(common, Side.LONG, cast(Decimal, outcome.atr), K_TARGET, K_STOP, p_target, p_stop, p_timeout)
    else:
        made = forecast.DirectionalForecast(common, cast(Decimal, decision.p))
    slot.forecast_record = forecast.to_record(made, run.sv.source_urn, decided)


def _decide_models(
    run: _Cycle,
    reader: snapshot.Reader,
    digests: dict[str, str],
    candidates: tuple[snapshot.Candidate, ...],
    symbols: dict[str, str],
    taken: int,
) -> None:
    """Ask the model arms, the candidates and horizons at the same time, and then put each answer into its slot in a fixed order."""
    models, protocol = run.sv.models, run.sv.protocol
    groups = sorted({(slot.candidate_id, slot.hours) for slot in run.slots.values() if slot.arm_id in model_arms.ORDER})
    if not groups:
        return
    assert models is not None
    by_id = {candidate.candidate_id: candidate for candidate in candidates}

    def now() -> int:
        return collect.now_ms(run.sv.env.clock)

    def work(group: tuple[str, int]) -> tuple[model_arms.Slotted, ...]:
        candidate_id, hours = group
        return model_arms.run_group(models, protocol, now, reader, by_id[candidate_id], symbols[candidate_id], hours, taken)

    with ThreadPoolExecutor(max_workers=models.workers) as pool:
        answered = list(pool.map(work, groups))  # a defect in any group is raised here, and the cycle is abandoned
    for (candidate_id, hours), slotted in zip(groups, answered, strict=True):
        for result in slotted:
            slot = next(s for s in run.slots.values() if (s.candidate_id, s.hours, s.arm_id) == (candidate_id, hours, result.arm))
            _apply_model(run, slot, result, digests, taken)


def _apply_model(run: _Cycle, slot: _Slot, result: model_arms.Slotted, digests: dict[str, str], taken: int) -> None:
    """One model arm's answer into its slot: the lifecycle, the forecast if it called, and the records of what it did."""
    protocol, source = run.sv.protocol, run.sv.source_urn
    run.move(slot, lifecycle.Step(lifecycle.State.EVIDENCE_FROZEN, taken))
    finished = result.finished_ms  # never before the snapshot: a cycle whose clock steps back is refused where it first reads it
    for call in result.calls:
        run.arm_records.append(model_call_record.to_record(call, source, finished))
    body = model_arms.run_body(result, run.cycle_id, slot.forecast_id, slot.candidate_id, slot.hours)
    run.arm_records.append(arm_run_record.to_record(body, source, finished))
    slot.reason = result.reason
    if result.verdict == "FAILED_LATE":
        run.move(slot, lifecycle.Step(lifecycle.State.FAILED_LATE, finished, reason=result.reason))
        return
    if result.verdict == "FAILED_EVIDENCE":
        run.move(slot, lifecycle.Step(lifecycle.State.FAILED_EVIDENCE, finished, reason=result.reason))
        return
    if result.verdict == "FAILED":
        run.move(slot, lifecycle.Step(lifecycle.State.FAILED, finished, reason=result.reason))
        return
    run.move(slot, lifecycle.Step(lifecycle.State.DECIDED, finished))
    if result.verdict == "NO_CALL":
        slot.no_call_reason = cast(str, result.reason)
        return
    common = forecast.Common(
        run.cycle_id,
        slot.arm_id,
        result.version,
        protocol.sha256,
        "D",
        slot.asset_id,
        slot.symbol,
        protocol.quote_class,
        protocol.instrument_kind,
        protocol.oracle_pair,
        protocol.primary_venue,
        protocol.second_venue,
        taken,
        slot.hours * HOUR_MS,
        result.invalidation,
        tuple((ident, digests[ident]) for ident in result.evidence_ids),
        result.observation_set_hash,
        False,
    )
    slot.forecast_record = forecast.to_record(forecast.DirectionalForecast(common, cast(Decimal, result.p)), source, finished)


def _after(run: _Cycle, commitment: Commitment, taken: int) -> None:
    """What needs the levels: the committed and no-call steps of the slots, the wait for the origin, and the end of the cycle."""
    sv = run.sv
    at = max(run.now(), commitment.attestation.committed_at_ms)  # the ledger's clock and ours may differ by a little
    eligible = commitment.attestation.eligible_after_ms
    records: list[dict[str, canonical.JsonValue]] = []
    for slot in sorted(run.slots.values(), key=lambda s: s.forecast_id):
        state = lifecycle.current_state(slot.history)
        if state in lifecycle.FAILURES:
            continue
        if slot.forecast_record is None:  # decided to make no call
            level = forecast.Level(commitment.level_of(sv.source_urn, cast(str, _decided_id(slot))))
            no_call = lifecycle.Step(lifecycle.State.NO_CALL, at, reason=slot.no_call_reason, level=level)
            records.append(run.slot_record(slot, run.move(slot, no_call)))
            continue
        committed = next(c for c in commitment.records if c.record_id == slot.forecast_id)
        step = lifecycle.Step(
            lifecycle.State.COMMITTED, at, level=forecast.Level(committed.level), committed_at_ms=committed.committed_at_ms
        )
        records.append(run.slot_record(slot, run.move(slot, step)))
        wait = lifecycle.Step(lifecycle.State.AWAITING_ORIGIN, at, eligible_after_ms=eligible)
        if committed.level == forecast.Level.C3.value:  # a C3 slot names the numbers its origin follows from
            attestation = commitment.attestation
            wait = replace(
                wait,
                attested_at_ms=attestation.attested_at_ms,
                uncertainty_ms=attestation.uncertainty_ms,
                spread_ms=round(cast(float, attestation.timing.spread_s) * 1000),
            )
        records.append(run.slot_record(slot, run.move(slot, wait)))
        horizon = slot.hours * HOUR_MS
        if void_gap(taken, origin(eligible), horizon):
            voided = settlement.void(slot.forecast_id, slot.contract, sv.protocol.oracle_pair, "VOID_GAP")
            digest = record_digest(slot.forecast_record)
            records.append(settlement_record.to_record(voided, digest, sv.source_urn, at))
            step = lifecycle.Step(lifecycle.State.VOID, at, reason="VOID_GAP", settlement_id=voided.settlement_id)
            records.append(run.slot_record(slot, run.move(slot, step)))
    final = cycle.Step(
        cycle.State.COMMITTED, at, commitment_id=commitment.commitment_id, committed_at_ms=commitment.attestation.committed_at_ms
    )
    run.history = cycle.advance(run.history, final)
    records.append(cycle.to_record(run.history[-1], sv.source_urn, at))
    put(sv.ledger, records)
