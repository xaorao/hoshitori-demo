"""When an agent reverses a call it still has open: a later call by the same agent, on the same token and horizon, pointing the other way.

A committed call is never edited. When the next call of the same agent on the same token and horizon starts before the earlier one's window
has closed, and the two point in opposite directions (one above an even chance of a rise, the other below), the agent has revised its view
while the first call is still running. This module finds those pairs from the ledger's records alone and says what the two calls were and
what the evidence each was made on showed: the last close and the return over the horizon, read from each cycle's own frozen features. It
writes no sentence of its own about why; the two readings are shown side by side, and the reader sees what moved.

* **Every reversal is linked.** Both receipts lead to each other, however small the swing.
* **Only a decisive one is an alert.** A swing from 51 to 49 percent is a reversal in name only; a revision becomes an alert
  (``decisive``) when both calls stand at least ``MARGIN`` from an even chance. The margin is fixed here, not tuned on outcomes.
* A call at exactly an even chance points nowhere and is never part of a reversal. A call without a probability (an abstention) is not a
  call. Windows that do not overlap (the next call starts after the first has closed) are a new call, not a revision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Any, Final

from hoshitori.timeline import to_ms
from hoshitori.timeutil import parse_rfc3339

HOUR_MS: Final = 3_600_000
HALF: Final = Decimal("0.5")
MARGIN: Final = Decimal("0.05")  # both calls at least this far from an even chance for the reversal to be an alert
FEATURES_SOURCE: Final = "hoshitori.features"


@dataclass(frozen=True)
class Reading:
    """What the frozen evidence of a cycle showed for the token: the last close, and the return over the call's horizon."""

    close: str | None
    ret: str | None


@dataclass(frozen=True)
class Revision:
    earlier: str  # the forecast id of the call that was still open
    later: str  # the forecast id of the call that pointed the other way
    arm: str
    symbol: str
    hours: int
    earlier_p: str
    later_p: str
    earlier_origin: str
    later_origin: str
    earlier_reading: Reading
    later_reading: Reading
    decisive: bool


def _reading(fields: Mapping[str, Any] | None, hours: int) -> Reading:
    if fields is None:
        return Reading(None, None)
    close, ret = fields.get("close"), fields.get(f"ret_{hours}h")
    return Reading(close if isinstance(close, str) else None, ret if isinstance(ret, str) else None)


def revisions(records: Sequence[Mapping[str, Any]]) -> tuple[Revision, ...]:
    """Every reversal of an open up-or-down call, oldest first."""
    calls = {
        r["id"]: r["body"]
        for r in records
        if r.get("type") == "forecast" and r["body"]["contract"] == "D" and r["body"].get("p") is not None
    }
    origins = {
        r["body"]["forecast_id"]: r["body"]["origin"]
        for r in records
        if r.get("type") == "forecast_state" and r["body"]["state"] == "OPEN" and r["body"].get("origin") is not None
    }
    evidence = {r["id"]: r["body"] for r in records if r.get("type") == "evidence" and r["body"].get("source") == FEATURES_SOURCE}
    features: dict[tuple[str, str], Mapping[str, Any]] = {}  # (cycle, asset) -> the features fields its snapshot froze
    for r in records:
        if r.get("type") != "snapshot":
            continue
        for cand in r["body"]["candidates"]:
            for item in cand["items"]:
                body = evidence.get(item["evidence_id"])
                if body is not None:
                    features[(r["body"]["cycle_id"], cand["asset_id"])] = body["fields"]
    lines: dict[tuple[str, str, int], list[tuple[int, str]]] = {}
    for fid, body in calls.items():
        if fid in origins and Decimal(body["p"]) != HALF:
            lines.setdefault((body["arm_id"], body["asset_id"], body["horizon_ms"]), []).append((to_ms(parse_rfc3339(origins[fid])), fid))
    out = []
    for (arm, asset, horizon), line in lines.items():
        line.sort()
        for (start, first), (next_start, second) in pairwise(line):
            a, b = calls[first], calls[second]
            pa, pb = Decimal(a["p"]), Decimal(b["p"])
            if next_start >= start + horizon or (pa > HALF) == (pb > HALF):
                continue
            hours = horizon // HOUR_MS
            out.append(
                Revision(
                    first,
                    second,
                    arm,
                    a["symbol"],
                    hours,
                    a["p"],
                    b["p"],
                    origins[first],
                    origins[second],
                    _reading(features.get((a["cycle_id"], asset)), hours),
                    _reading(features.get((b["cycle_id"], asset)), hours),
                    abs(pa - HALF) >= MARGIN and abs(pb - HALF) >= MARGIN,
                )
            )
    return tuple(sorted(out, key=lambda v: (v.later_origin, v.arm, v.symbol, v.hours)))
