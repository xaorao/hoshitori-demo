"""Replay a committed cycle from the ledger alone (contract section 11: deterministic reconstruction).

A committed cycle says what each arm decided and which evidence it read. Replay asks every arm again, on the snapshot rebuilt from
the evidence records the ledger holds and with the model's stored answers served back by digest of the request, and compares what
comes out with what the cycle committed. It proves that the record can be rebuilt from stored bytes; it proves nothing about the
model (it never calls one) and nothing about the market. A fresh run that asks the model again is a different measure and is not this.

For each slot one of four things is said, and a slot is never passed on a guess:

* ``MATCH``: the replayed decision is the committed one (probability or probabilities, the evidence and its digests, the observation
  set, the invalidation, the agent version; or the same abstention or failure with the same reason).
* ``DIFFERENT``: it is not, and the fields that differ are named.
* ``NOT_RECONSTRUCTIBLE``: something the replay needs is not there: the record of the slot, the run record of a model arm, or the
  stored answer to a request that was answered when the cycle ran.
* ``NOT_COMPARED``: the committed answer was discarded as late, so there is nothing committed to compare with.

A request that got no answer when the cycle ran (the model was down) has a call record without a capture; the replay finding no
stored answer for the same request is consistent with that and is not a loss.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, cast

from hoshitori import (
    arm_l1,
    arm_l1x,
    arm_l2,
    arm_run_record,
    barrier_arm,
    barrier_rates,
    canonical,
    forecast,
    lifecycle,
    model_call_record,
    rules_arm,
    snapshot,
)
from hoshitori.model_client import ModelCall, ModelClient, Outcome, ReplayFetcher
from hoshitori.protocol import Protocol
from hoshitori.snapshot import Candidate, Reader

HOUR_MS: Final = 3_600_000
RULES_NO_CALL: Final = "OUT_OF_SCOPE"  # how the lifecycle names a rules arm's abstention (league.NO_CALL_REASON)


class ReplayError(ValueError):
    """The cycle cannot be replayed at all: its snapshot cannot be rebuilt, or what was given is not what the protocol pins."""


class Status(StrEnum):
    MATCH = "MATCH"
    DIFFERENT = "DIFFERENT"
    NOT_RECONSTRUCTIBLE = "NOT_RECONSTRUCTIBLE"
    NOT_COMPARED = "NOT_COMPARED"


@dataclass(frozen=True)
class Inputs:
    protocol: Protocol
    mappings: Mapping[str, rules_arm.BaseRates]
    barrier_mappings: Mapping[str, barrier_rates.BarrierRates]
    answers: ReplayFetcher | None = None  # the model's stored answers; none when the protocol has no model arm
    l1: arm_l1.Config | None = None
    l2: arm_l2.Config | None = None
    l1x: arm_l1x.Config | None = None


@dataclass(frozen=True)
class SlotReplay:
    forecast_id: str
    arm_id: str
    symbol: str
    hours: int
    contract: str
    status: Status
    detail: str | None


@dataclass(frozen=True)
class Report:
    cycle_id: str
    slots: tuple[SlotReplay, ...]

    def count(self, status: Status) -> int:
        return sum(1 for s in self.slots if s.status is status)

    @property
    def clean(self) -> bool:
        """Whether nothing differs and nothing is missing."""
        return all(s.status in (Status.MATCH, Status.NOT_COMPARED) for s in self.slots)


@dataclass(frozen=True)
class _Decided:
    """What an arm decided, in the terms the ledger committed it in."""

    verdict: str
    reason: str | None
    p: tuple[Decimal, ...] | None
    atr: Decimal | None
    evidence_ids: tuple[str, ...]
    observation_set_hash: str
    invalidation: tuple[str, ...]
    version: str | None
    calls: tuple[ModelCall, ...] = ()


def _rules(decision: rules_arm.Decision) -> _Decided:
    p = None if decision.p is None else (decision.p,)
    reason = None if decision.reason is None else decision.reason.value
    return _Decided(
        decision.verdict.value, reason, p, None, decision.evidence_ids, decision.observation_set_hash, decision.invalidation, None
    )


def _barrier(decision: barrier_arm.BarrierDecision) -> _Decided:
    reason = None if decision.reason is None else decision.reason.value
    return _Decided(
        decision.verdict.value,
        reason,
        decision.p,
        decision.atr,
        decision.evidence_ids,
        decision.observation_set_hash,
        decision.invalidation,
        None,
    )


def _model(decision: arm_l1.Decision, calls: tuple[ModelCall, ...]) -> _Decided:
    reason = None if decision.reason is None else decision.reason.value
    p = None if decision.p is None else (decision.p,)
    return _Decided(
        decision.verdict.value,
        reason,
        p,
        None,
        decision.evidence_ids,
        decision.observation_set_hash,
        decision.invalidation,
        decision.agent_version,
        calls,
    )


def _differences(committed: forecast.Forecast, replayed: _Decided, version: str) -> list[str]:
    """The fields in which a replayed decision is not the committed forecast."""
    common = committed.common
    found = []
    if isinstance(committed, forecast.BarrierForecast):
        want_p: tuple[Decimal, ...] = (committed.p_target, committed.p_stop, committed.p_timeout)
        if replayed.atr != committed.atr:
            found.append("the ATR")
    else:
        want_p = (committed.p,)
    if replayed.p != want_p:
        found.append("the probability")
    if replayed.invalidation != common.invalidation:
        found.append("the invalidation")
    if replayed.observation_set_hash != common.observation_set_hash:
        found.append("the observation set")
    if tuple(replayed.evidence_ids) != tuple(i for i, _ in common.evidence):
        found.append("the evidence cited")
    if replayed.version is not None and replayed.version != common.agent_version_hash:
        found.append("the agent version")
    if common.agent_version_hash != version:
        found.append("the agent version the forecast names")
    return found


def replay_cycle(records: Sequence[Mapping[str, Any]], cycle_id: str, inputs: Inputs) -> Report:
    """Replay every slot of a committed cycle. ``records`` are the ledger's records in order, as the ledger holds them."""
    protocol = inputs.protocol
    pinned = {name for name, _, _ in protocol.model_arms}
    given = {name for name, config in (("L1", inputs.l1), ("L2", inputs.l2), ("L1x", inputs.l1x)) if config is not None}
    if pinned != given or (pinned and inputs.answers is None):
        raise ReplayError("the model arms given are those of the protocol, with their stored answers, no more and no fewer")
    configs: dict[str, tuple[str, Any]] = {}
    if inputs.l1 is not None:
        configs["L1"] = (arm_l1.agent_version_hash(inputs.l1), inputs.l1)
    if inputs.l2 is not None:
        configs["L2"] = (arm_l2.agent_version_hash(inputs.l2), inputs.l2)
    if inputs.l1x is not None:
        configs["L1x"] = (arm_l1x.agent_version_hash(inputs.l1x), inputs.l1x)
    for name, version, _ in protocol.model_arms:
        if configs[name][0] != version:
            raise ReplayError(f"the model arm {name} is not the version the protocol pins")
    taken_record = next(
        (r for r in records if r.get("type") == snapshot.TYPE and r["body"]["cycle_id"] == cycle_id),
        None,
    )
    manifest_record = next((r for r in records if r.get("type") == "manifest" and r["body"]["cycle_id"] == cycle_id), None)
    if taken_record is None or manifest_record is None:
        raise ReplayError("the ledger holds no snapshot and manifest of this cycle")
    evidence_records = {r["id"]: r for r in records if r.get("type") == "evidence"}
    try:
        rebuilt = snapshot.rebuild(taken_record, evidence_records)
    except snapshot.SnapshotError as exc:
        raise ReplayError(f"the snapshot of the cycle cannot be rebuilt: {exc}") from exc
    reader = Reader(rebuilt, snapshot.Access.INDEPENDENT)
    symbols = {e["candidate_id"]: e["symbol"] for e in manifest_record["body"]["entries"] if e["inclusion"] == "INCLUDED"}
    committed = {
        r["id"]: forecast.from_record(cast(dict[str, canonical.JsonValue], r))
        for r in records
        if r.get("type") == forecast.TYPE and r["body"]["cycle_id"] == cycle_id
    }
    histories = lifecycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get("type") == lifecycle.TYPE]))
    runs = {
        body["forecast_id"]: body
        for body in (
            arm_run_record.from_record(cast(dict[str, canonical.JsonValue], r)) for r in records if r.get("type") == arm_run_record.TYPE
        )
    }
    unanswered = {
        call.args_digest
        for call in (
            model_call_record.from_record(cast(dict[str, canonical.JsonValue], r)) for r in records if r.get("type") == "model_call"
        )
        if call.capture_id is None
    }
    client = ModelClient(inputs.answers) if inputs.answers is not None else None
    out: list[SlotReplay] = []
    for slot in rebuilt.slots:
        candidate = Candidate(slot.candidate_id, slot.asset_id)
        symbol = symbols[slot.candidate_id]
        for arm, _ in protocol.arms:
            for hours in protocol.horizons_hours:
                for contract in ("D", "B") if arm in inputs.barrier_mappings else ("D",):
                    fid = forecast.forecast_id(cycle_id, arm, slot.asset_id, contract, hours * HOUR_MS)
                    if contract == "B":
                        mapping = inputs.barrier_mappings[arm]
                        ruled = _barrier(barrier_arm.decide(reader, slot.candidate_id, mapping, hours))
                        version = barrier_arm.agent_version_hash(mapping)
                    else:
                        ruled = _rules(rules_arm.decide(reader, slot.candidate_id, inputs.mappings[arm], hours))
                        version = rules_arm.agent_version_hash(inputs.mappings[arm])
                    out.append(_compare(fid, arm, symbol, hours, contract, histories, committed, ruled, version, rules=True))
        for arm, _, horizons in protocol.model_arms:
            for hours, _ in horizons:
                fid = forecast.forecast_id(cycle_id, arm, slot.asset_id, "D", hours * HOUR_MS)
                run = runs.get(fid)
                if run is None:
                    out.append(SlotReplay(fid, arm, symbol, hours, "D", Status.NOT_RECONSTRUCTIBLE, "there is no run record of this slot"))
                    continue
                assert client is not None
                decided = _ask(arm, client, reader, candidate, symbol, hours, rebuilt.snapshot_at_ms, configs, run)
                missing = [c for c in decided.calls if c.outcome is Outcome.REPLAY_MISSING and c.args_digest not in unanswered]
                if missing:
                    out.append(
                        SlotReplay(
                            fid,
                            arm,
                            symbol,
                            hours,
                            "D",
                            Status.NOT_RECONSTRUCTIBLE,
                            f"{len(missing)} stored answer(s) to requests that were answered are gone",
                        )
                    )
                    continue
                out.append(_compare(fid, arm, symbol, hours, "D", histories, committed, decided, configs[arm][0], rules=False))
    return Report(cycle_id, tuple(out))


def _ask(
    arm: str,
    client: ModelClient,
    reader: Reader,
    candidate: Candidate,
    symbol: str,
    hours: int,
    taken_ms: int,
    configs: Mapping[str, tuple[str, Any]],
    run: Mapping[str, Any],
) -> _Decided:
    horizon = hours * HOUR_MS
    config = configs[arm][1]
    if arm == "L1":
        decision = arm_l1.decide(client, reader, candidate, symbol, horizon, taken_ms, config)
        return _model(decision, decision.calls)
    if arm == "L2":
        debate = arm_l2.decide(client, reader, candidate, symbol, horizon, taken_ms, config)
        return _model(debate.decision, debate.calls)
    control = arm_l1x.decide(client, reader, candidate, symbol, horizon, taken_ms, config, run["control"]["target_tokens"])
    return _model(control.decision, control.calls)


def _compare(
    fid: str,
    arm: str,
    symbol: str,
    hours: int,
    contract: str,
    histories: Mapping[str, lifecycle.History],
    committed: Mapping[str, forecast.Forecast],
    decided: _Decided,
    version: str,
    *,
    rules: bool,
) -> SlotReplay:
    def said(status: Status, detail: str | None = None) -> SlotReplay:
        return SlotReplay(fid, arm, symbol, hours, contract, status, detail)

    history = histories.get(fid)
    if history is None:
        return said(Status.NOT_RECONSTRUCTIBLE, "there is no record of this slot")
    made = committed.get(fid)
    if made is not None:
        if decided.verdict != "CALL":
            return said(Status.DIFFERENT, f"the cycle committed a forecast and the replay says {decided.verdict}")
        found = _differences(made, decided, version)
        return said(Status.DIFFERENT, "differs in " + ", ".join(found)) if found else said(Status.MATCH)
    ended = next((t.step for t in reversed(history) if t.step.state in lifecycle.TERMINAL), None)
    if ended is None:
        return said(Status.NOT_RECONSTRUCTIBLE, "the slot has no ending in the ledger")
    state = ended.state
    if state is lifecycle.State.FAILED_LATE:
        return said(Status.NOT_COMPARED, "the answer was discarded as late")
    if state is lifecycle.State.NO_CALL:
        reason = RULES_NO_CALL if rules else decided.reason
        if decided.verdict == "NO_CALL" and ended.reason == reason:
            return said(Status.MATCH)
        return said(Status.DIFFERENT, f"the cycle abstained ({ended.reason}) and the replay says {decided.verdict} {decided.reason}")
    expected = {lifecycle.State.FAILED_EVIDENCE: "FAILED_EVIDENCE", lifecycle.State.FAILED: "FAILED"}.get(state)
    if expected is not None and decided.verdict == expected and decided.reason == ended.reason:
        return said(Status.MATCH)
    return said(Status.DIFFERENT, f"the cycle ended {state.value} ({ended.reason}) and the replay says {decided.verdict} {decided.reason}")
