"""The checks the product runs itself on recorded claims, from the league's own evidence.

For each recorded claim, each part read as an observation about a price move (``claim_move``) is judged against the latest committed
snapshot (``move_check``). Nothing is fetched and nothing is guessed: a part that is not about a move that happened, or leaves its asset or
window open, is not checked, and the result says why. The result names the evidence it was judged against and the time of the snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from hoshitori.claim_move import read_move
from hoshitori.claims_file import Claim
from hoshitori.move_check import Held, check_move
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms


@dataclass(frozen=True)
class Check:
    claim_id: str
    node_id: str
    words: str
    status: str
    reason: str
    evidence_id: str | None
    cycle_id: str | None
    snapshot_at: str | None  # an RFC 3339 UTC time: the end of the window that was judged


def checks_for(claims: Sequence[Claim], held: Mapping[str, Held]) -> tuple[Check, ...]:
    """Every check that can be run on the claims, in the order of the claims and of their parts."""
    out: list[Check] = []
    for claim in claims:
        if claim.page is None:
            continue
        for part in claim.page["parts"]:
            if part["kind"] != "OBSERVATION":
                continue
            assertion = read_move(part["quote"], tuple(claim.assets))
            if assertion is None:
                continue
            result = check_move(assertion, held)
            at = None if result.snapshot_at_ms is None else format_rfc3339_ms(from_ms(result.snapshot_at_ms))
            out.append(
                Check(claim.id, part["node_id"], part["quote"], result.status, result.reason, result.evidence_id, result.cycle_id, at)
            )
    return tuple(out)
