"""How old the evidence was when the forecast's window began, and how far the price had moved by then (contract section 25, item 8).

Two numbers are known for every forecast from records the ledger already holds, so nothing is added to a committed record and no old
record changes:

* ``age_ms``: the forecast origin ``T0_cycle`` less the evidence time ``as_of``. It is known as soon as the origin is set.
* ``drift_atr``: ``|P0 - close| / ATR(14)``, where ``close`` and ``ATR(14)`` are the features computed from the frozen snapshot and ``P0``
  is the reference price the settlement read at the origin. It is known once the forecast has settled with a reference price; until
  then, and whenever the features lack a close or an ATR above zero, it is none, never zero.

No forecast is dropped for either number. They stand beside the results, which are split into three groups of equal size by each
(``tercile_cuts``, ``stratum``), so a reader can see whether older or more-drifted evidence did worse. The groups are the same for every
arm of a contract, computed from the calls that settled, and shown only when there are enough of them to fill each group
(``MIN_PER_STRATUM``); the split is exploratory unless the protocol names it a primary comparison.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Final

from hoshitori import lifecycle
from hoshitori.timeline import to_ms
from hoshitori.timeutil import TimeError, parse_rfc3339

MIN_PER_STRATUM: Final = 3  # settled calls in each of the three groups before a split is shown
PRECISION: Final = 60


@dataclass(frozen=True)
class Gap:
    age_ms: int | None
    drift_atr: Decimal | None


def _decimal(value: object) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        out = Decimal(value)
    except InvalidOperation:
        return None
    return out if out.is_finite() else None


def _as_of_ms(text: object) -> int | None:
    if not isinstance(text, str):
        return None
    try:
        return to_ms(parse_rfc3339(text))
    except TimeError:
        return None


def _features(by_id: Mapping[str, Mapping[str, Any]], evidence_ids: Sequence[str]) -> tuple[Decimal, Decimal] | None:
    """The close and the ATR(14) of the features item among the forecast's evidence, if both are there and the ATR is above zero."""
    for ident in evidence_ids:
        body = by_id.get(ident)
        if body is None or body.get("path") != "features" or body.get("source") != "hoshitori.features":
            continue
        fields = body.get("fields")
        if not isinstance(fields, dict):
            return None
        close, atr = _decimal(fields.get("close")), _decimal(fields.get("atr14"))
        return (close, atr) if close is not None and atr is not None and atr > 0 else None
    return None


def gaps(records: Sequence[Mapping[str, Any]]) -> dict[str, Gap]:
    """The gap numbers of every forecast in the records, by forecast id."""
    histories = lifecycle.rebuild(
        [{k: v for k, v in r.items() if k != "committed_at"} for r in records if r["type"] == "forecast_state"]
    )  # the time the ledger wrote it is not part of it
    evidence = {r["id"]: r["body"] for r in records if r["type"] == "evidence"}
    p0 = {r["body"]["forecast_id"]: _decimal(r["body"].get("p0")) for r in records if r["type"] == "settlement"}
    out: dict[str, Gap] = {}
    for record in records:
        if record["type"] != "forecast":
            continue
        fid, body = record["id"], record["body"]
        as_of = _as_of_ms(body.get("as_of"))
        history: lifecycle.History = histories.get(fid, ())
        origin = next((t.step.origin_ms for t in history if t.step.origin_ms is not None), None)
        age = None if as_of is None or origin is None else origin - as_of
        drift = None
        shown, reference = _features(evidence, body.get("evidence_ids", [])), p0.get(fid)
        if shown is not None and reference is not None:
            with localcontext() as context:
                context.prec = PRECISION
                drift = abs(reference - shown[0]) / shown[1]
        out[fid] = Gap(age, drift)
    return out


def tercile_cuts(values: Sequence[Decimal | int]) -> tuple[Decimal | int, Decimal | int] | None:
    """The two values that split the sorted values into three groups of equal size (the value at one third and two thirds of the way
    through), or none when there are too few values to fill each group."""
    if len(values) < 3 * MIN_PER_STRATUM:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3]


def stratum(value: Decimal | int, cuts: tuple[Decimal | int, Decimal | int]) -> int:
    """0 for the lowest third, 1 for the middle, 2 for the highest; a value on a cut belongs to the group above it."""
    return 0 if value < cuts[0] else 1 if value < cuts[1] else 2
