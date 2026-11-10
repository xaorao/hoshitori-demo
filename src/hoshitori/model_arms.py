"""The model arms of a cycle, for one candidate at one horizon (``LEAGUE_EXPERIMENT`` sections 3 to 5, ``CYCLE_RUNNER_SPEC``).

The league gives each arm the same frozen snapshot and the same deadline: the cutoff the protocol pins for the arm and the horizon,
counted from the snapshot. An arm that has not answered by then is ``FAILED_LATE`` and its answer is discarded, however good; an arm
the protocol does not admit to a horizon has no slot there at all (it is not a failure).

For one candidate and one horizon the single judgment (``L1``) and the debate (``L2``) are asked at the same time. The matched-compute
control (``L1x``) comes after the debate because its size is the debate's billed tokens, whatever the debate ended in: a debate that
failed on its second call still spent its first. A debate that spent nothing (its evidence was missing, or no call reached the model)
leaves nothing to match, so the control fails with that stated, and is never run against a target of zero.

Each time a request is made the time left to the arm's cutoff bounds the request's own timeout, so a slow model is cut off by the
arm's deadline and not by a longer default.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final

from hoshitori import arm_input, arm_l1, arm_l1x, arm_l2, canonical
from hoshitori.arm_run_record import ARMS as RECORDED_ARMS
from hoshitori.model_client import SPENT, ModelCall, ModelClient
from hoshitori.protocol import Protocol
from hoshitori.snapshot import Candidate, Reader

LATE_REASON: Final = "FINISHED_AFTER_CUTOFF"
NO_TARGET_REASON: Final = "NO_MATCH_TARGET"
TOO_MANY_REASON: Final = "TOO_MANY_ITEMS"
MIN_TIMEOUT_S: Final = 1.0
ORDER: Final = ("L1", "L2", "L1x")  # the model arms; results come back in this order


class ModelArmsError(ValueError):
    """The model arms are not the ones the protocol pins."""


@dataclass(frozen=True)
class ModelArms:
    client: ModelClient
    l1: arm_l1.Config | None = None
    l2: arm_l2.Config | None = None
    l1x: arm_l1x.Config | None = None
    workers: int = 4  # candidates and horizons decided at the same time


@dataclass(frozen=True)
class Slotted:
    """How one arm ended for one candidate and one horizon."""

    arm: str
    verdict: str  # CALL, NO_CALL, FAILED, FAILED_EVIDENCE or FAILED_LATE
    reason: str | None
    gate: str
    proposed_p: Decimal | None
    p: Decimal | None
    evidence_ids: tuple[str, ...]
    observation_set_hash: str
    invalidation: tuple[str, ...]
    version: str
    cutoff_ms: int
    finished_ms: int
    calls: tuple[ModelCall, ...]
    tokens: int
    debate: dict[str, canonical.JsonValue] | None
    control: dict[str, canonical.JsonValue] | None


def versions(models: ModelArms) -> dict[str, str]:
    """The agent version hash of each arm that is configured."""
    out: dict[str, str] = {}
    if models.l1 is not None:
        out["L1"] = arm_l1.agent_version_hash(models.l1)
    if models.l2 is not None:
        out["L2"] = arm_l2.agent_version_hash(models.l2)
    if models.l1x is not None:
        out["L1x"] = arm_l1x.agent_version_hash(models.l1x)
    return out


def check_against(models: ModelArms | None, protocol: Protocol) -> None:
    """The arms given are the arms the protocol pins, each at the version it pins."""
    pinned = {name: version for name, version, _ in protocol.model_arms}
    if models is None:
        if pinned:
            raise ModelArmsError("the protocol has model arms and no model arms were given")
        return
    if not isinstance(models.workers, int) or isinstance(models.workers, bool) or not 1 <= models.workers <= 64:
        raise ModelArmsError("the number of workers is a whole number from 1 to 64")
    given = versions(models)
    if set(given) != set(pinned):
        raise ModelArmsError("the model arms are those of the protocol, no more and no fewer")
    for arm, version in pinned.items():
        if given[arm] != version:
            raise ModelArmsError(f"the model arm {arm} is not the version the protocol pins")


def _tokens(calls: tuple[ModelCall, ...]) -> int:
    return sum(call.usage.billed for call in calls if call.usage is not None)


def _stopped(
    arm: str,
    verdict: str,
    reason: str,
    observation_set_hash: str,
    version: str,
    cutoff_ms: int,
    finished_ms: int,
    calls: tuple[ModelCall, ...] = (),
    tokens: int = 0,
    debate: dict[str, canonical.JsonValue] | None = None,
    control: dict[str, canonical.JsonValue] | None = None,
) -> Slotted:
    """A slot that has no forecast and no proposal: it failed, or its answer came too late to be kept."""
    return Slotted(
        arm,
        verdict,
        reason,
        "NONE",
        None,
        None,
        (),
        observation_set_hash,
        (),
        version,
        cutoff_ms,
        finished_ms,
        calls,
        tokens,
        debate,
        control,
    )


def _slotted(
    arm: str,
    decision: arm_l1.Decision,
    calls: tuple[ModelCall, ...],
    tokens: int,
    cutoff_ms: int,
    finished_ms: int,
    taken_ms: int,
    debate: dict[str, canonical.JsonValue] | None = None,
    control: dict[str, canonical.JsonValue] | None = None,
) -> Slotted:
    """The arm's answer as the league keeps it, unless it came after the cutoff, in which case it is discarded and the slot is late."""
    if finished_ms - taken_ms > cutoff_ms or any(call.detail == SPENT for call in calls):
        late = _stopped(arm, "FAILED_LATE", LATE_REASON, decision.observation_set_hash, decision.agent_version, cutoff_ms, finished_ms)
        return replace(late, calls=calls, tokens=tokens, debate=debate, control=control)
    proposed = decision.p if decision.proposal is None else decision.proposal.p
    return Slotted(
        arm,
        decision.verdict.value,
        None if decision.reason is None else decision.reason.value,
        decision.gate.value,
        proposed,
        decision.p,
        decision.evidence_ids,
        decision.observation_set_hash,
        decision.invalidation,
        decision.agent_version,
        cutoff_ms,
        finished_ms,
        calls,
        tokens,
        debate,
        control,
    )


def _with_time(config: arm_l1.Config, left_s: float) -> arm_l1.Config:
    return replace(config, timeout_s=min(config.timeout_s, max(MIN_TIMEOUT_S, left_s)))


def _debate_note(debate: arm_l2.Debate) -> dict[str, canonical.JsonValue]:
    d = debate.disagreement
    disagreement: canonical.JsonValue = (
        None
        if d is None
        else {
            "bull_only": list(d.bull_only),
            "bear_only": list(d.bear_only),
            "shared": list(d.shared),
            "judge_on_bull": list(d.judge_on_bull),
            "judge_on_bear": list(d.judge_on_bear),
        }
    )
    return {"collapsed": debate.collapsed, "failed_role": debate.failed_role, "disagreement": disagreement}


def _control_note(control: arm_l1x.Control) -> dict[str, canonical.JsonValue]:
    return {
        "target_tokens": control.target,
        "samples": len(control.samples),
        "mismatch": control.mismatch,
        "collapsed": control.collapsed,
    }


def run_group(
    models: ModelArms,
    protocol: Protocol,
    now_ms: Callable[[], int],
    reader: Reader,
    candidate: Candidate,
    symbol: str,
    hours: int,
    taken_ms: int,
) -> tuple[Slotted, ...]:
    """Every model arm admitted to this horizon, for this candidate. Exceptions that are not outcomes are not caught."""
    horizon = hours * 3_600_000

    configured = versions(models)

    def cutoff(arm: str) -> int | None:
        return protocol.cutoff_ms(arm, hours) if arm in configured else None

    crowded = arm_input.too_many(reader, candidate)
    if crowded is not None:  # more items than a request holds: every arm of the group fails for its evidence, before any model is asked
        seen = reader.set_hash(candidate.candidate_id, crowded)
        nothing: dict[str, canonical.JsonValue] = {"target_tokens": 0, "samples": 0, "mismatch": False, "collapsed": False}
        return tuple(
            _stopped(
                arm,
                "FAILED_EVIDENCE",
                TOO_MANY_REASON,
                seen,
                configured[arm],
                cutoff_ms,
                now_ms(),
                control=nothing if arm == "L1x" else None,
            )
            for arm in ORDER
            if (cutoff_ms := cutoff(arm)) is not None
        )

    def left_s(arm_cutoff: int) -> float:
        return (taken_ms + arm_cutoff - now_ms()) / 1000

    def single(arm_cutoff: int) -> Slotted:
        assert models.l1 is not None
        client = models.client.within(lambda: left_s(arm_cutoff))
        decision = arm_l1.decide(client, reader, candidate, symbol, horizon, taken_ms, _with_time(models.l1, left_s(arm_cutoff)))
        return _slotted("L1", decision, decision.calls, _tokens(decision.calls), arm_cutoff, now_ms(), taken_ms)

    def debate(arm_cutoff: int) -> tuple[Slotted, arm_l2.Debate]:
        assert models.l2 is not None
        t = left_s(arm_cutoff)
        cfg = replace(
            models.l2, bull=_with_time(models.l2.bull, t), bear=_with_time(models.l2.bear, t), judge=_with_time(models.l2.judge, t)
        )
        out = arm_l2.decide(models.client.within(lambda: left_s(arm_cutoff)), reader, candidate, symbol, horizon, taken_ms, cfg)
        return _slotted("L2", out.decision, out.calls, out.tokens, arm_cutoff, now_ms(), taken_ms, debate=_debate_note(out)), out

    def control(arm_cutoff: int, target: arm_l2.Debate) -> Slotted:
        assert models.l1x is not None
        version = arm_l1x.agent_version_hash(models.l1x)
        seen = target.decision.observation_set_hash
        if target.tokens == 0:
            missing = target.decision.verdict is arm_l1.Verdict.FAILED_EVIDENCE
            verdict, reason = ("FAILED_EVIDENCE", arm_l1.Reason.MISSING_EVIDENCE.value) if missing else ("FAILED", NO_TARGET_REASON)
            nothing: dict[str, canonical.JsonValue] = {"target_tokens": 0, "samples": 0, "mismatch": False, "collapsed": False}
            return _stopped("L1x", verdict, reason, seen, version, arm_cutoff, now_ms(), control=nothing)
        if left_s(arm_cutoff) < MIN_TIMEOUT_S:
            unrun: dict[str, canonical.JsonValue] = {"target_tokens": target.tokens, "samples": 0, "mismatch": False, "collapsed": False}
            return _stopped("L1x", "FAILED_LATE", LATE_REASON, seen, version, arm_cutoff, now_ms(), control=unrun)
        cfg = replace(models.l1x, sample=_with_time(models.l1x.sample, left_s(arm_cutoff)))
        out = arm_l1x.decide(
            models.client.within(lambda: left_s(arm_cutoff)), reader, candidate, symbol, horizon, taken_ms, cfg, target.tokens
        )
        return _slotted("L1x", out.decision, out.calls, out.tokens, arm_cutoff, now_ms(), taken_ms, control=_control_note(out))

    def chain(debate_cutoff: int, control_cutoff: int | None) -> tuple[Slotted, ...]:
        first, debated = debate(debate_cutoff)
        if control_cutoff is None:
            return (first,)
        return (first, control(control_cutoff, debated))

    c1, c2, cx = cutoff("L1"), cutoff("L2"), cutoff("L1x")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures: list[Future[tuple[Slotted, ...]]] = []
        if c1 is not None:
            futures.append(pool.submit(lambda: (single(c1),)))
        if c2 is not None:
            futures.append(pool.submit(chain, c2, cx))
        got = [item for future in futures for item in future.result()]
    return tuple(got)


def run_body(slotted: Slotted, cycle_id: str, forecast_id: str, candidate_id: str, hours: int) -> dict[str, canonical.JsonValue]:
    """The body of the arm run record for a slot."""
    assert slotted.arm in RECORDED_ARMS
    return {
        "cycle_id": cycle_id,
        "forecast_id": forecast_id,
        "arm": slotted.arm,
        "candidate_id": candidate_id,
        "hours": hours,
        "verdict": slotted.verdict,
        "reason": slotted.reason,
        "gate": slotted.gate,
        "proposed_p": None if slotted.proposed_p is None else format(slotted.proposed_p, "f"),
        "final_p": None if slotted.p is None else format(slotted.p, "f"),
        "version": slotted.version,
        "cutoff_ms": slotted.cutoff_ms,
        "finished_at_ms": slotted.finished_ms,
        "calls": len(slotted.calls),
        "tokens": slotted.tokens,
        "debate": slotted.debate,
        "control": slotted.control,
    }
