"""The rules arm's barrier decision (``RULES_ARM_SPEC`` section 5b): the frozen first-touch frequencies of the horizon, for a LONG side.

The arm reads the one features item of the candidate and nothing else. It needs the ATR in price (``atr14``): the levels of the forecast are
``k x ATR`` above and below the reference price, and the frequencies were counted with the same ATR. A missing ATR is failed evidence and
never a default; a zero ATR is no range; a horizon with too few settled windows is unmapped. The probabilities are the mapping's, not the
arm's judgement.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from hoshitori import canonical
from hoshitori.barrier_rates import K_STOP, K_TARGET, RULE_VERSION, BarrierRates
from hoshitori.evidence import EvidenceItem, Kind
from hoshitori.features import SOURCE
from hoshitori.rules_arm import HORIZONS, INVALIDATION, MIN_CELL, Reason, RulesError, Verdict
from hoshitori.snapshot import Reader

ARM: Final = "L0"
CONTRACT: Final = "B"


@dataclass(frozen=True)
class BarrierDecision:
    candidate_id: str
    horizon_hours: int
    verdict: Verdict
    reason: Reason | None
    p: tuple[Decimal, Decimal, Decimal] | None  # (target, stop, timeout)
    atr: Decimal | None
    evidence_ids: tuple[str, ...]
    observation_set_hash: str
    mapping_sha256: str
    invalidation: tuple[str, ...]


def agent_version_hash(mapping: BarrierRates) -> str:
    """The hash that stands for this arm's barrier forecasts: the rule, its constants and the mapping."""
    return hashlib.sha256(
        canonical.dumps(
            {
                "arm": ARM,
                "contract": CONTRACT,
                "rule_version": RULE_VERSION,
                "mapping": mapping.sha256,
                "min_cell": MIN_CELL,
                "k_target": format(K_TARGET, "f"),
                "k_stop": format(K_STOP, "f"),
            }
        )
    ).hexdigest()


def _number(item: EvidenceItem, name: str) -> Decimal | None:
    value = item.field(name)
    if not isinstance(value, str):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def decide(reader: Reader, candidate_id: str, mapping: BarrierRates, hours: int) -> BarrierDecision:
    """The arm's barrier decision for one candidate and one horizon, from what the reader gives it."""
    if hours not in HORIZONS:
        raise RulesError(f"the horizon is one of {HORIZONS} hours")
    items = [item for item in reader.items(candidate_id) if item.source == SOURCE and item.kind is Kind.DERIVED]
    ids = reader.ids(candidate_id)

    def result(
        verdict: Verdict, reason: Reason | None, p: tuple[Decimal, Decimal, Decimal] | None, atr: Decimal | None, used: tuple[str, ...]
    ) -> BarrierDecision:
        return BarrierDecision(
            candidate_id,
            hours,
            verdict,
            reason,
            p,
            atr,
            used,
            reader.set_hash(candidate_id, used),
            mapping.sha256,
            INVALIDATION if verdict is Verdict.CALL else (),
        )

    if len(items) != 1:
        return result(Verdict.FAILED_EVIDENCE, Reason.NO_FEATURES, None, None, ())
    item = items[0]
    used = (item.evidence_id, *(p for p, _ in item.derived_from if p in ids))
    atr = _number(item, "atr14")
    if atr is None:
        return result(Verdict.FAILED_EVIDENCE, Reason.FEATURE_UNKNOWN, None, None, used)
    if atr <= 0:
        return result(Verdict.NO_CALL, Reason.NO_RANGE, None, None, used)
    p = mapping.p(hours)
    if p is None:
        return result(Verdict.NO_CALL, Reason.UNMAPPED, None, atr, used)
    return result(Verdict.CALL, None, p, atr, used)
