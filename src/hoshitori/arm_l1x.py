"""Arm L1x: the matched-compute control for the debate (``LEAGUE_EXPERIMENT`` section 5).

A debate can win only because it spends more. This arm spends the same: it runs the method of arm L1 several times on the same view and
aggregates, and it is sized from what the debate of the same candidate and horizon actually billed.

* **The size.** Three samples are drawn first; ``m = max(3, round(target / mean billed per sample))`` made odd by rounding up and capped at
  ``max_samples``; the rest are drawn. One matching rule: total billed tokens (the completion count of the usage record already contains the
  reasoning tokens); the number of calls is reported and not matched. The total is compared with the target and ``mismatch`` is set when it
  is more than 10 percent away.
* **The aggregate.** The probability is the median of the samples that made a call; the action follows from that probability by the same
  mapping as arm L1 and never from a separate vote. The arm abstains when the samples that called are not a majority of the samples that
  could answer, with the most common reason. A sample that failed does not vote.
* **Collapse is counted.** Samples that are all the same proposal (a temperature of zero, a provider that ignores sampling) are not a valid
  control; ``collapsed`` says so and the pair is reported, not hidden.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from hoshitori import arm_l1, canonical, crosscheck, typed
from hoshitori.arm_input import build_view
from hoshitori.model_client import ModelCall, ModelClient
from hoshitori.snapshot import Candidate, Reader

ARM: Final = "L1x"
FIRST: Final = 3
MAX_SAMPLES: Final = 15
TOLERANCE: Final = Decimal("0.1")
_SIX: Final = Decimal("1E-6")


@dataclass(frozen=True)
class Config:
    sample: arm_l1.Config  # the settings of one sample: the model, a temperature above zero
    seed_base: int = 1000  # sample ``i`` is asked with the seed ``seed_base + i``
    max_samples: int = MAX_SAMPLES


@dataclass(frozen=True)
class Control:
    decision: arm_l1.Decision  # the aggregate
    samples: tuple[arm_l1.Decision, ...]
    target: int  # the billed tokens of the debate it is matched to
    tokens: int  # the billed tokens of all its samples
    mismatch: bool
    collapsed: bool
    calls: tuple[ModelCall, ...]


def agent_version_hash(config: Config) -> str:
    return hashlib.sha256(
        canonical.dumps(
            {
                "arm": ARM,
                "sample": arm_l1.agent_version_hash(config.sample),
                "seed_base": config.seed_base,
                "max_samples": config.max_samples,
                "first": FIRST,
                "tolerance": format(TOLERANCE, "f"),
                "schema": typed.json_schema(arm_l1.SCHEMA),
            }
        )
    ).hexdigest()


def _tokens(calls: tuple[ModelCall, ...]) -> int:
    return sum(c.usage.billed for c in calls if c.usage is not None)


def samples_needed(target: int, mean: Decimal, cap: int) -> int:
    """How many samples match ``target`` billed tokens when one costs ``mean``: at least three, odd (rounded up), at most ``cap``."""
    wanted = FIRST if mean <= 0 else max(FIRST, int((Decimal(target) / mean).to_integral_value(rounding=ROUND_HALF_EVEN)))
    odd = wanted + 1 if wanted % 2 == 0 else wanted
    return min(odd, cap if cap % 2 else cap - 1)


def median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    mid = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return mid.quantize(_SIX, rounding=ROUND_HALF_EVEN)


def cited(decisions: list[arm_l1.Decision]) -> tuple[str, ...]:
    """What the calls cited, once each and in sorted order, so that the receipt of a vote reads the same every time."""
    return tuple(sorted({e for s in decisions for e in s.evidence_ids}))


def aggregate(samples: tuple[arm_l1.Decision, ...], reader: Reader, candidate: Candidate, version: str, view_hash: str) -> arm_l1.Decision:
    base = samples[0]
    usable = [s for s in samples if s.verdict in (arm_l1.Verdict.CALL, arm_l1.Verdict.NO_CALL)]
    calls = tuple(c for s in samples for c in s.calls)
    if not usable:
        return replace(base, agent_version=version, observation_set_hash=view_hash, calls=calls, proposal=None, report=None)
    called = [s for s in usable if s.verdict is arm_l1.Verdict.CALL]
    if len(called) * 2 > len(usable):
        cites = cited(called)
        invalidation = tuple(dict.fromkeys(i for s in called for i in s.invalidation))[: arm_l1.MAX_INVALIDATIONS]
        return arm_l1.Decision(
            base.candidate_id,
            base.horizon_hours,
            arm_l1.Verdict.CALL,
            None,
            median([s.p for s in called if s.p is not None]),
            cites,
            reader.set_hash(candidate.candidate_id, cites),
            invalidation,
            version,
            None,
            arm_l1.Gate.NONE,
            None,
            calls,
        )
    reasons = Counter(s.reason for s in usable if s.reason is not None)  # an abstention always has its reason; a call has none
    top = max(reasons.values())
    reason = min((r for r, n in reasons.items() if n == top), key=lambda r: r.value)
    return arm_l1.Decision(
        base.candidate_id,
        base.horizon_hours,
        arm_l1.Verdict.NO_CALL,
        reason,
        None,
        (),
        reader.set_hash(candidate.candidate_id, ()),
        (),
        version,
        None,
        arm_l1.Gate.NONE,
        None,
        calls,
    )


def decide(
    client: ModelClient,
    reader: Reader,
    candidate: Candidate,
    symbol: str,
    horizon_ms: int,
    snapshot_at_ms: int,
    config: Config,
    target_tokens: int,
) -> Control:
    """The control for one candidate and one horizon, matched to ``target_tokens`` billed by the debate."""
    view = build_view(reader, candidate, symbol, horizon_ms, snapshot_at_ms)
    version = agent_version_hash(config)
    if not view.complete:
        return Control(arm_l1.missing(candidate, view, version), (), target_tokens, 0, False, False, ())
    stood = crosscheck.conflicts(reader.items(candidate.candidate_id))
    if stood:
        seen = reader.set_hash(candidate.candidate_id, stood)
        return Control(arm_l1.conflicted(candidate, view, version, stood, seen), (), target_tokens, 0, False, False, ())
    sample_version = arm_l1.agent_version_hash(config.sample)

    def one(index: int) -> arm_l1.Decision:
        settings = replace(config.sample, seed=config.seed_base + index)
        return arm_l1.propose(client, reader, candidate, view, arm_l1.build_request(settings, view), sample_version)

    with ThreadPoolExecutor(max_workers=FIRST) as pool:
        samples = list(pool.map(one, range(FIRST)))
        spent = _tokens(tuple(c for s in samples for c in s.calls))
        total = samples_needed(target_tokens, Decimal(spent) / FIRST, config.max_samples)
        samples += list(pool.map(one, range(FIRST, total)))
    drawn = tuple(samples)
    tokens = _tokens(tuple(c for s in drawn for c in s.calls))
    usable = [s.proposal for s in drawn if s.proposal is not None]
    collapsed = len(usable) >= 2 and all(p == usable[0] for p in usable)
    mismatch = abs(Decimal(tokens) - Decimal(target_tokens)) > TOLERANCE * Decimal(target_tokens)
    decision = aggregate(drawn, reader, candidate, version, view.set_hash)
    return Control(decision, drawn, target_tokens, tokens, mismatch, collapsed, decision.calls)
