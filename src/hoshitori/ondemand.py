"""A Desk call as an on-demand forecast: sealed before anything is time-stamped, and never part of the league (plan J-01).

Contract sections 2, 16, 24 and 33 are the rules. A person chose this case after seeing the world, so the forecast is class ``ON_DEMAND``.
It reuses the league's ledger, attestation, origin and settlement code and lives in a ledger of its own. No number of it is ever added to a
league score, ranking, calibration or comparison. A time-stamp protects the forecast's future measurement; it does not repair the choice of
the case.

* **Only a forecast is submitted.** A Desk commit whose final stance is flat carries no prediction (a flat position at probability one half
  is a reference, not a call): it is returned as ``NoForecast`` and nothing is time-stamped. A long stance is a directional probability,
  contract D.
* **Sealed first.** The complete forecast is built and checked before a token is requested; a refusal writes nothing.
* **Stale evidence is refused before any token is requested** (section 16, the interactive regime): the evidence may be at most
  ``MAX_EVIDENCE_AGE_MS`` older than the commit, and may not come from after it.
* **The name is natural.** The cycle id is the class prefix and the run id, so the same run is the same forecast and a second submission
  finds the first.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, cast

from hoshitori import canonical, commitment_store, desk, desk_gates, forecast, lifecycle, registry_job
from hoshitori.commit import Attestor, Commitment, attest_appended, commit_batch
from hoshitori.league import put
from hoshitori.ledger import Ledger
from hoshitori.lineage import EvidenceStore
from hoshitori.registry import Registry
from hoshitori.scheduler import waiting
from hoshitori.settle_job import Passed, SettleServices, settle_pass
from hoshitori.timeline import origin
from hoshitori.trusted_root import TimeStampRules

CLASS: Final = "ON_DEMAND"
ARM_ID: Final = "ON_DEMAND"
CYCLE_PREFIX: Final = "od-"
MAX_EVIDENCE_AGE_MS: Final = 900_000  # the loosest source limit of section 16 (news); the Desk's own staleness gate holds the stricter ones
_RUN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
SOURCE_PREFIX: Final = "urn:hoshitori:ondemand:"
INVALIDATION: Final = (
    "The move began before the announcement or the market moved alike.",
)  # the Desk's own sentence: no text of a source or a person
_IDENT: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HASH: Final = re.compile(r"[0-9a-f]{64}")
LONG: Final = "LONG"
FLAT: Final = "FLAT"


class OnDemandError(ValueError):
    """A submission that is refused: nothing was stamped."""


@dataclass(frozen=True)
class Context:
    """What the Desk run knows about the case, besides its final action."""

    asset_id: str
    symbol: str
    quote_class: str
    instrument_kind: str
    oracle_pair: str
    oracle_primary: str
    oracle_second: str
    as_of_ms: int
    horizon_ms: int
    agent_version_hash: str
    protocol_hash: str
    observation_set_hash: str
    evidence: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Submission:
    """A forecast sealed for time-stamping."""

    run_id: str
    forecast: forecast.DirectionalForecast


@dataclass(frozen=True)
class NoForecast:
    """A Desk commit that makes no prediction: an unscored reference, never time-stamped."""

    run_id: str
    reason: str


def cycle_id(run_id: str) -> str:
    return f"{CYCLE_PREFIX}{run_id}"


def submission_from_desk(final: desk_gates.FinalAction, run_id: str, ctx: Context, *, committed_at_ms: int) -> Submission | NoForecast:
    """The sealed forecast of a Desk run's final action, or the reason there is none."""
    if not isinstance(run_id, str) or _RUN.fullmatch(run_id) is None:
        raise OnDemandError("the run is an id of letters, digits and . _ : - (at most 128 characters)")
    if final.stance == FLAT:
        return NoForecast(run_id, "FLAT_REFERENCE")
    if final.stance != LONG:
        raise OnDemandError(f"the stance of a Desk call is long or flat, not {final.stance!r}")
    for name in ("asset_id", "symbol", "quote_class", "instrument_kind", "oracle_pair", "oracle_primary", "oracle_second"):
        if _IDENT.fullmatch(str(getattr(ctx, name))) is None:
            raise OnDemandError(f"{name} is an identifier, never free text")
    for name in ("agent_version_hash", "protocol_hash", "observation_set_hash"):
        if _HASH.fullmatch(str(getattr(ctx, name))) is None:
            raise OnDemandError(f"{name} is a sha-256 digest")
    for ident, digest in ctx.evidence:
        if _IDENT.fullmatch(str(ident)) is None or _HASH.fullmatch(str(digest)) is None:
            raise OnDemandError("evidence is a list of an identifier and a digest, never free text")
    if ctx.as_of_ms > committed_at_ms:
        raise OnDemandError("the evidence is from after the commit")
    if committed_at_ms - ctx.as_of_ms > MAX_EVIDENCE_AGE_MS:
        raise OnDemandError("the evidence is stale: it is older than the interactive limit")
    try:
        common = forecast.Common(
            cycle_id=cycle_id(run_id),
            arm_id=ARM_ID,
            agent_version_hash=ctx.agent_version_hash,
            protocol_hash=ctx.protocol_hash,
            contract="D",
            asset_id=ctx.asset_id,
            symbol=ctx.symbol,
            quote_class=ctx.quote_class,
            instrument_kind=ctx.instrument_kind,
            oracle_pair=ctx.oracle_pair,
            oracle_primary=ctx.oracle_primary,
            oracle_second=ctx.oracle_second,
            as_of_ms=ctx.as_of_ms,
            horizon_ms=ctx.horizon_ms,
            invalidation=INVALIDATION,
            evidence=ctx.evidence,
            observation_set_hash=ctx.observation_set_hash,
            interpretation_visible=False,
        )
        return Submission(run_id, forecast.DirectionalForecast(common, Decimal(final.p)))
    except forecast.ForecastError as exc:
        raise OnDemandError(str(exc)) from exc


def source_urn(run_id: str) -> str:
    return f"{SOURCE_PREFIX}{run_id}"


@dataclass(frozen=True)
class Committed:
    """What a submission reached: its level, the instant it was committed, and the origin its measurement follows."""

    forecast_id: str
    level: str
    committed_at_ms: int
    eligible_after_ms: int
    origin_ms: int
    commitment: Commitment | None


def _own(ledger: Ledger, urn: str) -> list[dict[str, canonical.JsonValue]]:
    """The records of this run's source, in the order the ledger holds them."""
    found = []
    for seq in range(ledger.size):
        record = canonical.parse_record(ledger.read_record(seq))
        if record.get("source") == urn:
            found.append(record)
    return found


def _finish(ledger: Ledger, urn: str, history: lifecycle.History, commitment: Commitment, at_ms: int) -> Committed:
    """The two steps that need the level: committed, and waiting for the origin. They are acknowledged together, so a process killed here
    leaves both or neither."""
    fid = history[0].forecast_id
    committed = next(c for c in commitment.records if c.record_id == fid)
    at = max(at_ms, commitment.attestation.committed_at_ms)
    eligible = commitment.attestation.eligible_after_ms
    step = lifecycle.Step(lifecycle.State.COMMITTED, at, level=forecast.Level(committed.level), committed_at_ms=committed.committed_at_ms)
    history = lifecycle.advance(history, step)
    records = [lifecycle.to_record(history[-1], urn, at)]
    wait = lifecycle.Step(lifecycle.State.AWAITING_ORIGIN, at, eligible_after_ms=eligible)
    if committed.level == forecast.Level.C3.value:
        attestation = commitment.attestation
        wait = replace(
            wait,
            attested_at_ms=attestation.attested_at_ms,
            uncertainty_ms=attestation.uncertainty_ms,
            spread_ms=round(cast(float, attestation.timing.spread_s) * 1000),
        )
    history = lifecycle.advance(history, wait)
    records.append(lifecycle.to_record(history[-1], urn, at))
    put(ledger, records)
    return Committed(fid, committed.level, committed.committed_at_ms, eligible, origin(eligible), commitment)


def _already(history: lifecycle.History) -> Committed:
    """What an earlier, finished commit reached, read back from its steps."""
    fid = history[0].forecast_id
    committed = next(t.step for t in history if t.step.state is lifecycle.State.COMMITTED)
    wait = next(t.step for t in history if t.step.state is lifecycle.State.AWAITING_ORIGIN)
    eligible = cast(int, wait.eligible_after_ms)
    return Committed(
        fid, cast(forecast.Level, committed.level).value, cast(int, committed.committed_at_ms), eligible, origin(eligible), None
    )


def write_registry(ledger: Ledger, registry: Registry, urn: str) -> None:
    """Put the instrument lists the forecast was made against into the ledger under the run's own source, before the checkpoint that is
    time-stamped, so that the oracle pair can be found again from the ledger alone (the registry is otherwise in a process's memory). Each
    run keeps its own lists because each process builds its registry from nothing: records of two such registries do not replay as one.
    A list already there is not written again."""
    wrapped = [registry_job.ledger_record(r, urn) for r in registry.records()]
    put(ledger, [r for r in wrapped if ledger.find(urn, str(r["id"])) is None])


def commit(
    sub: Submission,
    *,
    ledger: Ledger,
    attestor: Attestor,
    rules: TimeStampRules,
    at_ms: int,
    store: Path | None = None,
    registry: Registry | None = None,
) -> Committed:
    """Append the sealed forecast with its first steps, have the checkpoint time-stamped by independent authorities, then record what the
    level and the origin are. The level is whatever the counted tokens support; nothing here can write a higher one.

    The same submission again finds the first and writes nothing. A process killed after the batch was appended and before the stamp starts
    again from the records: the checkpoint is stamped now, and tokens that arrive after the counting window count for nothing, so the level
    is C1 with the commitment lost, never a C3 that was not earned. A different forecast under the same run is refused.

    ``registry`` is the one the forecast's asset and oracle pair were found in: its lists are written to the ledger first, so that the
    commitment covers them and the settlement can find the pair without it."""
    urn = source_urn(sub.run_id)
    fid = sub.forecast.common.forecast_id
    if ledger.find(urn, fid) is None:
        if registry is not None:
            write_registry(ledger, registry, urn)
        horizon = sub.forecast.common.horizon_ms
        history = lifecycle.begin(fid, "D", horizon, at_ms)
        for state in (lifecycle.State.CREATED, lifecycle.State.EVIDENCE_FROZEN, lifecycle.State.DECIDED):
            history = lifecycle.advance(history, lifecycle.Step(state, at_ms))
        members = [forecast.to_record(sub.forecast, urn, at_ms), *(lifecycle.to_record(t, urn, t.step.at_ms) for t in history)]
        commitment = commit_batch(ledger, members, attestor=attestor, rules=rules)
        if store is not None:
            commitment_store.save(
                store, commitment
            )  # the note and the tokens a verification bundle needs, before the steps that name the level
        return _finish(ledger, urn, history, commitment, at_ms)
    own = _own(ledger, urn)
    sealed = next(r for r in own if r["type"] == forecast.TYPE and r["id"] == fid)
    if sealed["body"] != sub.forecast.to_body():
        raise OnDemandError("a different forecast was already committed for this run")
    history = lifecycle.rebuild([r for r in own if r["type"] == lifecycle.TYPE])[fid]
    state = lifecycle.current_state(history)
    if any(t.step.state is lifecycle.State.COMMITTED for t in history):
        return _already(history)
    if state is not lifecycle.State.DECIDED:
        raise OnDemandError(f"a commit that stopped in the state {state.value} cannot be resumed")
    keys = [(urn, str(r["id"])) for r in own]
    commitment = attest_appended(ledger, keys, attestor=attestor, rules=rules)
    if store is not None:
        commitment_store.save(store, commitment)
    return _finish(ledger, urn, history, commitment, at_ms)


def settle(services: SettleServices, ledger: Ledger, *, wait_s: float = 30.0) -> Passed:
    """One pass of the league's settlement over every on-demand forecast that is waiting: the same code, the same oracles and the same
    records, appended to this ledger. A forecast whose candles are not all there yet stays where it is; running the pass again writes only
    what is new. The oracle pair of each forecast is found in the instrument lists its own run wrote to this ledger, never in the caller's
    registry: a settlement must not depend on what a process happens to have read."""
    records = cast(list[dict[str, Any]], [canonical.parse_record(ledger.read_record(seq)) for seq in range(ledger.size)])
    due = waiting(records)
    run_of = {str(r["id"]): str(r["source"]) for r in records if r["type"] == forecast.TYPE}
    runs: dict[str, list[Any]] = {}
    for slot in due:
        runs.setdefault(run_of[slot.forecast.common.forecast_id], []).append(slot)
    written: list[dict[str, canonical.JsonValue]] = []
    histories: dict[str, lifecycle.History] = {}
    pending: list[str] = []
    deferred: list[str] = []
    failed: list[tuple[str, str]] = []
    for urn, slots in runs.items():
        registry = registry_job.load([r for r in records if r["type"] == registry_job.TYPE and r["source"] == urn])
        if not registry.records():
            raise OnDemandError("the ledger holds no instrument lists for a run, so the oracle of its forecast cannot be found")
        part = settle_pass(replace(services, env=replace(services.env, registry=registry)), slots, wait_s=wait_s)
        written += part.records
        histories.update(part.histories)
        pending += part.pending
        deferred += part.deferred
        failed += part.failed
    put(ledger, written)
    return Passed(tuple(written), histories, tuple(pending), tuple(deferred), tuple(failed))


@dataclass(frozen=True)
class Wiring:
    """What a Desk run needs to commit on demand: the ledger of the class, the attestation, and the league's own oracle facts."""

    ledger: Ledger
    attestor: Attestor
    rules: TimeStampRules
    quote_class: str
    instrument_kind: str
    oracle_pair: str
    oracle_primary: str
    oracle_second: str
    store: Path | None = None  # where the note and the tokens of each commitment are kept for a verification bundle
    registry: Registry | None = None  # the instrument lists the run found its asset in: written to the ledger at the commit


def desk_commit_port(
    wiring: Wiring,
    *,
    asset_id: str,
    symbol: str,
    horizon_ms: int,
    started_ms: int,
    store: EvidenceStore,
    versions: tuple[str, str],
    now_ms: Callable[[], int],
) -> Callable[[desk.CommitRequest], str]:
    """The Desk's commit port that makes a Desk call an on-demand forecast: a long call is sealed, time-stamped and left to settle; a flat
    one is a reference and writes nothing. The evidence is what the run read, by id and digest; the age of the evidence is the age of the
    run's start. A refusal (stale evidence) raises, and the Desk closes the run as interrupted: nothing was stamped."""

    def port(request: desk.CommitRequest) -> str:
        ids = [str(i) for i in cast(list[Any], request.decision["evidence_ids"])]
        evidence = tuple((i, store.digest(i)) for i in ids)
        observation = hashlib.sha256(canonical.dumps({"evidence": [list(pair) for pair in sorted(evidence)]})).hexdigest()
        ctx = Context(
            asset_id,
            symbol,
            wiring.quote_class,
            wiring.instrument_kind,
            wiring.oracle_pair,
            wiring.oracle_primary,
            wiring.oracle_second,
            started_ms,
            horizon_ms,
            versions[0],
            versions[1],
            observation,
            evidence,
        )
        now = now_ms()
        made = submission_from_desk(request.final, request.run_id, ctx, committed_at_ms=now)
        if isinstance(made, NoForecast):
            return f"{request.label}-{request.run_id}"
        commit(
            made,
            ledger=wiring.ledger,
            attestor=wiring.attestor,
            rules=wiring.rules,
            at_ms=now,
            store=wiring.store,
            registry=wiring.registry,
        )
        return made.forecast.common.forecast_id

    return port
